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
implicit drift correction learned from the gaze grids
(`src/gaze_online_cal.py`).

A 3D ray formulation was tried and removed. It is head-pose invariant on
paper, but only if the calibration visits more than one head POSITION —
otherwise the pixel scale and the screen distance cannot be separated and
the fit compensates head rotation while leaving translation untouched.
Requiring the user to shift their head during calibration is not
acceptable here: many users of this app have limited neck mobility and
simply cannot perform it. Drift is therefore handled by the implicit
re-calibration, which asks nothing of the user at all.

Every gaze consumer (cursor, pointer jump, both grid renderers) goes
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


def load_calibration(screen_w, screen_h):
    """(cx, cy), falling back to the centre-of-screen default."""
    try:
        cal = (ConfigManager().config or {}).get(CAL_KEY, None)
        if (isinstance(cal, dict) and "cx" in cal and "cy" in cal
                and len(cal["cx"]) in _VALID_N
                and len(cal["cx"]) == len(cal["cy"])):
            return (np.array(cal["cx"], dtype=np.float64),
                    np.array(cal["cy"], dtype=np.float64))
    except Exception:
        pass
    d = default_gaze_cal(screen_w, screen_h)
    return (np.array(d["cx"], dtype=np.float64),
            np.array(d["cy"], dtype=np.float64))


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
    if apply_bias:
        try:
            from src import gaze_online_cal
            bx, by = gaze_online_cal.get_bias()
            sx += bx
            sy += by
        except Exception:
            pass
    return sx, sy
