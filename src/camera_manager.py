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

import concurrent.futures as futures
import logging
import threading
import time
from collections import deque
from threading import Thread

import cv2
import numpy as np
import numpy.typing as npt
from PIL import Image

import src.utils as utils
from src import app_status
from src import desktop_guard
from src import restart
from src.config_manager import ConfigManager
from src.controllers import MouseController
from src.singleton_meta import Singleton

MAX_SEARCH_CAMS = 5

logger = logging.getLogger("CameraManager")


def add_overlay(background, overlay, x, y, width, height):

    background_section = background[y:y + height, x:x + width]
    overlay_section = overlay[y:y + height, x:x + width]
    background[y:y + height,
               x:x + width] = cv2.addWeighted(background_section, 0.3,
                                              overlay_section, 0.7, 0)
    return background


class CameraManager(metaclass=Singleton):

    def __init__(self):
        logger.info("Intialize CameraManager singleton")
        self.thread_cameras = None

        # Placeholder frame until the camera delivers one: plain black at
        # the camera's usual 640x480 (the old face drawing is gone - on
        # screen the camera page shows a message instead of it).
        self.placeholder_im = np.zeros((480, 640, 3), dtype=np.uint8)

        # Overlays
        self.overlay_active = cv2.cvtColor(
            cv2.imread("assets/images/overlays/active.png",
                       cv2.IMREAD_UNCHANGED), cv2.COLOR_BGRA2RGB)
        self.overlay_disabled = cv2.cvtColor(
            cv2.imread("assets/images/overlays/disabled.png",
                       cv2.IMREAD_UNCHANGED), cv2.COLOR_BGRA2RGB)
        self.overlay_face_not_detected = cv2.cvtColor(
            cv2.imread("assets/images/overlays/face_not_detected.png",
                       cv2.IMREAD_UNCHANGED), cv2.COLOR_BGRA2RGB)

        # Use dict for pass as reference
        self.frame_buffers = {
            "raw": self.placeholder_im,
            "debug": self.placeholder_im
        }
        self.is_active = False
        self.is_destroyed = False

    def start(self):
        if not self.is_active:
            logger.info("Start CameraManager singleton")
            self.thread_cameras = ThreadCameras(self.frame_buffers)
            self.is_active = True

    def showing_placeholder(self) -> bool:
        """True while no camera frame has replaced the placeholder image
        (no camera yet, none at all, or right after a camera swap)."""
        return self.frame_buffers["raw"] is self.placeholder_im

    def get_camera_list(self) -> list[int]:
        if not self.is_active:
            return []
        ret_caps = list(self.thread_cameras.caps.keys())

        return sorted(ret_caps)

    def get_current_camera_id(self) -> int:
        if not self.is_active:
            return None
        else:
            return self.thread_cameras.curr_id

    def pick_camera(self, camera_id: int) -> None:
        logger.info(f"Swapping to camera id: {camera_id}")
        # Assign to ref
        self.frame_buffers["raw"] = self.placeholder_im
        self.frame_buffers["debug"] = self.placeholder_im
        self.thread_cameras.pick_camera(camera_id)

    def get_raw_frame(self):
        return self.frame_buffers["raw"].copy()

    def get_debug_frame(self):
        return self.frame_buffers["debug"]

    def put_debug_frame(self, frame_debug: npt.ArrayLike) -> None:
        self.frame_buffers["debug"] = frame_debug

    def leave(self):
        if self.thread_cameras is not None:
            self.thread_cameras.leave()

    def last_frame_ts(self) -> float:
        """Unix timestamp of the most recent successful camera frame.
        Returns 0.0 if no frame has ever been captured."""
        if self.thread_cameras is None:
            return 0.0
        return getattr(self.thread_cameras, "last_frame_unix_ts", 0.0)

    def request_reopen(self) -> None:
        """Thread-safe: ask the camera read loop to reopen the current
        camera on its next iteration. Safe from any thread — the actual
        release/reopen happens in the camera thread to avoid races."""
        if self.thread_cameras is not None:
            self.thread_cameras.reopen_requested = True

    def destroy(self):
        self.is_destroyed = True
        if self.thread_cameras is not None:
            self.thread_cameras.destroy()

    def draw_overlay(self, track_loc):
        if not self.is_active:
            return

        self.frame_buffers["debug"] = self.frame_buffers["raw"].copy()

        # Disabled
        if not MouseController().is_active.get():
            self.frame_buffers["debug"] = add_overlay(
                self.frame_buffers["debug"], self.overlay_disabled, 0, 0, 640,
                108)
            return

        # Face not detected
        if (track_loc is None):
            self.frame_buffers["debug"] = add_overlay(
                self.frame_buffers["debug"], self.overlay_face_not_detected, 0,
                0, 640, 108)

            return

        # Active

        if ConfigManager().config["use_transformation_matrix"]:
            cx = ConfigManager().config["fix_width"] // 2
            cy = ConfigManager().config["fix_height"] // 2
            cv2.line(self.frame_buffers["debug"], (cx, cy),
                     (int(track_loc[0]), int(track_loc[1])), (0, 255, 0), 3)

            cv2.circle(self.frame_buffers["debug"],
                       (int(track_loc[0]), int(track_loc[1])), 6, (255, 0, 0),
                       -1)

        else:
            cv2.circle(self.frame_buffers["debug"],
                       (int(track_loc[0]), int(track_loc[1])), 4,
                       (255, 255, 255), -1)


