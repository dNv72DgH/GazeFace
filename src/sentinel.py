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
"""External sentinel process — restarts the app after a HARD crash.

The in-process self-restart (watchdog -> request_restart -> spawn a
replacement) can only rescue failures where Python is still running. A
native crash inside OpenCV during a camera reopen kills the whole
process instantly: no exception is raised, no teardown runs, and the
watchdog dies with everything else, so nothing relaunches the app (the
15:03 "落ちて再起動せず"). The camera thread had just logged
"Reopening camera 0 ... / No frame returned" and the process vanished
without a single further line.

A separate, tiny process closes that gap. The main app spawns it at
start-up and hands it the main PID. The sentinel does nothing but watch
that PID; when it disappears it checks a marker file:

  * marker says {pid: <main>, clean: true}  -> the app exited on purpose
    (window close, an intentional restart whose replacement the app
    itself already spawned) -> the sentinel just exits.
  * marker still says {pid: <main>, clean: false} -> the app died
    without running its exit path, i.e. it crashed -> the sentinel
    relaunches it.
  * marker's pid is some OTHER value -> a newer generation is already
    running (a restart replaced the app) -> the sentinel exits.

Hangs (process alive but frozen) are NOT the sentinel's job — the
existing tk-stall watchdog handles those. The sentinel only reacts to
the process actually being gone.
"""

import json
import logging
import os
import subprocess
import sys
import time

from src import paths

logger = logging.getLogger("Sentinel")

_POLL_S = 1.0

# Only ever ONE sentinel process, however many main instances exist.
# Without this, every main spawns its own watcher and a restart loop
# leaves one behind per generation (observed: 21 sentinels next to 21
# stale mains). A named mutex dies with its owner, so there is no stale
# lock to clean up after a crash.
_MUTEX_NAME = "GazeFace_Sentinel_Mutex"
_ERROR_ALREADY_EXISTS = 183
_mutex_handle = None

# Relaunch budget. A sentinel that keeps resurrecting an app which keeps
# dying is a process fountain, not a recovery mechanism — past this it
# stops and leaves the app down, which is visible and fixable.
RELAUNCH_WINDOW_S = 600.0
MAX_RELAUNCH_IN_WINDOW = 6

# How long to wait for a self-restart's replacement to show up before
# concluding it never will. The replacement waits out the old process
# (up to 20 s in restart.wait_for_pid) and then has to start Python, so
# this has to comfortably exceed that.
RESTART_GRACE_S = 45.0

# The sentinel is spawned BY the main process, so it is one of its
# children and lands in the teardown child sweep unless spared. Teardown
# killing its own last line of defence is exactly how the app stayed down
# on 2026-08-12 22:06 (see TaskKiller._reap_children), so both the pid and
# the exe name are published here for that sweep to skip.
SENTINEL_PROC_NAMES = ("GazeFace_Sub.exe",)

# The main app ships as two exes with identical code and one differing
# manifest bit: GazeFace_UI.exe carries the UIAccess flag (see
# src/ui_access.py). Anything here that recognises "the main app" has to
# accept both, or enabling UIAccess silently switches crash recovery off:
# the sentinel would see zero main instances, count the running app as
# "another instance", and decline to relaunch it after a crash.
MAIN_PROC_NAMES = ("GazeFace.exe", "GazeFace_UI.exe")
# Windows process names are case-insensitive and psutil reports whatever
# case the file on disk happens to have (an in-place upgrade over an older
# lower-case install keeps the old spelling), so match on lower case.
_MAIN_LOWER = tuple(n.lower() for n in MAIN_PROC_NAMES)
_sentinel_pid = None


def sentinel_pid():
    """Pid of the sentinel this process spawned, or None."""
    return _sentinel_pid


def _claim_singleton() -> bool:
    """True if this process is now THE sentinel; False if one exists."""
    global _mutex_handle
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        _mutex_handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
            return False
        return True
    except Exception:
        # No mutex available — better to watch than to silently not.
        return True


