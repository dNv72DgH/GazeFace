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
"""Restarting the app itself.

The watchdog's soft reset can only re-open the camera and rebuild the
MediaPipe model. Some failures survive that — a camera stuck at 1 fps
after a reopen, a FaceMesh rebuild that itself fails, a tk main thread
wedged for a minute — and in every one of them the app keeps running,
keeps logging healthy worker heartbeats, and tracks nothing. For those,
the only honest recovery left is a full restart.

Three things make restarting this app non-obvious, all handled here:

  * the single-instance mutex is held for the process lifetime, so a
    child spawned before we die would see "already running" and quit.
    The child is passed --wait-pid and waits us out (and we close the
    handle early to shorten that wait).
  * TaskKiller's teardown reaps every descendant with psutil, so the
    replacement must be spawned AFTER that sweep and detached, or it
    would be killed on its way up.
  * paths were historically CWD-relative, so the child is launched with
    an explicit cwd rather than inheriting whatever we had.

Restart budget: a restart that does not fix the problem will just be
requested again, so a loop is the failure mode to design against.
MAX_IN_WINDOW restarts inside ten minutes means restarting is not
working. If — and
only if — those restarts happen while the app is still starting up, the
config it cannot survive reading is the prime suspect, and config_backup
walks the daily backups newest-first, then the factory template. A loop
that starts AFTER the app has been up a while (see
CONFIG_SUSPECT_UPTIME_S) is never blamed on config: the config was
already read and applied, so a rollback would destroy real settings
without fixing anything. Either way, once there is nothing left to try we
stop restarting and stay up (broken but diagnosable) rather than spin.
"""

import json
import logging
import os
import subprocess
import sys
import threading
import time

from src import config_backup
from src import paths

logger = logging.getLogger("Restart")

# A restart "spell": how far back we look, and how many restarts in that
# window mean restarting isn't the answer.
WINDOW_S = 600.0            # 10 minutes
MAX_IN_WINDOW = 6
# Uptime after which we declare the spell over and clear the history.
HEALTHY_S = 300.0           # 5 minutes

_requested = False           # one restart per process, ever
_lock = threading.Lock()     # guards the claim of that one restart

# Config can only be blamed for a failure that happens while the app is
# still coming up. Past this uptime the config has demonstrably been read,
# parsed and applied, so a later fault (a wedged tk thread, a camera that
# died) says nothing about it and MUST NOT trigger a rollback. Getting
# this wrong once walked every daily backup and then the factory template
# over a user's real settings while chasing a 60 s tk stall.
CONFIG_SUSPECT_UPTIME_S = 90.0
_start_ts = time.time()
# Set once this process has declined a restart because of the loop cap.
# Such a process is up but NOT healthy, so its uptime must not clear the
# history that is holding the cap (note_healthy).
_gave_up = False

# Passed to a replacement that was started as a DIFFERENT exe than the one
# that spawned it (the UIAccess switch). Its only job is to stop that
# process switching again, so the swap can never become a loop no matter
# what the setting says or what Windows allows.
#
# A command-line flag rather than an environment variable: the UIAccess
# exe has to be launched with ShellExecuteEx (CreateProcess refuses it
# outright), and ShellExecuteEx gives the child the caller's environment
# with no way to add to it. The flag travels on either launch path.
EXE_SWITCH_FLAG = "--exe-switched"


def switch_guard_set() -> bool:
    return EXE_SWITCH_FLAG in sys.argv


def _config_is_plausible_cause() -> bool:
    return (time.time() - _start_ts) < CONFIG_SUSPECT_UPTIME_S


def _load_times() -> list:
    try:
        p = paths.restart_state_path()
        if p.is_file():
            with open(p, encoding="utf-8") as f:
                return [float(t) for t in json.load(f).get("times", [])]
    except Exception as e:
        logger.error("restart state read failed: %s", e)
    return []


def _load_gave_up() -> bool:
    try:
        p = paths.restart_state_path()
        if p.is_file():
            with open(p, encoding="utf-8") as f:
                return bool(json.load(f).get("gave_up"))
    except Exception:
        pass
    return False


def _save_times(times: list, gave_up=None):
    """Write the history. `gave_up` None keeps the stored flag."""
    if gave_up is None:
        gave_up = _load_gave_up()
    try:
        p = paths.restart_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"times": times, "gave_up": bool(gave_up)}, f)
    except Exception as e:
        logger.error("restart state write failed: %s", e)


