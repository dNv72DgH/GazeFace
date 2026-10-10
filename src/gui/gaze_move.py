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
"""Gaze move — directional continuous cursor movement.

Flow:
  1. A fullscreen overlay shows 8 outward arrows (N/NE/E/SE/S/SW/W/NW)
     pushed out to the screen edges, plus a centre target, for
     `display_ms`. The arrows are fully OPAQUE on a dim backdrop so they
     stay crisp, and the window sits above the taskbar and even the
     Win+Tab task view (topmost, re-asserted each tick).
  2. While it shows, the gaze SCREEN position (via calibration) is binned
     into a 3x3 grid — the screen centre is "front": centre cell = no
     move, the 8 surrounding cells pick that compass direction.
  3. When the window elapses, the cursor moves straight that way at
     `speed` px/s, stopping at the screen edge. A bright ring follows
     the cursor so it isn't lost during the move.
  4. During movement all other pointer control is suppressed via
     MouseController._gaze_move_active.
  5. Any bound gesture ends the move (Keybinder cancels the session,
     then runs that gesture's own action — except Gaze move / Gaze Jump,
     which just end it).
"""

import ctypes
import logging
import math
import threading
import time
import tkinter as tk
from collections import deque

import numpy as np

from src.config_manager import ConfigManager
from src.detectors import FaceMesh
from src.detectors.facemesh import (
    poly_features, poly_features_pose, default_gaze_cal,
)

logger = logging.getLogger("GazeMove")

_D = 1.0 / math.sqrt(2.0)

# Screen-space unit vectors (y grows downward), keyed by compass name.
DIRECTIONS = {
    "N":  (0.0, -1.0),
    "NE": (_D, -_D),
    "E":  (1.0, 0.0),
    "SE": (_D, _D),
    "S":  (0.0, 1.0),
    "SW": (-_D, _D),
    "W":  (-1.0, 0.0),
    "NW": (-_D, -_D),
}

# The "no move" centre cell occupies the middle third of the screen.
CENTER_FRAC = 1.0 / 3.0

# Safety cap: a move that somehow never gets cancelled stops itself.
MAX_MOVE_S = 30.0

# Blink gate: gaze samples are garbage while the lids are closed and for a
# short window after — keep the current direction instead of chasing them.
# The window length is the per-config "ignore blinks (ms)" value.

DIM = "#888B90"
HILITE = "#34A853"
CENTER_DIM = "#888B90"
CENTER_HILITE = "#FBBC05"
# transparentcolor key: the background is fully transparent (only the
# opaque arrows + centre circle show); per "don't make the arrows/circle
# transparent, but the background is transparent".
GM_KEY = "#FF00FF"
CURSOR_KEY = "#FF00FF"      # transparentcolor key for the cursor ring
# Lite mode (secure desktop) draws NOTHING: the move itself runs, the
# arrows do not. On the sign-in screen's desktop the layered windows the
# normal version uses showed up as big black and magenta rectangles, and
# small opaque arrow windows were not shown at all (the sign-in screen
# stays in front of them). Looking toward an edge still moves the pointer.


def _sign(v):
    return 0 if abs(v) < 1e-9 else (1 if v > 0 else -1)


def _bring_topmost(win):
    """Keep `win` on top with NORMAL topmost only.

    The earlier WS_EX_TOPMOST style hack + per-tick SetWindowPos to sit
    above the Win+Tab task view proved unstable, so we just re-assert
    tkinter's plain -topmost."""
    try:
        win.attributes("-topmost", True)
    except Exception:
        pass


