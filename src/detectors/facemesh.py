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
import time

import mediapipe as mp
import numpy as np
import numpy.typing as npt
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

import src.utils as utils
from src.config_manager import ConfigManager
from src.singleton_meta import Singleton

logger = logging.getLogger("FaceMesh")

# No face although frames are processed: rebuild the detector after this
# many seconds, doubling up to the maximum while it stays that way.
NO_FACE_FIRST_RECOVER_S = 8.0
NO_FACE_MAX_RECOVER_S = 60.0
# Confidence the detector needs to FIND a face (MediaPipe's default 0.5).
# Once found, the face is tracked, which copes with poses the first find
# misses: seen after a Windows update, a still user resting in a usual
# pose was not found for two minutes, until a big head movement. Each
# no-face rebuild steps down this list; finding a face goes back to the
# first value at the next rebuild.
# Lowering it (0.35, 0.25) was tried and did not find the face either,
# so it stays at the default; the steps are kept for the log line only.
DETECT_CONFIDENCE_STEPS = (0.5,)

# Blendshape smoothing window. Fixed rather than configurable: the
# control was removed from the Cursor page, and this is the value every
# profile shipped with. A profile that still carries a "shape_smooth"
# key is simply ignored.
SHAPE_SMOOTH = 5

MP_TASK_FILE = "assets/task/face_landmarker_with_blendshapes.task"

BLENDS_MAX_BUFFER = 100
N_SHAPES = 52
np.set_printoptions(precision=2, suppress=True)

# Iris and eye-corner landmark indices (MediaPipe FaceLandmarker with
# refine_landmarks/iris -> 478 landmarks). Adopted from gaze-mouse:
# uses the iris CENTER landmark only and the two eye-corner landmarks.
# Vertical gaze offset is normalised by eye WIDTH (not height) because
# eye height varies a lot with blinking/expression and is unreliable.
R_EYE_CORNERS = (33, 133)
L_EYE_CORNERS = (263, 362)
R_IRIS_CENTER = 468
L_IRIS_CENTER = 473


def gaze_ratio(landmarks, w, h):
    """Return (rx, ry) — mean of left+right per-eye iris offsets,
    normalised by eye width. Output range is roughly [0, 1] for rx
    (0=looking right, 1=looking left after image flip) and a small
    signed value for ry (0 = level gaze).

    Ported verbatim from gaze-mouse so calibration semantics match.
    """
    def pt(i):
        lm = landmarks[i]
        return (lm.x * w, lm.y * h)

    def eye_ratio(corners, iris_idx):
        c1 = pt(corners[0])
        c2 = pt(corners[1])
        iris = pt(iris_idx)
        x_lo, x_hi = (c1[0], c2[0]) if c1[0] <= c2[0] else (c2[0], c1[0])
        width = max(1e-6, x_hi - x_lo)
        mid_y = (c1[1] + c2[1]) / 2
        rx = (iris[0] - x_lo) / width
        # NB: normalise vertical by eye WIDTH, not height.
        ry = (iris[1] - mid_y) / width
        return rx, ry

    rx1, ry1 = eye_ratio(R_EYE_CORNERS, R_IRIS_CENTER)
    rx2, ry2 = eye_ratio(L_EYE_CORNERS, L_IRIS_CENTER)
    return (rx1 + rx2) / 2, (ry1 + ry2) / 2


def poly_features(rx, ry):
    """Quadratic feature vector used by polynomial gaze->screen mapping.

    Six-feature variant. Kept for backwards-compat with calibrations
    saved before head-pose compensation existed (length-6 cx/cy).
    """
    return np.array([1.0, rx, ry, rx * ry, rx * rx, ry * ry])


def poly_features_pose(rx, ry, hx, hy):
    """Eight-feature variant: 6-feature core + linear head-pose terms.

    `hx`, `hy` are the x/y components of the head-forward direction
    (see FaceMesh.get_head_pose). Including them lets the polynomial
    regression learn how the apparent gaze ratio shifts when the user
    turns/tilts their head, so calibration remains valid across small
    head movements instead of being tied to one specific head pose.
    """
    return np.array([1.0, rx, ry, rx * ry, rx * rx, ry * ry, hx, hy])


