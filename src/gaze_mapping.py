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
"""Gaze -> screen mapping, in one place.

Polynomial regression on the 2D iris ratio (`gaze_ratio`), plus the
head position compensation (`src/head_shift.py`): the eyes' displacement
since calibration, converted to pixels with the monitor's physical size.

The polynomial is fitted HERE too (`fit_points`), on one point per
calibration target with the head term taken out of the target first.
That keeps the fit and the prediction consistent: the head moves while
the user looks from target to target, and a polynomial fitted without
the term absorbs part of it, which the prediction then adds a second
time.

A 3D ray formulation was tried and removed. It is head-pose invariant on
paper, but only if the calibration visits more than one head POSITION -
otherwise the pixel scale and the screen distance cannot be separated.
Requiring the user to shift their head during calibration is not
acceptable here: many users of this app have limited neck mobility and
simply cannot perform it.

The implicit re-calibration (`src/gaze_online_cal.py`) is off by
default: a user who compensates drift by deliberately looking off-target
lands the cursor on the cell anyway, so the confirmations carry no
information about the real error.

Every gaze consumer (cursor, pointer jump, grids, gaze move) goes
through `predict()` so the mapping exists exactly once.
"""

import logging

import numpy as np

from src.config_manager import ConfigManager
from src.detectors import (FaceMesh, poly_features, poly_features_pose,
                           default_gaze_cal)

logger = logging.getLogger("GazeMapping")

CAL_KEY = "gaze_calibration"
# 6 = the plain quadratic; 8 = the legacy head-pose-augmented variant,
# still honoured so an older saved calibration keeps working.
_VALID_N = (6, 8)


def _cal_dict():
    try:
        return (ConfigManager().config or {}).get(CAL_KEY, None)
    except Exception:
        return None


# (key, cx, cy) of the last in-memory refit - see _refit_without_head.
_refit_cache = None


def _refit_without_head(cal):
    """(cx, cy) fitted again from the calibration's stored points with
    no head term, or None.

    While head_shift.DISABLED is set, a calibration saved by an older
    version was fitted with the head term subtracted from its targets
    and would be off by that term. Refitting from its points gives what
    a calibration made now would. Done in memory (cached on the points),
    never written back: this runs on the pipeline threads.
    """
    global _refit_cache
    from src import head_shift
    if not head_shift.DISABLED or len(cal.get("cx", ())) != 6:
        return None
    pts = cal.get(head_shift.PTS_KEY)
    scr = cal.get(head_shift.SCREEN_KEY) or []
    if not pts or len(scr) != 2:
        return None
    try:
        key = (tuple(tuple(p) for p in pts), tuple(scr))
    except Exception:
        return None
    c = _refit_cache
    if c is not None and c[0] == key:
        return c[1], c[2]
    res = fit_points(pts, None, float(scr[0]), float(scr[1]),
                     min_points=MIN_POINTS_ANY)
    if res is None:
        return None
    _refit_cache = (key, res[0], res[1])
    logger.info("Gaze calibration refitted without the head term: "
                "RMSE x=%dpx y=%dpx", int(res[2]), int(res[3]))
    return res[0], res[1]


def load_calibration(screen_w, screen_h):
    """(cx, cy), falling back to the centre-of-screen default."""
    try:
        cal = _cal_dict()
        if (isinstance(cal, dict) and "cx" in cal and "cy" in cal
                and len(cal["cx"]) in _VALID_N
                and len(cal["cx"]) == len(cal["cy"])):
            refit = _refit_without_head(cal)
            if refit is not None:
                return refit
            return (np.array(cal["cx"], dtype=np.float64),
                    np.array(cal["cy"], dtype=np.float64))
    except Exception:
        pass
    d = default_gaze_cal(screen_w, screen_h)
    return (np.array(d["cx"], dtype=np.float64),
            np.array(d["cy"], dtype=np.float64))


