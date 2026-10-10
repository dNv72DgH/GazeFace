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
"""Large gesture-list editor. Per row: icon, enable checkbox, live
blendshape meter, per-gesture sensitivity slider + numeric entry."""

import logging
import tkinter as tk

import customtkinter
from PIL import Image

import src.shape_list as shape_list
from src.config_manager import ConfigManager
from src.detectors import FaceMesh
from src.gui.frames.safe_disposable_frame import style_scrollbar
from src.gui.popups._util import apply_popup_geometry, POPUP_BG
from src.i18n import t

logger = logging.getLogger("GestureListPopup")

ICON_SIZE = (54, 38)
SLIDER_WIDTH = 140
# Two-column grid for the gesture rows. With ~23 enabled gestures
# this turns a long single column into a ~12-row × 2-col grid that
# fits the user's screen better.
COLUMN_COUNT = 2

# Sensitivity slider: integer position / GAIN_DIVISOR = real multiplier.
# Matches the gesture-options popup so users see the same number twice.
GAIN_SLIDER_MIN = 0
GAIN_SLIDER_MAX = 200      # 20.0x ceiling
GAIN_DIVISOR = 10.0
GAIN_ENTRY_MIN = 0.0
GAIN_ENTRY_MAX = 20.0

# Bar scale: full = threshold value of 5.0 (matches the binding-page
# threshold slider's right edge).
BAR_SCALE = 5.0

METER_REFRESH_MS = 100


