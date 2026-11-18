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
"""Fullscreen gaze calibration overlay.

* 16 targets (4x4 grid) or, with "8 points" in Advanced settings, the 8
  of them in a checkerboard pattern; each is shown for 2 s and samples
  are taken after a 0.7 s settle period that skips the saccade to the
  target.
* Every sample records the gaze ratio (iris centre offset from the
  eye-corner midpoint, normalised by eye width - FaceMesh.gaze_ratio),
  whether the eyes were blinking, and where the eyes were in space.
* Each target is reduced to ONE point: the median of its samples, blink
  frames left out. A blink throws the iris landmarks for a few frames;
  as plain samples those frames pulled a target's fit by a hundred
  pixels or more, as part of a median they do nothing.
* gaze_mapping.fit_points fits two 6-coefficient polynomials
  ``x = cx @ [1, rx, ry, rx*ry, rx^2, ry^2]`` (likewise y) through the
  points, with the head position term (src/head_shift.py) taken out of
  each target first.
* cx/cy, the points and the reference eye position are saved to the
  active profile's ``cursor.json`` under ``gaze_calibration``.
* The raw samples go to ``gaze_cal_log.jsonl`` in the user data folder
  (numbers only, never images; the last few calibrations are kept), so
  the mapping can be studied on real data.

Every gaze consumer reads the result through gaze_mapping.predict().
"""

import json
import logging
import time
import tkinter as tk

import numpy as np
import pyautogui

from src import paths
from src.config_manager import ConfigManager
from src.detectors import FaceMesh
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

# Quick mode: every other target of the 4x4 grid, in a checkerboard
# pattern, so each row and column still has two targets. Fitted on its
# own (no earlier calibration needed): on the logged calibrations it
# predicted the 8 left-out targets about as well as a 16-point fit did
# (~230 px median on 4K), whereas correcting an older fit with 4 points
# stayed ~100 px worse.
POINTS_8 = [POINTS_16[i] for i in (0, 2, 5, 7, 8, 10, 13, 15)]

# Advanced settings > Calibration > Number of points (app-wide).
POINTS_KEY = "calibration_points"
POINT_CHOICES = (16, 8)


def point_count() -> int:
    try:
        n = int(ConfigManager()._read_app_setting(POINTS_KEY, 16))
    except Exception:
        n = 16
    return n if n in POINT_CHOICES else 16


def _points_for(n):
    return POINTS_8 if n == 8 else POINTS_16

# Per-point timing (ms). 2 s display with 0.7 s settle = 1.3 s of
# sampling per point (~30 samples at the 40 ms tick). Across 16 points
# that's ~36 s total including the intro, ~20 s with 8.
DURATION_MS = 2000
SAMPLE_AFTER_MS = 700

# The dot shrinks from DOT_R_START to DOT_R_END (px) over DURATION_MS:
# it shows how long is left and draws the eye to its centre.
DOT_R_START = 22
DOT_R_END = 5
SHRINK_TICK_MS = 30

# The app's own UI font (bundled, registered at startup by
# utils.install_font). "Helvetica" is not a Windows font, so Tk fell back
# to whatever the system picked, which differed between PCs.
UI_FONT = "IBM Plex Sans JP"

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
# app have limited neck mobility and cannot perform it. A change of seat
# is handled by src/head_shift.py instead: the eye position is recorded
# here and the prediction is shifted by how far it has moved since.

# Minimum samples across all points to consider the fit valid.
MIN_SAMPLES = 20
# A target with fewer usable samples than this is left out of the fit.
MIN_DOT_SAMPLES = 8