def _relaunch_history():
    m = _read_marker()
    out = []
    for t in (m.get("relaunches") or []):
        try:
            out.append(float(t))
        except Exception:
            pass
    return out


def clear_relaunch_history():
    """Forget the relaunch budget — the app has proved it can stay up.

    Without this the budget only decayed with time, so two unrelated
    faults an hour apart could still add up and switch crash recovery off
    for the second one. Called from restart.note_healthy, alongside the
    in-app restart history it already clears.
    """
    try:
        m = _read_marker()
        if not m.get("relaunches"):
            return
        m.pop("relaunches", None)
        p = paths.sentinel_marker_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(m, f)
        logger.info("Uptime healthy — cleared sentinel relaunch history")
    except Exception as e:
        logger.error("relaunch history clear failed: %s", e)


def _notify_gave_up(count: int) -> None:
    """Tell the user that automatic recovery has stopped.

    A native MessageBox, not a tk dialog: there is no app left to host a
    dialog, and this process deliberately imports no GUI stack.
    """
    try:
        import ctypes
        MB_ICONWARNING = 0x30
        MB_SETFOREGROUND = 0x10000
        ctypes.windll.user32.MessageBoxW(
            0,
            "GazeFace が %d 回連続で停止したため、自動復帰を中止しました。\n"
            "手動で起動し直してください。\n\n"
            "GazeFace stopped %d times in a row; automatic restart has "
            "been disabled. Please start it manually."
            % (count, count),
            "GazeFace", MB_ICONWARNING | MB_SETFOREGROUND)
    except Exception as e:
        logger.error("give-up notification failed: %s", e)


def _note_relaunch():
    now = time.time()
    hist = [t for t in _relaunch_history() if now - t < RELAUNCH_WINDOW_S]
    hist.append(now)
    m = _read_marker()
    m["relaunches"] = hist
    try:
        p = paths.sentinel_marker_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(m, f)
    except Exception as e:
        logger.error("relaunch history write failed: %s", e)


def live_main_count(exclude_pid=None) -> int:
    """How many MAIN app processes are running right now.

    The Qt overlay is the SAME exe relaunched as `GazeFace.exe
    --qt-overlay`, so a name-only count sees every normal single-instance
    run as "two instances". That mistake disabled crash recovery
    outright: the app logged "1 other GazeFace instance(s) running —
    crash sentinel not started" on a perfectly ordinary launch, and when
    it later died in a camera reopen nothing brought it back. Overlay
    processes must be excluded.

    A process whose command line cannot be read (access denied) is NOT
    counted: guessing "main" there would resurrect the same bug.
    """
    try:
        import psutil
    except Exception:
        return 0
    n = 0
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if (p.info["name"] or "").lower() not in _MAIN_LOWER:
                continue
            if exclude_pid is not None and p.info["pid"] == exclude_pid:
                continue
            cmd = p.cmdline()
            if not cmd:
                continue            # unreadable — don't assume it's a main
            if any("--qt-overlay" in str(a) for a in cmd):
                continue            # the overlay, not another app instance
            n += 1
        except Exception:
            continue
    return n


# How fresh the main app's heartbeat has to be for the sentinel to accept
# "another instance is already running and working". A stale heartbeat
# means every surviving process is wedged, and refusing to relaunch on
# their account would leave the user with no working app at all.
HEARTBEAT_FRESH_S = 60.0


# Set for the copy running on the secure desktop (src/desktop_guard.py).
# It lives next to the everyday copy and must not touch that copy's crash
# record: its start would overwrite the recorded pid, and its exit would
# mark the everyday copy as cleanly closed.
DISABLED = False


def note_heartbeat(pid: int) -> None:
    """Stamp 'a main app is alive and its UI thread is still ticking'.

    Called from the tk beat, so it stops on its own if that thread wedges
    — which is precisely the state where the surviving process must NOT
    count as a reason to skip crash recovery.
    """
    if DISABLED:
        return
    try:
        p = paths.sentinel_marker_path()
        m = _read_marker()
        m["hb"] = time.time()
        m["hb_pid"] = int(pid)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(m, f)
    except Exception:
        pass


