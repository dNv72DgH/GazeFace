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
"""Shared colour theme for the gaze grids (launcher / clipboard / task
switcher).

One set of colours + one transparency, used by BOTH grid renderers (the Qt
overlay grid and the tkinter fallback) and shared across all three tools —
there is no per-tool colour or per-tool transparency any more. Stored in
extensions.json under 'gaze_colors' so it survives restarts.

  * background — the frosted backdrop tint
  * cell       — the tile the icon/label sits on ("mount")
  * highlight  — the app-specific cell tint (the clipboard ◀ ▶ page cells)
  * transparency — % see-through of the backdrop (opacity = 1 - t/100)
"""

from src.config_manager import ConfigManager

DEFAULTS = {
    "background": "#101214",
    "cell": "#1B1D22",
    "highlight": "#2A3A22",
    "transparency": 80,
}


# Gaze-MOVE (the 8 compass arrows) has its own small palette: the arrows,
# the centre ring target, and the cursor-follow ring shown during the glide.
MOVE_DEFAULTS = {
    "arrow": "#F6F4F0",
    "center": "#F6F4F0",
    "pointer": "#F6F4F0",
}


def gaze_move_colors() -> dict:
    """Current gaze-move colours, filled in with the defaults."""
    try:
        e = ConfigManager().read_extensions() or {}
    except Exception:
        e = {}
    out = dict(MOVE_DEFAULTS)
    stored = e.get("gaze_move_colors", {}) or {}
    for k in MOVE_DEFAULTS:
        v = stored.get(k)
        if v not in (None, ""):
            out[k] = v
    return out


def save_gaze_move_colors(patch: dict) -> None:
    e = ConfigManager().read_extensions() or {}
    cur = dict(e.get("gaze_move_colors", {}) or {})
    cur.update(patch)
    e["gaze_move_colors"] = cur
    ConfigManager().write_extensions(e)


def gaze_colors() -> dict:
    """Current shared colours, filled in with the defaults."""
    try:
        e = ConfigManager().read_extensions() or {}
    except Exception:
        e = {}
    out = dict(DEFAULTS)
    stored = e.get("gaze_colors", {}) or {}
    for k in DEFAULTS:
        v = stored.get(k)
        if v not in (None, ""):
            out[k] = v
    return out


def gaze_opacity() -> float:
    """Backdrop opacity (0.1–1.0) derived from the transparency %."""
    try:
        t = float(gaze_colors()["transparency"])
    except Exception:
        t = 80.0
    return max(0.1, min(1.0, 1.0 - t / 100.0))


def save_gaze_colors(patch: dict) -> None:
    e = ConfigManager().read_extensions() or {}
    cur = dict(e.get("gaze_colors", {}) or {})
    cur.update(patch)
    e["gaze_colors"] = cur
    ConfigManager().write_extensions(e)
