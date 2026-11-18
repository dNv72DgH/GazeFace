"""Head position compensation for the gaze mapping.

The gaze polynomial is fitted at one head position. When the user sits
somewhere else the prediction is off; this moves it back by how far the
EYES have moved since calibration, without asking the user to do
anything.

What the numbers say (five calibrations of one setup, 10-15 Nov 2026,
head 24 cm apart at the extremes, each calibration applied to the
others' targets):

  * Left/right: the prediction FOLLOWS the head, 0.75 cm per cm, evenly
    over the whole screen. Taking that out cut the error between
    sessions from 483 to 318 px RMS. Hence 75 %, applied against the
    movement (default 0 - off - while it is a developer setting).
  * Up/down: the same rule at 100 % made things worse (217 -> 280 px);
    the opposite direction gained 6 % at best, which is inside the
    noise. Default 0 until there is data that can tell.
  * Head ROTATION: once left/right position is accounted for, turning
    the strength anywhere from -20 % to +40 % changed nothing (198-204
    px). Not compensated.
  * About 200 px RMS stays between two calibrations even from the same
    seat. A calibration fits its own samples to only 140-190 px, so that
    is the calibration's own noise, not posture, and no head term can
    remove it.

The first versions moved the prediction by the head's displacement on
top of a polynomial that had been fitted WITHOUT that term. The head
also moves while the user merely looks around - during the calibration
too - so the polynomial had already absorbed part of the effect and the
term then counted it twice: every head movement distorted the mapping,
and the setting that looked right depended on how the user happened to
move. The fit now takes the term out of the targets first
(gaze_mapping.fit_points), so looking around the way the calibration
did reproduces the targets exactly, whatever the strength.

Inputs:

  * The eye position: MediaPipe's facial transformation matrix applied
    to the midpoint of the four eye corners of its canonical face model.
    Not the matrix's own translation - that is the model's origin, some
    4 cm behind and below the eyes, and it moves less than the eyes do
    when the head turns.
  * The calibration's reference eye position (`eye_ref` inside
    `gaze_calibration`) and per-target points (`pts`).
  * The monitor's physical size from Windows (EDID, through
    GetDeviceCaps HORZSIZE / VERTSIZE), which turns cm into pixels.

Axes: the camera image is mirrored before detection, so MediaPipe's +x
is the user's right = screen right, +y is up (screen y grows down).

The Advanced page has developer controls (strength per axis, invert),
stored app-wide: the geometry belongs to the PC - its monitor and
camera - not to a profile.
"""

import ctypes
import logging
import math
import threading
import time

import numpy as np

from src.config_manager import ConfigManager

logger = logging.getLogger("HeadShift")

ENABLE_KEY = "gaze_head_shift_comp"
REF_KEY = "eye_ref"             # [x, y, z] cm, eye midpoint at calibration
PTS_KEY = "pts"                 # per-target [tx, ty, rx, ry, ex, ey, ez]
SCREEN_KEY = "screen"           # [w, h] px the calibration was run on

# Midpoint of the eye corners (landmarks 33, 133, 263, 362) in the
# canonical face model, cm - read out of the model bundled in the .task
# file (geometry_pipeline_metadata_landmarks.binarypb).
EYE_MID = np.array([0.0, 2.63, 3.47, 1.0])

# A displacement beyond this is a tracking glitch or a different person,
# not someone shifting in their seat.
MAX_SHIFT_CM = 30.0

# The eye position is low-passed before it is used: posture changes are
# slow, and the per-frame jitter of the pose estimate would otherwise be
# passed straight to the pointer, multiplied by the strength.
SMOOTH_TAU_S = 0.25
SMOOTH_RESET_S = 1.0            # no update for this long: start afresh

# Re-read the monitor size now and then: the primary monitor can change.
_SIZE_TTL_S = 30.0
_size = None             # (width_cm, height_cm) or None
_size_mono = -1e9
_size_logged = None

