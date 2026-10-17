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
"""Shared fullscreen gaze-grid selection UI.

A rows×cols grid drawn over the whole screen. The gaze SCREEN position
(via the saved calibration) selects the cell under the gaze; that cell
is highlighted. The owner (launcher / clipboard / task switcher /
keyboard) reads `current_cell()` and is driven by gesture callbacks
(confirm / back / exit / settings) — this widget only renders + tracks
gaze. Topmost above the taskbar / Win+Tab. Auto-closes after
`idle_timeout_s` with no action.
"""

import ctypes
import logging
import time
import tkinter as tk

import numpy as np

from src.config_manager import ConfigManager
from src.gui.gaze_select import BLINK_STABLE_DEFAULT_MS, GazeSelector

logger = logging.getLogger("GazeGrid")

BG = "#0E0F11"
# transparentcolor key for the OPAQUE content window: any pixel of this
# colour is fully see-through, so only the icons / titles / outlines we
# draw stay visible. The dimmed cell backgrounds live on a separate
# backdrop window whose -alpha is the only thing that's semi-transparent.
TRANSP_KEY = "#FF00FF"
CELL_BG = "#202228"
CELL_LINE = "#3C4043"
CELL_HILITE = "#1A73E8"      # cell currently under gaze
APP_CELL_BG = "#382E1F"      # app-specific item cell tint
TITLE_FG = "#E8EAED"
TITLE_DIM = "#9AA0A6"
TICK_MS = 50
# Blink gate, selection stability and dwell confirm: src/gui/gaze_select.py
# (shared with the Qt grid).


def _bring_topmost(win):
    # Normal topmost only. The earlier WS_EX_TOPMOST hack to sit above the
    # Win+Tab task view was unstable; re-assert tkinter's plain topmost.
    try:
        win.attributes("-topmost", True)
    except Exception:
        pass


