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

import os
import sys

from src import paths

# Task Scheduler (and any other launcher) starts the process with an
# arbitrary working directory — for "at logon, run with highest
# privileges" it is C:\Windows\system32 — where the app's relative
# "assets/..." paths (the customtkinter theme JSON, icons, the overlay
# QML) do not resolve, so startup died at import time with
# FileNotFoundError on google_theme.json. Anchor the CWD to the install
# dir before anything else so every relative asset path works no matter
# how we were launched. User data uses absolute paths (src/paths.py) and
# is unaffected. Must run before BOTH the overlay branch below and the
# main app, so it sits above everything.
try:
    os.chdir(paths.install_dir())
except Exception:
    pass

# Fast path: when this exe is relaunched as the Qt gaze overlay
# subprocess (see src.gui.qt_overlay.qt_gaze_grid.OverlayClient), run ONLY
# the overlay and skip the entire head-tracking app and its heavy imports.
if "--qt-overlay" in sys.argv:
    from src.gui.qt_overlay.overlay_main import main as _overlay_main
    raise SystemExit(_overlay_main())

# On the secure desktop (sign-in / lock screen, UAC prompt) run the lite
# app instead: that desktop cannot hold the full UI, and on the sign-in
# screen this process is SYSTEM. See src/lite_main.py.
from src import desktop_guard as _dg
if _dg.on_secure_desktop():
    from src.lite_main import main as _lite_main
    raise SystemExit(_lite_main())

import ctypes
import json
import logging
import logging.handlers
import threading
import time

import customtkinter

import src.gui as gui
from src.config_manager import ConfigManager
from src.pipeline import Pipeline
from src.rotating_log import ResilientRotatingFileHandler
from src.task_killer import TaskKiller

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(funcName)s: %(message)s"


# Settings and log now live under %LOCALAPPDATA%\GazeFace, seeded from
# the copy shipped next to the exe on first run. Must happen before the
# log handler and before anything reads config. See src/paths.py.
_CONFIGS_DIR = paths.ensure_user_data()


def _read_verbose_logging_flag() -> bool:
    """Peek at default.json BEFORE the rest of the app boots so the log
    handler can be set up with the right level/mode. We can't go through
    ConfigManager because logging needs to exist before
    ConfigManager.start() runs."""
    try:
        with open(_CONFIGS_DIR / "default.json") as f:
            data = json.load(f)
        return bool(data.get("verbose_logging", False))
    except Exception:
        return False


_VERBOSE = _read_verbose_logging_flag()

# Rotating log so consecutive sessions accumulate instead of clobbering
# log.txt every restart — essential when chasing an intermittent freeze
# the user can only reproduce sometimes. Keep ~5 sessions worth of
# history.
# ResilientRotatingFileHandler, not the stock one: the stock handler
# DROPS every record once a rotation fails, and on Windows a rotation
# fails for as long as any other process holds the file — which this app
# arranges for itself with allow_multi_instance and with the overlap
# during a restart. See src/rotating_log.py for the incident that
# prompted it.
_log_handler = ResilientRotatingFileHandler(
    str(paths.log_path()), mode='a', maxBytes=2 * 1024 * 1024,
    backupCount=4, encoding='utf-8')

# The file handler is utf-8, but the console StreamHandler inherits the
# OS console encoding (cp932 on Japanese Windows), which can't encode
# characters like the em dash used in some log messages and spews
# "UnicodeEncodeError / Logging error" noise. Force stdout to utf-8 with
# replacement, and only attach the console handler when a real stdout
# exists (a windowed PyInstaller build has stdout=None).
_handlers = [_log_handler]
if sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    _handlers.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(format=FORMAT,
                    level=logging.DEBUG if _VERBOSE else logging.INFO,
                    handlers=_handlers)
logging.info("===== App starting (verbose=%s) =====", _VERBOSE)
try:
    from src import display_info
    display_info.log_modes("at startup")
except Exception:
    pass