_HORZSIZE = 4
_VERTSIZE = 6

# Developer tuning. Read from the settings file once, then kept in
# memory: offset() runs every frame. (Earlier test builds used the
# head_shift_* / head_comp_* / head_move_* keys with other meanings; new
# keys so those leftovers do not apply.)
X_KEY = "head_pos_x_pct"
Y_KEY = "head_pos_y_pct"
INVERT_X_KEY = "head_pos_invert_x"
INVERT_Y_KEY = "head_pos_invert_y"
STRENGTH_MIN = 0
STRENGTH_MAX = 1000
# Off by default (0): the 75 % left/right value the data supported is
# a developer setting for now.
DEFAULTS = {X_KEY: 0, Y_KEY: 0, INVERT_X_KEY: False, INVERT_Y_KEY: False}

# The whole correction is off and its Advanced section hidden: it did not
# give a dependable improvement in use. Saved strengths are ignored (no
# head term in the fit or the prediction); gaze_mapping refits older
# calibrations, which were fitted WITH the term, from their stored points.
DISABLED = True
_params = None

_lock = threading.Lock()
_smooth = None           # smoothed eye position, np.array(3)
_smooth_mono = 0.0


def clamp_strength(v, default=0):
    try:
        v = int(round(float(v)))
    except Exception:
        v = default
    return max(STRENGTH_MIN, min(STRENGTH_MAX, v))


def params():
    global _params
    if _params is None:
        cm = ConfigManager()
        p = {}
        for key, default in DEFAULTS.items():
            v = cm._read_app_setting(key, default)
            p[key] = (bool(v) if isinstance(default, bool)
                      else clamp_strength(v, default))
        _params = p
    return _params


def set_param(key, value, save=True):
    """Live change from the Advanced page.

    With `save` (slider released, entry typed, box ticked) the value is
    stored app-wide and the calibration polynomial is fitted again from
    its stored points, so it stays consistent with the new strength.
    Without it (slider being dragged) only the live value changes.
    """
    p = params()
    if key not in DEFAULTS:
        return
    if isinstance(DEFAULTS[key], bool):
        value = bool(value)
    else:
        value = clamp_strength(value, DEFAULTS[key])
    p[key] = value
    if not save:
        return
    try:
        ConfigManager().write_app_setting(key, value)
    except Exception as e:
        logger.error("%s write failed: %s", key, e)
    try:
        from src import gaze_mapping
        gaze_mapping.refit()
    except Exception as e:
        logger.error("refit after %s change failed: %s", key, e)


def gains():
    """(gx, gy): cm the prediction is moved per cm of eye displacement,
    left/right AGAINST the movement and up/down WITH the screen
    direction it maps to (see offset_for). Inverting flips the sign."""
    if DISABLED:
        return 0.0, 0.0
    p = params()
    gx = p[X_KEY] / 100.0
    gy = p[Y_KEY] / 100.0
    return (-gx if p[INVERT_X_KEY] else gx), (-gy if p[INVERT_Y_KEY] else gy)


def enabled():
    if DISABLED:
        return False
    try:
        return bool((ConfigManager().config or {}).get(ENABLE_KEY, True))
    except Exception:
        return True


def _read_monitor_cm():
    """Physical (width_cm, height_cm) of the primary monitor, or None."""
    try:
        gdi = ctypes.windll.gdi32
        gdi.CreateDCW.restype = ctypes.c_void_p
        gdi.CreateDCW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p,
                                  ctypes.c_wchar_p, ctypes.c_void_p]
        gdi.GetDeviceCaps.argtypes = [ctypes.c_void_p, ctypes.c_int]
        gdi.DeleteDC.argtypes = [ctypes.c_void_p]
        dc = gdi.CreateDCW("DISPLAY", None, None, None)
        if not dc:
            return None
        try:
            w_mm = gdi.GetDeviceCaps(dc, _HORZSIZE)
            h_mm = gdi.GetDeviceCaps(dc, _VERTSIZE)
        finally:
            gdi.DeleteDC(dc)
    except Exception as e:
        logger.error("monitor size read failed: %s", e)
        return None
    w_cm, h_cm = w_mm / 10.0, h_mm / 10.0
    # Projectors and some TVs report 0 or nonsense.
    if not (10.0 <= w_cm <= 400.0 and 5.0 <= h_cm <= 250.0):
        return None
    return w_cm, h_cm