# ---------------------------------------------------------------------------- #
#                                 THREAD CAMERA                                #
# ---------------------------------------------------------------------------- #


class ThreadCameras():

    def __init__(self, frame_buffers: dict):
        logger.info("Intializing Threadcamera")
        self.lock = threading.Lock()
        self.pool = futures.ThreadPoolExecutor(max_workers=8)
        self.stop_flag = threading.Event()
        self.assign_done_flag = threading.Event()
        self.frame_buffers = frame_buffers

        # Open all cameras
        self.caps = {}

        self.assign_exe = Thread(target=utils.assign_caps_queue,
                                 args=(self.caps, self.assign_done,
                                       MAX_SEARCH_CAMS),
                                 daemon=True)
        self.assign_exe.start()
        # Decide what camera to use
        self.curr_id = None  #ConfigManager().config["camera_id"]
        logger.info(f"Found default camera_id {self.curr_id}")

        # Cross-thread reopen request flag (set by CameraManager.
        # request_reopen() / the watchdog). Serviced in the read loop.
        self.reopen_requested = False
        # Cross-thread camera-swap request (set by pick_camera from the
        # GUI/assign threads). Serviced in the read loop so all open/
        # release work happens on the camera thread.
        self.swap_requested = None

        self.loop_exe = Thread(target=self.read_camera_loop,
                               args=(self.stop_flag,),
                               daemon=True)
        self.loop_exe.start()

    def assign_done(self):
        """Set default camera after assign_caps is done
        """
        logger.info(f"Assign cameras completed. Found {self.caps}")

        # No camera at all (unplugged at launch): leave curr_id None
        # but STILL set assign_done_flag — the read loop notices the
        # missing camera and periodically rescans until one appears.
        # The old code raised IndexError here, which killed this
        # thread, left the flag unset, and parked the read loop
        # forever: the camera subsystem was permanently dead even if a
        # camera was plugged in later.
        if not self.caps:
            logger.warning(
                "No camera found at startup — will rescan periodically")
            self.curr_id = None
            self.assign_done_flag.set()
            return

        init_id = ConfigManager().config["camera_id"]

        # pick first camera available if camera in config not found
        if init_id not in self.caps:
            self.curr_id = list(self.caps.keys())[0]
        else:
            self.curr_id = init_id

        logger.info(f"Try to use camera {self.curr_id}")
        self.pick_camera(self.curr_id)
        self.assign_done_flag.set()

    def pick_camera(self, new_id: int) -> None:
        """Request a swap to camera `new_id`.

        Called from the GUI thread (camera page) and the assign thread.
        The actual open/release is deferred to the camera read loop —
        releasing the active capture from the GUI thread while the read
        loop is blocked inside cap.read() on the SAME object corrupts
        the DSHOW stream (garbled frames), and the old per-swap
        ThreadPoolExecutor in utils.open_camera leaked a pool + thread
        per swap on top of racing the read loop's own recovery opens.
        """
        logger.info(f"Pick camera {new_id} requested")
        self.swap_requested = new_id

    def _do_pick(self, new_id: int) -> None:
        """Service a camera swap ON the camera thread: open the target
        (synchronously — we own this thread) and release all others on
        throwaway daemon threads (release can block on a dead device)."""
        logger.info(f"Pick camera {new_id}, Releasing others...")

        if new_id not in self.caps:
            # The id may have been dropped by a failed reopen — try a
            # fresh synchronous open instead of refusing.
            if not self._open_sync_guarded(new_id, 6.0):
                logger.error(f"Camera {new_id} not found")
                return

        for cam_id in list(self.caps.keys()):
            if cam_id == new_id:
                if self.caps[new_id] is None:
                    if not self._open_sync_guarded(cam_id, 6.0):
                        logger.error("Camera %s failed to open on swap",
                                     cam_id)
            else:
                cap = self.caps.get(cam_id)
                if cap is not None:
                    Thread(target=lambda c=cap: self._safe_release(c),
                           daemon=True).start()
                self.caps[cam_id] = None

        self.curr_id = new_id
        # The detector keeps tracking state from the previous camera's
        # frames; after a swap it was seen returning "no face" for a full
        # minute (until the watchdog rebuilt it). Start it fresh.
        try:
            from src.detectors import FaceMesh
            FaceMesh().request_rebuild()
        except Exception as e:
            logger.error("detector rebuild after camera swap: %s", e)

    def release_all_cameras(self):
        if self.caps is not None:
            for cam_id, _ in self.caps.items():
                if self.caps[cam_id] is not None:
                    self.caps[cam_id].release()
                self.caps[cam_id] = None

    def reopen_current_camera(self) -> bool:
        """Release and reopen the current camera, in this (camera) thread.
        Recovers from a dropped USB connection / stalled driver without
        the cross-thread races that crash MediaPipe. Safe to call from
        the read loop only.

        Returns whether the camera came back — the caller escalates to a
        full restart once reopening stops working.
        """
        cid = self.curr_id
        if cid is None:
            return False
        logger.warning("Reopening camera %s after signal loss", cid)
        try:
            if cid in self.caps and self.caps[cid] is not None:
                old = self.caps[cid]
                self.caps[cid] = None
                # Release on a throwaway thread — release() can itself
                # block on a dead device.
                Thread(target=lambda c=old: self._safe_release(c),
                       daemon=True).start()

            # The VideoCapture constructor can block on a flaky DSHOW
            # device, so run the reopen on a daemon thread with a
            # timeout (guarded: a late zombie open can't clobber caps).
            if self._open_sync_guarded(cid, 8.0):
                logger.info("Camera %s reopened", cid)
                return True
            logger.warning("Camera %s reopen failed/timed out; "
                           "will retry", cid)
            return False
        except Exception as e:
            logger.error("reopen_current_camera failed: %s", e,
                         exc_info=e)
            return False

    @staticmethod
    def _safe_release(cap):
        try:
            cap.release()
        except Exception:
            pass

    def _open_sync_guarded(self, cid, timeout) -> bool:
        """Open camera `cid` with a timeout, committing into self.caps
        ONLY on in-time success.

        The worker opens into a private tmp dict, so a worker that
        times out but completes LATER (zombie) can never clobber a
        capture that recovery/rescan has since installed — it writes
        into the discarded tmp instead. A reaper thread releases any
        such late-arriving capture so the handle doesn't leak."""
        tmp = {}
        done, ok = self._timed_call(
            utils.reopen_camera_sync, timeout, tmp, cid)
        if done and ok is True and tmp.get(cid) is not None:
            self.caps[cid] = tmp[cid]
            return True

        def _reap():
            # Give the zombie worker ample time to finish, then release
            # whatever it produced (unless it somehow IS the live cap).
            time.sleep(timeout + 30.0)
            c = tmp.get(cid)
            if c is not None and (self.caps is None
                                  or self.caps.get(cid) is not c):
                try:
                    c.release()
                except Exception:
                    pass
        Thread(target=_reap, daemon=True).start()
        return False

    @staticmethod
    def _timed_call(fn, timeout, *args):
        """Run fn(*args) in a DAEMON thread, waiting up to `timeout`
        seconds. Returns (True, result) on completion or (False, None)
        on timeout. Using a daemon thread (not a ThreadPoolExecutor,
        whose workers are non-daemon) means a stuck cv2 call can never
        block interpreter shutdown or leave a lingering thread that
        survives process teardown."""
        box = {"result": None}
        ev = threading.Event()

        def _worker():
            try:
                box["result"] = fn(*args)
            except Exception as e:
                box["result"] = e
            finally:
                ev.set()

        Thread(target=_worker, daemon=True).start()
        if ev.wait(timeout):
            return True, box["result"]
        return False, None

    def rescan_and_pick(self) -> bool:
        """Release everything and re-scan all camera indices to find a
        working one. Handles a camera that was unplugged and came back —
        possibly on a DIFFERENT index than before. Returns True if a
        working camera was found and selected. Runs in the camera thread
        (blocking is fine here)."""
        logger.warning("Rescanning all cameras for a working device...")
        # Release all current captures (each on a throwaway thread, since
        # release() can block on a dead device).
        try:
            for cid in list(self.caps.keys()):
                cap = self.caps.get(cid)
                if cap is not None:
                    Thread(target=lambda c=cap: self._safe_release(c),
                           daemon=True).start()
                self.caps[cid] = None
        except Exception as e:
            logger.error("rescan release failed: %s", e)

        # Probe each index, guarded by a per-open timeout (daemon
        # thread) so a flaky device can't hang the whole scan, and a
        # late zombie probe can't clobber the chosen capture.
        found = None
        for i in range(MAX_SEARCH_CAMS):
            if self._open_sync_guarded(i, 6.0):
                found = i
                break
            logger.debug("Rescan: camera %s probe failed/timed out", i)

        if found is not None:
            self.curr_id = found
            logger.info("Rescan found working camera %s", found)
            return True
        logger.warning("Rescan found no working camera; will keep "
                       "retrying")
        return False

    def _abandon_cap(self, cid, cap, old_pool=None):
        """Give up on a capture whose read() is stuck. Drop our
        reference and release it on a throwaway daemon thread (release
        may also block on a dead device)."""
        try:
            if self.caps is not None and self.caps.get(cid) is cap:
                self.caps[cid] = None
        except Exception:
            pass

        def _rel():
            try:
                cap.release()
            except Exception:
                pass
        Thread(target=_rel, daemon=True).start()

        if old_pool is not None:
            try:
                old_pool.shutdown(wait=False)
            except Exception:
                pass

    def read_camera_loop(self, stop_flag) -> None:
        logger.info("Threadcamera main_loop started.")
        # Timestamp of the most recent successful cap.read(). The
        # watchdog reads this via CameraManager.last_frame_ts() to
        # decide if the camera signal has been lost.
        self.last_frame_unix_ts = 0.0
        consecutive_fail = 0
        FAIL_BEFORE_RECOVER = 6
        # cv2.VideoCapture.read() can block FOREVER with the DSHOW
        # backend when the camera is physically unplugged. To keep the
        # loop responsive, read() runs on a DAEMON thread with a
        # timeout (see _timed_call). On timeout we abandon the stuck
        # cap. Daemon threads (unlike ThreadPoolExecutor workers) never
        # block interpreter shutdown or linger after the process exits.
        READ_TIMEOUT_S = 2.0
        # Don't hammer a flaky DSHOW device — rapid open/close keeps some
        # cameras stuck. Wait this long between recovery attempts.
        RECOVER_COOLDOWN_S = 16.0
        # After this many same-index reopens fail to restore frames,
        # escalate to a full re-scan of every index (handles a camera
        # that came back on a different index after unplug/replug).
        RECOVERS_BEFORE_RESCAN = 3
        # If no SUCCESSFUL frame has arrived for this long, trigger
        # recovery regardless of the consecutive-failure count. This
        # catches an *intermittently* degraded camera (fail, success,
        # fail, success, ...) that never reaches FAIL_BEFORE_RECOVER
        # consecutive failures but is effectively dead. Independent of
        # the watchdog (which only runs while face control is ON).
        STALE_FRAME_RECOVER_S = 12.0
        # Degraded-camera detector (added after a real-world log showed
        # a camera alternating fail/success at ~1 fps for 8+ minutes:
        # consecutive_fail never exceeded 1 and frame age never exceeded
        # ~1 s, so NO existing detector fired). If this many failures
        # accumulate within DEGRADED_WINDOW_S — successes in between
        # don't matter — the device is degraded and gets recovered.
        DEGRADED_WINDOW_S = 10.0
        DEGRADED_FAILS = 16
        # Frozen-frame detector: a camera can also degrade by returning
        # ret=True with the SAME stuck image forever. Real video always
        # differs frame-to-frame (sensor noise), so byte-identical
        # sampled pixels for this long means the driver is wedged.
        FROZEN_FRAME_S = 10.0
        # Consecutive failed recoveries (reopen/rescan) before we give up
        # on in-process recovery and restart the app.
        REOPEN_FAILS_BEFORE_RESTART = 6
        # A camera that keeps delivering frames, just far too few of them,
        # is invisible to every other probe here: frames ARE arriving, so
        # "no fresh frame" never fires, and each one differs, so the
        # frozen-frame detector never fires either. Observed in the wild:
        # a webcam dropped from 30 fps to exactly 1 fps and stayed there
        # for 8 minutes with the app none the wiser. Below this, recover
        # once; if the next window is still starved, restart.
        LOW_FPS = 5.0
        # How often to test for the low-fps starve. Kept far shorter than
        # the 60 s heartbeat: a camera that quietly drops to 1 fps is
        # "working" to every other probe (frames arrive, each differs),
        # so this window is the ONLY thing that notices — and driving it
        # off the 60 s heartbeat meant the status sat on "Enabled" for up
        # to a minute after tracking had actually stopped.
        FPS_CHECK_S = 16.0
        # A read from a healthy camera returns the next frame in ~1/fps
        # (tens of ms even at 15 fps). A read that BLOCKS longer means the
        # device has crawled toward a stop while STILL handing back valid,
        # ever-changing frames — the exact failure in the 07:13 log
        # (30→1 fps): consecutive_fail stayed 0, frames stayed fresh and
        # unfrozen, so only the multi-second fps window ever noticed.
        # Watching per-frame latency flips the status the moment reads
        # start dragging, without waiting out a whole window. Bounded
        # above by READ_TIMEOUT_S — a read blocking past that hits the
        # timeout path instead — so this catches the ~0.4-2 s/frame band.
        SLOW_READ_S = 0.4
        # Consecutive slow reads before acting. At the ~1 s/frame a
        # crawling camera delivers this confirms the stall in ~3 s, while
        # ignoring a lone slow frame from a passing USB/CPU hiccup.
        SLOW_READS_BEFORE_RECOVER = 6
        # Consecutive read timeouts before restarting — the cap is not
        # answering at all, which a reopen has already failed to fix.
        READ_TIMEOUTS_BEFORE_RESTART = 6
        # Emit a heartbeat this often so the log can distinguish a
        # healthy loop (delivering frames) from a starving or dead one.
        HEARTBEAT_S = 60.0

        last_recover_ts = 0.0
        failed_recovers = 0
        frames_since_hb = 0
        last_hb_ts = time.time()
        fail_times = deque()
        frozen_sig = None
        frozen_since = None
        reopen_fails = 0            # consecutive failed recoveries
        low_fps_strikes = 0         # consecutive starved fps windows
        slow_reads = 0              # consecutive over-long reads
        read_timeouts = 0           # consecutive read timeouts
        fps_frames = 0              # successful reads in the current window
        fps_since = time.time()

        def note_failure(now):
            """Record a failure into the sliding window; recover when
            the window shows the device is degraded (many failures even
            with successes interleaved)."""
            fail_times.append(now)
            while fail_times and now - fail_times[0] > DEGRADED_WINDOW_S:
                fail_times.popleft()
            if len(fail_times) >= DEGRADED_FAILS:
                logger.warning(
                    "Camera degraded: %d read failures in %.0fs "
                    "(interleaved with successes) — recovering",
                    len(fail_times), DEGRADED_WINDOW_S)
                fail_times.clear()
                do_recover()

        def do_recover():
            """Cooldown-gated recovery: reopen the current camera, or
            after a few failed reopens, rescan all indices.

            Escalates to a full app restart once reopening has stopped
            working — the recovery machinery here can fix a dropped
            device, but not a driver/stack that needs the process gone.
            """
            nonlocal last_recover_ts, failed_recovers, reopen_fails
            now = time.time()
            # Show "recovering" the instant a recovery is triggered, not
            # only once one has failed — otherwise a camera going bad via
            # the No-frame / timeout / stale paths shows nothing until the
            # fps window catches up. Cleared on the next healthy read /
            # successful reopen below.
            app_status.set_error("Camera error", "Recovering")
            if now - last_recover_ts < RECOVER_COOLDOWN_S:
                return  # too soon — let the device settle
            failed_recovers += 1
            if failed_recovers >= RECOVERS_BEFORE_RESCAN:
                ok = bool(self.rescan_and_pick())
                failed_recovers = 0
            else:
                ok = bool(self.reopen_current_camera())
            last_recover_ts = time.time()
            if ok:
                reopen_fails = 0
                app_status.clear_error()
                return
            reopen_fails += 1
            app_status.set_error("Camera error", "Recovering")
            if reopen_fails >= REOPEN_FAILS_BEFORE_RESTART:
                restart.request_restart(
                    "camera recovery failed %d times in a row"
                    % reopen_fails)

        # When no camera exists at all (none at startup / all gone),
        # retry a full rescan this often until one appears.
        NO_CAMERA_RESCAN_S = 10.0
        # On the secure desktop the everyday copy is still letting go of
        # the camera when this one starts, and the user is waiting at a
        # sign-in screen or a UAC prompt: look again much sooner.
        if desktop_guard.on_secure_desktop():
            NO_CAMERA_RESCAN_S = 1.5
        last_no_cam_scan = 0.0
        yielded = False             # camera handed to another desktop
        # One pass of this loop never takes longer than a camera open
        # (<= 8 s) plus a read timeout; a wall-clock gap far beyond that
        # means the process was suspended (sleep / hibernate).
        SUSPEND_GAP_S = 60.0
        loop_wall = time.time()

        while not stop_flag.is_set():
            if not self.assign_done_flag.is_set():
                time.sleep(1)
                continue

            # Another desktop is in front (sign-in / lock screen, UAC
            # prompt) and GazeFace's copy there needs the webcam, which
            # only one process can hold: let go of it until our desktop
            # is back. Nothing below runs meanwhile, so no failure
            # detector mistakes the silence for a dead camera.
            if desktop_guard.camera_yielded():
                if not yielded:
                    yielded = True
                    cid = self.curr_id
                    cap = self.caps.get(cid) if cid is not None else None
                    logger.info("Input desktop is not ours - releasing "
                                "camera %s", cid)
                    if cap is not None:
                        self.caps[cid] = None
                        Thread(target=lambda c=cap: self._safe_release(c),
                               daemon=True).start()
                time.sleep(0.3)
                continue
            # Back from a suspend: every window below spans the time the
            # machine was asleep, and would read as a starved, stale
            # camera - reopening one that works (and flagging a camera
            # error) on every wake-up. Start all of them over. The
            # watchdog does the same for its own measurements.
            wall = time.time()
            if wall - loop_wall > SUSPEND_GAP_S:
                logger.info("Camera loop: %.0f s gap - resumed from a "
                            "suspend, restarting frame-rate windows",
                            wall - loop_wall)
                self.last_frame_unix_ts = wall
                fps_since, fps_frames = wall, 0
                last_hb_ts, frames_since_hb = wall, 0
                consecutive_fail = slow_reads = read_timeouts = 0
                low_fps_strikes = 0
                fail_times.clear()
                frozen_sig = frozen_since = None
            loop_wall = wall

            if yielded:
                yielded = False
                logger.info("Our desktop is back - reopening camera %s",
                            self.curr_id)
                # The other copy may still be letting go: a failed open
                # here is left to the ordinary recovery below.
                if self.curr_id is not None:
                    self._open_sync_guarded(self.curr_id, 8.0)
                now = time.time()
                self.last_frame_unix_ts = now
                fps_since, fps_frames = now, 0
                consecutive_fail = slow_reads = read_timeouts = 0
                low_fps_strikes = 0
                fail_times.clear()
                frozen_sig = frozen_since = None
                last_recover_ts = 0.0

            # Service a GUI/assign-thread camera swap request ON this
            # thread (all open/release must happen here).
            if self.swap_requested is not None:
                target = self.swap_requested
                self.swap_requested = None
                try:
                    self._do_pick(target)
                except Exception as e:
                    logger.error("camera swap failed: %s", e, exc_info=e)

            if self.curr_id is None:
                # No camera at all — periodically rescan so plugging
                # one in later just works.
                now = time.time()
                if now - last_no_cam_scan >= NO_CAMERA_RESCAN_S:
                    last_no_cam_scan = now
                    try:
                        self.rescan_and_pick()
                    except Exception as e:
                        logger.error("no-camera rescan failed: %s", e)
                time.sleep(1)
                continue

            # Honour a cross-thread reopen request (from the watchdog),
            # still respecting the cooldown.
            if self.reopen_requested:
                self.reopen_requested = False
                do_recover()

            now = time.time()

            # Periodic heartbeat: proves the loop is alive and shows
            # whether frames are actually flowing (frames>0) or the loop
            # is starving (frames==0) even though no error fired.
            if now - last_hb_ts >= HEARTBEAT_S:
                age = (now - self.last_frame_unix_ts
                       if self.last_frame_unix_ts > 0 else -1.0)
                logger.info(
                    "read_camera_loop heartbeat: alive, cam=%s, "
                    "frames_last_%.0fs=%d, last_frame_age=%.1fs",
                    self.curr_id, HEARTBEAT_S, frames_since_hb, age)
                frames_since_hb = 0
                last_hb_ts = now

            # Starvation check on its OWN short window, so the status flips
            # to "recovering" within FPS_CHECK_S of the camera slowing down
            # instead of waiting out the 60 s heartbeat. Divided by the
            # elapsed time; a window that spans a suspend is restarted at
            # the top of the loop instead (dividing by hours of sleep is
            # what used to read as a starved camera on every wake-up).
            if now - fps_since >= FPS_CHECK_S:
                dur = max(now - fps_since, 1e-6)
                fps = fps_frames / dur
                if (self.curr_id is not None
                        and self.last_frame_unix_ts > 0
                        and fps < LOW_FPS):
                    low_fps_strikes += 1
                    if low_fps_strikes == 1:
                        logger.warning(
                            "Camera starved: %.1f fps over the last "
                            "%.0fs — recovering", fps, dur)
                        app_status.set_error("Camera error", "Recovering")
                        # Force the attempt: do_recover() no-ops inside
                        # its cooldown, and a starved camera has usually
                        # just tripped the stale-frame path — the strike
                        # would be spent without ever trying anything.
                        last_recover_ts = 0.0
                        do_recover()
                    else:
                        # Still slow after a recovery attempt. Only restart
                        # when the camera is delivering NOTHING: a low but
                        # non-zero rate with frames still arriving is what
                        # another program hogging the CPU/GPU looks like
                        # (the user's magnifier, a game, a build), and
                        # restarting the app neither fixes that nor is
                        # wanted. Recover/report, never restart.
                        starved = (fps <= 0.0 or now - self.last_frame_unix_ts
                                   > STALE_FRAME_RECOVER_S)
                        # Only an OPEN camera that sends nothing earns a
                        # restart here. No capture at all (unplugged, or
                        # another app holds it) is the reopen path's call,
                        # with its own REOPEN_FAILS_BEFORE_RESTART budget;
                        # restarting ~30 s after an unplug fixes nothing
                        # and spends the restart-loop allowance.
                        cam_open = bool(self.caps) and (
                            self.caps.get(self.curr_id) is not None)
                        if starved and not cam_open:
                            logger.warning(
                                "Camera not open (%.1f fps) — left to the "
                                "reopen path, not restarting", fps)
                            app_status.set_error("Camera error",
                                                 "Recovering")
                        elif starved:
                            restart.request_restart(
                                "camera delivering no frames (%.1f fps, "
                                "last frame %.1fs ago)"
                                % (fps, now - self.last_frame_unix_ts))
                        else:
                            logger.warning(
                                "Camera slow at %.1f fps but still "
                                "delivering (last frame %.1fs ago) — "
                                "external load, not restarting",
                                fps, now - self.last_frame_unix_ts)
                            app_status.set_error("Camera error",
                                                 "Low frame rate")
                else:
                    if low_fps_strikes:
                        app_status.clear_error()
                    low_fps_strikes = 0
                fps_frames = 0
                fps_since = now

            # Frame-age recovery: independent of the consecutive-fail
            # counter, so an intermittently degraded camera that never
            # racks up FAIL_BEFORE_RECOVER consecutive misses still gets
            # recovered. Cooldown-gated inside do_recover().
            if (self.last_frame_unix_ts > 0.0
                    and now - self.last_frame_unix_ts
                    > STALE_FRAME_RECOVER_S):
                logger.warning(
                    "No fresh frame for %.1fs — triggering recovery",
                    now - self.last_frame_unix_ts)
                do_recover()

            cap = self.caps.get(self.curr_id) if self.caps else None

            if cap is None:
                consecutive_fail += 1
                if consecutive_fail % FAIL_BEFORE_RECOVER == 0:
                    do_recover()
                time.sleep(0.5)
                continue

            # Timeout-guarded read on a daemon thread.
            read_start = time.time()
            done, result = self._timed_call(cap.read, READ_TIMEOUT_S)
            if not done:
                consecutive_fail += 1
                read_timeouts += 1
                logger.error(
                    "Camera read timed out (%d) — abandoning cap",
                    consecutive_fail)
                # The stuck read() daemon thread is left to die on its
                # own / at process exit; we just drop our cap reference.
                self._abandon_cap(self.curr_id, cap, None)
                note_failure(time.time())
                do_recover()
                if read_timeouts >= READ_TIMEOUTS_BEFORE_RESTART:
                    restart.request_restart(
                        "camera read timed out %d times in a row"
                        % read_timeouts)
                time.sleep(0.3)
                continue
            if isinstance(result, Exception):
                consecutive_fail += 1
                logger.error("Camera read error (%d): %s",
                             consecutive_fail, result)
                note_failure(time.time())
                if consecutive_fail % FAIL_BEFORE_RECOVER == 0:
                    do_recover()
                time.sleep(0.5)
                continue
            ret, frame = result

            cv2.waitKey(1)
            if not ret:
                consecutive_fail += 1
                # Throttle the log — only every FAIL_BEFORE_RECOVER to
                # avoid filling the log when a camera is dead for a long
                # time.
                if (consecutive_fail == 1
                        or consecutive_fail % FAIL_BEFORE_RECOVER == 0):
                    logger.error("No frame returned (%d)", consecutive_fail)
                note_failure(time.time())
                if consecutive_fail % FAIL_BEFORE_RECOVER == 0:
                    do_recover()
                time.sleep(0.5)
                continue

            # Frozen-frame detector: sample a sparse pixel grid (~900
            # bytes) and compare with the previous frame. Identical
            # samples for FROZEN_FRAME_S means the driver is feeding us
            # the same stuck image (real video always carries sensor
            # noise) — recover. Cost is negligible (~µs per frame).
            frame_is_fresh = True
            try:
                sig = frame[::32, ::32].tobytes()
                if sig == frozen_sig:
                    frame_is_fresh = False
                    if frozen_since is None:
                        frozen_since = time.time()
                    elif time.time() - frozen_since > FROZEN_FRAME_S:
                        logger.warning(
                            "Camera frozen: identical frames for %.1fs "
                            "— recovering",
                            time.time() - frozen_since)
                        frozen_since = None
                        do_recover()
                else:
                    frozen_sig = sig
                    frozen_since = None
            except Exception:
                pass

            # Successful read — clear failure state + mark timestamp.
            consecutive_fail = 0
            read_timeouts = 0
            # Only a FRESH frame proves recovery worked; a frozen
            # (identical) frame must not reset the reopen→rescan
            # escalation counter, or a stuck driver would be reopened
            # forever without ever reaching the full rescan.
            if frame_is_fresh:
                failed_recovers = 0
            frames_since_hb += 1
            fps_frames += 1
            self.last_frame_unix_ts = time.time()

            # Read-latency detector: how long this frame took to arrive.
            # A crawling camera blocks ~1 s per (still valid, still
            # changing) frame — invisible to the fail/frozen/stale probes
            # and slow for the fps window to average out. Flag the stall
            # after a few consecutive slow reads so the status flips to
            # "recovering" in ~3 s instead of a window later.
            if self.last_frame_unix_ts - read_start > SLOW_READ_S:
                slow_reads += 1
                if slow_reads >= SLOW_READS_BEFORE_RECOVER:
                    logger.warning(
                        "Camera crawling: %d consecutive reads over %.2fs "
                        "(last %.2fs) — recovering",
                        slow_reads, SLOW_READ_S,
                        self.last_frame_unix_ts - read_start)
                    slow_reads = 0
                    # Flip the status immediately (the point of this
                    # detector), then recover. Left cooldown-gated on
                    # purpose: at onset no other path has fired so the
                    # cooldown is clear and this acts at once, but if a
                    # reopen just happened and the camera is still
                    # crawling, do_recover() no-ops here and the 8 s fps
                    # window handles the restart escalation — no reopen
                    # thrash every few frames.
                    app_status.set_error("Camera error", "Recovering")
                    do_recover()
            else:
                slow_reads = 0

            frame.flags.writeable = False
            h, w, _ = frame.shape

            # Trim image
            if h != ConfigManager().config["fix_height"] or w != ConfigManager(
            ).config["fix_width"]:
                target_width = int(h * 4 / 3)
                if w > target_width:
                    trim_width = w - target_width
                    trim_left = trim_width // 2
                    trim_right = trim_width - trim_left
                    frame = frame[:, trim_left:-trim_right, :]
                frame = cv2.resize(frame,
                                   (ConfigManager().config["fix_width"],
                                    ConfigManager().config["fix_height"]))
            frame = cv2.flip(frame, 1)
            self.frame_buffers["raw"] = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        return

    def leave(self):
        pass

    def destroy(self):
        logger.info("Destroying Threadcamera")
        self.stop_flag.set()
        # Join with a timeout so a thread stuck inside a cv2 call can't
        # block shutdown. These are daemon-backed helpers; if they don't
        # come back promptly we proceed anyway (os._exit cleans up).
        try:
            self.assign_exe.join(timeout=2.0)
        except Exception:
            pass
        try:
            self.loop_exe.join(timeout=3.0)
        except Exception:
            pass

        # Release all cameras
        self.release_all_cameras()
        self.frame_buffers = None
        self.caps = None

        logger.info("Threadcamera destroyed")
