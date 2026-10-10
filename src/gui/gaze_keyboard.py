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
"""Gaze keyboard (test) — a fullscreen 50-on (kana) soft keyboard.

The typed text shows along the top. A key is entered by dwelling the
gaze on it for `gaze_keyboard_dwell_ms`, or by left-clicking it. Enter
copies the buffer to the clipboard and closes. No dakuten / kanji
conversion (plain 50-on + Enter only), per the test spec.
"""

import ctypes
import logging
import time
import tkinter as tk

import numpy as np

from src.config_manager import ConfigManager
from src.detectors import FaceMesh
from src.detectors.facemesh import (
    poly_features, poly_features_pose, default_gaze_cal,
)

logger = logging.getLogger("GazeKeyboard")

# 50-on grid: 5 rows (a/i/u/e/o) x 10 columns (a..wa). Blanks where the
# row has no kana. Column 10 is the Enter key.
KANA = [
    ["あ", "か", "さ", "た", "な", "は", "ま", "や", "ら", "わ"],
    ["い", "き", "し", "ち", "に", "ひ", "み", "",  "り", "を"],
    ["う", "く", "す", "つ", "ぬ", "ふ", "む", "ゆ", "る", "ん"],
    ["え", "け", "せ", "て", "ね", "へ", "め", "",  "れ", ""],
    ["お", "こ", "そ", "と", "の", "ほ", "も", "よ", "ろ", ""],
]
ROWS = 5
KANA_COLS = 10
ENTER_COL = 10
COLS = 11
TOP_FRAC = 0.16          # top band for the typed text
DWELL_DEFAULT = 800

BG = "#101114"
KEY_BG = "#1B1D22"
KEY_LINE = "#3C4043"
KEY_HILITE = "#1A73E8"
KEY_FG = "#E8EAED"
TEXT_FG = "#FBBC05"
ENTER_BG = "#0B8043"


def _bring_topmost(win):
    # Normal topmost only. The earlier WS_EX_TOPMOST hack to sit above the
    # Win+Tab task view was unstable; re-assert tkinter's plain topmost.
    try:
        win.attributes("-topmost", True)
    except Exception:
        pass


