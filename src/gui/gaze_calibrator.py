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
"""Fullscreen 9-point gaze calibration overlay.

Uses gaze-mouse's calibration method:

* 9 target points (3x3 grid). Each is shown for ~1.8s and samples are
  collected after a ~0.7s settle period to skip the saccade-to-target.
* For every sample we record (rx, ry, target_screen_x, target_screen_y),
  where (rx, ry) is FaceMesh.gaze_ratio in gaze-mouse's units (iris
  centre offset from eye-corner midpoint, normalised by eye width).
* We fit two 6-coefficient polynomial regressions
  ``x = cx @ [1, rx, ry, rx*ry, rx^2, ry^2]`` and likewise for y, via
  numpy.linalg.lstsq.
* The resulting cx/cy arrays are saved to the active profile's
  ``cursor.json`` under the ``gaze_calibration`` key.

MouseController._gaze_tick reads those coefficients and applies them
each tick to produce the absolute screen position.
"""

import logging
import tkinter as tk

import numpy as np
import pyautogui

from src.config_manager import ConfigManager
from src.detectors import FaceMesh
from src.detectors.facemesh import poly_features
from src.i18n import t

logger = logging.getLogger("GazeCalibrator")

# Advanced settings > Calibration > "Confirm before running" (app-wide).
CONFIRM_KEY = "calibration_confirm"


def confirm_enabled() -> bool:
    try:
        return bool(ConfigManager()._read_app_setting(CONFIRM_KEY, False))
    except Exception:
        return False


# A camera counts as connected while frames keep arriving.
CAMERA_FRESH_S = 5.0


def camera_live() -> bool:
    """Has the camera delivered a frame within CAMERA_FRESH_S?"""
    try:
        import time
        from src.camera_manager import CameraManager
        ts = float(CameraManager().last_frame_ts() or 0.0)
        return ts > 0 and time.time() - ts < CAMERA_FRESH_S
    except Exception as e:
        logger.debug("camera check failed: %s", e)
        return False

# 4x4 grid of fractional screen positions (corners clearly inside the
# screen at 0.08 / 0.92). Denser than the 3x3 used by gaze-mouse so the
# polynomial regression has a better-conditioned design matrix and the
# screen edges/corners are mapped more accurately.
POINTS_16 = [
    (fx, fy)
    for fy in (0.08, 0.36, 0.64, 0.92)
    for fx in (0.08, 0.36, 0.64, 0.92)
]

# Per-point timing (ms). 3 s display with 0.7 s settle = 2.3 s of
# sampling per point. Across 16 points that's ~37 s total and gives
# the lstsq fit roughly 70 independent samples per point at 30 fps —
# plenty of redundancy for outlier rejection.
DURATION_MS = 3000
SAMPLE_AFTER_MS = 700

# Centre intro: one sentence at a time, this far apart, before the first
# target appears.
INTRO_MS = 2000
INTRO_LINES = [
    "Look at the green dots in order.",
    "Starting in 2 seconds...",
]
SAMPLE_TICK_MS = 40

# NOTE: calibration deliberately asks the user to move NOTHING but their
# eyes. A head-movement phase was prototyped (it is what a 3D-ray mapping
# needs to become head-position invariant) and dropped: many users of this
# app have limited neck mobility and cannot perform it. Drift from head
# movement is handled afterwards by src/gaze_online_cal.py instead, which
# learns it from ordinary use.

# Minimum samples across all points to consider the fit valid.
MIN_SAMPLES = 20