def monitor_cm():
    global _size, _size_mono, _size_logged
    now = time.monotonic()
    if now - _size_mono >= _SIZE_TTL_S:
        _size = _read_monitor_cm()
        _size_mono = now
        if _size != _size_logged:
            _size_logged = _size
            if _size is None:
                logger.warning("monitor size unknown - head position "
                               "compensation off")
            else:
                logger.info("monitor size %.1f x %.1f cm", *_size)
    return _size


def pose_matrix():
    """MediaPipe's latest 4x4 facial transformation matrix (canonical
    face -> camera space, cm), or None."""
    try:
        from src.detectors import FaceMesh
        M = FaceMesh()._latest_transform_matrix
        if M is None:
            return None
        M = np.asarray(M, dtype=np.float64)
        if M.shape != (4, 4) or not np.all(np.isfinite(M)):
            return None
        return M
    except Exception:
        return None


def eye_position(M=None):
    """(x, y, z) of the midpoint between the eyes in camera space, cm,
    for this frame (unsmoothed), or None."""
    if M is None:
        M = pose_matrix()
    if M is None:
        return None
    p = M[:3, :] @ EYE_MID
    return float(p[0]), float(p[1]), float(p[2])


def _smoothed_eye():
    """The eye position, low-passed. Called from more than one thread
    (pipeline, pointer-jump sampling) and more than once per frame."""
    global _smooth, _smooth_mono
    raw = eye_position()
    if raw is None:
        return None
    raw = np.array(raw)
    now = time.monotonic()
    with _lock:
        dt = now - _smooth_mono
        if _smooth is None or dt > SMOOTH_RESET_S or dt < 0:
            _smooth = raw
        else:
            _smooth = _smooth + (1.0 - math.exp(-dt / SMOOTH_TAU_S)) * (
                raw - _smooth)
        _smooth_mono = now
        return _smooth.copy()


def _clamp(v, lim):
    return max(-lim, min(lim, v))


def offset_for(eye, ref, screen_w, screen_h):
    """(dx, dy) pixels to ADD to the polynomial's prediction for an eye
    at `eye`, when the calibration's reference position is `ref`.

    One formula for the live prediction and for the calibration fit, so
    the two can never disagree.
    """
    size = monitor_cm()
    if size is None or eye is None or ref is None:
        return 0.0, 0.0
    try:
        dx = _clamp(float(eye[0]) - float(ref[0]), MAX_SHIFT_CM)
        dy = _clamp(float(eye[1]) - float(ref[1]), MAX_SHIFT_CM)
    except Exception:
        return 0.0, 0.0
    gx, gy = gains()
    # Left/right: the prediction follows the head, so push it back.
    # Up/down: same rule - head up (+y) reads as gaze up, so push the
    # prediction down the screen (+y in pixels).
    return -gx * dx * screen_w / size[0], gy * dy * screen_h / size[1]


def offset(cal, screen_w, screen_h):
    """(dx, dy) pixels to ADD to the polynomial's prediction now."""
    if not enabled() or not isinstance(cal, dict):
        return 0.0, 0.0
    ref = cal.get(REF_KEY)
    if not isinstance(ref, (list, tuple)) or len(ref) < 2:
        return 0.0, 0.0          # calibrated before eye_ref was recorded
    eye = _smoothed_eye()
    if eye is None:
        return 0.0, 0.0
    return offset_for(eye, ref, screen_w, screen_h)
