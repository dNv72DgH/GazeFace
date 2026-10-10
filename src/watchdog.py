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
"""Self-recovery watchdog (in-process soft reset).

Policy (per user request):
  * It NEVER touches the Face control on/off state. If the user paused
    face control, that's respected — the watchdog leaves it off and
    does nothing while it's off.
  * It only acts while Face control is ON.
  * The conditions it recovers from are:
        - webcam signal lost (no fresh frame for a while)
        - no face detected (FaceMesh produced no landmarks for a while)
    When either holds continuously for `auto_restart_seconds`
    (default 60 s), it performs a soft reset: re-pick the camera and
    rebuild the FaceMesh model. The app keeps running.
  * If the bad condition persists after a reset, it retries the reset
    every `RETRY_INTERVAL_S` (5 minutes) until a face is detected
    again, rather than hammering the camera continuously.
"""

import logging
import threading
import time

from src.singleton_meta import Singleton

logger = logging.getLogger("Watchdog")


class Watchdog(metaclass=Singleton):

    TICK_S = 2.0
    # How long with no fresh camera frame counts as "signal lost".
    CAMERA_LOST_S = 6.0
    # How long the tk main thread may go without servicing its event loop
    # before we call it stalled. The beacon ticks every 1 s; a busy GUI
    # moment can miss a few, so keep this well clear of normal jitter.
    UI_STALL_S = 10.0
    # A watchdog tick this much later than scheduled means the process
    # was suspended (sleep, hibernate, modern standby) - see _loop.
    SUSPEND_GAP_S = 15.0
    # A stall this long is not a hiccup. Observed stalls that recovered
    # on their own ran ~18-30 s; the one that did not ran 8 minutes with
    # tracking dead the whole time. Past this, restart — a soft reset
    # cannot help, since servicing it needs the very thread that is
    # wedged (the FaceMesh rebuild flag is read from the tk tick).
    #
    # Doubled from 60 s: another program hogging the CPU/GPU (a magnifier,
    # a build, a game) can freeze this thread well past a minute and then
    # let go on its own. Restarting through that helps nobody — it throws
    # away a working session to fix a problem that is not ours.
    UI_STALL_RESTART_S = 120.0
    # Repeat-reset cadence while a condition persists after the first
    # reset. Escalating backoff: retry quickly at first (a corrupted
    # camera usually recovers within a couple of reopens), then back
    # off so we don't hammer a genuinely-gone device forever. The old
    # behaviour was a flat 5 min, which meant a camera that needed a
    # second reset sat unusable for 5+ minutes. These quick early
    # retries cut that to ~15-30 s.
    RETRY_BACKOFF_S = (15.0, 30.0, 60.0, 120.0, 300.0)

    def __init__(self):
        self._thread = None
        self._stop = threading.Event()
        # When the current bad condition first appeared (None = good).
        self._camera_lost_since = None
        self._no_face_since = None
        # Timestamp after which the next repeat-reset is allowed. None
        # = no reset has happened yet for the current bad spell.
        self._next_retry_at = None
        # How many resets we've done in the current bad spell — indexes
        # into RETRY_BACKOFF_S for the next wait.
        self._reset_count = 0
        self._enabled = True
        self._timeout_s = 60.0
        # True while the tk main thread is currently considered stalled —
        # used to log the stall once on entry and its duration on exit.
        self._ui_stalled = False
        # monotonic() of the last detected wake-up from a suspend; ages
        # measured across it are not stalls (see _check_ui_stall).
        self._resumed_at = None

    # ---------------------------------------------------------------- #
    # Lifecycle
    # ---------------------------------------------------------------- #

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="Watchdog", daemon=True)
        self._thread.start()
        logger.info("Watchdog started")

    def stop(self):
        self._stop.set()
        logger.info("Watchdog stopped")

    # ---------------------------------------------------------------- #
    # Main loop
    # ---------------------------------------------------------------- #

    def _loop(self):
        last, last_wall = time.monotonic(), time.time()
        while not self._stop.is_set():
            # The clocks keep running across a suspend, so the first tick
            # after one sees minutes since the last tk beat and since the
            # last camera frame, although nothing was stuck - the whole
            # process simply was not running. Spot the gap in our OWN
            # schedule and start every measurement over from here. Both
            # clocks: the stall age is monotonic, the camera age is wall
            # time, and only the wall clock is certain to include a sleep.
            now, now_wall = time.monotonic(), time.time()
            gap = max(now - last, now_wall - last_wall)
            if gap > self.SUSPEND_GAP_S:
                logger.info("Watchdog: %.0f s since the last tick - "
                            "resumed from a suspend, restarting "
                            "measurements", gap)
                self._resumed_at = now
                self._reset_state()
            last, last_wall = now, now_wall
            try:
                self._tick()
            except Exception as e:
                logger.error("Watchdog tick failed: %s", e, exc_info=e)
            time.sleep(self.TICK_S)

    def _tick(self):
        try:
            from src.config_manager import ConfigManager
            cfg = ConfigManager().config or {}
            self._enabled = bool(cfg.get("auto_restart_enabled", True))
            self._timeout_s = float(cfg.get("auto_restart_seconds", 60))
        except Exception:
            pass

        # Runs regardless of the auto-recovery setting and of face-control
        # state, because it is the one signal that catches "everything
        # looks alive but tracking is frozen". With auto-recovery off it
        # only reports: the restart it escalates to is recovery too.
        self._check_ui_stall()

        if not self._enabled:
            self._reset_state()
            return

        # The camera has been handed to GazeFace's copy on the secure
        # desktop (src/desktop_guard.py): no frames is expected, not lost.
        try:
            from src import desktop_guard
            if desktop_guard.camera_yielded():
                self._reset_state()
                return
        except Exception:
            pass

        # The watchdog is a no-op whenever Face control is OFF. The
        # user (or a paused state) owns that; we don't fight it, and we
        # don't treat "no face while paused" as something to recover.
        if self._is_face_control_off():
            self._reset_state()
            return

        now = time.time()

        cam_lost = self._is_camera_lost(now)
        self._camera_lost_since = self._update_since(
            self._camera_lost_since, cam_lost, now)

        no_face = self._is_no_face()
        self._no_face_since = self._update_since(
            self._no_face_since, no_face, now)

        # If neither condition is bad, clear the retry schedule AND the
        # backoff position so a future bad spell starts again at the
        # fast 15s retry instead of wherever the last spell ended.
        if not cam_lost and not no_face:
            self._next_retry_at = None
            self._reset_count = 0
            return

        # Decide whether it's time to (re)act.
        reasons = []
        if (self._camera_lost_since is not None
                and now - self._camera_lost_since >= self._timeout_s):
            reasons.append("camera_lost")
        if (self._no_face_since is not None
                and now - self._no_face_since >= self._timeout_s):
            reasons.append("no_face")

        if not reasons:
            return

        # First reset for this bad spell, or a scheduled retry.
        if self._next_retry_at is None or now >= self._next_retry_at:
            self._soft_reset(reasons)
            # Pick the next backoff delay, clamped to the last entry.
            idx = min(self._reset_count, len(self.RETRY_BACKOFF_S) - 1)
            wait_s = self.RETRY_BACKOFF_S[idx]
            self._reset_count += 1
            self._next_retry_at = now + wait_s
            logger.info(
                "Watchdog: next retry in %.0f s if still unrecovered "
                "(reset #%d)", wait_s, self._reset_count)

    def _reset_state(self):
        self._camera_lost_since = None
        self._no_face_since = None
        self._next_retry_at = None
        self._reset_count = 0

    @staticmethod
    def _update_since(since, bad: bool, now: float):
        if bad:
            return since if since is not None else now
        return None

    # ---------------------------------------------------------------- #
    # Signal probes
    # ---------------------------------------------------------------- #

    @staticmethod
    def _dump_stacks():
        """Log the Python stack of every live thread.

        The whole difficulty with a tk stall is that the thread is stuck
        inside SOMETHING and the surrounding log lines only show what
        happened before it. `sys._current_frames()` reads other threads'
        frames from this one, so the wedged main thread's stack — and,
        just as usefully, whether the overlay writer is parked in
        stdin.flush() — lands in the log while the stall is live.

        A stack ending in a C call (MediaPipe detect, a Tcl call, a pipe
        write) shows as its Python caller, which is exactly the frame
        needed to name the culprit.
        """
        try:
            import sys
            import threading
            import traceback
            names = {t.ident: t.name for t in threading.enumerate()}
            main_id = threading.main_thread().ident
            for tid, frame in sys._current_frames().items():
                label = names.get(tid, "?")
                if tid == main_id:
                    label += " [MAIN — this is the stalled one]"
                stack = "".join(traceback.format_stack(frame))
                logger.warning("STALL STACK thread %s (%s):\n%s",
                               tid, label, stack)
        except Exception as e:
            logger.error("stack dump failed: %s", e)

    def _check_ui_stall(self):
        """Report tk-main-thread stalls.

        The existing probes cannot see this: the camera reader keeps
        producing frames and FaceMesh keeps holding its LAST landmarks
        (not None), so `_is_camera_lost` / `_is_no_face` both read healthy
        while detection has actually stopped and the cursor is frozen.

        A soft reset is useless here — it is serviced by the tk main
        thread, which is the thread that is stuck — so a short stall is
        only reported, and a long one is escalated to a restart.
        """
        try:
            from src import ui_heartbeat
            if not ui_heartbeat.started():
                return                      # UI still building
            age = ui_heartbeat.age_s()
        except Exception:
            return
        # Time spent suspended is not stall time: count from the wake-up.
        if self._resumed_at is not None:
            age = min(age, time.monotonic() - self._resumed_at)
        if age >= self.UI_STALL_S:
            if not self._ui_stalled:
                self._ui_stalled = True
                logger.warning(
                    "UI STALL: tk main thread has not ticked for %.1f s — "
                    "face detection is stopped, head tracking is frozen "
                    "(worker heartbeats stay healthy in this state)", age)
                # The log alone never says WHERE the thread is stuck, and
                # a stall that ends on its own leaves no other evidence.
                # Dump every thread's stack while it is still wedged.
                self._dump_stacks()
                try:
                    from src import app_status
                    app_status.set_error("Not responding", "Recovering")
                except Exception:
                    pass
            if age >= self.UI_STALL_RESTART_S and self._enabled:
                try:
                    from src import restart
                    restart.request_restart(
                        "tk main thread stalled %.0f s" % age)
                except Exception as e:
                    logger.error("stall restart failed: %s", e)
        elif self._ui_stalled:
            self._ui_stalled = False
            logger.warning("UI STALL ended (main thread ticking again)")
            try:
                from src import app_status
                app_status.clear_error()
            except Exception:
                pass

    def _is_face_control_off(self) -> bool:
        try:
            from src.controllers.mouse_controller import MouseController
            return not bool(
                getattr(MouseController(), "_active_flag", True))
        except Exception:
            return False

    def _is_camera_lost(self, now: float) -> bool:
        try:
            from src.camera_manager import CameraManager
            last = CameraManager().last_frame_ts()
        except Exception:
            return False
        if last <= 0.0:
            # Never produced a frame yet (still starting up) — don't
            # treat as lost.
            return False
        return (now - last) > self.CAMERA_LOST_S

    def _is_no_face(self) -> bool:
        try:
            from src.detectors.facemesh import FaceMesh
            return FaceMesh().mp_landmarks is None
        except Exception:
            return False

    # ---------------------------------------------------------------- #
    # In-process recovery (camera + facemesh only — never face control)
    # ---------------------------------------------------------------- #

    def _soft_reset(self, reasons):
        logger.warning(
            "Watchdog soft reset (face control left untouched). "
            "Reasons: %s", reasons)

        # IMPORTANT: never close/recreate the camera or the MediaPipe
        # model from this (watchdog) thread. Doing so while the
        # pipeline thread is mid-detect_async crashes the process with
        # a C-level access violation. Instead we set request flags that
        # the owning threads service on their next iteration.

        # Clear any stuck pointer-jump lock (a plain bool, safe to set
        # from here) so head tracking isn't frozen by a crashed jump
        # thread.
        try:
            from src.controllers.mouse_controller import MouseController
            MouseController()._jump_locked = False
        except Exception:
            pass

        # Reopen the camera ONLY when the camera is actually the problem.
        # A pure `no_face` spell with the camera still streaming (the user
        # simply looked away or stepped out of frame) must NOT reopen a
        # healthy camera: reopening a live capture mid-stream drops the
        # stream and trips the camera_manager degraded/starve cascade,
        # which in a real 14:07 log escalated a perfectly good 30 fps
        # camera all the way to a full app restart (and a crash on
        # teardown). Reopening cannot bring a face back anyway — a healthy
        # camera with no face is either the user away or a wedged detector,
        # and the FaceMesh rebuild below is the correct fix for both.
        if "camera_lost" in reasons:
            try:
                from src.camera_manager import CameraManager
                CameraManager().request_reopen()
                logger.info("Watchdog: camera reopen requested")
            except Exception as e:
                logger.error("Watchdog: camera reopen request failed: %s", e)

        # Ask the pipeline thread to rebuild the FaceMesh model on its
        # next detect_frame.
        try:
            from src.detectors.facemesh import FaceMesh
            FaceMesh().request_rebuild()
            logger.info("Watchdog: FaceMesh rebuild requested")
        except Exception as e:
            logger.error("Watchdog: FaceMesh rebuild request failed: %s",
                         e)

        logger.info("Watchdog: soft reset requested")