def note_healthy():
    """The app has been up long enough to call the bad spell over.

    Clears both the restart history and config_backup's record of which
    backups were already tried, so a future problem starts from a clean
    budget and the newest backup.
    """
    if _gave_up:
        logger.info("Uptime reached, but restarts were given up on in "
                    "this run — keeping the restart history")
        return
    if _load_times() or _load_gave_up():
        logger.info("Uptime healthy — clearing restart history")
    _save_times([], gave_up=False)
    config_backup.clear_tried()
    # The out-of-process sentinel keeps its own budget; a healthy run ends
    # its bad spell too, or an old fault could still cost us the recovery
    # for an unrelated one later.
    try:
        from src import sentinel
        sentinel.clear_relaunch_history()
    except Exception as e:
        logger.error("sentinel history clear failed: %s", e)


def restarts_in_window(now=None) -> int:
    now = now if now is not None else time.time()
    return len([t for t in _load_times() if now - t < WINDOW_S])


def request_restart(reason: str, config_suspect: bool = False) -> bool:
    """Restart the app. Returns False if we declined to.

    `config_suspect` says whether the user's settings could be what is
    breaking the app. Only then may a restart loop roll the settings back
    to a backup. Every current trigger is a runtime fault - a camera that
    stopped delivering, a FaceMesh rebuild, a stalled UI thread - and
    passes False: on 2026-10-09 a dead camera restarted the app every
    51 s, each run under CONFIG_SUSPECT_UPTIME_S, and the loop walked the
    settings back through five daily backups (10-09 -> 10-04), throwing
    away days of tuning while the camera stayed dead.

    Safe to call from any thread.
    """
    global _requested, _gave_up
    # Claim the restart under the lock. Triggers fire from three threads
    # (camera, watchdog, tk) and a dying app tends to trip several at
    # once — with the flag set after the file I/O below, two threads both
    # passed the check, both tore down, both spawned a replacement (one
    # of which then greeted the user with "already running"), and one
    # real restart burned two budget slots, bringing on an unwarranted
    # config rollback.
    with _lock:
        if _requested:
            return True                 # already on our way down
        _requested = True
    # The app is already closing (the user chose Exit, or an exe switch
    # is under way): that exit wins, and a restart that will not happen
    # must not be recorded as one - it would spend a slot of the loop
    # budget below.
    try:
        from src.task_killer import TaskKiller
        if TaskKiller.is_exiting():
            logger.info("Restart not requested - the app is already "
                        "exiting (%s)", reason)
            return False
    except Exception:
        pass
    now = time.time()
    times = [t for t in _load_times() if now - t < WINDOW_S]

    # Restarts were given up on - in this run or an earlier one that
    # never reached a healthy uptime since. Without this the cap only
    # rate-limited the loop: each time the oldest restart aged out of the
    # window, one more was allowed, forever.
    if _gave_up or _load_gave_up():
        if not _gave_up:
            logger.error("Restarts were given up on earlier and no run has "
                         "been healthy since — not restarting. Reason "
                         "was: %s", reason)
        _gave_up = True
        with _lock:
            _requested = False
        return False

    if len(times) >= MAX_IN_WINDOW:
        # Restarting has not helped. Config is a suspect ONLY if the app
        # never got past start-up — otherwise it was read and applied
        # successfully and rolling it back destroys good settings while
        # fixing nothing.
        what = ""
        if config_suspect and _config_is_plausible_cause():
            what = config_backup.restore_next_candidate()
        elif not config_suspect:
            logger.warning(
                "Restart loop (%d in %.0f min) from a runtime fault — "
                "settings are not the cause, NOT rolling them back. "
                "Reason was: %s", len(times), WINDOW_S / 60.0, reason)
        else:
            logger.warning(
                "Restart loop (%d in %.0f min) but the app had been up "
                "%.0f s — config is not the cause, NOT rolling it back. "
                "Reason was: %s",
                len(times), WINDOW_S / 60.0, time.time() - _start_ts,
                reason)
        if not what:
            # Logged once per run: the triggers keep firing every few
            # seconds while the fault lasts.
            (logger.debug if _gave_up else logger.error)(
                "Restart loop: %d restarts in %.0f min and no restore "
                "candidate applies. Staying up (broken) rather than "
                "looping. Reason was: %s",
                len(times), WINDOW_S / 60.0, reason)
            _gave_up = True
            _save_times(times, gave_up=True)
            # The app stays up but is not recovering — say so in the
            # status pane rather than leaving it looking normal.
            try:
                from src import app_status
                app_status.set_error(
                    "Recovery stopped",
                    "Restarted %d times without fixing it" % len(times))
            except Exception:
                pass
            # The history is KEPT and the give-up is recorded. Clearing
            # it here let the very next trigger - seconds later, for the
            # same fault - find an empty budget and restart, so the cap
            # never held. Only a run that stays up healthily clears it
            # (note_healthy).
            with _lock:
                _requested = False      # we declined — un-claim it
            return False
        logger.warning(
            "Restart loop (%d in %.0f min) — restored %s, restarting",
            len(times), WINDOW_S / 60.0, what)
        times = []                      # fresh budget for the new config

    times.append(now)
    _save_times(times)
    logger.warning("RESTART requested: %s", reason)
    try:
        from src.app_status import set_restarting
        set_restarting(reason)
    except Exception:
        pass
    try:
        from src.task_killer import TaskKiller
        TaskKiller().exit(restart=True)
    except Exception as e:
        logger.error("restart teardown failed: %s", e, exc_info=e)
        # Teardown is best-effort; the replacement still needs launching.
        spawn_replacement()
        os._exit(0)
    return True


