# Copyright 2023 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import os
import threading

import psutil

import src.utils as utils
from src.singleton_meta import Singleton

logger = logging.getLogger("TaskKiller")


def _is_named(proc, names):
    """True if `proc` is one of `names` (case-insensitive exe name)."""
    try:
        return proc.name().lower() in {n.lower() for n in names}
    except Exception:
        # An unreadable name is treated as "not the sentinel": reaping a
        # stray overlay matters less than the risk of sparing everything
        # when process info is unavailable.
        return False


def hard_exit(code: int = 0, flush_logs: bool = True):
    """End this process with TerminateProcess, not os._exit.

    os._exit ends in ExitProcess, which kills the other threads and then
    runs every DLL's detach routine in this one. A killed thread can die
    holding a lock a detach routine needs, and then the process never
    finishes exiting. Measured 2026-10-27: an instance hammered by the
    6 s force-kill at 08:58 was still there at 18:40 - one thread left,
    exe file locked, and neither Stop-Process -Force nor taskkill could
    end it (a process already exiting refuses TerminateProcess from
    outside), so installing over it failed. TerminateProcess on ourselves
    is done by the kernel and runs no user-mode code here.
    """
    if flush_logs:
        try:
            for h in logging.getLogger().handlers:
                h.flush()
        except Exception:
            pass
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.GetCurrentProcess.argtypes = []
        k.TerminateProcess.restype = wintypes.BOOL
        k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.TerminateProcess(k.GetCurrentProcess(), code)
    except Exception:
        pass
    os._exit(code)      # only if the above failed


