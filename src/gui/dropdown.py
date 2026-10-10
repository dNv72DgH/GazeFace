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
"""Gesture selector — opened by clicking a binding-row combobox.

Was a small floating popup; rewritten as a proper Toplevel window
sized like the main app, with one row per gesture, a live meter
showing the (curve + gain) shaped value, and click-to-select that
closes the popup. The maximize button is enabled so the user can
expand to fullscreen to read tiny variations more easily.
"""

import logging
from functools import partial

import customtkinter
import tkinter as tk
from PIL import Image

import src.shape_list as shape_list
from src.config_manager import ConfigManager
from src.detectors import FaceMesh
from src.gui.frames.safe_disposable_frame import style_scrollbar
from src.gui.popups._util import POPUP_BG
from src.i18n import t

logger = logging.getLogger("Dropdown")

ICON_SIZE = (68, 48)
ROW_HEIGHT = 56
BAR_WIDTH = 200
BAR_SCALE = 5.0
LIGHT_BLUE = "#FBFBFF"
HIGHLIGHT_BG = "#F7E8DF"
DISABLED_TEXT = "gray60"
METER_REFRESH_MS = 100

# 2-column row layout — fits the 24 gestures into ~12 rows so users
# can see most of them at a glance.
COLUMN_COUNT = 2

# Live-meter numeric display scale. Matches the binding-page threshold
# entry (1..5000) so users can read it directly and pick a threshold
# value without converting from float.
VALUE_DISPLAY_SCALE = 1000
VALUE_DISPLAY_MAX = 5000