ERROR_ELEVATION_REQUIRED = 740


def _k32():
    """kernel32 on a PRIVATE loader, with prototypes.

    Both halves matter. Private, because setting argtypes on
    ctypes.windll.* rewrites the signature for the whole process and has
    already broken an unrelated caller here (src/extensions/launcher.py,
    which passes its own POINT type to GetCursorPos).
    Prototyped, because without argtypes a 64-bit HANDLE passed as a
    Python int is truncated to a C int — which is exactly why the first
    version of the exit check below always answered "still running".
    """
    import ctypes
    from ctypes import wintypes
    lib = ctypes.WinDLL("kernel32", use_last_error=True)
    lib.WaitForSingleObject.restype = wintypes.DWORD
    lib.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    lib.CloseHandle.restype = wintypes.BOOL
    lib.CloseHandle.argtypes = [wintypes.HANDLE]
    lib.GetProcessId.restype = wintypes.DWORD
    lib.GetProcessId.argtypes = [wintypes.HANDLE]
    return lib


class _Launched:
    """A started replacement, however it was started."""

    def __init__(self, pid, proc=None, handle=None):
        self.pid = pid
        self._proc = proc
        self._handle = handle

    def exited(self) -> bool:
        if self._proc is not None:
            return self._proc.poll() is not None
        if self._handle:
            try:
                # WAIT_OBJECT_0 (0) means the process object is
                # signalled, i.e. it has already exited.
                return _k32().WaitForSingleObject(self._handle, 0) == 0
            except Exception:
                return False
        return False


def _shell_execute_launch(exe, args, cwd):
    """Start `exe` through the shell, for exes CreateProcess refuses.

    An exe whose manifest asks for UIAccess cannot be started with
    CreateProcess: the call fails with ERROR_ELEVATION_REQUIRED (740)
    because only the AppInfo service can mint a token carrying the
    UIAccess flag. ShellExecuteEx routes through that service. With
    level="asInvoker" this raises no UAC prompt - it is not an elevation
    to administrator, just a differently-built token.

    Returns a _Launched or None.
    """
    import ctypes
    from ctypes import wintypes

    # Private handles. Setting argtypes on ctypes.windll.* rewrites the
    # prototype for the whole process and has already broken an unrelated
    # caller here once (src/extensions/launcher.py and GetCursorPos).
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    k32 = _k32()

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    shell32.ShellExecuteExW.restype = wintypes.BOOL
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    # NOASYNC matters here specifically: the caller is on its way out, and
    # without it ShellExecuteEx can return before the out-of-process
    # handler has finished with our request - which, from a dying
    # process, means the replacement never starts.
    SEE_MASK_NOASYNC = 0x00000100
    SEE_MASK_FLAG_NO_UI = 0x00000400
    SW_SHOWNORMAL = 1

    try:
        # ShellExecuteEx needs COM on this thread. Already-initialised and
        # initialised-in-another-mode both come back as errors we do not
        # care about.
        ole32.CoInitializeEx(None, 0x2)
    except Exception:
        pass

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = (SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
                  | SEE_MASK_FLAG_NO_UI)
    info.lpVerb = "open"
    info.lpFile = exe
    info.lpParameters = subprocess.list2cmdline(args)
    info.lpDirectory = cwd
    info.nShow = SW_SHOWNORMAL

    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        try:
            logger.error("ShellExecuteEx(%s) failed: WinError %s",
                         os.path.basename(exe), err)
        except Exception:
            pass
        return None
    if not info.hProcess:
        return None
    return _Launched(int(k32.GetProcessId(info.hProcess)),
                     handle=info.hProcess)