def healthy_other_main(exclude_pid) -> bool:
    """True when a DIFFERENT main instance is alive and still ticking."""
    if live_main_count(exclude_pid=exclude_pid) <= 0:
        return False
    m = _read_marker()
    try:
        hb = float(m.get("hb", 0))
        hb_pid = int(m.get("hb_pid", -1))
    except Exception:
        return False
    if hb_pid == exclude_pid:
        return False            # the heartbeat belonged to the dead one
    return (time.time() - hb) < HEARTBEAT_FRESH_S


def write_marker(pid: int, clean: bool, expect_restart: bool = False) -> None:
    """Record the running app's pid and whether a clean exit is in
    progress. Written (clean=False) at start-up and flipped to True the
    moment the app begins an intentional shutdown.

    `expect_restart` marks the difference between "the user quit" and
    "we are restarting ourselves". Both are clean exits, but only the
    second one PROMISES that another instance will appear — and that
    promise can be broken (a teardown that wedges before it manages to
    spawn the replacement leaves no app at all, which is exactly how the
    app went missing for ten minutes on 08-04). The sentinel treats a
    broken promise as something to fix.

    The relaunch history is preserved across writes — it belongs to the
    sentinel's budget, not to any one app generation.
    """
    if DISABLED:
        return
    try:
        p = paths.sentinel_marker_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        old = _read_marker()
        data = {"pid": int(pid), "clean": bool(clean),
                "expect_restart": bool(expect_restart)}
        for k in ("relaunches", "hb", "hb_pid"):
            if old.get(k) is not None:
                data[k] = old[k]
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        logger.error("sentinel marker write failed: %s", e)