class Dropdown:
    """Public API matches the old floating-dropdown for caller
    compatibility:

        d = Dropdown(master, items, width, callback)
        d.register_widget(widget, name)
        d.refresh_items()
        d.hide_dropdown()
        d.enable_item(name) / disable_item(name) / enable_all_except(list)
    """

    def __init__(self, master, dropdown_items: dict, width, callback: callable):
        self.master = master
        self.master_toplevel = master.winfo_toplevel()
        self.dropdown_items = dropdown_items
        self.dropdown_keys = list(dropdown_items.keys())
        self.selected_gesture = self.dropdown_keys[0] if self.dropdown_keys else None
        self.master_callback = callback
        self.current_user = None

        # Window-level state. Created lazily on first show.
        self.popup = None
        self._row_widgets = {}
        # Persisted "disabled" set (gestures already bound to another
        # action) so we don't re-enable them on refresh.
        self._disabled = set()
        self._meter_after_id = None
        self._icon_refs = []

    # ---------------------------------------------------------------- #
    # Public API used by the binding pages
    # ---------------------------------------------------------------- #

    def register_widget(self, widget, name):
        """Bind `widget` (the per-row combobox) so clicking it opens
        the selector popup with `name` as the caller identifier."""
        widget.bind("<ButtonPress-1>",
                    partial(self.show_dropdown, widget, name))

    def show_dropdown(self, widget, name, _event=None):
        # Re-use one popup instance — close-then-open if already open
        # for a different row.
        if self.popup is not None and self.popup.winfo_exists():
            self.hide_dropdown()
        self.current_user = name
        self._build_popup()

    def hide_dropdown(self, _event=None):
        self._stop_meter()
        if self.popup is not None:
            try:
                self.popup.destroy()
            except Exception:
                pass
        self.popup = None
        self._row_widgets = {}
        self._icon_refs = []

    def disable_item(self, target_gesture):
        self._disabled.add(target_gesture)
        self._apply_disabled_state()

    def enable_item(self, target_gesture):
        self._disabled.discard(target_gesture)
        self._apply_disabled_state()

    def enable_all_except(self, target_gestures: list):
        # "Disable everything currently bound; enable everything else."
        self._disabled = set(target_gestures)
        self._apply_disabled_state()

    def refresh_items(self):
        """Recompute which items are disabled based on current bindings.
        Called from the binding pages every time the popup is about to
        open or whenever bindings change."""
        self.enable_all_except(
            list(ConfigManager().mouse_bindings.keys()) +
            list(ConfigManager().keyboard_bindings.keys()))

    # ---------------------------------------------------------------- #
    # Popup window construction
    # ---------------------------------------------------------------- #

    def _build_popup(self):
        # Refresh disabled set just before building so the visual
        # matches the latest bindings.
        try:
            self.refresh_items()
        except Exception as e:
            logger.warning("refresh_items failed: %s", e)

        self.popup = tk.Toplevel(self.master_toplevel)
        self.popup.title(t("Select gesture"))
        self.popup.configure(bg=POPUP_BG)
        self.popup.transient(self.master_toplevel)
        self.popup.bind("<Escape>", lambda _e: self.hide_dropdown())
        self.popup.protocol("WM_DELETE_WINDOW", self.hide_dropdown)

        # Open at the main window's size and position so the popup
        # exactly covers the app. resizable=True so the user can
        # maximise.
        self.master_toplevel.update_idletasks()
        mw = self.master_toplevel.winfo_width() or 1024
        mh = self.master_toplevel.winfo_height() or 658
        mx = self.master_toplevel.winfo_rootx()
        my = self.master_toplevel.winfo_rooty()
        self.popup.geometry(f"{mw}x{mh}+{mx}+{my}")
        self.popup.minsize(640, 480)
        self.popup.resizable(True, True)

        # Header
        header = customtkinter.CTkLabel(
            master=self.popup,
            text=t("Select gesture"),
            text_color="black", fg_color=POPUP_BG)
        header.cget("font").configure(size=22, weight="bold")
        header.pack(padx=20, pady=(20, 5), anchor="w")

        hint = customtkinter.CTkLabel(
            master=self.popup,
            text=t("Click a gesture to bind it to this action. The bar shows the gained blendshape value (= what's compared against the threshold). Greyed-out items are already bound to another action."),
            wraplength=1000,
            justify=tk.LEFT,
            text_color="black", fg_color=POPUP_BG)
        hint.cget("font").configure(size=12)
        hint.pack(padx=20, pady=(0, 8), anchor="w", fill="x")

        # Scrollable list of gesture rows.
        scroll = customtkinter.CTkScrollableFrame(
            master=self.popup, fg_color=POPUP_BG)
        style_scrollbar(scroll)
        scroll.pack(padx=20, pady=10, fill="both", expand=True)
        # 2-column grid, both columns balanced via uniform="cols".
        for c in range(COLUMN_COUNT):
            scroll.grid_columnconfigure(c, weight=1, uniform="cols")

        # One row per gesture. We always show ALL gestures (the old
        # enabled_gestures filter has been removed — the user can pick
        # any of MediaPipe's blendshapes).
        for idx, (gesture, image_path) in enumerate(
                self.dropdown_items.items()):
            self._build_row(scroll, idx, gesture, image_path)

        # Footer with a cancel button
        footer = customtkinter.CTkFrame(
            master=self.popup, fg_color=POPUP_BG)
        footer.pack(padx=20, pady=(0, 14), fill="x")
        cancel_btn = customtkinter.CTkButton(
            master=footer, text=t("Cancel"),
            command=self.hide_dropdown, width=120,
            fg_color="white", text_color="#D9622B",
            border_width=1, border_color="#D9622B")
        cancel_btn.pack(side="right")

        # Apply current disabled state to the freshly created buttons.
        self._apply_disabled_state()

        # Kick off the live-meter loop.
        self._meter_after_id = self.popup.after(METER_REFRESH_MS,
                                                self._meter_tick)

    def _build_row(self, parent, idx: int, gesture: str,
                   image_path: str):
        # 2-column wrap: fill left-to-right, top-to-bottom.
        grid_row = idx // COLUMN_COUNT
        grid_col = idx % COLUMN_COUNT
        row = customtkinter.CTkFrame(
            master=parent, fg_color="transparent")
        row.grid(row=grid_row, column=grid_col,
                 padx=4, pady=2, sticky="ew")
        # Layout: icon | name+select-btn | meter | value
        row.grid_columnconfigure(2, weight=1)

        # Icon
        try:
            pil_img = Image.open(image_path).resize(ICON_SIZE)
            icon = customtkinter.CTkImage(pil_img, size=ICON_SIZE)
            self._icon_refs.append(icon)
            icon_label = customtkinter.CTkLabel(
                master=row, image=icon, text="",
                fg_color="transparent")
            icon_label.grid(row=0, column=0, padx=(0, 8), sticky="w")
        except Exception as e:
            logger.warning("Icon load failed for %s: %s", gesture, e)

        # Click-to-select button
        select_btn = customtkinter.CTkButton(
            master=row,
            text=t(gesture),
            anchor="w",
            fg_color=LIGHT_BLUE,
            text_color="black",
            hover_color=HIGHLIGHT_BG,
            border_width=0,
            corner_radius=4,
            height=ROW_HEIGHT,
            width=260,
            command=partial(self._on_select, gesture))
        select_btn.grid(row=0, column=1, padx=(0, 12), sticky="w")

        # Live meter
        bar = customtkinter.CTkProgressBar(
            master=row, width=BAR_WIDTH, height=14)
        bar.set(0.0)
        bar.configure(progress_color="#D9622B")
        bar.grid(row=0, column=2, padx=(0, 8), sticky="ew")

        value_lbl = customtkinter.CTkLabel(
            master=row, text="0",
            width=60, anchor="e",
            text_color="#7A736A", fg_color="transparent")
        value_lbl.cget("font").configure(size=11)
        value_lbl.grid(row=0, column=3, sticky="e", padx=(0, 4))

        self._row_widgets[gesture] = {
            "button": select_btn,
            "bar": bar,
            "value_label": value_lbl,
        }

    # ---------------------------------------------------------------- #
    # Selection / disabled state
    # ---------------------------------------------------------------- #

    def _on_select(self, gesture: str):
        # "None" is always selectable (the unbind sentinel).
        if gesture in self._disabled and gesture != self.dropdown_keys[0]:
            return
        self.selected_gesture = gesture
        # Notify caller before tearing down the popup so any sibling
        # UI redraws happen while widgets still exist.
        cb = self.master_callback
        user = self.current_user
        self.hide_dropdown()
        try:
            cb(user, gesture)
        except Exception as e:
            logger.error("dropdown callback failed: %s", e, exc_info=e)

    def _apply_disabled_state(self):
        if not self._row_widgets:
            return
        first_key = self.dropdown_keys[0] if self.dropdown_keys else None
        for gesture, row in self._row_widgets.items():
            btn = row["button"]
            # The first item ("None") is the unbind sentinel — always
            # active so the user can release a binding.
            if gesture == first_key:
                btn.configure(state="normal", text_color="black")
                continue
            if gesture in self._disabled:
                btn.configure(state="disabled",
                              text_color=DISABLED_TEXT)
            else:
                btn.configure(state="normal", text_color="black")

    # ---------------------------------------------------------------- #
    # Live meter
    # ---------------------------------------------------------------- #

    def _meter_tick(self):
        if self.popup is None:
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
                for gesture, row in self._row_widgets.items():
                    idx = shape_list.blendshape_indices.get(gesture)
                    if idx is None or idx >= len(bs):
                        continue
                    raw = float(bs[idx])
                    curve_fn = RESPONSE_CURVES.get(
                        curves.get(gesture, "linear"),
                        RESPONSE_CURVES["linear"])
                    shaped = curve_fn(max(0.0, min(1.0, raw)))
                    per = float(per_gestures.get(gesture, 1.0))
                    gained = max(0.0, shaped * global_gain * per)
                    row["bar"].set(min(1.0, gained / BAR_SCALE))
                    # Display as integer on the same 0..5000 scale the
                    # binding-page threshold entry uses, so the user
                    # can read the meter number and pick a threshold
                    # without any conversion.
                    display = int(round(gained * VALUE_DISPLAY_SCALE))
                    display = max(0, min(VALUE_DISPLAY_MAX, display))
                    row["value_label"].configure(text=str(display))
        except Exception as e:
            logger.debug("meter_tick: %s", e)
        if self.popup is not None:
            self._meter_after_id = self.popup.after(
                METER_REFRESH_MS, self._meter_tick)

    def _stop_meter(self):
        if self._meter_after_id is not None and self.popup is not None:
            try:
                self.popup.after_cancel(self._meter_after_id)
            except Exception:
                pass
        self._meter_after_id = None