class GazeCalibrator:
    """Owns the lifecycle of one calibration run."""

    # Class-level "a calibration is on screen" flag: while it is set, the
    # gaze tools (gaze move / launcher / clipboard / task switcher) must
    # not open — their fullscreen overlays would fight the calibration
    # window and their gaze logic would chase the moving target dot.
    _running = False
    _running_inst = None

    @classmethod
    def is_active(cls):
        """True while a calibration window is genuinely on screen.

        Self-healing: if the window was destroyed without _teardown()
        (Alt+F4 on the fullscreen Toplevel, parent destroyed, a crash in
        the after-chain), a stale True here would permanently disable
        every gaze tool — so verify the window still exists and clear the
        flag when it doesn't.

        Thread-safe: winfo_exists() touches Tcl and MUST run on the tk main
        thread (Python 3.12+ raises otherwise). This is also called from
        worker threads (the jump->gaze-move chain runs on the PointerJump
        thread); from there we trust the plain-bool flag and do NOT touch
        the widget — otherwise the exception would be swallowed AND wrongly
        wipe the running flag mid-calibration."""
        if not cls._running:
            return False
        inst = cls._running_inst
        if inst is None or inst.top is None:   # plain reads — thread-safe
            cls._running = False
            cls._running_inst = None
            return False
        import threading
        if threading.current_thread() is not threading.main_thread():
            return True                        # trust the flag off-thread
        try:
            if not inst.top.winfo_exists():
                cls._running = False
                cls._running_inst = None
                return False
        except Exception:
            cls._running = False
            cls._running_inst = None
            return False
        return True

    def __init__(self, master, on_complete=None):
        self.master = master
        self.on_complete = on_complete
        self.top = None
        self.canvas = None
        self.dot = None
        self.idx = -1
        # Collected list of (feature_row, target_x_px, target_y_px).
        self.samples = []
        self._after_ids = []
        self._screen_w = 0
        self._screen_h = 0

    # ---------------------------------------------------------------- #
    # Lifecycle
    # ---------------------------------------------------------------- #

    def _ask_first(self):
        """A small topmost window, not a panel: the calibration can be
        started from the gaze launcher while the main window is hidden,
        where a panel inside it would never be seen. Not a native
        messagebox either - that would block the Tk loop and face
        control with it."""
        import customtkinter
        W, H = 380, 150
        dlg = customtkinter.CTkToplevel(self.master)
        dlg.title(t("Gaze calibration"))
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        x = (dlg.winfo_screenwidth() - W) // 2
        y = (dlg.winfo_screenheight() - H) // 2
        dlg.geometry("%dx%d+%d+%d" % (W, H, x, y))
        customtkinter.CTkLabel(dlg, text=t("Start the gaze calibration?"),
                               wraplength=W - 48).pack(padx=24, pady=(28, 16))
        row = customtkinter.CTkFrame(dlg, fg_color="transparent")
        row.pack()

        def close():
            try:
                dlg.destroy()
            except Exception:
                pass

        def ok():
            close()
            self.start(confirmed=True)

        customtkinter.CTkButton(row, text=t("OK"), width=110, height=36,
                                command=ok).pack(side="left", padx=(0, 10))
        customtkinter.CTkButton(row, text=t("Cancel"), width=110, height=36,
                                command=close).pack(side="left")
        dlg.protocol("WM_DELETE_WINDOW", close)
        dlg.bind("<Escape>", lambda _e: close())
        try:
            dlg.lift()
            dlg.focus_force()
        except Exception:
            pass

    def start(self, confirmed=False):
        """`confirmed`: the caller already asked (first launch), so the
        "Confirm before running" setting does not ask a second time."""
        if self.top is not None:
            return
        # A calibration may already be running from ANOTHER instance
        # (each menu click builds a fresh GazeCalibrator). Starting a
        # second run would overwrite _running_inst, and the first run's
        # teardown would then disarm the guard while the second is still
        # on screen.
        if GazeCalibrator.is_active():
            logger.warning("Calibration already running — ignoring start")
            return
        # Without frames there is nothing to calibrate with: the overlay
        # would cover the screen for half a minute and save a fit made of
        # no samples. Every way in (left menu, launcher, first launch)
        # comes through here.
        if not camera_live():
            logger.warning("No camera frames — calibration not started")
            try:
                from src.gui.popups._util import ConfirmDialog
                ConfirmDialog(self.master).open(
                    t("The camera is not connected, so the calibration "
                      "cannot run. Connect the camera first, restart the "
                      "app, and then run it."),
                    cancel=False)
            except Exception as e:
                logger.error("no-camera notice failed: %s", e)
            return
        # Advanced settings > Calibration > "Confirm before running": a
        # gesture that opens the calibration by mistake should not take
        # over the screen for half a minute.
        if not confirmed and confirm_enabled():
            self._ask_first()
            return
        # Use the real screen size for target placement (gaze-mouse
        # likewise targets physical pixels). pyautogui handles DPI on
        # Windows after SetProcessDPIAware (called from MouseController).
        self._screen_w, self._screen_h = pyautogui.size()

        self.top = tk.Toplevel(self.master)
        self.top.attributes("-fullscreen", True)
        self.top.attributes("-topmost", True)
        self.top.configure(bg="black")
        self.top.bind("<Escape>", lambda _e: self.cancel())
        # External close (Alt+F4 etc.) must run the full cancel path so
        # the class-level running flag is cleared.
        try:
            self.top.protocol("WM_DELETE_WINDOW", self.cancel)
        except Exception:
            pass
        # Flag AFTER the window exists (an exception above must not leak
        # a stale True — that would block every gaze tool).
        GazeCalibrator._running = True
        GazeCalibrator._running_inst = self

        self.canvas = tk.Canvas(self.top, bg="black",
                                width=self._screen_w,
                                height=self._screen_h,
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        title = t("Gaze calibration")
        cancel_hint = t("Press ESC to cancel.")

        self.canvas.create_text(
            self._screen_w // 2, 60, text=title,
            fill="white", font=("Helvetica", 28, "bold"))
        self.canvas.create_text(
            self._screen_w // 2, self._screen_h - 40, text=cancel_hint,
            fill="#aaaaaa", font=("Helvetica", 12))

        # Intro shown in the centre ONE SENTENCE AT A TIME, INTRO_MS apart
        # (replaces the old top-of-screen block of text). The last sentence
        # IS the countdown; it is cleared when the first target shows.
        self._intro_idx = 0
        self.prompt_label = self.canvas.create_text(
            self._screen_w // 2, self._screen_h // 2,
            text=t(INTRO_LINES[0]),
            fill="white", font=("Helvetica", 22))
        self.starting_label = None

        self._after_ids.append(self.top.after(INTRO_MS, self._intro_tick))

    def _intro_tick(self):
        """Advance the centre intro one sentence every INTRO_MS, then begin
        the calibration."""
        if self.canvas is None:
            return
        self._intro_idx += 1
        if self._intro_idx < len(INTRO_LINES):
            try:
                self.canvas.itemconfig(
                    self.prompt_label, text=t(INTRO_LINES[self._intro_idx]))
            except Exception:
                pass
            self._after_ids.append(
                self.top.after(INTRO_MS, self._intro_tick))
            return
        self._next_point()

    # ---------------------------------------------------------------- #
    # Per-target sampling
    # ---------------------------------------------------------------- #

    def _next_point(self):
        if self.canvas is None:
            return
        if self.starting_label is not None:
            try:
                self.canvas.delete(self.starting_label)
            except Exception:
                pass
            self.starting_label = None
        if getattr(self, "prompt_label", None) is not None:
            try:
                self.canvas.delete(self.prompt_label)
            except Exception:
                pass
            self.prompt_label = None

        self.idx += 1
        if self.idx >= len(POINTS_16):
            self._finish()
            return

        fx, fy = POINTS_16[self.idx]
        tx = int(fx * self._screen_w)
        ty = int(fy * self._screen_h)

        if self.dot is not None:
            self.canvas.delete(self.dot)
        r = 18
        self.dot = self.canvas.create_oval(tx - r, ty - r, tx + r, ty + r,
                                           fill="#34A853", outline="white",
                                           width=2)

        # Settle, then sample.
        self._after_ids.append(
            self.top.after(SAMPLE_AFTER_MS,
                           lambda: self._sample_tick(0, tx, ty)))

    def _sample_once(self, tx, ty):
        """Capture one (feature_row, target_x, target_y) sample."""
        ratio = FaceMesh().get_gaze_ratio()
        if ratio is None:
            return
        rx, ry = ratio
        # Head pose is intentionally NOT captured. A still-head
        # calibration leaves the head-pose columns nearly constant, which
        # makes the lstsq fit ill-conditioned and produces huge spurious
        # head-pose coefficients — small head movements at runtime then
        # throw the cursor way off-target. Head-movement drift is handled
        # after the fact by the implicit re-calibration instead.
        self.samples.append((poly_features(rx, ry),
                             float(tx), float(ty)))

    def _sample_tick(self, elapsed_ms, tx, ty):
        if self.canvas is None:
            return
        self._sample_once(tx, ty)

        if elapsed_ms + SAMPLE_AFTER_MS >= DURATION_MS:
            self._after_ids.append(
                self.top.after(120, self._next_point))
            return

        self._after_ids.append(
            self.top.after(SAMPLE_TICK_MS,
                           lambda: self._sample_tick(
                               elapsed_ms + SAMPLE_TICK_MS, tx, ty)))

    # ---------------------------------------------------------------- #
    # Fit + save
    # ---------------------------------------------------------------- #

    def _finish(self):
        if len(self.samples) < MIN_SAMPLES:
            logger.warning("Calibration captured too few samples (%d); "
                           "aborting", len(self.samples))
            self._teardown()
            if self.on_complete is not None:
                try:
                    self.on_complete(False)
                except Exception:
                    pass
            return

        # Samples are (feature_row, target_x, target_y).
        from src import gaze_mapping
        X = np.stack([s[0] for s in self.samples]).astype(np.float64)
        tx_arr = np.array([s[1] for s in self.samples], dtype=np.float64)
        ty_arr = np.array([s[2] for s in self.samples], dtype=np.float64)

        cx, *_ = np.linalg.lstsq(X, tx_arr, rcond=None)
        cy, *_ = np.linalg.lstsq(X, ty_arr, rcond=None)

        pred_x = X @ cx
        pred_y = X @ cy
        rmse_x = float(np.sqrt(np.mean((pred_x - tx_arr) ** 2)))
        rmse_y = float(np.sqrt(np.mean((pred_y - ty_arr) ** 2)))
        logger.info("Gaze calibration: %d samples, %d features, "
                    "RMSE x=%dpx y=%dpx",
                    len(self.samples), X.shape[1],
                    int(rmse_x), int(rmse_y))

        ConfigManager().set_temp_config(
            gaze_mapping.CAL_KEY,
            {"cx": cx.tolist(), "cy": cy.tolist()})
        ConfigManager().apply_config()

        # A fresh explicit calibration supersedes whatever drift the
        # implicit re-calibration had accumulated against the OLD mapping.
        try:
            from src import gaze_online_cal
            gaze_online_cal.reset()
        except Exception as e:
            logger.error("gaze bias reset failed: %s", e)

        self._teardown()
        if self.on_complete is not None:
            try:
                self.on_complete(True)
            except Exception as e:
                logger.error("on_complete callback failed: %s", e)

    def cancel(self):
        logger.info("Calibration cancelled by user")
        self._teardown()
        if self.on_complete is not None:
            try:
                self.on_complete(False)
            except Exception:
                pass

    def _teardown(self):
        # Only the OWNING run may clear the class-level guard — a stale
        # instance tearing down must not disarm a newer active run.
        if GazeCalibrator._running_inst is None \
                or GazeCalibrator._running_inst is self:
            GazeCalibrator._running = False
            GazeCalibrator._running_inst = None
        for aid in self._after_ids:
            try:
                self.top.after_cancel(aid)
            except Exception:
                pass
        self._after_ids = []
        if self.top is not None:
            try:
                self.top.destroy()
            except Exception:
                pass
        self.top = None
        self.canvas = None
        self.dot = None
