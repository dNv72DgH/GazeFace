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
"""Implicit, continuous gaze re-calibration.

The explicit 16-point calibration fits a polynomial that is only valid
for the head position it was recorded at: `gaze_ratio` measures where the
iris sits between the eye corners, and that ratio changes for the same
screen point as soon as the head moves. Today that means recalibrating
every time the user shifts — the thing this module exists to avoid.

The gaze grids (launcher / clipboard / task switcher) hand us free
ground truth: when the user executes a cell, we know they were looking at
that cell. Pairing the cell's centre with the gaze position we predicted
at that moment gives a residual, and the running mean of those residuals
IS the drift the head movement introduced.

Deliberately a BIAS-only correction (two numbers, dx/dy) rather than a
full recursive least-squares refit of all six polynomial coefficients:

  * a grid cell is a coarse target — on a 1920x1080 screen a 3x3 cell is
    640x360 px, so a single observation locates the gaze only to within
    a few hundred px. That is ample to estimate a slowly-varying offset,
    and nowhere near enough to identify six coefficients: an RLS fit on
    targets that coarse would distort the mapping's SHAPE and make things
    worse the more the user used the app.
  * head movement shows up predominantly as a translation of the mapping,
    which is exactly what a bias term absorbs.

The correction is stored SEPARATELY from the user's calibration
(`gaze_bias`, not `gaze_calibration`), so an explicit recalibration is
never overwritten and resetting is a matter of clearing one key.
"""

import logging
import threading
import time

from src.config_manager import ConfigManager

logger = logging.getLogger("GazeOnlineCal")

# How strongly one confirmation pulls the bias toward the observed
# residual. Small on purpose: each sample is worth only "somewhere in
# this cell", so the estimate has to come from many of them.
EMA_ALPHA = 0.10

# A residual larger than this many cell-diagonals is not drift — the user
# confirmed with a gesture while looking somewhere else, or a blink threw
# the sample. Rejected outright.
OUTLIER_CELL_FACTOR = 1.5

# Hard cap on the correction, as a fraction of the screen. Guards against
# a pathological run of bad samples walking the cursor off-screen; past
# this the calibration itself is wrong and the user should redo it.
MAX_BIAS_FRAC = 0.20

# Samples older than this at confirm time are ignored — we want the
# fixation that led to the selection, not where the eye was before it.
FIXATION_WINDOW_S = 1.2

# Don't write to disk on every confirmation.
SAVE_EVERY = 5

_lock = threading.Lock()
_samples = []              # (ts, predicted_sx, predicted_sy), pre-bias
_bias = None               # [dx, dy]; None until first load
_bias_owner = None         # profile folder _bias was loaded from
_since_save = 0


def enabled() -> bool:
    try:
        return bool((ConfigManager().config or {}).get(
            "gaze_online_cal_enabled", True))
    except Exception:
        return True


def _profile_key():
    try:
        return str(getattr(ConfigManager(), "curr_profile_path", "") or "")
    except Exception:
        return ""


def _load_bias():
    """The bias for the CURRENT profile.

    gaze_bias lives in each profile's cursor.json next to that profile's
    own calibration, so the cached value is only good for the profile it
    was read from. Keeping it across a switch applied profile A's
    correction on top of B's calibration, and then saved it into B.
    """
    global _bias, _bias_owner, _since_save
    owner = _profile_key()
    if _bias is not None and owner == _bias_owner:
        return _bias
    if _bias is not None:
        logger.info("online cal: profile changed - loading its own bias")
        with _lock:
            _samples.clear()
            _since_save = 0
    _bias_owner = owner
    try:
        b = (ConfigManager().config or {}).get("gaze_bias", None)
        if isinstance(b, (list, tuple)) and len(b) == 2:
            _bias = [float(b[0]), float(b[1])]
        else:
            _bias = [0.0, 0.0]
    except Exception:
        _bias = [0.0, 0.0]
    return _bias


def get_bias():
    """(dx, dy) to ADD to the polynomial's screen prediction."""
    if not enabled():
        return 0.0, 0.0
    b = _load_bias()
    return b[0], b[1]


def reset():
    """Drop the learned correction — called after an explicit
    calibration, whose whole point is a fresh, correct mapping."""
    global _bias, _bias_owner, _since_save
    with _lock:
        _bias = [0.0, 0.0]
        _bias_owner = _profile_key()
        _samples.clear()
        _since_save = 0
    try:
        ConfigManager().set_temp_config("gaze_bias", [0.0, 0.0])
        ConfigManager().apply_config()
    except Exception as e:
        logger.error("gaze bias reset failed: %s", e)


def note_sample(sx: float, sy: float) -> None:
    """Record a PRE-bias predicted gaze position. Called from the grid
    tick, i.e. only while a gaze grid is on screen."""
    if not enabled():
        return
    now = time.monotonic()
    with _lock:
        _samples.append((now, float(sx), float(sy)))
        # Keep the buffer to the fixation window plus a little slack.
        cutoff = now - (FIXATION_WINDOW_S * 2)
        while _samples and _samples[0][0] < cutoff:
            _samples.pop(0)


def _median(vals):
    vals = sorted(vals)
    n = len(vals)
    if n == 0:
        return None
    mid = n // 2
    if n % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def note_confirm(target_x: float, target_y: float,
                 cell_w: float, cell_h: float,
                 screen_w: float, screen_h: float) -> None:
    """The user just executed the cell centred at (target_x, target_y).

    Pairs that with the gaze we predicted during the fixation and nudges
    the bias toward the difference.
    """
    if not enabled():
        return
    global _bias, _since_save
    now = time.monotonic()
    with _lock:
        recent = [(x, y) for ts, x, y in _samples
                  if now - ts <= FIXATION_WINDOW_S]
        _samples.clear()
    if len(recent) < 3:
        return                      # not enough of a fixation to trust

    px = _median([p[0] for p in recent])
    py = _median([p[1] for p in recent])
    if px is None or py is None:
        return

    b = _load_bias()
    # Residual measured against where the cursor ACTUALLY landed, i.e.
    # prediction plus the bias already in force.
    err_x = target_x - (px + b[0])
    err_y = target_y - (py + b[1])

    limit = OUTLIER_CELL_FACTOR * ((cell_w ** 2 + cell_h ** 2) ** 0.5)
    if (err_x ** 2 + err_y ** 2) ** 0.5 > limit:
        logger.debug("online cal: rejecting outlier residual "
                     "(%.0f, %.0f)", err_x, err_y)
        return

    max_x = MAX_BIAS_FRAC * screen_w
    max_y = MAX_BIAS_FRAC * screen_h
    new_x = max(-max_x, min(max_x, b[0] + EMA_ALPHA * err_x))
    new_y = max(-max_y, min(max_y, b[1] + EMA_ALPHA * err_y))

    # The profile was switched while this sample was being judged: the
    # residual was measured against the other profile's mapping.
    if _profile_key() != _bias_owner:
        return
    with _lock:
        _bias = [new_x, new_y]
        _since_save += 1
        do_save = _since_save >= SAVE_EVERY
        if do_save:
            _since_save = 0
    if do_save:
        try:
            ConfigManager().set_temp_config("gaze_bias", [new_x, new_y])
            ConfigManager().apply_config()
            logger.info("Gaze bias updated: dx=%.0f dy=%.0f px",
                        new_x, new_y)
        except Exception as e:
            logger.error("gaze bias save failed: %s", e)