def default_gaze_cal(screen_w, screen_h):
    """Identity-ish fallback that maps centre ratios to the screen centre.

    Our rx is normalised to ~[0, 1] with 0.5 = centre gaze (see
    gaze_ratio), so the constant term must subtract the slope's
    contribution at rx=0.5: sx = -0.5W + 2W*rx -> 0.5W at centre.
    (The previous +0.5W constant put centre gaze at 1.5W — pinned to
    the right screen edge whenever the fallback was active.)
    ry is already signed around 0, so cy keeps the plain 0.5H centre.
    """
    cx = [-screen_w * 0.5, screen_w * 2.0, 0.0, 0.0, 0.0, 0.0]
    cy = [screen_h * 0.5, 0.0, screen_h * 4.0, 0.0, 0.0, 0.0]
    return {"cx": cx, "cy": cy}


class FaceMesh(metaclass=Singleton):

    def __init__(self):
        # monotonic times: model start, and the latest callback of any
        # kind (face or no face). 0 = none yet. Read by the home screen's
        # status ("preparing face tracking" until results flow).
        self.started_mono = 0.0
        self.last_result_mono = 0.0
        self.last_face_mono = 0.0
        self._detect_step = 0
        self._detect_conf = DETECT_CONFIDENCE_STEPS[0]
        # Frame-change diagnostics: a small grey copy of the previous
        # frame and how much the latest one differs from it.
        self._prev_small = None
        self._frame_change = None
        # No-face recovery (see _maybe_recover): next allowed rebuild,
        # and the current back-off.
        self._next_recover_mono = 0.0
        self._recover_gap_s = NO_FACE_FIRST_RECOVER_S
        logger.info("Intialize FaceMesh singleton")
        self.mp_landmarks = None
        self.track_loc = None
        self.blendshapes_buffer = np.zeros([BLENDS_MAX_BUFFER, N_SHAPES])
        self.smooth_blendshapes = None
        self.model = None
        self.latest_time_ms = 0
        self.is_started = False
        # Most-recent 4x4 head transformation matrix from MediaPipe.
        # Stored from mp_callback so callers can read head pose without
        # re-running detection.
        self._latest_transform_matrix = None
        # Cross-thread rebuild request. The MediaPipe model must only be
        # closed/recreated on the same thread that calls detect_async
        # (the pipeline / tk main thread). Closing it from another
        # thread while a detect_async is in flight crashes the process
        # with a C-level access violation. So other threads (e.g. the
        # watchdog) set this flag and detect_frame performs the rebuild.
        self._rebuild_requested = False

    def _build_model(self):
        self._detect_conf = DETECT_CONFIDENCE_STEPS[
            getattr(self, "_detect_step", 0)]
        # In Windows, needs to open buffer directly
        with open(MP_TASK_FILE, mode="rb") as f:
            f_buffer = f.read()
        base_options = python.BaseOptions(model_asset_buffer=f_buffer)
        options = vision.FaceLandmarkerOptions(
            base_options=base_options,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
            running_mode=mp.tasks.vision.RunningMode.LIVE_STREAM,
            num_faces=1,
            min_face_detection_confidence=self._detect_conf,
            min_face_presence_confidence=min(0.5, self._detect_conf),
            result_callback=self.mp_callback)
        self.model = vision.FaceLandmarker.create_from_options(options)

    def start(self):
        if not self.is_started:
            logger.info("Start FaceMesh singleton")
            self.started_mono = time.monotonic()
            self._build_model()
            self.calc_smooth_kernel()
            self.is_started = True

    def request_rebuild(self):
        """Thread-safe: ask the pipeline thread to rebuild the model on
        its next detect_frame. Safe to call from any thread."""
        logger.info("FaceMesh rebuild requested")
        self._rebuild_requested = True

    def _rebuild_model_inline(self):
        """Rebuild the MediaPipe model. MUST be called from the same
        thread that runs detect_frame (the pipeline/main thread)."""
        try:
            if self.model is not None:
                try:
                    self.model.close()
                except Exception:
                    pass
            self.model = None
            self._build_model()
            # A fresh model has no timestamp history — reset ours too
            # so detection resumes immediately regardless of what the
            # old counter held.
            self.latest_time_ms = 0
            logger.info("FaceMesh model rebuilt (pipeline thread)")
        except Exception as e:
            logger.error("FaceMesh rebuild failed: %s", e, exc_info=e)
            self.model = None
            # With no model, detect_frame early-returns on every frame:
            # the app is permanently blind and nothing in-process can
            # fix it (the rebuild WAS the fix). Restart instead of
            # sitting here tracking nothing.
            try:
                from src import app_status, restart
                app_status.set_error("Face detection error", "restarting")
                restart.request_restart("FaceMesh rebuild failed: %s" % e)
            except Exception:
                pass

    def calc_smooth_kernel(self):
        self.smooth_kernel = utils.calc_smooth_kernel(
            SHAPE_SMOOTH)

    def calc_track_loc(self, mp_result, use_transformation_matrix=False):
        screen_w = ConfigManager().config["fix_width"]
        screen_h = ConfigManager().config["fix_height"]
        landmarks = mp_result.face_landmarks[0]

        # Note: gaze tracking no longer overrides track_loc here. The gaze
        # path is owned end-to-end by MouseController (it reads raw gaze
        # ratios via get_gaze_ratio(), applies polynomial calibration,
        # median-smooths, and positions the cursor absolutely). This
        # function continues to return a head-tracking position for the
        # head-mode path and as a fallback.

        if use_transformation_matrix:
            M = mp_result.facial_transformation_matrixes[0]
            U, _, V = np.linalg.svd(M[:3, :3])
            R = U @ V

            res = R @ np.array([0, 0, 1])

            x_pixel = (res[0] / 1) * 0.3
            y_pixel = (res[1] / 1) * 0.3

            x_pixel = screen_w / 2 + (x_pixel * screen_w / 2)
            y_pixel = screen_h / 2 - (y_pixel * screen_h / 2)

        else:
            axs = []
            ays = []

            for p in ConfigManager().config["tracking_vert_idxs"]:
                px = landmarks[p].x * screen_w
                py = landmarks[p].y * screen_h
                axs.append(px)
                ays.append(py)

            x_pixel = np.mean(axs)
            y_pixel = np.mean(ays)

        return np.array([x_pixel, y_pixel], np.float32)

    def get_gaze_ratio(self):
        """Latest (rx, ry) gaze ratio (gaze-mouse formula). None if no face.

        Used by both the gaze tracker (MouseController) and the
        calibration routine. The values are normalised by EYE WIDTH per
        gaze-mouse so they're roughly comparable to gaze-mouse's
        saved calibration coefficients.
        """
        if self.mp_landmarks is None:
            return None
        return self._gaze_ratio_from_landmarks(self.mp_landmarks)

    def _gaze_ratio_from_landmarks(self, landmarks):
        if landmarks is None:
            return None
        # Need at least 478 landmarks for iris indices to exist.
        if len(landmarks) <= max(R_IRIS_CENTER, L_IRIS_CENTER):
            return None
        # The actual w/h cancel out for our purposes because ratios are
        # used afterwards, but pass the fixed cam frame size for
        # consistency with the calibration that captured them.
        w = ConfigManager().config["fix_width"]
        h = ConfigManager().config["fix_height"]
        try:
            return gaze_ratio(landmarks, w, h)
        except Exception:
            return None

    def get_head_pose(self):
        """Return (head_x, head_y) — x/y components of the head-forward
        unit vector in the camera frame. Both lie in roughly [-0.5, 0.5]
        for normal head movements:
            head_x > 0 -> looking to user's right
            head_y < 0 -> looking down (camera y-axis flipped)
        Returns (0.0, 0.0) when no face is detected so callers don't
        have to special-case None.

        Used by the head-pose-compensated gaze polynomial (8-feature
        variant). The values are deliberately small/normalised so
        polynomial-regression coefficients stay numerically sane.
        """
        M = self._latest_transform_matrix
        if M is None:
            return 0.0, 0.0
        try:
            # SVD-orthogonalise the rotation block (defensive — guards
            # against numerical drift in M[:3,:3] not being exactly a
            # rotation).
            U, _, V = np.linalg.svd(M[:3, :3])
            R = U @ V
            forward = R @ np.array([0.0, 0.0, 1.0])
            return float(forward[0]), float(forward[1])
        except Exception:
            return 0.0, 0.0

    def mp_callback(self, mp_result, output_image, timestamp_ms: int):
        now = time.monotonic()
        if not self.last_result_mono and self.started_mono:
            logger.info("Face tracking ready: first result %.1f s after "
                        "start", now - self.started_mono)
        self.last_result_mono = now
        try:
            if len(mp_result.face_landmarks) >= 1:
                if not self.last_face_mono or (
                        now - self.last_face_mono > NO_FACE_FIRST_RECOVER_S):
                    logger.info("Face found (%s)", self._frame_stats())
                self.last_face_mono = now
                self._recover_gap_s = NO_FACE_FIRST_RECOVER_S
                self._next_recover_mono = 0.0
                self._detect_step = 0
        except Exception:
            pass
        # The whole body is guarded: this is invoked from MediaPipe's
        # C++ callback context, where an escaping Python exception can
        # corrupt/stop the detector. calc_track_loc in particular can
        # raise on an empty facial_transformation_matrixes list or an
        # out-of-range tracking_vert_idxs from a hand-edited config.
        try:
            if len(mp_result.face_landmarks) >= 1 and len(
                    mp_result.face_blendshapes) >= 1:
                self.mp_landmarks = mp_result.face_landmarks[0]
                # Cache the head transformation matrix so get_head_pose()
                # can read it without re-running detection.
                try:
                    if mp_result.facial_transformation_matrixes:
                        self._latest_transform_matrix = (
                            mp_result.facial_transformation_matrixes[0])
                except Exception:
                    self._latest_transform_matrix = None
                # Point for moving pointer
                self.track_loc = self.calc_track_loc(
                    mp_result,
                    use_transformation_matrix=ConfigManager(
                    ).config["use_transformation_matrix"])
                self.blendshapes_buffer = np.roll(self.blendshapes_buffer,
                                                  shift=-1,
                                                  axis=0)

                self.blendshapes_buffer[-1] = np.array(
                    [b.score for b in mp_result.face_blendshapes[0]])
                self.smooth_blendshapes = utils.apply_smoothing(
                    self.blendshapes_buffer, self.smooth_kernel)

            else:
                self.mp_landmarks = None
                self.track_loc = None
                self._latest_transform_matrix = None
        except Exception as e:
            logger.error("mp_callback failed: %s", e, exc_info=e)
            self.mp_landmarks = None
            self.track_loc = None

    def detect_frame(self, frame_np: npt.ArrayLike):

        # Service a cross-thread rebuild request here, on the pipeline
        # thread, so we never close the model out from under an
        # in-flight detect_async (which crashes with a C access
        # violation).
        if self._rebuild_requested:
            self._rebuild_requested = False
            self._rebuild_model_inline()

        self._maybe_recover()

        # If the model failed to (re)build, skip this frame rather than
        # raising 'NoneType has no attribute detect_async'.
        if self.model is None:
            return

        # MONOTONIC clock, not wall clock: detect_async timestamps only
        # need to be strictly increasing. With time.time(), a backward
        # system-clock step (NTP sync, manual change) made this guard
        # reject every frame until the wall clock caught up — face
        # tracking silently froze for the entire step, and a model
        # rebuild couldn't recover because latest_time_ms kept the
        # future value. time.monotonic() can never step backwards.
        t_ms = int(time.monotonic() * 1000)
        if t_ms <= self.latest_time_ms:
            return

        self._last_frame = frame_np         # for the no-face diagnostics
        try:
            small = np.asarray(frame_np[::16, ::16], np.float32).mean(axis=2)
            if self._prev_small is not None and \
                    self._prev_small.shape == small.shape:
                self._frame_change = float(
                    np.abs(small - self._prev_small).mean())
            self._prev_small = small
        except Exception:
            pass
        frame_mp = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_np)
        self.model.detect_async(frame_mp, t_ms)
        self.latest_time_ms = t_ms

    def _maybe_recover(self):
        """Rebuild the detector when it keeps answering "no face".

        Seen after start-up: frames were processed and results came back,
        but without a face, for a whole minute until the watchdog rebuilt
        the model - with the user sitting in front of the camera, and
        nothing on screen moving. Someone who is really away costs a
        rebuild now and then (a few hundred ms), so the gaps grow:
        8 s, 16 s, 32 s, then once a minute. Pipeline thread only."""
        if not self.last_result_mono:
            return                  # still starting: nothing to judge yet
        now = time.monotonic()
        since = now - max(self.last_face_mono, self.started_mono)
        if since < NO_FACE_FIRST_RECOVER_S or now < self._next_recover_mono:
            return
        logger.warning("No face for %.0f s although frames are processed "
                       "- rebuilding the detector (%s)", since,
                       self._frame_stats())
        self._next_recover_mono = now + self._recover_gap_s
        self._recover_gap_s = min(self._recover_gap_s * 2,
                                  NO_FACE_MAX_RECOVER_S)
        self._detect_step = min(self._detect_step + 1,
                                len(DETECT_CONFIDENCE_STEPS) - 1)
        self._rebuild_model_inline()

    def _frame_stats(self) -> str:
        """Size and brightness of the latest frame, for the log: tells a
        dark or blank camera (exposure still settling, lens covered, the
        wrong device) from a detector problem. No image is kept."""
        f = getattr(self, "_last_frame", None)
        if f is None:
            return "no frame"
        try:
            g = np.asarray(f, dtype=np.float32).mean(axis=2)
            change = ("%.1f" % self._frame_change
                      if self._frame_change is not None else "?")
            return ("frame %dx%d, brightness mean %.0f, contrast %.0f, "
                    "change from previous frame %s, detect confidence %.2f"
                    % (g.shape[1], g.shape[0], float(g.mean()),
                       float(g.std()), change, self._detect_conf))
        except Exception as e:
            return "frame stats failed: %s" % e

    def results_state(self) -> str:
        """'starting' until the first result, 'stalled' when results
        stopped for over a second and a half, 'noface' when results come
        but without a face for over two seconds, else 'ok'."""
        if not self.last_result_mono:
            return "starting"
        now = time.monotonic()
        if now - self.last_result_mono > 1.5:
            return "stalled"
        if now - max(self.last_face_mono, self.started_mono) > 2.0:
            return "noface"
        return "ok"

    def get_landmarks(self):
        return self.mp_landmarks

    def get_track_loc(self):
        return self.track_loc

    def get_blendshapes(self):
        return self.smooth_blendshapes

    # Mediapipe blendshape indices: 9 = eyeBlinkLeft, 10 = eyeBlinkRight
    # (see src.shape_list). Used to gate gaze sampling — iris positions are
    # garbage while the lids are closed.
    def is_blinking(self, thresh=0.3):
        """True while either eye is blinking (blendshape above `thresh`).

        Checks the RAW latest frame as well as the smoothed value: a blink
        is fast (~100 ms) and the smoothing kernel clips its peak, so the
        smoothed eyeBlink score often never reaches the threshold. The raw
        buffer keeps the true peak, catching the blink as it happens."""
        try:
            raw = self.blendshapes_buffer
            if raw is not None:
                r = raw[-1]
                if float(r[9]) > thresh or float(r[10]) > thresh:
                    return True
            bs = self.smooth_blendshapes
            if bs is not None:
                return float(bs[9]) > thresh or float(bs[10]) > thresh
        except Exception:
            pass
        return False

    def destroy(self):
        if self.model is not None:
            self.model.close()
        self.model = None
        self.mp_landmarks = None
        self.blendshapes_buffer = None
