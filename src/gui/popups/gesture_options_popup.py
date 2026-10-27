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
"""Popup with options specific to a particular gesture (face shape).

Options live keyed by gesture name in ConfigManager.config so they
survive even when the user rebinds the gesture to a different action.

Currently surfaces:
  * Per-gesture sensitivity gain      (all gestures)
  * Hold-before-fire window (ms)      (Close left/right eye only)
"""

import logging
import tkinter as tk

import customtkinter

from src.config_manager import ConfigManager
from src.detectors import FaceMesh
import src.shape_list as shape_list
from src.gui.popups._util import apply_popup_geometry, POPUP_BG
from src.gui.help_icon import HelpIcon, section_with_help
from src.i18n import t

logger = logging.getLogger("GestureOptionsPopup")

# Per-gesture sensitivity slider bounds. Slider position / GAIN_DIVISOR
# = real multiplier. e.g. 10 = 1.0x, 50 = 5.0x.
GAIN_SLIDER_MIN = 0
GAIN_SLIDER_MAX = 200       # up to 20.0x (matches the entry's clamp)
GAIN_DIVISOR = 10.0

# Hold-time (ms) bounds for the universal hold-time control.
HOLD_MIN_MS = 0             # 0 disables the hold guard
HOLD_MAX_MS = 2000
# Default is 0 (fire immediately). Close left/right eye default to 100ms
# through the seeded values in configs_template/*/cursor.json. Keep in
# step with keybinder.HOLD_DEFAULT_MS.
HOLD_DEFAULT_MS = 0

# Motion-freeze sensitivity bounds, in the 0..5000 display scale
# (= per-frame change of the gained value × 1000). Smaller = freezes on
# smaller motion (more sensitive).
FREEZE_MIN = 1
FREEZE_MAX = 2000
FREEZE_DEFAULT = 100

# Grace period (ms) before a freeze is committed — lets a brief
# involuntary spike (blink/twitch) pass without freezing the cursor.
GRACE_MIN_MS = 0
GRACE_MAX_MS = 1000
GRACE_DEFAULT_MS = 100

# Simultaneous-input block: movement sensitivity (same display scale)
# and priority bounds.
BLOCK_MIN = 1
BLOCK_MAX = 2000
BLOCK_DEFAULT = 100
PRIORITY_MIN = 0
PRIORITY_MAX = 100
PRIORITY_DEFAULT = 0

# Disable on strong reaction: sensitivity sets the level, relative to the
# gesture's own threshold, above which a reaction counts as strong and
# unintended (a sudden laugh) - 1 = threshold x 1.05, each step +0.05
# (100 = x 6.0); the gesture then stays off for the disabled time (s).
STRONG_MIN = 1
STRONG_MAX = 100
STRONG_DEFAULT = 20
STRONG_SECS_MIN = 1
STRONG_SECS_MAX = 120
STRONG_SECS_DEFAULT = 2

# Per-gesture threshold-slider upper limit (in the binding page's slider
# units; /LIMIT_FACTOR = the real threshold ceiling). Lowering it keeps
# the threshold slider's width but shrinks its range, so small thresholds
# are easier to dial in; the volume/sub-monitor meters rescale to match.
LIMIT_MIN = 0
LIMIT_MAX = 5000
LIMIT_DEFAULT = 5000
LIMIT_FACTOR = 1000.0
# Lower limit (same units): 0 = none. Must stay FLOOR_MIN_SPAN under the
# upper limit, matching page_select_gestures.MIN_SPAN.
FLOOR_DEFAULT = 0
FLOOR_MIN_SPAN = 10