# Diagnostics: the raw samples of the last few calibrations (app-wide
# setting, on by default; numbers only).
LOG_KEY = "gaze_cal_log"
LOG_FILE = "gaze_cal_log.jsonl"
LOG_KEEP = 20
LOG_COLUMNS = (
    ["ms", "dot", "tx", "ty", "rx", "ry", "blink",
     "rx_r", "ry_r", "w_r", "rx_l", "ry_l", "w_l"]
    # Blendshapes 9-18: eyeBlink L/R, eyeLookDown L/R, eyeLookIn L/R,
    # eyeLookOut L/R, eyeLookUp L/R.
    + ["bs%d" % i for i in range(9, 19)]
    # Rows 0-2 of the facial transformation matrix (rotation | position).
    + ["m%d%d" % (r, c) for r in range(3) for c in range(4)])


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
        # One dict per sample: dot, tx, ty, rx, ry, blink, eye, log.
        self.samples = []
        self._t0 = time.monotonic()
        self._after_ids = []
        self._screen_w = 0
        self._screen_h = 0
        self._points = POINTS_16

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
        self._points = _points_for(point_count())

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
            fill="white", font=(UI_FONT, 28, "bold"))
        self.canvas.create_text(
            self._screen_w // 2, self._screen_h - 40, text=cancel_hint,
            fill="#aaaaaa", font=(UI_FONT, 12))

        # Intro shown in the centre ONE SENTENCE AT A TIME, INTRO_MS apart
        # (replaces the old top-of-screen block of text). The last sentence
        # IS the countdown; it is cleared when the first target shows.
        self._intro_idx = 0
        self.prompt_label = self.canvas.create_text(
            self._screen_w // 2, self._screen_h // 2,
            text=t(INTRO_LINES[0]),
            fill="white", font=(UI_FONT, 22))
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
        if self.idx >= len(self._points):
            self._finish()
            return

        fx, fy = self._points[self.idx]
        tx = int(fx * self._screen_w)
        ty = int(fy * self._screen_h)

        if self.dot is not None:
            self.canvas.delete(self.dot)
        r = DOT_R_START
        self.dot = self.canvas.create_oval(tx - r, ty - r, tx + r, ty + r,
                                           fill="#34A853", outline="white",
                                           width=2)
        self._shrink(self.dot, tx, ty, time.monotonic())

        # Settle, then sample.
        self._after_ids.append(
            self.top.after(SAMPLE_AFTER_MS,
                           lambda: self._sample_tick(0, tx, ty)))

    def _shrink(self, dot, tx, ty, t0):
        """Shrink the current dot over DURATION_MS; stops when the dot
        is replaced or the run ends."""
        self._shrink_after = None
        if self.canvas is None or dot is not self.dot:
            return
        f = min(1.0, (time.monotonic() - t0) * 1000.0 / DURATION_MS)
        r = DOT_R_START + (DOT_R_END - DOT_R_START) * f
        try:
            self.canvas.coords(dot, tx - r, ty - r, tx + r, ty + r)
        except Exception:
            return
        if f < 1.0:
            self._shrink_after = self.top.after(
                SHRINK_TICK_MS, lambda: self._shrink(dot, tx, ty, t0))

    def _sample_once(self, tx, ty):
        """Capture one sample for the target at (tx, ty)."""
        fm = FaceMesh()
        ratio = fm.get_gaze_ratio()
        if ratio is None:
            return
        rx, ry = ratio
        from src import head_shift
        M = head_shift.pose_matrix()
        try:
            blink = bool(fm.is_blinking())
        except Exception:
            blink = False
        ms = int((time.monotonic() - self._t0) * 1000)
        row = [ms, self.idx, int(tx), int(ty), round(rx, 5), round(ry, 5),
               int(blink)]
        try:
            eyes = fm.get_eye_ratios()
            for e in eyes or ((0, 0, 0), (0, 0, 0)):
                row += [round(e[0], 5), round(e[1], 5), round(e[2], 2)]
            bs = fm.blendshapes_buffer[-1]
            row += [round(float(v), 3) for v in bs[9:19]]
            row += ([round(float(v), 4) for v in M[:3, :].ravel()]
                    if M is not None else [0.0] * 12)
        except Exception:
            pass
        self.samples.append({
            "dot": self.idx, "tx": float(tx), "ty": float(ty),
            "rx": float(rx), "ry": float(ry), "blink": blink,
            "eye": head_shift.eye_position(M), "log": row})

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

    def _fail(self, why, *args):
        logger.warning("Calibration " + why + "; aborting", *args)
        self._teardown()
        if self.on_complete is not None:
            try:
                self.on_complete(False)
            except Exception:
                pass

    def _dot_points(self):
        """One point per target: [tx, ty, rx, ry] plus [ex, ey, ez] when
        the eye position was seen - medians over the target's samples,
        blink frames left out (unless that leaves too few)."""
        by_dot = {}
        for smp in self.samples:
            by_dot.setdefault(smp["dot"], []).append(smp)
        pts = []
        for dot in sorted(by_dot):
            ss = by_dot[dot]
            good = [x for x in ss if not x["blink"]]
            if len(good) < MIN_DOT_SAMPLES:
                good = ss
            if len(good) < MIN_DOT_SAMPLES:
                logger.warning("Calibration target %d: only %d samples, "
                               "left out", dot, len(good))
                continue
            p = [good[0]["tx"], good[0]["ty"],
                 float(np.median([x["rx"] for x in good])),
                 float(np.median([x["ry"] for x in good]))]
            eyes = [x["eye"] for x in good if x["eye"] is not None]
            if len(eyes) * 2 >= len(good):
                p += [float(v) for v in np.median(np.array(eyes), axis=0)]
            pts.append(p)
        return pts

    def _finish(self):
        if len(self.samples) < MIN_SAMPLES:
            self._fail("captured too few samples (%d)", len(self.samples))
            return

        from src import gaze_mapping, head_shift
        pts = self._dot_points()
        eyes = [x["eye"] for x in self.samples
                if x["eye"] is not None and not x["blink"]]
        eye_ref = (np.median(np.array(eyes), axis=0)
                   if len(eyes) >= MIN_SAMPLES else None)
        # 16 targets: at least 10 usable. 8 targets: at most one lost.
        need = min(gaze_mapping.MIN_POINTS, len(self._points) - 1)
        res = gaze_mapping.fit_points(pts, eye_ref,
                                      self._screen_w, self._screen_h,
                                      min_points=need)
        if res is None:
            self._fail("has too few usable targets (%d of %d)",
                       len(pts), len(self._points))
            return
        cx, cy, rmse_x, rmse_y = res
        blinks = sum(1 for x in self.samples if x["blink"])
        logger.info("Gaze calibration: %d samples (%d in blinks), %d of "
                    "%d targets, RMSE x=%dpx y=%dpx (per target)",
                    len(self.samples), blinks, len(pts), len(self._points),
                    int(rmse_x), int(rmse_y))

        cal = {"cx": cx.tolist(), "cy": cy.tolist(),
               head_shift.SCREEN_KEY: [int(self._screen_w),
                                       int(self._screen_h)],
               head_shift.PTS_KEY: [
                   [int(p[0]), int(p[1]), round(p[2], 5), round(p[3], 5)]
                   + [round(v, 2) for v in p[4:7]] for p in pts]}
        if eye_ref is not None:
            cal[head_shift.REF_KEY] = [round(float(v), 2) for v in eye_ref]
            logger.info("Gaze calibration eye position: x=%.1f y=%.1f "
                        "z=%.1f cm; head term %s", eye_ref[0], eye_ref[1],
                        eye_ref[2], ("off" if head_shift.DISABLED else head_shift.params()))
        ConfigManager().set_temp_config(gaze_mapping.CAL_KEY, cal)
        ConfigManager().apply_config()
        self._write_log(cal, rmse_x, rmse_y)

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

    def _write_log(self, cal, rmse_x, rmse_y):
        """Append this calibration's raw samples to the diagnostics log,
        keeping the last LOG_KEEP calibrations."""
        try:
            if not ConfigManager()._read_app_setting(LOG_KEY, True):
                return
            from src import config_manager, head_shift
            entry = dict(cal)
            entry.update({
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "version": getattr(config_manager, "VERSION", ""),
                "monitor_cm": head_shift.monitor_cm(),
                "head_term": ("off" if head_shift.DISABLED else head_shift.params()),
                "rmse": [round(rmse_x, 1), round(rmse_y, 1)],
                "columns": LOG_COLUMNS,
                "samples": [x["log"] for x in self.samples]})
            path = paths.user_data_dir() / LOG_FILE
            lines = []
            if path.exists():
                with open(path, encoding="utf-8") as f:
                    lines = [ln for ln in f.read().splitlines() if ln]
            lines.append(json.dumps(entry, separators=(",", ":")))
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(chr(10).join(lines[-LOG_KEEP:]) + chr(10))
            tmp.replace(path)
        except Exception as e:
            logger.error("calibration log write failed: %s", e)

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
        for aid in self._after_ids + [getattr(self, "_shrink_after", None)]:
            if aid is None:
                continue
            try:
                self.top.after_cancel(aid)
            except Exception:
                pass
        self._after_ids = []
        self._shrink_after = None
        if self.top is not None:
            try:
                self.top.destroy()
            except Exception:
                pass
        self.top = None
        self.canvas = None
        self.dot = None