def spawn_replacement():
    """Launch a fresh copy that waits for this process to die first.

    Returns the new pid (truthy) or None. The caller passes that pid to
    TaskKiller's child sweep so our own teardown does not reap the
    successor we just started.

    The Popen happens BEFORE any logging here: the log handler is shared
    (and, with several instances, contended), and a restart that cannot
    log must still produce a replacement.
    """
    pid = os.getpid()

    # Which exe. The UIAccess build is a separate exe (see src/ui_access.py
    # — the flag comes from the manifest and cannot be toggled at runtime),
    # so switching the setting means the successor is a DIFFERENT file.
    candidates = [sys.executable]
    if getattr(sys, "frozen", False):
        try:
            from src import ui_access
            want = ui_access.target_exe()
            if want and os.path.normcase(want) != os.path.normcase(
                    sys.executable):
                # Privileged build first, ordinary build as the fallback.
                # Windows REFUSES to start a UIAccess exe that is unsigned
                # or outside a secure location, and it refuses at
                # CreateProcess time, so "it launched" is the only proof
                # that it can run. Without the fallback a bad setting
                # would leave the user with no app at all — and for
                # someone whose only input device is this app, that is
                # unrecoverable without help.
                candidates = [want, sys.executable]
        except Exception:
            pass

    # The child must never repeat the switch we are making for it.
    switching = len(candidates) > 1 or os.path.normcase(
        candidates[0]) != os.path.normcase(sys.executable)

    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    cwd = str(paths.install_dir())
    last_err = None
    for i, exe in enumerate(candidates):
        args = []
        if not getattr(sys, "frozen", False):
            args.append(os.path.join(cwd, "run_app.py"))
        args += ["--wait-pid", str(pid)]
        if switching:
            args.append(EXE_SWITCH_FLAG)

        started = None
        try:
            # DETACHED_PROCESS + a new process group: no console, no job
            # inheritance, and nothing tying the child's lifetime to ours.
            proc = subprocess.Popen(
                [exe] + args, cwd=cwd,
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True)
            started = _Launched(proc.pid, proc=proc)
        except OSError as e:
            if getattr(e, "winerror", None) == ERROR_ELEVATION_REQUIRED:
                # Expected for the UIAccess build, not a fault: only the
                # AppInfo service can create a process holding that flag,
                # and CreateProcess is not it.
                try:
                    logger.info("%s needs the shell launch path "
                                "(WinError 740)", os.path.basename(exe))
                except Exception:
                    pass
                started = _shell_execute_launch(exe, args, cwd)
                if started is None:
                    last_err = e
            else:
                last_err = e
                try:
                    logger.error("spawn of %s failed: %s", exe, e)
                except Exception:
                    pass
        except Exception as e:
            last_err = e
            try:
                logger.error("spawn of %s failed: %s", exe, e)
            except Exception:
                pass

        if started is None:
            continue

        # Starting is not proof of survival: a process can be created and
        # die immediately. Only worth waiting for when there is a
        # fallback left to use.
        if i + 1 < len(candidates):
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if started.exited():
                    break
                time.sleep(0.05)
            if started.exited():
                last_err = "exited immediately"
                try:
                    logger.error("spawn of %s %s — falling back",
                                 exe, last_err)
                except Exception:
                    pass
                continue

        try:
            logger.warning("Replacement spawned pid=%s from %s "
                           "(waits for pid %s)",
                           started.pid, os.path.basename(exe), pid)
        except Exception:
            pass
        return started.pid

    try:
        logger.error("spawn_replacement failed for every candidate: %s",
                     last_err)
    except Exception:
        pass
    return None


def wait_for_pid(pid: int, timeout_s: float = 20.0):
    """Block until `pid` exits (or timeout). Run before taking the mutex.

    The outgoing instance holds the single-instance mutex until it dies;
    starting the guard before then would bounce us straight back out.
    """
    try:
        import psutil
    except Exception:
        time.sleep(2.0)                 # no psutil — just give it a beat
        return
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if not psutil.pid_exists(pid):
                logging.info("Old instance %s gone — continuing", pid)
                return
        except Exception:
            return
        time.sleep(0.1)
    logging.warning("Old instance %s still alive after %.0fs — "
                    "continuing anyway", pid, timeout_s)