class TaskKiller(metaclass=Singleton):
    """Singleton class for saftly killing the process and free the memory
    """

    # Class-level: exit() must be one-shot across every thread, and the
    # replacement must be spawned exactly once (the force-kill timer and
    # the normal path can otherwise both reach it).
    _exit_lock = threading.Lock()
    _exiting = False
    # Whether the exit in progress ends in a replacement. Read at the two
    # spawn sites rather than captured, so a user's Exit that arrives
    # during an automatic restart's teardown can still call it off.
    _restart_pending = False
    _spawned = False
    # pid of the replacement instance, so our own child sweep spares it.
    _spawned_pid = None
    # Held for the WHOLE spawn, so "spawned" and "its pid" become known
    # together. With only the flag guarded, the other exit path could see
    # _spawned True while the pid was still None and reap the brand-new
    # replacement (it is still our child by ppid).
    _spawn_lock = threading.Lock()

    @classmethod
    def _reap_children(cls, why="", skip_pids=()):
        """Terminate every descendant. Called from BOTH exit paths — the
        graceful one and the force-kill hammer — because a child left
        behind (the Qt overlay) outlives us and accumulates one stray
        process per restart.

        `skip_pids` spares the replacement instance: on the force-kill
        path it is spawned BEFORE this runs (so a blocked reap can never
        cost us a successor), which would otherwise put it in range of
        our own sweep.

        The crash sentinel is spared unconditionally. It is our own child,
        so it used to land in this sweep — and on 2026-08-12 22:06 that
        killed it moments before the main process died of an access
        violation mid-teardown, leaving nothing alive to relaunch and the
        app down until the user noticed 13 minutes later. The sentinel
        exists precisely for the case where teardown does not survive to
        spawn a replacement, so teardown must never be able to take it
        with it. Matched by pid AND by exe name: after a restart chain the
        recorded pid belongs to a previous generation, but the name still
        identifies it."""
        try:
            parent = psutil.Process(os.getpid())
            skip = {int(p) for p in (skip_pids or ())}
            try:
                from src import sentinel as _sentinel
                sp = _sentinel.sentinel_pid()
                if sp:
                    skip.add(int(sp))
                sentinel_names = _sentinel.SENTINEL_PROC_NAMES
            except Exception:
                sentinel_names = ("GazeFace_Sub.exe",)
            children = [c for c in parent.children(recursive=True)
                        if c.pid not in skip
                        and not _is_named(c, sentinel_names)]
            if not children:
                return
            logger.info("Kill children of %s (%s): %s",
                        parent.pid, why, children)
            for c in children:
                try:
                    c.terminate()
                except psutil.NoSuchProcess:
                    pass
            _gone, alive = psutil.wait_procs(children, timeout=1.0)
            for c in alive:
                try:
                    c.kill()
                except psutil.NoSuchProcess:
                    pass
        except Exception as e:
            logger.error("Error reaping children: %s", e)

    @classmethod
    def _spawn_once(cls):
        with cls._spawn_lock:
            if cls._spawned:
                return
            cls._spawned = True
            try:
                from src.restart import spawn_replacement
                cls._spawned_pid = spawn_replacement()
            except Exception as e:
                logger.error("spawn replacement failed: %s", e, exc_info=e)

    @classmethod
    def _replacement_pids(cls):
        """The replacement's pid to spare, once any spawn in progress has
        finished (waits for it)."""
        with cls._spawn_lock:
            return [cls._spawned_pid] if cls._spawned_pid else []

    def __init__(self):
        logger.info("Intialize TaskKiller singleton")
        self.is_started = False

    def start(self):
        if not self.is_started:
            logging.info("Installing google fonts.")
            utils.install_fonts("assets/fonts")

            # Start singletons
            from src.config_manager import ConfigManager
            ConfigManager().start()

            from src.camera_manager import CameraManager
            CameraManager().start()

            from src.controllers import Keybinder, MouseController
            MouseController().start()
            Keybinder().start()

            from src.detectors import FaceMesh
            FaceMesh().start()

            # Start the clipboard monitor if the extension is enabled.
            try:
                from src.extensions.clipboard_history import (
                    maybe_start_monitor)
                maybe_start_monitor()
            except Exception as e:
                logger.error("clipboard monitor start failed: %s", e)

            self.is_started = True

    @classmethod
    def is_exiting(cls) -> bool:
        return cls._exiting

    def exit(self, restart: bool = False, cancel_restart: bool = False):
        """Tear down and end the process.

        `restart=True` launches a replacement first. The spawn happens
        late — after the psutil sweep below, which reaps descendants
        recursively and would otherwise kill the very process we just
        started — and detached, so nothing ties its lifetime to ours.
        """
        # Reentrancy guard. exit() is reachable from the tk thread (menu
        # Restart, window close), the watchdog thread and the camera
        # thread, and a dying app trips several triggers at once. Without
        # this, each entry started its own force-kill timer and its own
        # spawn — two replacements, the loser of which greets the user
        # with "GazeFace is already running".
        #
        # Returns False when an exit was already under way. A restart
        # never overrides it (request_restart checks is_exiting() first
        # and so does not spend a restart slot on it). The user closing
        # the app (cancel_restart=True, from the confirmed Exit) does
        # cancel a pending restart, because bringing the app back is the
        # opposite of what they just asked for. Other plain exits do not:
        # the post-mainloop backstop in run_app runs in the middle of
        # every restart's own teardown.
        with TaskKiller._exit_lock:
            if TaskKiller._exiting:
                cancel = (TaskKiller._restart_pending and not restart
                          and cancel_restart)
                if cancel:
                    TaskKiller._restart_pending = False
            else:
                cancel = None
                TaskKiller._exiting = True
                TaskKiller._restart_pending = bool(restart)
        if cancel is not None:
            if cancel:
                logger.info("Exit requested during a restart - the "
                            "restart is cancelled")
                try:
                    from src import sentinel
                    sentinel.write_marker(os.getpid(), clean=True,
                                          expect_restart=False)
                except Exception:
                    pass
            return False
        logger.info("Exit program (restart=%s)", restart)

        # Tell the crash sentinel this is a DELIBERATE exit, so it does not
        # relaunch us. `expect_restart` keeps it honest: on a restart the
        # sentinel goes on to CHECK that the replacement really came up,
        # because a teardown that wedges before spawn_replacement runs
        # would otherwise leave no app at all and look like a clean quit.
        try:
            from src import sentinel
            sentinel.write_marker(os.getpid(), clean=True,
                                  expect_restart=bool(restart))
        except Exception:
            pass

        # Hard guarantee against "process residual": no matter what any
        # teardown step below does (a wedged pool.shutdown, a stuck C
        # call inside a thread join, a blocked device release), force
        # the whole process to die within a few seconds. This daemon
        # backstop calls os._exit() — which bypasses every blocked
        # cleanup path — so the window closing always means the process
        # is gone, even if graceful teardown hangs.
        import threading
        import time as _time

        def _force_kill():
            # Longer than the camera teardown's own join timeouts
            # (2 s + 3 s) so a normal-but-slow teardown still finishes
            # font removal / child reaping before the hammer falls.
            _time.sleep(6.0)
            # Order matters, and it is deliberately NOT the obvious one:
            # the replacement is spawned FIRST, before logging and before
            # reaping. Everything else in this path can block — logging
            # takes the handler lock (several instances share one rotating
            # file), and reaping waits on psutil — and if it does, the app
            # dies with no successor. Getting the replacement out the door
            # first makes it the one thing that cannot be starved.
            #
            # The replacement is detached AND started after this process
            # has stopped doing real work, so the reap below cannot
            # meaningfully race it; a fresh child that young is not in the
            # snapshot psutil takes next.
            if TaskKiller._restart_pending:
                TaskKiller._spawn_once()
            try:
                logger.warning("Teardown exceeded 6s — forcing os._exit")
            except Exception:
                pass
            # Reap children HERE too. The graceful path reaps them before
            # exiting, but this path skipped it entirely — so every
            # hammered teardown orphaned the Qt overlay subprocess (and
            # the crash sentinel), which is how a restart loop ended up
            # leaving one stray GazeFace.exe per generation behind.
            TaskKiller._reap_children(
                "force-kill",
                skip_pids=TaskKiller._replacement_pids())
            # No log flush: the handler lock may be what is stuck.
            hard_exit(0, flush_logs=False)

        threading.Thread(target=_force_kill, name="ForceKill",
                         daemon=True).start()

        # Stop the self-recovery watchdog so it doesn't kick off a
        # camera reopen / facemesh rebuild mid-teardown.
        try:
            from src.watchdog import Watchdog
            Watchdog().stop()
        except Exception as e:
            logger.error("Error stopping watchdog: %s", e)

        # Release singletons in order: input devices first (so any in-flight
        # keys are released), then the camera. Doing this *before* killing
        # the process is what prevents the device-handle leaks that used to
        # require a full OS reboot.
        try:
            from src.controllers import Keybinder, MouseController
            MouseController().destroy()
            Keybinder().destroy()
        except Exception as e:
            logger.error("Error destroying controllers: %s", e)

        try:
            from src.detectors import FaceMesh
            FaceMesh().destroy()
        except Exception as e:
            logger.error("Error destroying facemesh: %s", e)

        try:
            from src.camera_manager import CameraManager
            CameraManager().destroy()
        except Exception as e:
            logger.error("Error destroying camera: %s", e)

        try:
            utils.remove_fonts("assets/fonts")
        except Exception as e:
            logger.error("Error removing fonts: %s", e)

        # Reap any leftover child processes. (Spare the replacement if the
        # force-kill thread already started one — a slow-but-finishing
        # teardown can reach this after the 6 s hammer has fired.)
        TaskKiller._reap_children(
            "teardown",
            skip_pids=TaskKiller._replacement_pids())

        # Diagnostic: list any threads still alive at exit. A non-daemon
        # thread here is what would otherwise keep the process alive
        # after the window closes (the "something remains" symptom).
        try:
            import threading
            alive = [t for t in threading.enumerate()
                     if t is not threading.current_thread()]
            non_daemon = [t for t in alive if not t.daemon]
            logger.info(
                "Threads alive at exit: %d total, %d non-daemon. "
                "Non-daemon: %s",
                len(alive), len(non_daemon),
                [t.name for t in non_daemon])
        except Exception as e:
            logger.error("thread dump failed: %s", e)

        if TaskKiller._restart_pending:
            # Everything is torn down and every child has been reaped —
            # safe to start the replacement. It waits on our pid before
            # taking the single-instance mutex; dropping the handle here
            # spares it that wait.
            try:
                from src import single_instance
                single_instance.release()
            except Exception as e:
                logger.error("mutex release failed: %s", e)
            TaskKiller._spawn_once()

        # Skip atexit handlers that may touch already-released resources,
        # and ExitProcess's DLL detach (see hard_exit). The log is flushed
        # first; every remaining thread dies with the process.
        hard_exit(0)
