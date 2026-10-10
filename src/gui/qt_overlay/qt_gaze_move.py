# GPU gaze-move session. Same interface as src.gui.gaze_move.GazeMoveSession
# (start / cancel, on_done callback), but the visuals (8 compass arrows +
# centre target, then a cursor-follow ring) are drawn by the Qt overlay
# subprocess. The cursor MOVE itself stays in this process (OS-level
# SetCursorPos), exactly like the tkinter version.
import logging
import math
import threading
import time
from collections import deque

import numpy as np

from src.config_manager import ConfigManager
from src.detectors import FaceMesh
from src.detectors.facemesh import (
    poly_features, poly_features_pose, default_gaze_cal,
)
from src.gui.qt_overlay.qt_gaze_grid import OverlayClient

logger = logging.getLogger("QtGazeMove")

_D = 1.0 / math.sqrt(2.0)
DIRECTIONS = {
    "N": (0.0, -1.0), "NE": (_D, -_D), "E": (1.0, 0.0), "SE": (_D, _D),
    "S": (0.0, 1.0), "SW": (-_D, _D), "W": (-1.0, 0.0), "NW": (-_D, -_D),
}
CENTER_FRAC = 1.0 / 3.0
MAX_MOVE_S = 30.0
RING_ACCENT = "#FFD23F"
TICK_MS = 50
# Blink gate: gaze samples are garbage while the lids are closed and for a
# short window after — keep the current direction instead of chasing them.
# The window length is the per-config "ignore blinks (ms)" value.


def _sign(v):
    return 0 if abs(v) < 1e-9 else (1 if v > 0 else -1)


class QtGazeMoveSession:

    def __init__(self, mc, tk_root, display_ms, speed_px_s, on_done=None):
        self.mc = mc
        self.tk_root = tk_root
        self.display_ms = max(0, int(display_ms))
        self.speed = max(1.0, float(speed_px_s))
        self.on_done = on_done
        self.client = OverlayClient.instance()

        self._cancelled = False
        self._ended = False
        self._gaze_hist = deque(maxlen=5)
        self._cur_dir = None
        self._display_end = 0.0
        # Ignore blinks (ms): keep holding the current direction for this
        # long after a blink is seen, so a blink can't drop or flip the
        # arrow (0 = off — a blink acts immediately). Same option as the
        # launcher's "ignore blinks". Per-config.
        try:
            self._blink_grace_s = max(0, int(
                (ConfigManager().config or {}).get(
                    "gaze_move_ignore_blink_ms", 200) or 0)) / 1000.0
        except Exception:
            self._blink_grace_s = 0.2
        self._dir_since = 0.0
        self._blink_until = 0.0
        self._screen_w = 0
        self._screen_h = 0
        self._after = None

    # ---------------------------------------------------------------- #

    def start(self):
        self.mc._gaze_move_active = True
        try:
            import pyautogui
            self._screen_w, self._screen_h = pyautogui.size()
        except Exception:
            self._screen_w, self._screen_h = 1920, 1080
        try:
            cfg = ConfigManager().config or {}
            alpha = float(cfg.get("gaze_move_alpha", 0.2))
        except Exception:
            alpha = 0.2
        a = int(max(0.1, min(1.0, alpha)) * 255)
        gradient = (a << 24) | 0x101214
        from src.gui.gaze_colors import gaze_move_colors
        mc = gaze_move_colors()
        self.client.send({
            "cmd": "open", "mode": "arrows",
            "arrowDir": "", "accent": RING_ACCENT, "gradient": gradient,
            "arrowColor": mc["arrow"], "centerColor": mc["center"],
            "pointerColor": mc["pointer"]})
        self._display_end = time.monotonic() + self.display_ms / 1000.0
        self._gaze_hist.clear()
        self._tick_display()

    # --- direction detection ---

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
        if self._cancelled or self._ended:
            return
        d = self._classify()
        if d != self._cur_dir:
            self._cur_dir = d
            self._dir_since = time.monotonic()
            self.client.send({"cmd": "arrow", "dir": d or ""})
        if time.monotonic() >= self._display_end:
            self._begin_move()
            return
        try:
            self._after = self.tk_root.after(TICK_MS, self._tick_display)
        except Exception:
            self._after = None

    # --- movement ---

    def _begin_move(self):
        d = self._cur_dir
        if d is None:
            self._end()
            return
        # Switch the overlay to the cursor-follow ring for the glide.
        from src.gui.gaze_colors import gaze_move_colors
        self.client.send({
            "cmd": "ring_start", "accent": RING_ACCENT,
            "pointerColor": gaze_move_colors()["pointer"]})
        vec = DIRECTIONS[d]
        threading.Thread(target=self._move_loop, args=(vec,),
                         name="QtGazeMove", daemon=True).start()

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
                logger.info("Qt gaze move reached edge — stopping")
                break
            time.sleep(0.012)
        self._end()

    # --- teardown ---

    def cancel(self):
        self._cancelled = True
        self.mc._gaze_move_active = False
        self._end()

    def _end(self):
        if self._ended:
            return
        self._ended = True
        self.mc._gaze_move_active = False
        if self._after is not None:
            try:
                self.tk_root.after_cancel(self._after)
            except Exception:
                pass
            self._after = None
        self.client.send({"cmd": "ring_stop"})
        if self.on_done is not None:
            try:
                self.on_done()
            except Exception:
                pass