def _read_marker() -> dict:
    try:
        p = paths.sentinel_marker_path()
        if p.is_file():
            with open(p, encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception:
        pass
    return {}


def _main_exe():
    """Path to the MAIN app exe, resolved relative to whichever exe is
    currently running (the main app spawns the sentinel, the sentinel
    relaunches the main app — both live in the same dir).

    Always the ORDINARY build, never GazeFace_UI.exe, even when the user
    has UIAccess switched on. This runs after a hard crash, and the
    UIAccess exe is the one Windows can refuse to start at all; a
    recovery path that can be refused is not a recovery path. The app
    reads the setting on startup and switches itself over once it is
    safely up (see run_app.py), so the preference is honoured without
    betting the crash recovery on it.
    """
    return os.path.join(os.path.dirname(sys.executable), "GazeFace.exe")


def _sub_exe():
    """Path to the sentinel exe (GazeFace_Sub.exe), same directory."""
    return os.path.join(os.path.dirname(sys.executable), "GazeFace_Sub.exe")


def _main_launch_cmd(wait_pid: int):
    """Command that starts a fresh main app, waiting for `wait_pid` to die
    first (so it clears the single-instance mutex before the guard runs)."""
    if getattr(sys, "frozen", False):
        return [_main_exe(), "--wait-pid", str(wait_pid)]
    script = os.path.join(str(paths.install_dir()), "run_app.py")
    return [sys.executable, script, "--wait-pid", str(wait_pid)]


def spawn_sentinel(main_pid: int) -> bool:
    """Launch the sentinel process (GazeFace_Sub.exe) from the main app."""
    if DISABLED:
        return False
    try:
        if getattr(sys, "frozen", False):
            cmd = [_sub_exe(), str(main_pid)]
        else:
            script = os.path.join(str(paths.install_dir()),
                                  "sentinel_main.py")
            cmd = [sys.executable, script, str(main_pid)]
        global _sentinel_pid
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        p = subprocess.Popen(
            cmd, cwd=str(paths.install_dir()),
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True)
        _sentinel_pid = p.pid
        logger.info("Sentinel spawned to watch pid %s (sentinel pid=%s)",
                    main_pid, p.pid)
        return True
    except Exception as e:
        logger.error("spawn_sentinel failed: %s", e, exc_info=e)
        return False


def _relaunch_main(dead_pid: int) -> None:
    try:
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        subprocess.Popen(
            _main_launch_cmd(dead_pid), cwd=str(paths.install_dir()),
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True)
        logger.warning("Sentinel relaunched the app after a hard crash "
                       "(dead pid %s)", dead_pid)
    except Exception as e:
        logger.error("sentinel relaunch failed: %s", e, exc_info=e)


def run_sentinel(main_pid: int) -> None:
    """Watch `main_pid`; relaunch the app if it dies uncleanly.

    Runs as its own process (`--sentinel <pid>`). Returns only when it has
    decided to stop watching (the app exited cleanly, a newer generation
    took over, or it relaunched the app itself).
    """
    if not _claim_singleton():
        logger.info("Sentinel already running — this one exits")
        return
    logger.info("Sentinel watching pid %s", main_pid)
    try:
        import psutil
    except Exception as e:
        logger.error("sentinel has no psutil — cannot watch: %s", e)
        return

    # Wait for the process to disappear.
    while True:
        try:
            alive = psutil.pid_exists(main_pid)
        except Exception:
            alive = False
        if not alive:
            break
        time.sleep(_POLL_S)

    # It's gone — a moment for the marker to settle if the app was mid
    # clean-exit when it vanished.
    time.sleep(0.3)
    marker = _read_marker()
    marker_pid = marker.get("pid")
    clean = bool(marker.get("clean", False))

    if marker_pid is not None and int(marker_pid) != int(main_pid):
        logger.info("Sentinel: a newer app generation (pid %s) is active "
                    "— nothing to do", marker_pid)
        return

    if clean and bool(marker.get("expect_restart", False)):
        # The app said it was restarting itself. Verify that it actually
        # managed to: a teardown that wedges before spawn_replacement runs
        # leaves the user with no app and, because the exit was "clean",
        # nothing else would ever notice. Wait for the promised instance,
        # then step in if it never arrives.
        deadline = time.time() + RESTART_GRACE_S
        while time.time() < deadline:
            if live_main_count(exclude_pid=main_pid) > 0:
                logger.info("Sentinel: self-restart completed — nothing "
                            "to do")
                return
            time.sleep(_POLL_S)
        logger.error("Sentinel: app exited for a self-restart but no "
                     "replacement appeared within %.0fs — starting one",
                     RESTART_GRACE_S)
    elif clean:
        logger.info("Sentinel: app exited cleanly — nothing to do")
        return

    # Another main is already up AND still ticking (the app restarted
    # itself, or the user runs with allow_multi_instance). Relaunching
    # would ADD an instance rather than replace one, and every added
    # instance brings its own camera contention. Wedged leftovers do NOT
    # count — otherwise a few stale processes disable crash recovery for
    # good.
    if healthy_other_main(main_pid):
        logger.warning("Sentinel: another GazeFace instance is running and "
                       "responsive — not relaunching")
        return

    now = time.time()
    recent = [t for t in _relaunch_history() if now - t < RELAUNCH_WINDOW_S]
    if len(recent) >= MAX_RELAUNCH_IN_WINDOW:
        logger.error("Sentinel: %d relaunches in %.0f min already — the app "
                     "is not staying up. Stopping rather than spawning more.",
                     len(recent), RELAUNCH_WINDOW_S / 60.0)
        # Giving up silently is the worst outcome for the person using
        # this: the app is a head-tracking mouse, so when it stays gone
        # they may have no easy way to notice or to react. Say it.
        _notify_gave_up(len(recent))
        return

    logger.warning("Sentinel: app pid %s vanished with no clean-exit mark "
                   "— treating as a crash, relaunching", main_pid)
    _note_relaunch()
    _relaunch_main(main_pid)