class GazeKeyboard:

    def __init__(self, tk_root):
        self.tk_root = tk_root
        self.top = None
        self.canvas = None
        self._cells = {}        # (r,c) -> dict(rect, char)
        self.buffer = ""
        self._cur = None
        self._dwell_start = 0.0
        self._last_input = None
        self._eligible = False
        self._after = None
        self._gaze_hist = []
        self.W = self.H = 0
        self.y0 = 0
        self.cw = self.ch = 0
        self._active = False
        self._mc = None

    def open(self):
        from src.controllers.mouse_controller import MouseController
        import pyautogui
        self._mc = MouseController()
        root = getattr(self._mc, "_tk_root", None) or self.tk_root
        if root is None:
            return
        self.tk_root = root
        self.W, self.H = pyautogui.size()
        self.y0 = int(self.H * TOP_FRAC)
        self.cw = self.W / COLS
        self.ch = (self.H - self.y0) / ROWS
        self.buffer = ""
        self.dwell_s = max(0.1, int(ConfigManager().config.get(
            "gaze_keyboard_dwell_ms", DWELL_DEFAULT)) / 1000.0)
        self._active = True
        self._mc._gaze_grid_active = True

        self.top = tk.Toplevel(self.tk_root)
        self.top.attributes("-fullscreen", True)
        self.top.attributes("-topmost", True)
        self.top.overrideredirect(True)
        self.top.configure(bg=BG)
        self.top.bind("<Escape>", lambda _e: self.close())
        self.canvas = tk.Canvas(self.top, bg=BG, highlightthickness=0,
                                width=self.W, height=self.H)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_click)
        self._draw()
        _bring_topmost(self.top)
        self._gaze_hist = []
        self._tick()

    def _draw(self):
        self.text_id = self.canvas.create_text(
            self.W // 2, self.y0 // 2, text="", fill=TEXT_FG,
            font=("Meiryo", 28))
        for r in range(ROWS):
            for c in range(COLS):
                if c == ENTER_COL:
                    if r != 0:
                        continue
                    x0, y0 = c * self.cw + 3, self.y0 + 3
                    x1 = (c + 1) * self.cw - 3
                    y1 = self.H - 3            # Enter spans full height
                    rect = self.canvas.create_rectangle(
                        x0, y0, x1, y1, fill=ENTER_BG, outline=KEY_LINE,
                        width=2)
                    self.canvas.create_text(
                        (x0 + x1) / 2, (y0 + y1) / 2, text="Enter",
                        fill=KEY_FG, font=("Meiryo", 20))
                    self._cells[(r, c)] = {"rect": rect, "char": "ENTER",
                                           "fill": ENTER_BG}
                    continue
                ch = KANA[r][c]
                if not ch:
                    continue
                x0, y0 = c * self.cw + 3, self.y0 + r * self.ch + 3
                x1, y1 = (c + 1) * self.cw - 3, self.y0 + (r + 1) * self.ch - 3
                rect = self.canvas.create_rectangle(
                    x0, y0, x1, y1, fill=KEY_BG, outline=KEY_LINE, width=2)
                self.canvas.create_text(
                    (x0 + x1) / 2, (y0 + y1) / 2, text=ch, fill=KEY_FG,
                    font=("Meiryo", 30))
                self._cells[(r, c)] = {"rect": rect, "char": ch,
                                       "fill": KEY_BG}

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
            d = default_gaze_cal(self.W, self.H)
            cx = np.array(d["cx"], dtype=np.float64)
            cy = np.array(d["cy"], dtype=np.float64)
        if len(cx) == 8:
            hx, hy = FaceMesh().get_head_pose()
            f = poly_features_pose(rx, ry, hx, hy)
        else:
            f = poly_features(rx, ry)
        self._gaze_hist.append((float(f @ cx), float(f @ cy)))
        if len(self._gaze_hist) > 5:
            self._gaze_hist.pop(0)
        n = len(self._gaze_hist)
        return (sum(p[0] for p in self._gaze_hist) / n,
                sum(p[1] for p in self._gaze_hist) / n)

    def _key_at(self, sx, sy):
        if sy < self.y0:
            return None
        col = int(max(0, min(COLS - 1, sx // self.cw)))
        row = int(max(0, min(ROWS - 1, (sy - self.y0) // self.ch)))
        if col == ENTER_COL:
            return (0, ENTER_COL)
        if (row, col) in self._cells:
            return (row, col)
        return None

    def _tick(self):
        if not self._active or self.canvas is None:
            return
        _bring_topmost(self.top)
        p = self._gaze_screen_pos()
        key = self._key_at(*p) if p else None
        now = time.monotonic()
        if key is None:
            self._set_hilite(None)
        elif key != self._cur:
            self._cur = key
            self._dwell_start = now
            self._set_hilite(key)
            self._eligible = (key != self._last_input)
        else:
            if self._eligible and now - self._dwell_start >= self.dwell_s:
                self._input(key)
                self._last_input = key
                self._eligible = False
        self._after = self.top.after(50, self._tick)

    def _set_hilite(self, key):
        for k, cell in self._cells.items():
            self.canvas.itemconfig(
                cell["rect"],
                outline=(KEY_HILITE if k == key else KEY_LINE),
                width=(5 if k == key else 2))
        self._cur = key if key is not None else self._cur

    def _on_click(self, e):
        key = self._key_at(e.x, e.y)
        if key is not None:
            self._input(key)
            self._last_input = key
            self._eligible = False

    def _input(self, key):
        cell = self._cells.get(key)
        if not cell:
            return
        if cell["char"] == "ENTER":
            self._enter()
            return
        self.buffer += cell["char"]
        try:
            self.canvas.itemconfig(self.text_id, text=self.buffer)
        except Exception:
            pass

    def _enter(self):
        buf = self.buffer
        self.close()
        if buf:
            try:
                from src.extensions.clipboard_history import (
                    _set_clipboard_text)
                _set_clipboard_text(buf)
            except Exception as e:
                logger.error("clipboard set failed: %s", e)

    def close(self):
        self._active = False
        if self._mc is not None:
            try:
                # release_gaze_grid() also resets the head-velocity
                # buffers — a raw flag clear makes the cursor lurch
                # toward the head motion accumulated while open.
                self._mc.release_gaze_grid()
            except Exception:
                pass
        if self._after is not None and self.top is not None:
            try:
                self.top.after_cancel(self._after)
            except Exception:
                pass
        self._after = None
        if self.top is not None:
            try:
                self.top.destroy()
            except Exception:
                pass
        self.top = None
        self.canvas = None