try:
    # Our integrity level decides which windows we may send input to at
    # all, so it belongs on the record from the first line of every run.
    # So does UIAccess: with it we may drive higher-integrity windows,
    # without it those windows freeze head tracking, and the two builds
    # are otherwise indistinguishable in a log.
    from src import uipi as _uipi
    _rid, _il = _uipi.integrity_level(os.getpid())
    logging.info("PRIVILEGE integrity=%s uiaccess=%s exe=%s",
                 _il, _uipi.has_uiaccess(), sys.executable)
except Exception:
    pass


# The single-instance mutex lives in src/single_instance.py: teardown
# has to release it, and teardown cannot import THIS module (it is the
# PyInstaller entry script, so it exists only as __main__).


# --- wake-up detection (see MainApp._resume_reason) -------------------- #

# A tick that is this much later than its 60 s schedule means the process
# was not running in between: sleep, hibernate, modern standby or a hard
# freeze. Well above any scheduling jitter, well below a real idle spell.
RESUME_GAP_S = 180.0
# No user input for this long counts as "away". Coming back after that is
# treated as a wake-up even when the machine never slept, because the
# display power-down alone is enough to invalidate the overlay's GPU
# surfaces. Head tracking moves the cursor through SendInput, which does
# refresh the last-input time, so this only accrues when nobody is
# driving the app.
IDLE_LONG_S = 600.0


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def _user_idle_s():
    """Seconds since the last keyboard/mouse input, or None if unknown."""
    try:
        lii = _LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return None
        # GetTickCount is a 32-bit ms counter that wraps every ~49 days;
        # mask the subtraction so a wrap reads as a small delta instead
        # of a 49-day idle.
        now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        return ((now - lii.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return None


class MainApp(gui.MainGui, Pipeline):

    def __init__(self, tk_root):
        super().__init__(tk_root)
        # Wait for window drawing.
        self.tk_root.wm_protocol("WM_DELETE_WINDOW", self.close_all)

        self.is_active = True

        # Enter loop
        self.tk_root.after(1, self.anim_loop)
        # Surviving this long means the restart budget can be forgiven:
        # a genuine fault later starts from a full allowance and the
        # newest backup, instead of inheriting an old bad spell.
        try:
            from src.restart import HEALTHY_S, note_healthy
            self.tk_root.after(int(HEALTHY_S * 1000), note_healthy)
        except Exception:
            pass
        # Warm up the Qt overlay subprocess shortly after launch so the
        # first gaze-grid open is instant instead of paying PySide6's
        # cold-start (~2-3 s). Off-switch: qt_overlay_prewarm = false.
        self.tk_root.after(2500, self._prewarm_overlay)
        # Periodic overlay recycling (see _overlay_recycle_tick). Seeded
        # to "just recycled" so the first one is a full interval away.
        self._last_overlay_recycle = time.time()
        self._last_recycle_tick = time.time()
        self._idle_was_long = False
        self._resume_pending = None
        self.tk_root.after(60000, self._overlay_recycle_tick)

    def _prewarm_overlay(self):
        # Spawn on a throwaway thread, never on the tk main thread. This
        # was the one remaining place where the UI thread launched the
        # overlay process itself: CreateProcess on a 100 MB frozen exe can
        # take seconds under AV scanning, and the tk thread is also the
        # face-detection thread — any time it spends here is time head
        # tracking is frozen. Everything else already goes through the
        # OverlayClient writer thread.
        def _run():
            try:
                cfg = ConfigManager().config or {}
                if (cfg.get("qt_overlay", True)
                        and cfg.get("qt_overlay_prewarm", True)):
                    from src.gui.qt_overlay.qt_gaze_grid import OverlayClient
                    OverlayClient.instance().ensure()
            except Exception as e:
                logging.error("overlay prewarm failed: %s", e)
        try:
            import threading
            threading.Thread(target=_run, name="OverlayPrewarm",
                             daemon=True).start()
        except Exception as e:
            logging.error("overlay prewarm thread failed: %s", e)

    # --- overlay recycling ------------------------------------------- #
    #
    # The Qt overlay's GPU footprint grows over a long session and only a
    # full app restart gave it back. Recycling that ONE process reclaims
    # the same memory while the user keeps their camera, calibration and
    # bindings — but only if it happens when nothing is on screen, so the
    # window never blinks and no gesture is interrupted.

    def _resume_reason(self):
        """Name the wake-up event since the last tick, or None.

        Two things break the overlay's GPU surfaces without killing the
        process, and both look like "the overlay is running but nothing
        renders" afterwards:

          * the machine slept / hibernated / went into modern standby —
            the D3D device is lost and the swapchain is stale on resume;
          * the display idled off (or the GPU dropped to a low power
            state) while the app kept running, which resets the same
            surfaces without any process ever dying.

        The first shows up as a wall-clock gap: our tick is scheduled
        every 60 s, so a much larger gap means we were not running.
        The second does not — the timer keeps firing normally — so it is
        caught from the other side: the user was away for a long time
        and has just come back (raw input seen again).
        """
        now = time.time()
        gap = now - self._last_recycle_tick
        self._last_recycle_tick = now
        if gap >= RESUME_GAP_S:
            self._idle_was_long = False      # this event supersedes it
            return "sleep/freeze gap %.0fs" % gap

        idle = _user_idle_s()
        if idle is None:
            return None
        if idle >= IDLE_LONG_S:
            self._idle_was_long = True       # away — remember it
            return None
        if self._idle_was_long:
            self._idle_was_long = False
            return "returned from long idle"
        return None

    def _overlay_recycle_safe(self):
        """True when nothing is showing, so a respawn cannot be seen."""
        from src.controllers.mouse_controller import MouseController
        mc = MouseController()
        if getattr(mc, "_gaze_grid_active", False):
            return False
        if getattr(mc, "_gaze_move_active", False):
            return False
        if getattr(mc, "_jump_locked", False):
            return False
        try:
            from src.gui.gaze_calibrator import GazeCalibrator
            if GazeCalibrator.is_active():
                return False
        except Exception:
            pass
        return True

    def _overlay_recycle_why(self):
        """Reason to recycle the overlay right now, or None.

        Order matters: the resume check advances the tick clock, so it
        has to run on every tick, including the ones where recycling is
        switched off or unsafe — otherwise the next tick would read the
        skipped minutes as a sleep gap. A wake-up that lands while a
        gaze tool is on screen is held pending and fires on the first
        safe tick instead of being dropped, since the overlay stays
        broken until it is replaced.
        """
        resume = self._resume_reason()       # side-effecting: always first
        if resume:
            self._resume_pending = resume
        cfg = ConfigManager().config or {}
        if not cfg.get("qt_overlay", True):
            return None
        if not self._overlay_recycle_safe():
            return None
        if self._resume_pending:
            why, self._resume_pending = self._resume_pending, None
            if cfg.get("overlay_recycle_on_resume", True):
                return why
        # Default OFF. Periodic recycling was built to head off the
        # "overlay stops drawing" fault; it ran 150 times without ever
        # preventing one (the cause turned out to be a phantom duplicate
        # display, which only an app restart clears). Measured cost:
        # overlay RSS trims itself from ~240 MB to ~80 MB when left alone,
        # and each respawn puts it back to ~180 MB — so recycling made
        # memory worse, not better. It also gave a start-up failure four
        # chances an hour to recur instead of once. Still available for
        # anyone who wants it; just not on by default.
        minutes = float(cfg.get("overlay_recycle_minutes", 0) or 0)
        if minutes <= 0:
            return None                      # periodic recycling disabled
        if time.time() - self._last_overlay_recycle < minutes * 60.0:
            return None
        return "%.0f min elapsed" % minutes

    def _overlay_recycle_tick(self):
        try:
            why = self._overlay_recycle_why()
            if why:
                self._last_overlay_recycle = time.time()

                def _run():
                    try:
                        from src.gui.qt_overlay.qt_gaze_grid import (
                            OverlayClient)
                        logging.info("overlay recycle: %s", why)
                        # A wake-up is exactly when Windows may have put a
                        # 120 Hz display back to 60, which is what makes
                        # the overlay's pacing beat against vsync.
                        from src import display_info
                        display_info.log_modes("at recycle")
                        OverlayClient.instance().recycle()
                    except Exception as e:
                        logging.error("overlay recycle failed: %s", e)
                # Off the tk thread: killing and respawning a process must
                # never sit on the thread that also runs face detection.
                threading.Thread(target=_run, name="OverlayRecycle",
                                 daemon=True).start()
        except Exception as e:
            logging.error("overlay recycle check failed: %s", e)
        try:
            self.tk_root.after(60000, self._overlay_recycle_tick)
        except Exception:
            pass

    def anim_loop(self):
        if not self.is_active:
            return
        try:
            # Run detectors and controllers.
            self.pipeline_tick()
        except Exception as e:
            # Don't drop out of the loop on a transient error — that would
            # leave head tracking dead until the app is restarted.
            logging.critical(e, exc_info=e)
        finally:
            if self.is_active:
                # Run the pipeline at the configured camera rate instead of
                # 1000 Hz. Camera + FaceMesh only update at ~30-60 fps, so
                # ticking faster than that just burns CPU/battery.
                try:
                    tick = max(1, int(
                        ConfigManager().config.get("tick_interval_ms", 16)))
                except Exception:
                    tick = 16
                self.tk_root.after(tick, self.anim_loop)

    def close_all(self):
        # Confirm before quitting — a stray gesture/click on the window's X
        # should not kill head tracking without asking. The dialog is
        # non-modal (see MainGui.confirm_exit), so the shutdown continues
        # from its callback rather than from a blocking return value.
        try:
            self.confirm_exit(self._do_close)
            return
        except Exception as e:
            logging.error("exit confirm failed, closing anyway: %s", e)
        self._do_close()

    def _do_close(self):
        logging.info("Close all")
        # Remember where the window was for next launch.
        try:
            self.save_window_pos()
        except Exception:
            pass
        self.is_active = False
        # Shut down the persistent Qt overlay subprocess (its own stdin-EOF
        # watchdog also covers a hard crash of this process).
        try:
            from src.gui.qt_overlay.qt_gaze_grid import OverlayClient
            OverlayClient.instance().quit()
        except Exception:
            pass
        # Completely close this process. The user asked for it, so it
        # also calls off an automatic restart already tearing down.
        TaskKiller().exit(cancel_restart=True)


if __name__ == "__main__":
    # (The crash sentinel is its own exe now — sentinel_main.py /
    # GazeFace_Sub.exe — not a flag on this one.)

    # Relaunched by src/restart.py: the outgoing instance still holds the
    # single-instance mutex, so wait it out before the guard runs.
    if "--wait-pid" in sys.argv:
        try:
            from src.restart import wait_for_pid
            wait_for_pid(int(sys.argv[sys.argv.index("--wait-pid") + 1]))
        except Exception as e:
            logging.error("wait-pid failed: %s", e)

    # Started by Windows as an assistive technology (src/at_startup.py),
    # after sign-in. (The copy Windows starts on the secure desktop - the
    # sign-in / lock screen, a UAC prompt - left for lite_main at the top
    # of this file, so everything below is the everyday desktop.)
    from src import desktop_guard
    _AT_LAUNCH = "--at" in sys.argv
    logging.info("DESKTOP own=%s input=%s at_launch=%s",
                 desktop_guard.own_desktop(),
                 desktop_guard.input_desktop(), _AT_LAUNCH)

    from src import single_instance
    single_instance.acquire(
        _CONFIGS_DIR, quiet=_AT_LAUNCH, scope="")

    # One config snapshot per day, five kept. Every config write in the
    # app is a truncating rewrite, so a crash mid-write can leave a file
    # the next launch cannot parse; these are what restart.py restores
    # from when repeated restarts say the config itself is the problem.
    try:
        from src import config_backup
        config_backup.backup_daily()
    except Exception as e:
        logging.error("config backup failed: %s", e, exc_info=e)

    tk_root = customtkinter.CTk()

    logging.info("Starting main app.")

    # External crash sentinel: mark ourselves running-not-clean, then spawn
    # a separate process that relaunches us if we vanish without flipping
    # that mark (i.e. a native crash that never reaches TaskKiller.exit).
    try:
        from src import sentinel
        sentinel.write_marker(os.getpid(), clean=False)
        # Always start it. A named mutex inside the sentinel keeps exactly
        # one alive, so spawning unconditionally cannot multiply them —
        # whereas the previous "skip if any other instance exists" check
        # silently disabled crash recovery on every ordinary launch (the
        # Qt overlay is the same exe, so it counted as another instance),
        # and left the app dead after the next camera-reopen crash.
        sentinel.spawn_sentinel(os.getpid())
    except Exception as e:
        logging.error("sentinel start failed: %s", e, exc_info=e)

    TaskKiller().start()

    # UIAccess is a manifest bit read by the loader, so honouring the
    # setting means running a different exe. That is done here, once,
    # after config is loaded — and never when this process is itself the
    # product of a switch (restart.EXE_SWITCH_GUARD), so the swap cannot
    # become a restart loop even if the setting and reality disagree
    # about what should be possible.
    #
    # The common way to arrive here as the wrong variant is a crash: the
    # sentinel always relaunches the ORDINARY exe, because that is the
    # one Windows cannot refuse to start.
    try:
        from src import restart as _restart
        from src import ui_access as _ui_access
        logging.info("UIACCESS %s", _ui_access.describe())
        # A copy Windows started at sign-in stays as it is: Windows
        # tracks it by process, and only a copy it knows about is brought
        # along onto UAC prompts.
        _want_exe = None if _AT_LAUNCH else _ui_access.target_exe()
        if _want_exe and _restart.switch_guard_set():
            logging.info(
                "UIACCESS: would switch to %s but this process already is "
                "a switch — staying put",
                os.path.basename(_want_exe))
        elif _want_exe:
            _ui_access.apply_switch("setting says %s"
                                    % os.path.basename(_want_exe))
    except Exception as e:
        logging.error("UIAccess variant check failed: %s", e, exc_info=e)

    # Windows startup options: hand the camera to the secure-desktop copy
    # while that desktop is in front; as that copy, leave with it. Also
    # refresh the settings the sign-in screen copy reads.
    try:
        from src import at_startup as _at
        desktop_guard.set_yield_enabled(_at.any_enabled())
        if _at.before_signin():
            threading.Thread(target=_at.mirror_configs, daemon=True,
                             name="signin-settings").start()
    except Exception as e:
        logging.error("startup options: %s", e, exc_info=e)

    # Start the self-restart watchdog after singletons are up so it
    # has working references to MouseController / CameraManager /
    # FaceMesh. Its own loop reads `auto_restart_enabled` and
    # `auto_restart_seconds` from config on every tick.
    try:
        from src.watchdog import Watchdog
        Watchdog().start()
    except Exception as e:
        logging.error("Watchdog start failed: %s", e, exc_info=e)

    # Any exception escaping construction or mainloop must still go
    # through TaskKiller.exit() — otherwise interpreter shutdown blocks
    # joining the non-daemon MouseController pool worker and the
    # process lingers as a window-less zombie.
    try:
        main_app = MainApp(tk_root)
        main_app.tk_root.mainloop()
    except BaseException as e:
        logging.critical("Fatal error in main app: %s", e, exc_info=True)

    main_app = None

    # Guarantee a hard exit even if mainloop() returned WITHOUT going
    # through close_all (e.g. the window was destroyed by other means).
    # Otherwise Python's interpreter shutdown would try to join any
    # lingering non-daemon threads (e.g. a cv2 read stuck in C) and the
    # process would hang as a zombie — the "something remains after
    # exit" symptom. TaskKiller.exit() ends in os._exit(0).
    try:
        TaskKiller().exit()
    except SystemExit:
        raise
    except BaseException:
        import os
        os._exit(0)