class GestureListPopup:

    def __init__(self, master):
        self.master = master
        self.top = None
        self.checkbox_vars = {}
        # Per-gesture widget refs for the live meter + sensitivity row.
        # gesture name -> dict of widgets/state.
        self.rows = {}
        self._meter_after_id = None
        # Keep PIL references alive (CTkImage doesn't retain them).
        self._icon_refs = []

    # ---------------------------------------------------------------- #
    # Build
    # ---------------------------------------------------------------- #

    def open(self):
        if self.top is not None:
            try:
                self.top.lift()
            except Exception:
                pass
            return

        self.top = tk.Toplevel(self.master)
        self.top.title(t("Gesture List"))
        self.top.transient(self.master.winfo_toplevel())
        # 3/4 of main window, resizable so the user can maximise.
        apply_popup_geometry(self.top, self.master,
                             fraction=0.75, resizable=True,
                             min_w=720, min_h=480)
        self.top.bind("<Escape>", lambda _e: self.close())
        self.top.protocol("WM_DELETE_WINDOW", self.close)

        # Header strip
        header = customtkinter.CTkLabel(
            master=self.top,
            text=t("Gesture List"),
            text_color="black", fg_color=POPUP_BG)
        header.cget("font").configure(size=22, weight="bold")
        header.pack(padx=20, pady=(20, 5), anchor="w", fill="x")

        desc = customtkinter.CTkLabel(
            master=self.top,
            text=t("Tick the gestures you want available in the bindings dropdown. The bar shows the gained blendshape value (= number compared against the threshold). The number on the right is the per-gesture sensitivity multiplier."),
            wraplength=900,
            justify=tk.LEFT,
            text_color="black", fg_color=POPUP_BG)
        desc.cget("font").configure(size=12)
        desc.pack(padx=20, pady=(0, 10), anchor="w", fill="x")

        # Scrollable list — fg_color matches the main page so the popup
        # reads as a single surface, not a stack of cards.
        scroll = customtkinter.CTkScrollableFrame(
            master=self.top, fg_color=POPUP_BG)
        style_scrollbar(scroll)
        scroll.pack(padx=20, pady=10, fill="both", expand=True)
        # Configure both grid columns with equal weight so the two
        # rows balance and stretch evenly when the user maximises the
        # window. `uniform` ties them to the same allocation slot.
        for c in range(COLUMN_COUNT):
            scroll.grid_columnconfigure(c, weight=1, uniform="cols")
        self._scroll = scroll

        enabled = set(ConfigManager().config.get(
            "enabled_gestures", []) or [])
        per_gestures_cfg = dict(ConfigManager().config.get(
            "gesture_gains", {}) or {})

        # Build one row per gesture (skip "None" — it's the
        # unbind sentinel and isn't user-toggleable). 2-column layout:
        # fill left-to-right, top-to-bottom.
        gestures = [g for g in shape_list.available_gestures
                    if g != "None"]
        for idx, name in enumerate(gestures):
            self._build_row(scroll, idx, name,
                            enabled=name in enabled,
                            gain=float(per_gestures_cfg.get(name, 1.0)))

        # Footer
        footer = customtkinter.CTkFrame(
            master=self.top, fg_color=POPUP_BG)
        footer.pack(padx=20, pady=(0, 16), fill="x")

        all_btn = customtkinter.CTkButton(
            master=footer, text=t("Select all"),
            command=self._select_all, width=120,
            fg_color="white", text_color="#D9622B",
            border_width=1, border_color="#D9622B")
        all_btn.pack(side="left", padx=(0, 8))

        none_btn = customtkinter.CTkButton(
            master=footer, text=t("Select none"),
            command=self._select_none, width=120,
            fg_color="white", text_color="#D9622B",
            border_width=1, border_color="#D9622B")
        none_btn.pack(side="left", padx=(0, 8))

        reset_btn = customtkinter.CTkButton(
            master=footer, text=t("Reset to defaults"),
            command=self._reset_defaults, width=160,
            fg_color="white", text_color="#D9622B",
            border_width=1, border_color="#D9622B")
        reset_btn.pack(side="left", padx=(0, 8))

        ok_btn = customtkinter.CTkButton(
            master=footer, text=t("Apply"),
            command=self._apply_and_close, width=120)
        ok_btn.pack(side="right")

        # Live-meter loop
        self._meter_after_id = self.top.after(METER_REFRESH_MS,
                                              self._meter_tick)

    # ---------------------------------------------------------------- #
    # Per-row builder
    # ---------------------------------------------------------------- #

    def _build_row(self, parent, idx: int, name: str,
                   enabled: bool, gain: float):
        # 2-column placement: left-to-right, top-to-bottom.
        grid_row = idx // COLUMN_COUNT
        grid_col = idx % COLUMN_COUNT

        # Outer row spans its grid cell. Each row's meter cell stretches
        # so when the window is wider the meter widens (slider stays a
        # fixed compact size).
        row_frame = customtkinter.CTkFrame(
            master=parent, fg_color="transparent")
        row_frame.grid(row=grid_row, column=grid_col,
                       padx=8, pady=4, sticky="ew")
        # Columns inside the row: 0 icon | 1 checkbox+name | 2 meter | 3 gain
        row_frame.grid_columnconfigure(2, weight=1)

        # Icon
        try:
            img_path = shape_list.available_gestures[name]
            pil_img = Image.open(img_path).resize(ICON_SIZE)
            icon = customtkinter.CTkImage(pil_img, size=ICON_SIZE)
            self._icon_refs.append(icon)
            icon_label = customtkinter.CTkLabel(
                master=row_frame, image=icon, text="")
            icon_label.grid(row=0, column=0, padx=(0, 8))
        except Exception as e:
            logger.warning("Icon load failed for %s: %s", name, e)

        # Enable checkbox + gesture name
        chk_var = tk.BooleanVar(value=enabled)
        cb = customtkinter.CTkCheckBox(
            master=row_frame,
            text=t(name),
            variable=chk_var,
            onvalue=True, offvalue=False)
        cb.grid(row=0, column=1, sticky="w", padx=(0, 16))
        self.checkbox_vars[name] = chk_var

        # Live blendshape meter + numeric readout
        meter_cell = customtkinter.CTkFrame(
            master=row_frame, fg_color="transparent")
        meter_cell.grid(row=0, column=2, sticky="ew", padx=(0, 12))
        meter_cell.grid_columnconfigure(0, weight=1)
        bar = customtkinter.CTkProgressBar(
            master=meter_cell, height=14)
        bar.set(0.0)
        bar.configure(progress_color="#D9622B")
        bar.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        value_lbl = customtkinter.CTkLabel(
            master=meter_cell, text="0.000",
            width=60, anchor="e",
            text_color="#7A736A")
        value_lbl.cget("font").configure(size=11)
        value_lbl.grid(row=0, column=1, sticky="e")

        # Per-gesture sensitivity: slider + numeric entry
        gain_cell = customtkinter.CTkFrame(
            master=row_frame, fg_color="transparent")
        gain_cell.grid(row=0, column=3, sticky="e")

        slider_pos = int(round(gain * GAIN_DIVISOR))
        slider_pos = max(GAIN_SLIDER_MIN,
                         min(GAIN_SLIDER_MAX, slider_pos))

        gain_slider = customtkinter.CTkSlider(
            master=gain_cell,
            from_=GAIN_SLIDER_MIN, to=GAIN_SLIDER_MAX,
            width=SLIDER_WIDTH,
            number_of_steps=GAIN_SLIDER_MAX - GAIN_SLIDER_MIN,
            command=lambda v, g=name: self._on_gain_drag(g, v))
        gain_slider.set(slider_pos)
        gain_slider.bind(
            "<Button-1>",
            lambda _e, g=name: self._set_drag_flag(g, True))
        gain_slider.bind(
            "<ButtonRelease-1>",
            lambda _e, g=name: self._on_gain_release(g))
        gain_slider.grid(row=0, column=0, padx=(0, 8))

        gain_var = tk.StringVar(value=f"{slider_pos / GAIN_DIVISOR:.1f}")
        gain_entry = customtkinter.CTkEntry(
            master=gain_cell, textvariable=gain_var,
            width=60, justify=tk.RIGHT)
        gain_entry.grid(row=0, column=1)
        trace_id = gain_var.trace(
            "w", lambda *_a, g=name: self._on_gain_entry_changed(g))

        self.rows[name] = {
            "bar": bar,
            "value_label": value_lbl,
            "slider": gain_slider,
            "entry": gain_entry,
            "entry_var": gain_var,
            "trace_id": trace_id,
            "dragging": False,
        }

    # ---------------------------------------------------------------- #
    # Slider / entry callbacks (mirror what gesture_options_popup does
    # for the same field, so values stay consistent across both UIs).
    # ---------------------------------------------------------------- #

    def _set_drag_flag(self, gesture: str, val: bool):
        if gesture in self.rows:
            self.rows[gesture]["dragging"] = val

    def _on_gain_drag(self, gesture: str, value):
        row = self.rows.get(gesture)
        if row is None:
            return
        row["dragging"] = True
        pos = int(value)
        # Live-mirror to entry without ping-pong.
        try:
            row["entry_var"].trace_vdelete("w", row["trace_id"])
        except Exception:
            pass
        row["entry_var"].set(f"{pos / GAIN_DIVISOR:.1f}")
        row["trace_id"] = row["entry_var"].trace(
            "w", lambda *_a, g=gesture: self._on_gain_entry_changed(g))

    def _on_gain_release(self, gesture: str):
        row = self.rows.get(gesture)
        if row is None:
            return
        row["dragging"] = False
        pos = int(row["slider"].get())
        self._save_gain(gesture, pos / GAIN_DIVISOR)

    def _on_gain_entry_changed(self, gesture: str):
        row = self.rows.get(gesture)
        if row is None:
            return
        text = row["entry_var"].get().strip()
        try:
            v = float(text)
        except ValueError:
            row["entry"].configure(fg_color="#ee9e9d")
            return
        if v < GAIN_ENTRY_MIN or v > GAIN_ENTRY_MAX:
            row["entry"].configure(fg_color="#ee9e9d")
            return
        row["entry"].configure(fg_color="white")
        pos = int(round(v * GAIN_DIVISOR))
        row["slider"].set(pos)
        if not row["dragging"]:
            self._save_gain(gesture, v)

    def _save_gain(self, gesture: str, value: float):
        gains = dict(ConfigManager().config.get("gesture_gains", {}) or {})
        gains[gesture] = value
        ConfigManager().set_temp_config("gesture_gains", gains)
        ConfigManager().apply_config()

    # ---------------------------------------------------------------- #
    # Live meter
    # ---------------------------------------------------------------- #

    def _meter_tick(self):
        if self.top is None:
            return
        try:
            from src.controllers.keybinder import RESPONSE_CURVES
            bs = FaceMesh().get_blendshapes()
            if bs is not None:
                global_gain = float(
                    ConfigManager().config.get("gesture_gain", 3.0))
                per_gestures = dict(ConfigManager().config.get(
                    "gesture_gains", {}) or {})
                curves = dict(ConfigManager().config.get(
                    "gesture_curves", {}) or {})
                for name, row in self.rows.items():
                    idx = shape_list.blendshape_indices.get(name)
                    if idx is None or idx >= len(bs):
                        continue
                    raw = float(bs[idx])
                    curve_fn = RESPONSE_CURVES.get(
                        curves.get(name, "linear"),
                        RESPONSE_CURVES["linear"])
                    shaped = curve_fn(max(0.0, min(1.0, raw)))
                    per = float(per_gestures.get(name, 1.0))
                    gained = max(0.0, shaped * global_gain * per)
                    row["bar"].set(min(1.0, gained / BAR_SCALE))
                    row["value_label"].configure(text=f"{gained:.3f}")
        except Exception as e:
            logger.debug("meter_tick: %s", e)
        self._meter_after_id = self.top.after(METER_REFRESH_MS,
                                              self._meter_tick)

    # ---------------------------------------------------------------- #
    # Footer actions
    # ---------------------------------------------------------------- #

    def _select_all(self):
        for v in self.checkbox_vars.values():
            v.set(True)

    def _select_none(self):
        for v in self.checkbox_vars.values():
            v.set(False)

    def _reset_defaults(self):
        defaults = set(shape_list.DEFAULT_ENABLED_GESTURES)
        for name, var in self.checkbox_vars.items():
            var.set(name in defaults)

    def _apply_and_close(self):
        enabled = [name for name, var in self.checkbox_vars.items()
                   if var.get()]
        ConfigManager().set_temp_config("enabled_gestures", enabled)
        ConfigManager().apply_config()
        logger.info("enabled_gestures updated (%d items)", len(enabled))
        self.close()

    def close(self):
        if self._meter_after_id is not None and self.top is not None:
            try:
                self.top.after_cancel(self._meter_after_id)
            except Exception:
                pass
        self._meter_after_id = None
        if self.top is not None:
            try:
                self.top.destroy()
            except Exception:
                pass
        self.top = None
        self.checkbox_vars = {}
        self.rows = {}
        self._icon_refs = []