# Fewer calibration targets than this cannot pin down six coefficients
# with anything to spare (16-point calibration).
MIN_POINTS = 10
# The absolute floor - six coefficients plus one to spare. An 8-point
# calibration that lost one target still fits.
MIN_POINTS_ANY = 7


def fit_points(pts, eye_ref, screen_w, screen_h, min_points=MIN_POINTS):
    """Fit the polynomial through per-target points.

    `pts` rows are [target_x, target_y, rx, ry] or, with the eye
    position recorded, [target_x, target_y, rx, ry, ex, ey, ez]. The
    head term for each row's eye position is subtracted from its target
    before the fit, so that  polynomial + head term  lands on the
    target. Returns (cx, cy, rmse_x, rmse_y) or None.
    """
    from src import head_shift
    rows, tx, ty = [], [], []
    for p in pts or []:
        try:
            ox = oy = 0.0
            if eye_ref is not None and len(p) >= 7:
                ox, oy = head_shift.offset_for(p[4:7], eye_ref,
                                               screen_w, screen_h)
            rows.append(poly_features(float(p[2]), float(p[3])))
            tx.append(float(p[0]) - ox)
            ty.append(float(p[1]) - oy)
        except Exception:
            continue
    if len(rows) < max(MIN_POINTS_ANY, int(min_points)):
        return None
    X = np.array(rows, dtype=np.float64)
    tx = np.array(tx, dtype=np.float64)
    ty = np.array(ty, dtype=np.float64)
    cx, *_ = np.linalg.lstsq(X, tx, rcond=None)
    cy, *_ = np.linalg.lstsq(X, ty, rcond=None)
    rmse_x = float(np.sqrt(np.mean((X @ cx - tx) ** 2)))
    rmse_y = float(np.sqrt(np.mean((X @ cy - ty) ** 2)))
    return cx, cy, rmse_x, rmse_y


def refit():
    """Fit the saved calibration again from its stored points - after
    the head term's strength changed. False when there is nothing to
    refit (no calibration, or one made before points were stored)."""
    from src import head_shift
    cal = _cal_dict()
    if not isinstance(cal, dict) or not cal.get(head_shift.PTS_KEY):
        return False
    scr = cal.get(head_shift.SCREEN_KEY) or []
    if len(scr) != 2:
        return False
    res = fit_points(cal[head_shift.PTS_KEY], cal.get(head_shift.REF_KEY),
                     float(scr[0]), float(scr[1]),
                     min_points=MIN_POINTS_ANY)
    if res is None:
        return False
    cx, cy, rmse_x, rmse_y = res
    new = dict(cal)
    new["cx"] = cx.tolist()
    new["cy"] = cy.tolist()
    ConfigManager().set_temp_config(CAL_KEY, new)
    ConfigManager().apply_config()
    logger.info("Gaze calibration refitted for the new head term: "
                "RMSE x=%dpx y=%dpx", int(rmse_x), int(rmse_y))
    return True


def features(n):
    """This frame's feature vector, or None when there is no usable face."""
    ratio = FaceMesh().get_gaze_ratio()
    if ratio is None:
        return None
    rx, ry = ratio
    if n == 8:
        hx, hy = FaceMesh().get_head_pose()
        return poly_features_pose(rx, ry, hx, hy)
    return poly_features(rx, ry)


def predict(screen_w, screen_h, apply_bias=True):
    """Predicted gaze position in screen pixels, or None.

    `apply_bias=False` returns the RAW prediction — what the implicit
    re-calibration needs to measure its own residual against.
    """
    cx, cy = load_calibration(screen_w, screen_h)
    f = features(len(cx))
    if f is None or len(f) != len(cx):
        return None
    sx = float(f @ cx)
    sy = float(f @ cy)
    try:
        from src import head_shift
        hx, hy = head_shift.offset(_cal_dict(), screen_w, screen_h)
        sx += hx
        sy += hy
    except Exception:
        pass
    if apply_bias:
        try:
            from src import gaze_online_cal
            bx, by = gaze_online_cal.get_bias()
            sx += bx
            sy += by
        except Exception:
            pass
    return sx, sy