class GazeGrid:

    def __init__(self, tk_root):
        self.tk_root = tk_root
        self.top = None
        self.canvas = None
        self.back = None         # dimmed backdrop window (semi-transparent)
        self.back_canvas = None
        self.rows = 3
        self.cols = 3
        self.cells = {}          # "r,c" -> dict(title, icon, app_specific)
        self._cell_items = {}    # "r,c" -> dict(rect, title_id, ...)
        # Which cell the gaze has selected, and the dwell on it (see
        # GazeSelector). _drawn is the cell whose outline is highlighted.
        self._sel = GazeSelector()
        self._drawn = None
        self._on_confirm = None
        self._after = None
        self._last_action = 0.0
        self.idle_timeout_s = 60.0
        self.on_exit = None
        self._icon_imgs = []     # keep PhotoImage refs
        self._screen_w = 0
        self._screen_h = 0
        self._active = False

    # ---------------------------------------------------------------- #

    def open(self, rows, cols, cells, on_exit, idle_timeout_s=60.0,
             alpha=0.2, confirm_ms=0, on_confirm=None,
             ignore_blink_ms=BLINK_STABLE_DEFAULT_MS):
        import pyautogui
        self.rows = max(1, int(rows))
        self.cols = max(1, int(cols))
        self.cells = dict(cells or {})
        self.on_exit = on_exit
        self.idle_timeout_s = float(idle_timeout_s)
        self._on_confirm = on_confirm
        self._screen_w, self._screen_h = pyautogui.size()
        self._sel.start(self.rows, self.cols, self._screen_w,
                        self._screen_h, ignore_blink_ms, confirm_ms)
        self._drawn = None
        self._last_action = time.monotonic()
        self._active = True
        # Shared gaze colours (background / cell "mount" / app-cell
        # highlight) + shared transparency — same set the Qt overlay uses,
        # replacing the old per-tool constants and per-tool alpha.
        try:
            from src.gui.gaze_colors import gaze_colors, gaze_opacity
            _c = gaze_colors()
            self._bg = _c["background"]
            self._cell_bg = _c["cell"]
            self._app_bg = _c["highlight"]
            op = gaze_opacity()
        except Exception:
            self._bg, self._cell_bg, self._app_bg = BG, CELL_BG, APP_CELL_BG
            op = max(0.1, min(1.0, float(alpha)))     # backdrop opacity

        # Backdrop window: the dimmed cell backgrounds. This is the ONLY
        # semi-transparent layer (its whole-window -alpha lets the desktop
        # show through). Drawn beneath the content window.
        self.back = tk.Toplevel(self.tk_root)
        self.back.attributes("-fullscreen", True)
        self.back.attributes("-topmost", True)
        self.back.overrideredirect(True)
        self.back.configure(bg=self._bg)
        try:
            self.back.attributes("-alpha", op)
        except Exception:
            pass
        self.back_canvas = tk.Canvas(
            self.back, bg=self._bg, highlightthickness=0,
            width=self._screen_w, height=self._screen_h)
        self.back_canvas.pack(fill="both", expand=True)

        # Content window: fully OPAQUE icons / titles / outlines. Its
        # background is the transparentcolor key so every un-drawn pixel is
        # see-through, revealing the dimmed backdrop (and desktop) behind.
        self.top = tk.Toplevel(self.tk_root)
        self.top.attributes("-fullscreen", True)
        self.top.attributes("-topmost", True)
        self.top.overrideredirect(True)
        self.top.configure(bg=TRANSP_KEY)
        try:
            self.top.attributes("-transparentcolor", TRANSP_KEY)
        except Exception:
            pass
        self.top.bind("<Escape>", lambda _e: self._fire_exit())
        self.canvas = tk.Canvas(self.top, bg=TRANSP_KEY, highlightthickness=0,
                                width=self._screen_w, height=self._screen_h)
        self.canvas.pack(fill="both", expand=True)
        self._draw_cells()
        self._raise()
        self._tick()

    def _raise(self):
        """Keep the backdrop and content windows topmost, content above
        backdrop. Both are plain -topmost (the earlier above-Win+Tab hack
        was unstable)."""
        try:
            if self.back is not None:
                self.back.attributes("-topmost", True)
            if self.top is not None:
                self.top.attributes("-topmost", True)
                self.top.lift()
        except Exception:
            pass

    def _draw_cells(self):
        from PIL import Image, ImageTk
        W, H = self._screen_w, self._screen_h
        cw, ch = W / self.cols, H / self.rows
        pad = 6
        # Scale the icon and title font to the cell so the grid fills the
        # screen legibly. With icons (launcher / task switcher) the icon
        # dominates and the title sits beneath it; text-only cells
        # (clipboard) get a much larger centred font. The cell fills go on
        # the dimmed BACKDROP canvas; icons / titles / outlines go on the
        # OPAQUE content canvas.
        has_icon = any(v.get("icon") for v in self.cells.values())
        if has_icon:
            # Icon fills as much of the cell as possible while leaving one
            # title line beneath it; capped by cell width too.
            font_px = int(max(14, min(ch * 0.12, 38)))
            title_h = int(font_px * 1.3)
            avail_h = ch - 2 * pad - title_h - 8
            icon_px = int(max(56, min(cw - 2 * pad - 12, avail_h)))
        else:
            icon_px = 0
            title_h = 0
            font_px = int(max(16, min(ch * 0.26, 96)))
        for r in range(self.rows):
            for c in range(self.cols):
                key = f"{r},{c}"
                cell = self.cells.get(key, {})
                x0, y0 = c * cw + pad, r * ch + pad
                x1, y1 = (c + 1) * cw - pad, (r + 1) * ch - pad
                fill = self._app_bg if cell.get("app_specific") \
                    else self._cell_bg
                # Dimmed cell background (only this is semi-transparent).
                self.back_canvas.create_rectangle(
                    x0, y0, x1, y1, fill=fill, outline="", width=0)
                # Opaque cell outline (also carries the gaze highlight).
                rect = self.canvas.create_rectangle(
                    x0, y0, x1, y1, fill="", outline=CELL_LINE, width=2)
                cxm, cym = (x0 + x1) / 2, (y0 + y1) / 2
                if has_icon:
                    block = icon_px + 4 + title_h
                    icon_cy = cym - block / 2 + icon_px / 2
                    title_cy = cym + block / 2 - title_h / 2
                else:
                    icon_cy = cym
                    title_cy = cym
                icon_id = None
                icon_path = cell.get("icon")
                if icon_path and icon_px > 0:
                    try:
                        im = Image.open(icon_path).convert("RGBA").resize(
                            (icon_px, icon_px), Image.LANCZOS)
                        ph = ImageTk.PhotoImage(im)
                        self._icon_imgs.append(ph)
                        icon_id = self.canvas.create_image(
                            cxm, icon_cy, image=ph)
                    except Exception:
                        icon_id = None
                title = cell.get("title", "")
                ty = title_cy if (has_icon and icon_id) else cym
                title_id = self.canvas.create_text(
                    cxm, ty, text=title, fill=TITLE_FG,
                    font=("Segoe UI", font_px, "bold"), width=int(cw - 20),
                    justify="center")
                self._cell_items[key] = {
                    "rect": rect, "fill": fill, "title": title_id,
                    "icon": icon_id}

    # ---------------------------------------------------------------- #

    def _tick(self):
        if not self._active or self.canvas is None:
            return
        self._raise()
        committed, confirm = self._sel.step()
        if committed is not None:
            self._set_highlight(committed)
        if confirm and self._on_confirm is not None:
            try:
                self._on_confirm()
            except Exception:
                logger.error("dwell confirm failed", exc_info=True)
            if not self._active or self.top is None:
                return              # confirm closed the grid
        if time.monotonic() - self._last_action > self.idle_timeout_s:
            logger.info("Gaze grid idle timeout — closing")
            self._fire_exit()
            return
        self._after = self.top.after(TICK_MS, self._tick)

    def _set_highlight(self, cell):
        # restore old
        if self._drawn is not None:
            okey = f"{self._drawn[0]},{self._drawn[1]}"
            old = self._cell_items.get(okey)
            if old:
                # Content-canvas outline only (fill stays "" so the dimmed
                # backdrop shows through the cell).
                self.canvas.itemconfig(old["rect"], outline=CELL_LINE,
                                       width=2)
        self._drawn = cell
        nkey = f"{cell[0]},{cell[1]}"
        new = self._cell_items.get(nkey)
        if new:
            self.canvas.itemconfig(new["rect"], outline=CELL_HILITE, width=5)

    # ---------------------------------------------------------------- #

    def update_cells(self, cells):
        """Replace the cell contents (same rows×cols) — for paging."""
        if self.canvas is None:
            return
        self.cells = dict(cells or {})
        self.canvas.delete("all")
        if self.back_canvas is not None:
            self.back_canvas.delete("all")
        self._cell_items = {}
        self._icon_imgs = []
        self._sel.reset_selection()
        self._drawn = None
        self._draw_cells()
        self.note_action()

    def current_cell(self):
        # Default to (0,0) for a gesture-confirm before any dwell commit.
        cur = self._sel.cur
        return cur if cur is not None else (0, 0)

    def notice(self, r, c, text, color="#D9622B"):
        """Flash cell (r, c) and show `text` for a moment (the simpler
        tkinter version of the Qt overlay's notice)."""
        if self.canvas is None or self.top is None:
            return
        item = self._cell_items.get(f"{r},{c}")
        if item:
            self.canvas.itemconfig(item["rect"], outline=color, width=10)

            def _restore(rect=item["rect"], cell=(r, c)):
                if self.canvas is None:
                    return
                try:
                    on = self._drawn == cell
                    self.canvas.itemconfig(
                        rect, outline=CELL_HILITE if on else CELL_LINE,
                        width=5 if on else 2)
                except tk.TclError:
                    pass          # the page was redrawn meanwhile
            self.top.after(700, _restore)
        self.canvas.delete("notice")
        self.canvas.create_text(
            self._screen_w / 2, max(30, self._screen_h * 0.04),
            text=str(text), fill=TITLE_FG, font=("Segoe UI", 28, "bold"),
            tags="notice")
        self.top.after(2000, lambda: self.canvas is not None
                       and self.canvas.delete("notice"))

    def note_action(self):
        self._last_action = time.monotonic()

    def note_confirmed_cell(self):
        """Tell the implicit re-calibration that the CURRENT cell was just
        executed — the user was demonstrably looking at it. Content cells
        only, never the ◀ ▶ paging cells."""
        try:
            from src import gaze_online_cal
            r, c = self.current_cell()
            cw = self._screen_w / max(1, self.cols)
            ch = self._screen_h / max(1, self.rows)
            gaze_online_cal.note_confirm(
                (c + 0.5) * cw, (r + 0.5) * ch, cw, ch,
                self._screen_w, self._screen_h)
        except Exception as e:
            logger.debug("note_confirmed_cell failed: %s", e)

    def _fire_exit(self):
        cb = self.on_exit
        self.close()
        if cb:
            try:
                cb()
            except Exception:
                pass

    def close(self):
        self._active = False
        if self._after is not None and self.top is not None:
            try:
                self.top.after_cancel(self._after)
            except Exception:
                pass
        self._after = None
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
        self._cell_items = {}
        self._icon_imgs = []