class GestureOptionsPopup:

    def __init__(self, master):
        self.master = master
        self.top = None
        self._col = None        # current target column for section builders
        self._window = None
        self.gesture = None
        # Live meter widgets + the after() id that drives them.
        self.meter_bar = None
        self.meter_label = None
        self._meter_after_id = None

    def open(self, gesture: str):
        if self.top is not None:
            self.close()

        self.gesture = gesture
        # In-window panel (no Toplevel). self.top is the panel body; the
        # section builders are pointed at the left / right columns.
        from src.gui.panel_host import panel_host
        host = panel_host()
        if host is None:
            return
        self._host = host
        self.top = host.push(f"{t(gesture)} {t('Gesture options')}",
                             on_close=self._stop_meter)
        self._col = self.top

        # (No in-body title — the panel header already carries it.)

        # Two columns so the five sections fit with far less scrolling.
        cols = customtkinter.CTkFrame(self.top, fg_color="transparent")
        cols.pack(fill="both", expand=True)
        left = customtkinter.CTkFrame(cols, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nw", padx=(0, 8))
        right = customtkinter.CTkFrame(cols, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nw", padx=(8, 0))

        self._col = left
        self._build_gain_section()
        self._build_curve_section()
        # Hold time is now exposed for every gesture (not just
        # close-eye). Default 0 = no hold.
        self._build_hold_section()
        self._build_limit_section()
        self._build_floor_section()
        self._col = right
        self._build_motion_freeze_section()
        self._build_block_section()
        self._build_strong_off_section()

        # Start live-meter loop after the geometry is settled. Drive
        # it off the scrollable body — the body is created inside the
        # Toplevel so its after() will be cancelled together with the
        # window in close().
        self._meter_after_id = self.top.after(100, self._meter_tick)

    # ---------------------------------------------------------------- #
    # Per-gesture sensitivity
    # ---------------------------------------------------------------- #

    def _build_gain_section(self):
        section = customtkinter.CTkLabel(
            master=self._col, text=t("Sensitivity (per-gesture)"))
        section.cget("font").configure(size=14, weight="bold")
        section.pack(padx=20, pady=(6, 2), anchor="w")

        # Live meter — shows the gained blendshape value (the number the
        # threshold comparison actually sees). Makes "is my sensitivity
        # high enough?" answerable at a glance.
        meter_row = customtkinter.CTkFrame(
            master=self._col, fg_color="transparent")
        meter_row.pack(padx=20, anchor="w", fill="x", pady=(0, 6))
        self.meter_bar = customtkinter.CTkProgressBar(
            master=meter_row, width=220, height=14)
        self.meter_bar.set(0.0)
        self.meter_bar.configure(progress_color="#D9622B")
        self.meter_bar.pack(side="left", padx=(0, 10))
        self.meter_label = customtkinter.CTkLabel(
            master=meter_row, text="raw 0.000  →  0.000",
            width=120, anchor="w", justify=tk.LEFT,
            text_color="#7A736A")
        self.meter_label.cget("font").configure(size=11)
        self.meter_label.pack(side="left")

        row = customtkinter.CTkFrame(
            master=self._col, fg_color="transparent")
        row.pack(padx=20, anchor="w", fill="x")

        self._gain_dragging = False
        self.gain_slider = customtkinter.CTkSlider(
            master=row,
            from_=GAIN_SLIDER_MIN, to=GAIN_SLIDER_MAX,
            width=220,
            number_of_steps=GAIN_SLIDER_MAX - GAIN_SLIDER_MIN,
            command=self._on_gain_drag)
        self.gain_slider.bind("<Button-1>",
                              lambda _e: setattr(self, "_gain_dragging",
                                                 True))
        self.gain_slider.bind("<ButtonRelease-1>",
                              self._on_gain_release)
        self.gain_slider.pack(side="left", padx=(0, 10))

        self.gain_var = tk.StringVar()
        self._gain_trace_id = self.gain_var.trace(
            "w", self._on_gain_entry_changed)
        self.gain_entry = customtkinter.CTkEntry(
            master=row, textvariable=self.gain_var, width=64)
        self.gain_entry.pack(side="left")

        # Seed from config
        current = float(self._gesture_gains().get(self.gesture, 1.0))
        slider_pos = int(round(current * GAIN_DIVISOR))
        slider_pos = max(GAIN_SLIDER_MIN,
                         min(GAIN_SLIDER_MAX, slider_pos))
        self.gain_slider.set(slider_pos)
        self.gain_var.trace_vdelete("w", self._gain_trace_id)
        self.gain_var.set(f"{slider_pos / GAIN_DIVISOR:.1f}")
        self._gain_trace_id = self.gain_var.trace(
            "w", self._on_gain_entry_changed)

    def _gesture_gains(self):
        return dict(ConfigManager().config.get("gesture_gains", {}) or {})

    def _on_gain_drag(self, value):
        self._gain_dragging = True
        pos = int(value)
        try:
            self.gain_var.trace_vdelete("w", self._gain_trace_id)
        except Exception:
            pass
        self.gain_var.set(f"{pos / GAIN_DIVISOR:.1f}")
        self._gain_trace_id = self.gain_var.trace(
            "w", self._on_gain_entry_changed)

    def _on_gain_release(self, _event):
        self._gain_dragging = False
        pos = int(self.gain_slider.get())
        gains = self._gesture_gains()
        gains[self.gesture] = pos / GAIN_DIVISOR
        ConfigManager().set_temp_config("gesture_gains", gains)
        ConfigManager().apply_config()

    def _on_gain_entry_changed(self, *_args):
        text = self.gain_var.get().strip()
        try:
            v = float(text)
        except ValueError:
            self.gain_entry.configure(fg_color="#ee9e9d")
            return
        if v < 0.0 or v > GAIN_SLIDER_MAX / GAIN_DIVISOR:
            self.gain_entry.configure(fg_color="#ee9e9d")
            return
        self.gain_entry.configure(fg_color="white")
        pos = int(round(v * GAIN_DIVISOR))
        self.gain_slider.set(pos)
        if not self._gain_dragging:
            gains = self._gesture_gains()
            gains[self.gesture] = v
            ConfigManager().set_temp_config("gesture_gains", gains)
            ConfigManager().apply_config()

    # ---------------------------------------------------------------- #
    # Response curve (linear / slow-start / fast-start)
    # ---------------------------------------------------------------- #

    # Keep in sync with src.controllers.keybinder.RESPONSE_CURVES.
    CURVE_NAMES = ("linear", "slow_start", "fast_start")

    def _build_curve_section(self):
        section_with_help(self._col, t("Response curve"), "Response curve")

        current = self._curve_map().get(self.gesture, "linear")
        if current not in self.CURVE_NAMES:
            current = "linear"
        self.curve_var = tk.StringVar(value=current)

        row = customtkinter.CTkFrame(
            master=self._col, fg_color="transparent")
        row.pack(padx=20, anchor="w", fill="x")
        labels = {
            "linear": t("Linear (default)"),
            "slow_start": t("Slow start, fast end"),
            "fast_start": t("Fast start, slow end"),
        }
        for name in self.CURVE_NAMES:
            # Each radio in its own line so the shaped curves can carry a
            # help marker right after their label.
            line = customtkinter.CTkFrame(master=row, fg_color="transparent")
            line.pack(side="top", anchor="w", pady=2)
            r = customtkinter.CTkRadioButton(
                master=line, text=labels[name],
                variable=self.curve_var, value=name,
                command=self._on_curve_change)
            r.pack(side="left")
            if name != "linear":
                HelpIcon(line, "curve:" + name).pack(side="left",
                                                     padx=(8, 0))

    def _curve_map(self):
        return dict(ConfigManager().config.get("gesture_curves", {}) or {})

    def _on_curve_change(self):
        choice = self.curve_var.get()
        if choice not in self.CURVE_NAMES:
            return
        curves = self._curve_map()
        curves[self.gesture] = choice
        ConfigManager().set_temp_config("gesture_curves", curves)
        ConfigManager().apply_config()

    # ---------------------------------------------------------------- #
    # Hold-before-fire window
    # ---------------------------------------------------------------- #

    def _build_hold_section(self):
        section_with_help(self._col, t("Hold time (ms)"), "Hold time (ms)")

        row = customtkinter.CTkFrame(
            master=self._col, fg_color="transparent")
        row.pack(padx=20, anchor="w", fill="x")

        self._hold_dragging = False
        self.hold_slider = customtkinter.CTkSlider(
            master=row,
            from_=HOLD_MIN_MS, to=HOLD_MAX_MS,
            width=220,
            number_of_steps=(HOLD_MAX_MS - HOLD_MIN_MS) // 10,
            command=self._on_hold_drag)
        self.hold_slider.bind("<Button-1>",
                              lambda _e: setattr(self, "_hold_dragging",
                                                 True))
        self.hold_slider.bind("<ButtonRelease-1>",
                              self._on_hold_release)
        self.hold_slider.pack(side="left", padx=(0, 10))

        self.hold_var = tk.StringVar()
        self._hold_trace_id = self.hold_var.trace(
            "w", self._on_hold_entry_changed)
        self.hold_entry = customtkinter.CTkEntry(
            master=row, textvariable=self.hold_var, width=64)
        self.hold_entry.pack(side="left")

        current = int(self._hold_map().get(self.gesture, HOLD_DEFAULT_MS))
        current = max(HOLD_MIN_MS, min(HOLD_MAX_MS, current))
        self.hold_slider.set(current)
        self.hold_var.trace_vdelete("w", self._hold_trace_id)
        self.hold_var.set(str(current))
        self._hold_trace_id = self.hold_var.trace(
            "w", self._on_hold_entry_changed)

    def _hold_map(self):
        return dict(ConfigManager().config.get("gesture_hold_ms", {}) or {})

    def _on_hold_drag(self, value):
        self._hold_dragging = True
        v = int(value)
        try:
            self.hold_var.trace_vdelete("w", self._hold_trace_id)
        except Exception:
            pass
        self.hold_var.set(str(v))
        self._hold_trace_id = self.hold_var.trace(
            "w", self._on_hold_entry_changed)

    def _on_hold_release(self, _event):
        self._hold_dragging = False
        v = int(self.hold_slider.get())
        m = self._hold_map()
        m[self.gesture] = v
        ConfigManager().set_temp_config("gesture_hold_ms", m)
        ConfigManager().apply_config()

    def _on_hold_entry_changed(self, *_args):
        text = self.hold_var.get().strip()
        try:
            v = int(text)
        except ValueError:
            self.hold_entry.configure(fg_color="#ee9e9d")
            return
        if v < HOLD_MIN_MS or v > HOLD_MAX_MS:
            self.hold_entry.configure(fg_color="#ee9e9d")
            return
        self.hold_entry.configure(fg_color="white")
        self.hold_slider.set(v)
        if not self._hold_dragging:
            m = self._hold_map()
            m[self.gesture] = v
            ConfigManager().set_temp_config("gesture_hold_ms", m)
            ConfigManager().apply_config()

    # ---------------------------------------------------------------- #
    # Motion-freeze: stop the cursor while this gesture is moving
    # ---------------------------------------------------------------- #

    def _build_motion_freeze_section(self):
        self.freeze_enabled_var = tk.BooleanVar(
            value=bool(self._freeze_enabled_map().get(self.gesture, False)))
        section_with_help(self._col, t("Freeze pointer on gesture"),
                          "Freeze pointer on gesture",
                          pady=(16, 8), variable=self.freeze_enabled_var,
                          command=self._on_freeze_toggle)

        sens_lbl = customtkinter.CTkLabel(
            master=self._col, text=t("Freeze sensitivity"))
        sens_lbl.cget("font").configure(size=12)
        sens_lbl.pack(padx=20, pady=(0, 2), anchor="w")
        row = customtkinter.CTkFrame(
            master=self._col, fg_color="transparent")
        row.pack(padx=20, anchor="w", fill="x")

        self._freeze_dragging = False
        self.freeze_slider = customtkinter.CTkSlider(
            master=row,
            from_=FREEZE_MIN, to=FREEZE_MAX,
            width=220,
            number_of_steps=FREEZE_MAX - FREEZE_MIN,
            command=self._on_freeze_drag)
        self.freeze_slider.bind(
            "<Button-1>",
            lambda _e: setattr(self, "_freeze_dragging", True))
        self.freeze_slider.bind("<ButtonRelease-1>",
                                self._on_freeze_release)
        self.freeze_slider.pack(side="left", padx=(0, 10))

        self.freeze_var = tk.StringVar()
        self._freeze_trace_id = self.freeze_var.trace(
            "w", self._on_freeze_entry_changed)
        self.freeze_entry = customtkinter.CTkEntry(
            master=row, textvariable=self.freeze_var, width=64)
        self.freeze_entry.pack(side="left")

        current = int(self._freeze_sens_map().get(
            self.gesture, FREEZE_DEFAULT))
        current = max(FREEZE_MIN, min(FREEZE_MAX, current))
        self.freeze_slider.set(current)
        self.freeze_var.trace_vdelete("w", self._freeze_trace_id)
        self.freeze_var.set(str(current))
        self._freeze_trace_id = self.freeze_var.trace(
            "w", self._on_freeze_entry_changed)

        # --- Ignore involuntary movements (blinks/twitches) ---
        # Ignore-blinks + grace are sub-options of "Freeze pointer" — indent
        # them, and grey them out unless the freeze checkbox is on.
        self.freeze_invol_var = tk.BooleanVar(
            value=bool(self._freeze_invol_map().get(self.gesture, False)))
        # The checkbox is its own label, so the marker goes beside
        # the control rather than after a heading.
        invol_row = customtkinter.CTkFrame(
            master=self._col, fg_color="transparent")
        invol_row.pack(padx=40, pady=(10, 2), anchor="w")
        self.invol_cb = customtkinter.CTkCheckBox(
            master=invol_row,
            text=t("Ignore blinks / involuntary movements"),
            variable=self.freeze_invol_var,
            onvalue=True, offvalue=False,
            command=self._on_freeze_invol_toggle)
        self.invol_cb.pack(side="left")
        HelpIcon(invol_row,
                 "Ignore blinks / involuntary movements").pack(
                     side="left", padx=(8, 0))

        grow = customtkinter.CTkFrame(
            master=self._col, fg_color="transparent")
        grow.pack(padx=40, anchor="w", fill="x")
        self._grace_dragging = False
        self.grace_slider = customtkinter.CTkSlider(
            master=grow, from_=GRACE_MIN_MS, to=GRACE_MAX_MS, width=220,
            number_of_steps=(GRACE_MAX_MS - GRACE_MIN_MS) // 10,
            command=self._on_grace_drag)
        self.grace_slider.bind(
            "<Button-1>",
            lambda _e: setattr(self, "_grace_dragging", True))
        self.grace_slider.bind("<ButtonRelease-1>", self._on_grace_release)
        self.grace_slider.pack(side="left", padx=(0, 10))
        self.grace_var = tk.StringVar()
        self._grace_trace_id = self.grace_var.trace(
            "w", self._on_grace_entry_changed)
        self.grace_entry = customtkinter.CTkEntry(
            master=grow, textvariable=self.grace_var, width=64)
        self.grace_entry.pack(side="left")
        gcur = int(self._freeze_grace_map().get(
            self.gesture, GRACE_DEFAULT_MS))
        gcur = max(GRACE_MIN_MS, min(GRACE_MAX_MS, gcur))
        self.grace_slider.set(gcur)
        self.grace_var.trace_vdelete("w", self._grace_trace_id)
        self.grace_var.set(str(gcur))
        self._grace_trace_id = self.grace_var.trace(
            "w", self._on_grace_entry_changed)

        self._set_freeze_gated()

    def _set_freeze_gated(self):
        """Enable the blink-ignore + grace sub-controls only while the
        'Freeze pointer' checkbox is on (grey them out otherwise)."""
        on = bool(self.freeze_enabled_var.get())
        st = "normal" if on else "disabled"
        for w in (getattr(self, "invol_cb", None),
                  getattr(self, "grace_slider", None),
                  getattr(self, "grace_entry", None)):
            if w is not None:
                try:
                    w.configure(state=st)
                except Exception:
                    pass

    def _freeze_invol_map(self):
        return dict(ConfigManager().config.get(
            "gesture_freeze_ignore_involuntary", {}) or {})

    def _freeze_grace_map(self):
        return dict(ConfigManager().config.get(
            "gesture_freeze_grace_ms", {}) or {})

    def _on_freeze_invol_toggle(self):
        m = self._freeze_invol_map()
        m[self.gesture] = bool(self.freeze_invol_var.get())
        ConfigManager().set_temp_config(
            "gesture_freeze_ignore_involuntary", m)
        ConfigManager().apply_config()

    def _on_grace_drag(self, value):
        self._grace_dragging = True
        v = int(value)
        try:
            self.grace_var.trace_vdelete("w", self._grace_trace_id)
        except Exception:
            pass
        self.grace_var.set(str(v))
        self._grace_trace_id = self.grace_var.trace(
            "w", self._on_grace_entry_changed)

    def _on_grace_release(self, _e):
        self._grace_dragging = False
        m = self._freeze_grace_map()
        m[self.gesture] = int(self.grace_slider.get())
        ConfigManager().set_temp_config("gesture_freeze_grace_ms", m)
        ConfigManager().apply_config()

    def _on_grace_entry_changed(self, *_a):
        text = self.grace_var.get().strip()
        try:
            v = int(text)
        except ValueError:
            self.grace_entry.configure(fg_color="#ee9e9d")
            return
        if v < GRACE_MIN_MS or v > GRACE_MAX_MS:
            self.grace_entry.configure(fg_color="#ee9e9d")
            return
        self.grace_entry.configure(fg_color="white")
        self.grace_slider.set(v)
        if not self._grace_dragging:
            m = self._freeze_grace_map()
            m[self.gesture] = v
            ConfigManager().set_temp_config("gesture_freeze_grace_ms", m)
            ConfigManager().apply_config()

    def _freeze_enabled_map(self):
        return dict(ConfigManager().config.get(
            "gesture_freeze_enabled", {}) or {})

    def _freeze_sens_map(self):
        return dict(ConfigManager().config.get(
            "gesture_freeze_sensitivity", {}) or {})

    def _on_freeze_toggle(self):
        m = self._freeze_enabled_map()
        m[self.gesture] = bool(self.freeze_enabled_var.get())
        ConfigManager().set_temp_config("gesture_freeze_enabled", m)
        ConfigManager().apply_config()
        self._set_freeze_gated()

    def _on_freeze_drag(self, value):
        self._freeze_dragging = True
        v = int(value)
        try:
            self.freeze_var.trace_vdelete("w", self._freeze_trace_id)
        except Exception:
            pass
        self.freeze_var.set(str(v))
        self._freeze_trace_id = self.freeze_var.trace(
            "w", self._on_freeze_entry_changed)

    def _on_freeze_release(self, _event):
        self._freeze_dragging = False
        v = int(self.freeze_slider.get())
        m = self._freeze_sens_map()
        m[self.gesture] = v
        ConfigManager().set_temp_config("gesture_freeze_sensitivity", m)
        ConfigManager().apply_config()

    def _on_freeze_entry_changed(self, *_args):
        text = self.freeze_var.get().strip()
        try:
            v = int(text)
        except ValueError:
            self.freeze_entry.configure(fg_color="#ee9e9d")
            return
        if v < FREEZE_MIN or v > FREEZE_MAX:
            self.freeze_entry.configure(fg_color="#ee9e9d")
            return
        self.freeze_entry.configure(fg_color="white")
        self.freeze_slider.set(v)
        if not self._freeze_dragging:
            m = self._freeze_sens_map()
            m[self.gesture] = v
            ConfigManager().set_temp_config(
                "gesture_freeze_sensitivity", m)
            ConfigManager().apply_config()

    # ---------------------------------------------------------------- #
    # Simultaneous-input block
    # ---------------------------------------------------------------- #

    def _build_block_section(self):
        self.block_enabled_var = tk.BooleanVar(
            value=bool(self._block_enabled_map().get(self.gesture, False)))
        section_with_help(self._col, t("Block simultaneous input"),
                          "Block simultaneous input",
                          pady=(16, 8), variable=self.block_enabled_var,
                          command=self._on_block_toggle)

        # Movement sensitivity
        bsens_lbl = customtkinter.CTkLabel(
            master=self._col, text=t("Movement sensitivity"))
        bsens_lbl.cget("font").configure(size=12)
        bsens_lbl.pack(padx=20, pady=(0, 2), anchor="w")
        brow = customtkinter.CTkFrame(
            master=self._col, fg_color="transparent")
        brow.pack(padx=20, anchor="w", fill="x")
        self._block_dragging = False
        self.block_slider = customtkinter.CTkSlider(
            master=brow, from_=BLOCK_MIN, to=BLOCK_MAX, width=220,
            number_of_steps=BLOCK_MAX - BLOCK_MIN,
            command=self._on_block_drag)
        self.block_slider.bind(
            "<Button-1>",
            lambda _e: setattr(self, "_block_dragging", True))
        self.block_slider.bind("<ButtonRelease-1>",
                               self._on_block_release)
        self.block_slider.pack(side="left", padx=(0, 10))
        self.block_var = tk.StringVar()
        self._block_trace_id = self.block_var.trace(
            "w", self._on_block_entry_changed)
        self.block_entry = customtkinter.CTkEntry(
            master=brow, textvariable=self.block_var, width=64)
        self.block_entry.pack(side="left")
        bcur = int(self._block_sens_map().get(self.gesture, BLOCK_DEFAULT))
        bcur = max(BLOCK_MIN, min(BLOCK_MAX, bcur))
        self.block_slider.set(bcur)
        self.block_var.trace_vdelete("w", self._block_trace_id)
        self.block_var.set(str(bcur))
        self._block_trace_id = self.block_var.trace(
            "w", self._on_block_entry_changed)

        # Priority
        prio_lbl = customtkinter.CTkLabel(
            master=self._col, text=t("Priority (higher wins)"))
        prio_lbl.cget("font").configure(size=12)
        prio_lbl.pack(padx=20, pady=(8, 2), anchor="w")
        prow = customtkinter.CTkFrame(
            master=self._col, fg_color="transparent")
        prow.pack(padx=20, anchor="w", fill="x")
        self._prio_dragging = False
        self.prio_slider = customtkinter.CTkSlider(
            master=prow, from_=PRIORITY_MIN, to=PRIORITY_MAX, width=220,
            number_of_steps=PRIORITY_MAX - PRIORITY_MIN,
            command=self._on_prio_drag)
        self.prio_slider.bind(
            "<Button-1>",
            lambda _e: setattr(self, "_prio_dragging", True))
        self.prio_slider.bind("<ButtonRelease-1>",
                              self._on_prio_release)
        self.prio_slider.pack(side="left", padx=(0, 10))
        self.prio_var = tk.StringVar()
        self._prio_trace_id = self.prio_var.trace(
            "w", self._on_prio_entry_changed)
        self.prio_entry = customtkinter.CTkEntry(
            master=prow, textvariable=self.prio_var, width=64)
        self.prio_entry.pack(side="left")
        pcur = int(self._priority_map().get(
            self.gesture, PRIORITY_DEFAULT))
        pcur = max(PRIORITY_MIN, min(PRIORITY_MAX, pcur))
        self.prio_slider.set(pcur)
        self.prio_var.trace_vdelete("w", self._prio_trace_id)
        self.prio_var.set(str(pcur))
        self._prio_trace_id = self.prio_var.trace(
            "w", self._on_prio_entry_changed)

    def _block_enabled_map(self):
        return dict(ConfigManager().config.get(
            "gesture_block_enabled", {}) or {})

    def _block_sens_map(self):
        return dict(ConfigManager().config.get(
            "gesture_block_sensitivity", {}) or {})

    def _priority_map(self):
        return dict(ConfigManager().config.get(
            "gesture_priority", {}) or {})

    def _on_block_toggle(self):
        m = self._block_enabled_map()
        m[self.gesture] = bool(self.block_enabled_var.get())
        ConfigManager().set_temp_config("gesture_block_enabled", m)
        ConfigManager().apply_config()

    def _on_block_drag(self, value):
        self._block_dragging = True
        v = int(value)
        try:
            self.block_var.trace_vdelete("w", self._block_trace_id)
        except Exception:
            pass
        self.block_var.set(str(v))
        self._block_trace_id = self.block_var.trace(
            "w", self._on_block_entry_changed)

    def _on_block_release(self, _e):
        self._block_dragging = False
        m = self._block_sens_map()
        m[self.gesture] = int(self.block_slider.get())
        ConfigManager().set_temp_config("gesture_block_sensitivity", m)
        ConfigManager().apply_config()

    def _on_block_entry_changed(self, *_a):
        text = self.block_var.get().strip()
        try:
            v = int(text)
        except ValueError:
            self.block_entry.configure(fg_color="#ee9e9d")
            return
        if v < BLOCK_MIN or v > BLOCK_MAX:
            self.block_entry.configure(fg_color="#ee9e9d")
            return
        self.block_entry.configure(fg_color="white")
        self.block_slider.set(v)
        if not self._block_dragging:
            m = self._block_sens_map()
            m[self.gesture] = v
            ConfigManager().set_temp_config(
                "gesture_block_sensitivity", m)
            ConfigManager().apply_config()

    def _on_prio_drag(self, value):
        self._prio_dragging = True
        v = int(value)
        try:
            self.prio_var.trace_vdelete("w", self._prio_trace_id)
        except Exception:
            pass
        self.prio_var.set(str(v))
        self._prio_trace_id = self.prio_var.trace(
            "w", self._on_prio_entry_changed)

    def _on_prio_release(self, _e):
        self._prio_dragging = False
        m = self._priority_map()
        m[self.gesture] = int(self.prio_slider.get())
        ConfigManager().set_temp_config("gesture_priority", m)
        ConfigManager().apply_config()

    def _on_prio_entry_changed(self, *_a):
        text = self.prio_var.get().strip()
        try:
            v = int(text)
        except ValueError:
            self.prio_entry.configure(fg_color="#ee9e9d")
            return
        if v < PRIORITY_MIN or v > PRIORITY_MAX:
            self.prio_entry.configure(fg_color="#ee9e9d")
            return
        self.prio_entry.configure(fg_color="white")
        self.prio_slider.set(v)
        if not self._prio_dragging:
            m = self._priority_map()
            m[self.gesture] = v
            ConfigManager().set_temp_config("gesture_priority", m)
            ConfigManager().apply_config()

    # ---------------------------------------------------------------- #
    # Disable on strong reaction
    # ---------------------------------------------------------------- #

    def _build_strong_off_section(self):
        self.strong_enabled_var = tk.BooleanVar(
            value=bool(self._strong_enabled_map().get(self.gesture, False)))
        section_with_help(self._col, t("Disable on strong reaction"),
                          "Disable on strong reaction",
                          pady=(16, 8), variable=self.strong_enabled_var,
                          command=self._on_strong_toggle)

        self.strong_slider, self.strong_entry, self.strong_var =             self._int_slider_row(
                t("Sensitivity"), STRONG_MIN, STRONG_MAX,
                self._strong_value("gesture_strong_off_sens",
                                   STRONG_DEFAULT, STRONG_MIN, STRONG_MAX),
                "gesture_strong_off_sens", top_pad=0)

        self.secs_slider, self.secs_entry, self.secs_var =             self._int_slider_row(
                t("Disabled time (s)"), STRONG_SECS_MIN, STRONG_SECS_MAX,
                self._strong_value("gesture_strong_off_secs",
                                   STRONG_SECS_DEFAULT, STRONG_SECS_MIN,
                                   STRONG_SECS_MAX),
                "gesture_strong_off_secs", top_pad=8)

    def _strong_enabled_map(self):
        return dict(ConfigManager().config.get(
            "gesture_strong_off_enabled", {}) or {})

    def _strong_value(self, key, default, lo, hi):
        m = ConfigManager().config.get(key, {}) or {}
        try:
            v = int(m.get(self.gesture, default))
        except (TypeError, ValueError):
            v = default
        return max(lo, min(hi, v))

    def _save_strong(self, key, value):
        m = dict(ConfigManager().config.get(key, {}) or {})
        m[self.gesture] = value
        ConfigManager().set_temp_config(key, m)
        ConfigManager().apply_config()

    def _on_strong_toggle(self):
        self._save_strong("gesture_strong_off_enabled",
                          bool(self.strong_enabled_var.get()))

    def _int_slider_row(self, label, lo, hi, current, key, top_pad):
        """Label + 1-step integer slider + entry, saved to `key` (per
        gesture) on release / on a valid entry. Returns (slider, entry,
        var)."""
        lbl = customtkinter.CTkLabel(master=self._col, text=label)
        lbl.cget("font").configure(size=12)
        lbl.pack(padx=20, pady=(top_pad, 2), anchor="w")
        row = customtkinter.CTkFrame(master=self._col, fg_color="transparent")
        row.pack(padx=20, anchor="w", fill="x")
        state = {"dragging": False, "trace": None}
        var = tk.StringVar()
        slider = customtkinter.CTkSlider(
            master=row, from_=lo, to=hi, width=220, number_of_steps=hi - lo)
        entry = customtkinter.CTkEntry(master=row, textvariable=var, width=64)

        def set_text(v):
            if state["trace"] is not None:
                try:
                    var.trace_vdelete("w", state["trace"])
                except Exception:
                    pass
            var.set(str(v))
            state["trace"] = var.trace("w", on_entry)

        def on_drag(value):
            state["dragging"] = True
            v = int(round(value))
            set_text(v)

        def on_release(_e):
            state["dragging"] = False
            self._save_strong(key, int(round(slider.get())))

        def on_entry(*_a):
            try:
                v = int(var.get().strip())
            except ValueError:
                entry.configure(fg_color="#ee9e9d")
                return
            if v < lo or v > hi:
                entry.configure(fg_color="#ee9e9d")
                return
            entry.configure(fg_color="white")
            slider.set(v)
            if not state["dragging"]:
                self._save_strong(key, v)

        slider.configure(command=on_drag)
        slider.bind("<Button-1>",
                    lambda _e: state.__setitem__("dragging", True))
        slider.bind("<ButtonRelease-1>", on_release)
        slider.pack(side="left", padx=(0, 10))
        entry.pack(side="left")
        slider.set(current)
        set_text(current)
        return slider, entry, var

    # ---------------------------------------------------------------- #

    # ---------------------------------------------------------------- #
    # Threshold-slider upper limit
    # ---------------------------------------------------------------- #

    def _limit_map(self):
        return dict(ConfigManager().config.get("gesture_slider_max", {})
                    or {})

    def _limit_units(self):
        """Effective ceiling in slider units for the current gesture."""
        v = self._limit_map().get(self.gesture, LIMIT_DEFAULT)
        try:
            v = int(v)
        except Exception:
            v = LIMIT_DEFAULT
        return max(LIMIT_MIN, min(LIMIT_MAX, v))

    def _build_limit_section(self):
        section_with_help(self._col, t("Slider upper limit"), "Slider upper limit")

        row = customtkinter.CTkFrame(
            master=self._col, fg_color="transparent")
        row.pack(padx=20, anchor="w", fill="x")

        self._limit_dragging = False
        self.limit_slider = customtkinter.CTkSlider(
            master=row, from_=LIMIT_MIN, to=LIMIT_MAX, width=220,
            number_of_steps=LIMIT_MAX // 10,
            command=self._on_limit_drag)
        self.limit_slider.bind(
            "<Button-1>",
            lambda _e: setattr(self, "_limit_dragging", True))
        self.limit_slider.bind("<ButtonRelease-1>", self._on_limit_release)
        self.limit_slider.pack(side="left", padx=(0, 10))

        self.limit_var = tk.StringVar()
        self._limit_trace_id = self.limit_var.trace(
            "w", self._on_limit_entry_changed)
        self.limit_entry = customtkinter.CTkEntry(
            master=row, textvariable=self.limit_var, width=64)
        self.limit_entry.pack(side="left")

        cur = self._limit_units()
        self.limit_slider.set(cur)
        self.limit_var.trace_vdelete("w", self._limit_trace_id)
        self.limit_var.set(str(cur))
        self._limit_trace_id = self.limit_var.trace(
            "w", self._on_limit_entry_changed)

    def _save_limit(self, units):
        units = max(LIMIT_MIN, min(LIMIT_MAX, int(units)))
        limits = self._limit_map()
        limits[self.gesture] = units
        ConfigManager().set_temp_config("gesture_slider_max", limits)
        ConfigManager().apply_config()
        # Push the new ceiling to the live binding-page slider/meter.
        try:
            self.master.apply_gesture_ceiling(self.gesture)
        except Exception:
            pass

    def _on_limit_drag(self, value):
        self._limit_dragging = True
        pos = int(value)
        try:
            self.limit_var.trace_vdelete("w", self._limit_trace_id)
        except Exception:
            pass
        self.limit_var.set(str(pos))
        self._limit_trace_id = self.limit_var.trace(
            "w", self._on_limit_entry_changed)

    def _on_limit_release(self, _event):
        self._limit_dragging = False
        self._save_limit(int(self.limit_slider.get()))

    def _on_limit_entry_changed(self, *_args):
        text = self.limit_var.get().strip()
        if not text.isdigit():
            self.limit_entry.configure(fg_color="#ee9e9d")
            return
        v = int(text)
        if not (LIMIT_MIN <= v <= LIMIT_MAX):
            self.limit_entry.configure(fg_color="#ee9e9d")
            return
        self.limit_entry.configure(fg_color="white")
        self.limit_slider.set(v)
        if not self._limit_dragging:
            self._save_limit(v)

    # ---------------------------------------------------------------- #
    # Threshold-slider lower limit
    # ---------------------------------------------------------------- #

    def _floor_map(self):
        return dict(ConfigManager().config.get("gesture_slider_min", {})
                    or {})

    def _floor_units(self):
        """Effective floor in slider units (0 = none). Ignored when it
        leaves no room under the upper limit, as on the binding page."""
        v = self._floor_map().get(self.gesture, FLOOR_DEFAULT)
        try:
            v = int(v)
        except Exception:
            v = FLOOR_DEFAULT
        v = max(LIMIT_MIN, min(LIMIT_MAX, v))
        return v if v <= self._limit_units() - FLOOR_MIN_SPAN else 0

    def _build_floor_section(self):
        section_with_help(self._col, t("Slider lower limit"),
                          "Slider lower limit")
        row = customtkinter.CTkFrame(
            master=self._col, fg_color="transparent")
        row.pack(padx=20, anchor="w", fill="x")
        self._floor_dragging = False
        self.floor_slider = customtkinter.CTkSlider(
            master=row, from_=LIMIT_MIN, to=LIMIT_MAX, width=220,
            number_of_steps=LIMIT_MAX // 10,
            command=self._on_floor_drag)
        self.floor_slider.bind(
            "<Button-1>",
            lambda _e: setattr(self, "_floor_dragging", True))
        self.floor_slider.bind("<ButtonRelease-1>", self._on_floor_release)
        self.floor_slider.pack(side="left", padx=(0, 10))
        self.floor_var = tk.StringVar()
        self.floor_entry = customtkinter.CTkEntry(
            master=row, textvariable=self.floor_var, width=64)
        self.floor_entry.pack(side="left")
        cur = self._floor_units()
        self.floor_slider.set(cur)
        self.floor_var.set(str(cur))
        self._floor_trace_id = self.floor_var.trace(
            "w", self._on_floor_entry_changed)

    def _floor_ok(self, v):
        return LIMIT_MIN <= v <= self._limit_units() - FLOOR_MIN_SPAN

    def _save_floor(self, units):
        if not self._floor_ok(int(units)):
            self.floor_entry.configure(fg_color="#ee9e9d")
            return
        self.floor_entry.configure(fg_color="white")
        floors = self._floor_map()
        floors[self.gesture] = int(units)
        ConfigManager().set_temp_config("gesture_slider_min", floors)
        ConfigManager().apply_config()
        # Same live push as the upper limit: the binding page re-reads
        # both ends of the slider.
        try:
            self.master.apply_gesture_ceiling(self.gesture)
        except Exception:
            pass

    def _on_floor_drag(self, value):
        self._floor_dragging = True
        try:
            self.floor_var.trace_vdelete("w", self._floor_trace_id)
        except Exception:
            pass
        self.floor_var.set(str(int(value)))
        self._floor_trace_id = self.floor_var.trace(
            "w", self._on_floor_entry_changed)

    def _on_floor_release(self, _event):
        self._floor_dragging = False
        self._save_floor(int(self.floor_slider.get()))

    def _on_floor_entry_changed(self, *_args):
        text = self.floor_var.get().strip()
        if not text.isdigit() or not self._floor_ok(int(text)):
            self.floor_entry.configure(fg_color="#ee9e9d")
            return
        v = int(text)
        self.floor_entry.configure(fg_color="white")
        self.floor_slider.set(v)
        if not self._floor_dragging:
            self._save_floor(v)

    def _meter_tick(self):
        """Refresh the live meter from the current blendshape values,
        applying the same shaping (response curve + gain) that the
        Keybinder uses for the threshold comparison."""
        if self.top is None or self.meter_bar is None:
            return
        try:
            bs = FaceMesh().get_blendshapes()
            if bs is not None and self.gesture in shape_list.blendshape_indices:
                idx = shape_list.blendshape_indices[self.gesture]
                if idx < len(bs):
                    raw = float(bs[idx])
                    # Curve first (clamped to [0, 1]), then gains.
                    from src.controllers.keybinder import RESPONSE_CURVES
                    curve_name = self._curve_map().get(
                        self.gesture, "linear")
                    curve_fn = RESPONSE_CURVES.get(
                        curve_name, RESPONSE_CURVES["linear"])
                    shaped = curve_fn(max(0.0, min(1.0, raw)))
                    global_gain = float(
                        ConfigManager().config.get("gesture_gain", 3.0))
                    per_gain = float(self._gesture_gains().get(
                        self.gesture, 1.0))
                    gained = max(0.0, shaped * global_gain * per_gain)
                    # Bar full = the gesture's slider ceiling, so it tracks
                    # the rescaled threshold slider / sub-monitor meter.
                    # ...and empty = its floor.
                    ceil_thres = max(1, self._limit_units()) / LIMIT_FACTOR
                    floor_thres = self._floor_units() / LIMIT_FACTOR
                    span = max(ceil_thres - floor_thres, 1e-6)
                    self.meter_bar.set(min(1.0, max(
                        0.0, (gained - floor_thres) / span)))
                    self.meter_label.configure(
                        text=f"raw {raw:.3f}  →  {gained:.3f}")
        except Exception as e:
            logger.debug("meter_tick: %s", e)
        self._meter_after_id = self.top.after(100, self._meter_tick)

    def _stop_meter(self):
        if self._meter_after_id is not None and self.top is not None:
            try:
                self.top.after_cancel(self._meter_after_id)
            except Exception:
                pass
        self._meter_after_id = None

    def close(self):
        self._stop_meter()
        self.meter_bar = None
        self.meter_label = None
        h = getattr(self, "_host", None)
        if h is not None and self.top is not None:
            h.pop(self.top)
        self._window = None
        self.top = None
        self.gesture = None