class GazeMoveSession:
    """One run of the Gaze move interaction. Owned by MouseController."""

    def __init__(self, mc, tk_root, display_ms, speed_px_s, on_done=None):
        self.mc = mc
        self.tk_root = tk_root
        self.display_ms = max(0, int(display_ms))
        self.speed = max(1.0, float(speed_px_s))
        self.on_done = on_done

        self.top = None
        self.canvas = None
        self.back = None          # dimmed backdrop (semi-transparent)
        self.back_canvas = None
        self.arrows = {}
        self.center_item = None
        self._after_ids = []
        self._cancelled = False
        self._ended = False
        self._gaze_hist = deque(maxlen=5)
        self._cur_dir = None
        self._display_end = 0.0
        self._blink_until = 0.0
        # Ignore blinks (ms): keep holding the current direction this long
        # after a blink is seen (0 = off). Parity with the Qt overlay
        # version and the launcher's "ignore blinks".
        try:
            self._blink_grace_s = max(0, int(
                (ConfigManager().config or {}).get(
                    "gaze_move_ignore_blink_ms", 200) or 0)) / 1000.0
        except Exception:
            self._blink_grace_s = 0.2
        self._dir_since = 0.0
        self._screen_w = 0
        self._screen_h = 0
        # Cursor-highlight overlay (shown during the move).
        self._hl_win = None
        self._hl_canvas = None
        self._hl_ring = None
        self._hl_after = None
        # Lite mode: no overlay windows (see the note at the top).
        try:
            from src import lite_mode
            self._lite = bool(lite_mode.ACTIVE)
        except Exception:
            self._lite = False

    # ---------------------------------------------------------------- #
    # Lifecycle
    # ---------------------------------------------------------------- #

    def start(self):
        self.mc._gaze_move_active = True
        try:
            self.tk_root.after(0, self._build)
        except Exception as e:
            logger.error("gaze move start failed: %s", e)
            self._end()

    def _build(self):
        if self._cancelled or self._ended:
            return
        import pyautogui
        if self._lite:
            self._screen_w, self._screen_h = pyautogui.size()
            self._gaze_hist.clear()
            self._display_end = (time.monotonic()
                                 + self.display_ms / 1000.0)
            self._tick_display()
            return
        try:
            self._screen_w, self._screen_h = pyautogui.size()

            # Dimmed backdrop window: the ONLY semi-transparent layer. Its
            # whole-window -alpha (= 1 - transparency, default 0.2) lets the
            # desktop show through; the arrows on the window above stay
            # fully opaque. Default transparency 80.
            try:
                _cfg = ConfigManager().config or {}
                _a = float(_cfg.get("gaze_move_alpha", 0.2))
            except Exception:
                _a = 0.2
            self.back = tk.Toplevel(self.tk_root)
            self.back.attributes("-fullscreen", True)
            self.back.attributes("-topmost", True)
            self.back.overrideredirect(True)
            self.back.configure(bg="#0E0F11")
            try:
                self.back.attributes("-alpha", max(0.1, min(1.0, _a)))
            except Exception:
                pass

            # Arrows window: fully OPAQUE. Its background is the
            # transparentcolor key so only the arrows / centre circle show,
            # revealing the dimmed backdrop (and desktop) behind them.
            self.top = tk.Toplevel(self.tk_root)
            self.top.attributes("-fullscreen", True)
            self.top.attributes("-topmost", True)
            self.top.overrideredirect(True)       # no taskbar entry
            self.top.configure(bg=GM_KEY)
            try:
                self.top.attributes("-transparentcolor", GM_KEY)
            except Exception:
                pass
            self.top.bind("<Escape>", lambda _e: self.cancel())
            self.canvas = tk.Canvas(
                self.top, bg=GM_KEY, highlightthickness=0,
                width=self._screen_w, height=self._screen_h)
            self.canvas.pack(fill="both", expand=True)
            self._draw_arrows()
            self.back.attributes("-topmost", True)
            _bring_topmost(self.top)
            self.top.lift()
        except Exception as e:
            logger.error("gaze move overlay build failed: %s", e)
            self._end()
            return

        self._gaze_hist.clear()
        self._display_end = time.monotonic() + self.display_ms / 1000.0
        self._tick_display()

    def _draw_arrows(self):
        cx, cy = self._screen_w // 2, self._screen_h // 2
        span = min(self._screen_w, self._screen_h)
        L = int(span * 0.12)
        ex, ey = self._screen_w * 0.42, self._screen_h * 0.42
        for name, (dx, dy) in DIRECTIONS.items():
            ax = cx + _sign(dx) * ex
            ay = cy + _sign(dy) * ey
            self.arrows[name] = self.canvas.create_line(
                ax - dx * L / 2, ay - dy * L / 2,
                ax + dx * L / 2, ay + dy * L / 2,
                fill=DIM, width=16, arrow="last",
                arrowshape=(36, 42, 18), capstyle="round")
        cr = int(span * 0.05)
        self.center_item = self.canvas.create_oval(
            cx - cr, cy - cr, cx + cr, cy + cr,
            outline=CENTER_DIM, width=9)

    # ---------------------------------------------------------------- #
    # Direction detection + display
    # ---------------------------------------------------------------- #

    def _gaze_screen_pos(self):
        g = FaceMesh().get_gaze_ratio()
        if g is None:
            return None
        rx, ry = g
        cal = ConfigManager().config.get("gaze_calibration", None)
        if (isinstance(cal, dict) and "cx" in cal and "cy" in cal
                and len(cal["cx"]) in (6, 8)
                and len(cal["cx"]) == len(cal["cy"])):
            cx = np.array(cal["cx"], dtype=np.float64)
            cy = np.array(cal["cy"], dtype=np.float64)
        else:
            d = default_gaze_cal(self._screen_w, self._screen_h)
            cx = np.array(d["cx"], dtype=np.float64)
            cy = np.array(d["cy"], dtype=np.float64)
        if len(cx) == 8:
            hx, hy = FaceMesh().get_head_pose()
            f = poly_features_pose(rx, ry, hx, hy)
        else:
            f = poly_features(rx, ry)
        return float(f @ cx), float(f @ cy)

    def _blinking(self):
        if self._blink_grace_s <= 0:
            return False
        now = time.monotonic()
        if FaceMesh().is_blinking():
            self._blink_until = now + self._blink_grace_s
            return True
        return now < self._blink_until

    def _classify(self):
        if self._blinking():
            return self._cur_dir           # hold direction through blinks
        p = self._gaze_screen_pos()
        if p is None:
            return self._cur_dir
        self._gaze_hist.append(p)
        n = len(self._gaze_hist)
        sx = sum(q[0] for q in self._gaze_hist) / n
        sy = sum(q[1] for q in self._gaze_hist) / n
        W, H = self._screen_w, self._screen_h
        lo_x, hi_x = W * CENTER_FRAC, W * (1.0 - CENTER_FRAC)
        lo_y, hi_y = H * CENTER_FRAC, H * (1.0 - CENTER_FRAC)
        col = -1 if sx < lo_x else (1 if sx > hi_x else 0)
        row = -1 if sy < lo_y else (1 if sy > hi_y else 0)
        if col == 0 and row == 0:
            return None
        for name, (vx, vy) in DIRECTIONS.items():
            if _sign(vx) == col and _sign(vy) == row:
                return name
        return None

    def _tick_display(self):
        if self._cancelled:
            return
        if self._lite:
            # Same timing and direction choice, nothing on screen.
            d = self._classify()
            if d != self._cur_dir:
                self._cur_dir = d
                self._dir_since = time.monotonic()
            if time.monotonic() >= self._display_end:
                self._begin_move()
                return
            self._after_ids.append(self.tk_root.after(50,
                                                      self._tick_display))
            return
        if self.canvas is None:
            return
        if self.back is not None:         # keep backdrop just below arrows
            try:
                self.back.attributes("-topmost", True)
            except Exception:
                pass
        _bring_topmost(self.top)          # stay above the task view
        try:
            self.top.lift()
        except Exception:
            pass
        d = self._classify()
        if d != self._cur_dir:
            self._cur_dir = d
            self._dir_since = time.monotonic()
        self._highlight(self._cur_dir)
        if time.monotonic() >= self._display_end:
            self._begin_move()
            return
        self._after_ids.append(self.top.after(50, self._tick_display))

    def _highlight(self, d):
        for name, aid in self.arrows.items():
            self.canvas.itemconfig(aid, fill=(HILITE if name == d else DIM))
        if self.center_item is not None:
            self.canvas.itemconfig(
                self.center_item,
                outline=(CENTER_HILITE if d is None else CENTER_DIM))

    # ---------------------------------------------------------------- #
    # Movement + cursor highlight
    # ---------------------------------------------------------------- #

    def _begin_move(self):
        d = self._cur_dir
        self._destroy_overlay()
        if d is None:
            self._end()
            return
        if not self._lite:              # full-screen colour-keyed ring
            self._build_cursor_highlight()
        vec = DIRECTIONS[d]
        threading.Thread(target=self._move_loop, args=(vec,),
                         name="GazeMove", daemon=True).start()

    def _build_cursor_highlight(self):
        """A fullscreen click-through overlay that draws a bright ring
        around the cursor so it's easy to follow while it glides."""
        try:
            self._hl_win = tk.Toplevel(self.tk_root)
            self._hl_win.attributes("-fullscreen", True)
            self._hl_win.attributes("-topmost", True)
            self._hl_win.overrideredirect(True)
            self._hl_win.configure(bg=CURSOR_KEY)
            self._hl_win.attributes("-transparentcolor", CURSOR_KEY)
            self._hl_canvas = tk.Canvas(
                self._hl_win, bg=CURSOR_KEY, highlightthickness=0,
                width=self._screen_w, height=self._screen_h)
            self._hl_canvas.pack(fill="both", expand=True)
            self._hl_ring = self._hl_canvas.create_oval(
                0, 0, 0, 0, outline=CENTER_HILITE, width=5)
            self._hl_cross = self._hl_canvas.create_line(
                0, 0, 0, 0, fill=CENTER_HILITE, width=3)
            _bring_topmost(self._hl_win)
            self._update_highlight()
        except Exception as e:
            logger.error("cursor highlight build failed: %s", e)

    def _update_highlight(self):
        if self._cancelled or self._ended or self._hl_canvas is None:
            return
        try:
            import win32api
            x, y = win32api.GetCursorPos()
        except Exception:
            x, y = self._screen_w // 2, self._screen_h // 2
        r = 26
        self._hl_canvas.coords(self._hl_ring, x - r, y - r, x + r, y + r)
        self._hl_canvas.coords(self._hl_cross, x - r - 8, y, x + r + 8, y)
        _bring_topmost(self._hl_win)
        self._hl_after = self._hl_win.after(16, self._update_highlight)

    def _move_loop(self, vec):
        from src.controllers.mouse_controller import _set_cursor_absolute
        try:
            import win32api
            x, y = win32api.GetCursorPos()
        except Exception:
            x, y = self._screen_w // 2, self._screen_h // 2
        fx, fy = float(x), float(y)
        W, H = self._screen_w, self._screen_h
        start = time.monotonic()
        last = start
        while (not self._cancelled and self.mc._gaze_move_active
               and time.monotonic() - start < MAX_MOVE_S):
            now = time.monotonic()
            dt = now - last
            last = now
            fx += vec[0] * self.speed * dt
            fy += vec[1] * self.speed * dt
            edge = False
            if fx <= 0:
                fx = 0.0
                edge = edge or vec[0] < 0
            elif fx >= W - 1:
                fx = W - 1
                edge = edge or vec[0] > 0
            if fy <= 0:
                fy = 0.0
                edge = edge or vec[1] < 0
            elif fy >= H - 1:
                fy = H - 1
                edge = edge or vec[1] > 0
            try:
                _set_cursor_absolute(int(fx), int(fy), W, H)
            except Exception:
                break
            if edge:
                logger.info("Gaze move reached screen edge — stopping")
                break
            time.sleep(0.012)
        self._end()

    # ---------------------------------------------------------------- #
    # Teardown
    # ---------------------------------------------------------------- #

    def cancel(self):
        self._cancelled = True
        self.mc._gaze_move_active = False
        self._end()

    def _end(self):
        if self._ended:
            return
        self._ended = True
        self.mc._gaze_move_active = False
        try:
            if self.tk_root is not None:
                self.tk_root.after(0, self._destroy_overlay)
                self.tk_root.after(0, self._destroy_highlight)
        except Exception:
            pass
        if self.on_done is not None:
            try:
                self.on_done()
            except Exception:
                pass

    def _destroy_overlay(self):
        for aid in self._after_ids:
            try:
                (self.top or self.tk_root).after_cancel(aid)
            except Exception:
                pass
        self._after_ids = []
        for w in (self.top, self.back):
            if w is not None:
                try:
                    w.destroy()
                except Exception:
                    pass
        self.top = None
        self.back = None
        self.canvas = None
        self.back_canvas = None

    def _destroy_highlight(self):
        if self._hl_after is not None and self._hl_win is not None:
            try:
                self._hl_win.after_cancel(self._hl_after)
            except Exception:
                pass
        self._hl_after = None
        if self._hl_win is not None:
            try:
                self._hl_win.destroy()
            except Exception:
                pass
        self._hl_win = None
        self._hl_canvas = None
