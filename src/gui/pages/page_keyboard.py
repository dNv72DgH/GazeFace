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

import logging
import tkinter as tk
import uuid
from functools import partial

import customtkinter
from PIL import Image

import src.shape_list as shape_list
from src.config_manager import ConfigManager
from src.detectors import FaceMesh
from src.gui.balloon import Balloon
from src.gui.dropdown import Dropdown
from src.gui.frames.safe_disposable_frame import SafeDisposableFrame, SafeDisposableScrollableFrame
from src.i18n import t

logger = logging.getLogger("PageKeyboard")

# Threshold slider expressed as int / SLIDER_FACTOR. Range is 0.001
# (slider=1) through 5.0 (slider=5000). Numeric entry beside each
# slider accepts down to ENTRY_MIN_THRES for sub-slider precision.
SLIDER_FACTOR = 1000
SLIDER_MIN = 1
SLIDER_MAX = 5000
# Threshold value at SLIDER_MAX. Used to scale the volume bar so its
# full extent matches the slider's right edge.
SLIDER_MAX_THRES = SLIDER_MAX / SLIDER_FACTOR  # 5.0
# Numeric entry uses the slider's own 1..5000 integer scale (entry
# value equals slider position).
ENTRY_SCALE = float(SLIDER_FACTOR)
ENTRY_MIN = SLIDER_MIN
ENTRY_MAX = SLIDER_MAX

DEFAULT_TRIGGER_TYPE = "hold"
LIGHT_RED = "#F95245"
RED = "#E94235"
GREEN = "#D9622B"      # over threshold (accent)
YELLOW = "#EFC1A8"     # under threshold (mid accent)
LIGHT_BLUE = "#FBFBFF"
BLUE = "#D9622B"
PAD_X = 40
DIV_WIDTH = 240
HELP_ICON_SIZE = (18, 18)
A_BUTTON_SIZE = (96, 48)
BIN_ICON_SIZE = (24, 24)

BALLOON_TXT = "Set how prominent your gesture has\nto be in order to trigger the action"

# Modifier keys that can be combined with the captured key. The stored
# binding is "mod+mod+key" (e.g. "ctrl+shift+t"); Keybinder presses the
# parts left-to-right and releases them right-to-left.
MODIFIERS = [("ctrl", "Ctrl"), ("shift", "Shift"),
             ("alt", "Alt"), ("win", "Win")]
MODIFIER_KEYS = [m for m, _ in MODIFIERS]


def split_binding(key_action):
    """"ctrl+shift+t" -> (["ctrl", "shift"], "t")."""
    if not key_action or key_action == "None":
        return [], "None"
    parts = [p for p in str(key_action).split("+") if p]
    if not parts:
        return [], "None"
    mods = [p for p in parts[:-1] if p in MODIFIER_KEYS]
    return mods, parts[-1]


def join_binding(mods, base):
    """(["ctrl"], "t") -> "ctrl+t"; no base -> "None"."""
    if not base or base == "None":
        return "None"
    ordered = [m for m in MODIFIER_KEYS if m in mods]
    return "+".join(ordered + [base])


class FrameSelectKeyboard(SafeDisposableScrollableFrame):

    def __init__(
        self,
        master,
        **kwargs,
    ):
        super().__init__(master, **kwargs)
        self.is_active = False

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        # Float UIs
        self.shared_info_balloon = Balloon(
            self, image_path="assets/images/balloon.png")
        self.shared_dropdown = Dropdown(
            self,
            dropdown_items=shape_list.available_gestures,
            width=DIV_WIDTH,
            callback=self.dropdown_callback)

        self.help_icon = customtkinter.CTkImage(
            Image.open("assets/images/help.png").resize(HELP_ICON_SIZE),
            size=HELP_ICON_SIZE)

        self.a_button_image = customtkinter.CTkImage(
            Image.open("assets/images/a_button.png").resize(A_BUTTON_SIZE),
            size=A_BUTTON_SIZE)

        self.blank_a_button_image = customtkinter.CTkImage(Image.open(
            "assets/images/blank_a_button.png").resize(A_BUTTON_SIZE),
                                                           size=A_BUTTON_SIZE)

        self.a_button_active_image = customtkinter.CTkImage(Image.open(
            "assets/images/a_button_active.png").resize(A_BUTTON_SIZE),
                                                            size=A_BUTTON_SIZE)

        self.bin_image = customtkinter.CTkImage(
            Image.open("assets/images/bin.png").resize(BIN_ICON_SIZE),
            size=BIN_ICON_SIZE)

        self.wait_for_key_bind_id = None
        self.next_empty_row = 0
        self.waiting_div = None
        self.waiting_button = None
        self.slider_dragging = False

        # Divs
        self.divs = {}
        self.load_initial_keybinds()

    def load_initial_keybinds(self):
        """Load default from config and set the UI
        """
        for gesture_name, bind_info in ConfigManager().keyboard_bindings.items(
        ):
            div_name = f"div_{self.next_empty_row}"

            div = self.create_div(self.next_empty_row, div_name, gesture_name,
                                  bind_info)

            # Show elements related to gesture
            div["selected_gesture"] = gesture_name
            div["entry_field"].configure(image=self.blank_a_button_image)
            div["combobox"].set(t(gesture_name))
            div["slider"].set(
                max(SLIDER_MIN,
                    min(SLIDER_MAX, int(bind_info[2] * SLIDER_FACTOR))))
            self._set_thres_entry(div, float(bind_info[2]))
            div["combobox"].grid()
            div["tips_label"].grid()
            div["subtle_label"].grid()
            div["slider"].grid()
            div["volume_bar"].grid()
            div["thres_entry"].grid()
            self.shared_dropdown.disable_item(gesture_name)
            self.divs[div_name] = div
            self.next_empty_row += 1

        self.shared_dropdown.refresh_items()
        self.refresh_scrollbar()

    def add_blank_div(self):
        new_uuid = uuid.uuid1()
        div_name = f"div_{new_uuid}"
        logger.info(f"Add {div_name}")
        # Seed default threshold at 1.0 (= entry 1000), matching the
        # mouse-binding page's SLIDER_DEFAULT. Combined with a default
        # gesture_gain of 3.0 this gives a comfortable starting point
        # for most users.
        div = self.create_div(row=self.next_empty_row,
                              div_name=div_name,
                              gesture_name="None",
                              bind_info=["keyboard", "None", 1.0, "hold"])

        self.divs[div_name] = div
        self.next_empty_row += 1
        self.refresh_scrollbar()

    def remove_keybind(self, selected_key_action, selected_gesture):
        logger.info(f"Remove keyboard binding {selected_key_action}")
        ConfigManager().remove_temp_keyboard_binding(
            device="keyboard",
            key_action=selected_key_action,
            gesture=selected_gesture)
        ConfigManager().apply_keyboard_bindings()
        self.shared_dropdown.hide_dropdown()
        self.shared_dropdown.enable_item(selected_gesture)

    def bin_button_callback(self, div_name, event):
        div = self.divs[div_name]
        self.remove_div(div_name)
        self.remove_keybind(div["selected_key_action"], div["selected_gesture"])

        self.divs.pop(div_name)
        self.refresh_scrollbar()

    def remove_div(self, div_name):
        logger.info(f"Remove {div_name}")
        div = self.divs[div_name]

        for widget in div.values():
            if isinstance(
                    widget, customtkinter.windows.widgets.core_widget_classes.
                    CTkBaseClass):
                widget.grid_forget()
                widget.destroy()

    def create_div(self, row: int, div_name: str, gesture_name: str,
                   bind_info: list):
        _, key_action, thres, _ = bind_info

        # Bin button
        remove_button = customtkinter.CTkButton(master=self,
                                                text="",
                                                hover=False,
                                                image=self.bin_image,
                                                fg_color="white",
                                                anchor="e",
                                                cursor="hand2",
                                                width=25)

        remove_button.cget("font").configure(size=18)
        remove_button.bind("<ButtonRelease-1>",
                           partial(self.bin_button_callback, div_name))

        remove_button.grid(row=row,
                           column=0,
                           padx=(302, 0),
                           pady=(18, 10),
                           sticky="nw")

        # Key entry — shows the BASE key; the modifiers are the
        # checkboxes beside it.
        mods, base_key = split_binding(key_action)
        field_txt = "" if base_key == "None" else base_key
        entry_field = customtkinter.CTkLabel(master=self,
                                             text=field_txt,
                                             image=self.a_button_image,
                                             width=A_BUTTON_SIZE[0],
                                             height=A_BUTTON_SIZE[1],
                                             cursor="hand2")
        entry_field.cget("font").configure(size=17)

        entry_field.bind(
            "<ButtonRelease-1>",
            partial(self.button_click_callback, div_name, entry_field))

        entry_field.grid(row=row,
                         column=0,
                         padx=PAD_X,
                         pady=(10, 10),
                         sticky="nw")

        # Modifier checkboxes (2x2) beside the key button, so any
        # combination (Ctrl+Shift+T, Alt+F4, Win+D …) can be bound.
        mod_frame = customtkinter.CTkFrame(master=self,
                                           fg_color="transparent")
        mod_frame.grid(row=row, column=0, padx=(PAD_X + 104, 0),
                       pady=(6, 10), sticky="nw")
        mod_vars = {}
        for i, (mod_key, mod_label) in enumerate(MODIFIERS):
            var = tk.BooleanVar(value=mod_key in mods)
            cb = customtkinter.CTkCheckBox(
                master=mod_frame, text=mod_label, variable=var,
                onvalue=True, offvalue=False,
                checkbox_width=17, checkbox_height=17, width=70,
                command=partial(self._mod_changed, div_name))
            cb.cget("font").configure(size=11)
            cb.grid(row=i // 2, column=i % 2, padx=(0, 6), pady=2,
                    sticky="w")
            mod_vars[mod_key] = var

        # Combobox — display-only (permanently disabled; the shared
        # dropdown drives it), so keep the NORMAL text colour instead of
        # the theme's faint disabled grey.
        drop = customtkinter.CTkOptionMenu(master=self,
                                           values=[t(gesture_name)],
                                           width=DIV_WIDTH,
                                           dynamic_resizing=False,
                                           state="disabled",
                                           text_color_disabled="#201D1A")
        drop.grid(row=row, column=0, padx=PAD_X, pady=(64, 10), sticky="nw")
        self.shared_dropdown.register_widget(drop, div_name)

        # Label ?
        tips_label = customtkinter.CTkLabel(master=self,
                                            image=self.help_icon,
                                            compound='right',
                                            text=t("Gesture size"),
                                            text_color="#5E5E5E",
                                            justify='left')
        tips_label.cget("font").configure(size=12)
        tips_label.grid(row=row,
                        column=0,
                        padx=PAD_X,
                        pady=(92, 10),
                        sticky="nw")
        self.shared_info_balloon.register_widget(tips_label, BALLOON_TXT)

        # Volume bar
        volume_bar = customtkinter.CTkProgressBar(
            master=self,
            width=DIV_WIDTH,
        )

        volume_bar.grid(row=row,
                        column=0,
                        padx=PAD_X,
                        pady=(122, 10),
                        sticky="nw")

        # Slider — extended to SLIDER_MIN for ultra-low thresholds and
        # up to SLIDER_MAX (5.0 with default factor) for high-gain
        # workflows.
        slider = customtkinter.CTkSlider(master=self,
                                         from_=SLIDER_MIN,
                                         to=SLIDER_MAX,
                                         width=DIV_WIDTH + 10,
                                         number_of_steps=SLIDER_MAX,
                                         command=partial(
                                             self.slider_drag_callback,
                                             div_name))
        slider.set(max(SLIDER_MIN,
                       min(SLIDER_MAX, int(thres * SLIDER_FACTOR))))
        slider.bind("<Button-1>",
                    partial(self.slider_mouse_down_callback, div_name))
        slider.bind("<ButtonRelease-1>",
                    partial(self.slider_mouse_up_callback, div_name))

        slider.grid(row=row,
                    column=0,
                    padx=PAD_X - 5,
                    pady=(142, 10),
                    sticky="nw")

        # The "Subtle / Exaggerated" scale caption under the slider was
        # removed (per request). The widget is kept as an EMPTY 1px
        # placeholder so the show/hide calls elsewhere keep working, and
        # the row tightens by the height the caption used to take.
        subtle_label = customtkinter.CTkLabel(master=self, text="",
                                              height=1)
        subtle_label.grid(row=row, column=0, padx=PAD_X, pady=(0, 0),
                          sticky="nw")

        # Numeric threshold entry: integer-only on the slider's own
        # 1..5000 scale (entry value == slider position).
        thres_var = tk.StringVar()
        _initial_entry = int(round(float(thres) * ENTRY_SCALE))
        _initial_entry = max(ENTRY_MIN, min(ENTRY_MAX, _initial_entry))
        thres_var.set(str(_initial_entry))
        thres_entry_trace_fn = partial(self._thres_entry_changed,
                                       div_name)
        thres_entry_trace_id = thres_var.trace("w",
                                               thres_entry_trace_fn)
        thres_entry = customtkinter.CTkEntry(
            master=self,
            textvariable=thres_var,
            width=70,
            justify=tk.RIGHT)
        thres_entry.grid(row=row, column=0,
                         padx=(PAD_X + DIV_WIDTH + 8, 5),
                         pady=(138, 10),
                         sticky="nw")

        # Hide element related to gesture
        drop.grid_remove()
        tips_label.grid_remove()
        slider.grid_remove()
        volume_bar.grid_remove()
        subtle_label.grid_remove()
        thres_entry.grid_remove()

        return {
            "entry_field": entry_field,
            "mod_frame": mod_frame,
            "mod_vars": mod_vars,
            "base_key": base_key,
            "combobox": drop,
            "tips_label": tips_label,
            "slider": slider,
            "volume_bar": volume_bar,
            "subtle_label": subtle_label,
            "selected_gesture": gesture_name,
            "selected_key_action": key_action,
            "remove_button": remove_button,
            "thres_entry": thres_entry,
            "thres_var": thres_var,
            "thres_entry_trace_id": thres_entry_trace_id,
            "thres_entry_trace_fn": thres_entry_trace_fn,
        }

    def _selected_mods(self, div):
        return [k for k, v in div.get("mod_vars", {}).items() if v.get()]

    def _rebuild_binding(self, div):
        """Recompute selected_key_action from the base key + modifiers."""
        div["selected_key_action"] = join_binding(
            self._selected_mods(div), div.get("base_key", "None"))

    def _mod_changed(self, div_name):
        div = self.divs.get(div_name)
        if div is None:
            return
        # Drop the OLD combination before writing the new one — the
        # binding store is keyed by gesture, but a stale keyboard entry
        # for the previous combo would otherwise keep firing.
        old = div.get("selected_key_action")
        self._rebuild_binding(div)
        if old and old != div["selected_key_action"]:
            try:
                ConfigManager().remove_temp_keyboard_binding(
                    device="keyboard", key_action=old,
                    gesture=div["selected_gesture"])
            except Exception:
                pass
        self.set_new_keyboard_binding(div)

    def _set_thres_entry(self, div, thres):
        """Update threshold entry text without triggering its write
        callback (avoids slider<->entry ping-pong).

        Entry uses the slider's own integer 1..5000 scale.
        """
        try:
            div["thres_var"].trace_vdelete(
                "w", div["thres_entry_trace_id"])
        except Exception:
            pass
        entry_val = int(round(thres * ENTRY_SCALE))
        entry_val = max(ENTRY_MIN, min(ENTRY_MAX, entry_val))
        div["thres_var"].set(str(entry_val))
        div["thres_entry_trace_id"] = div["thres_var"].trace(
            "w", div["thres_entry_trace_fn"])

    def _thres_entry_changed(self, div_name: str, *_args):
        div = self.divs.get(div_name)
        if div is None:
            return
        text = div["thres_var"].get().strip()
        # Strict integer parsing — no decimals.
        try:
            entry_value = int(text)
        except ValueError:
            div["thres_entry"].configure(fg_color="#ee9e9d")
            return
        if entry_value < ENTRY_MIN or entry_value > ENTRY_MAX:
            div["thres_entry"].configure(fg_color="#ee9e9d")
            return
        div["thres_entry"].configure(fg_color="white")
        threshold = entry_value / ENTRY_SCALE
        slider_pos = max(SLIDER_MIN,
                         min(SLIDER_MAX,
                             int(round(threshold * SLIDER_FACTOR))))
        div["slider"].set(slider_pos)
        if not self.slider_dragging:
            self.set_new_keyboard_binding(div)

    def set_new_keyboard_binding(self, div):

        # Remove keybind if set to invalid key
        if (div["selected_gesture"] == "None") or (div["selected_key_action"]
                                                   == "None"):
            logger.info(f"Remove keyboard binding {div['selected_key_action']}")
            ConfigManager().remove_temp_keyboard_binding(
                device="keyboard", gesture=div["selected_gesture"])

            ConfigManager().apply_keyboard_bindings()
            return

        # Set the keybinding. Prefer the numeric entry value if it's a
        # valid float (lets users type < SLIDER_MIN / SLIDER_FACTOR for
        # ultra-low thresholds).
        try:
            thres_value = float(div["thres_var"].get())
        except (ValueError, KeyError):
            thres_value = div["slider"].get() / SLIDER_FACTOR
        ConfigManager().set_temp_keyboard_binding(
            device="keyboard",
            key_action=div["selected_key_action"],
            gesture=div["selected_gesture"],
            threshold=thres_value,
            trigger_type=DEFAULT_TRIGGER_TYPE)
        ConfigManager().apply_keyboard_bindings()

    def wait_for_key(self, div_name: str, entry_button, keydown: tk.Event):
        """Wait for user to press any key then set the config
        """
        if div_name not in self.divs:
            return
        if self.waiting_div is None:
            return

        div = self.divs[div_name]

        keydown_txt = keydown.keysym.lower() if isinstance(
            keydown, tk.Event) else keydown
        logger.info(f"Key press: <{div_name}> {keydown_txt}")

        # Occupancy is per FULL combination, not per base key — Ctrl+T and
        # plain T are different bindings and may coexist.
        new_base = shape_list.keyboard_keys.get(keydown_txt)
        new_combo = join_binding(self._selected_mods(div), new_base) \
            if new_base else None
        occupied = [d["selected_key_action"] for k, d in self.divs.items()
                    if k != div_name]

        # Not valid key
        if (new_combo in occupied) or (
                keydown_txt not in shape_list.available_keyboard_keys):
            logger.info(
                f"Key action <{keydown_txt}> not found in available list")
            entry_button.configure(image=self.a_button_image)
            div["base_key"] = "None"
            div["selected_key_action"] = "None"
            div["slider"].grid_remove()
            div["combobox"].grid_remove()
            div["volume_bar"].grid_remove()
            div["tips_label"].grid_remove()
            div["subtle_label"].grid_remove()
            div["thres_entry"].grid_remove()
            self.set_new_keyboard_binding(div)

        # Valid key
        else:
            logger.info(f"Found <{keydown_txt}> in available list")
            # Convert key symbol from tkinter to pydirectinput
            pydirectinput_key = shape_list.keyboard_keys[keydown_txt]

            entry_button.configure(text=pydirectinput_key)
            entry_button.configure(image=self.blank_a_button_image)

            div["combobox"].grid()
            div["base_key"] = pydirectinput_key
            self._rebuild_binding(div)
            self.set_new_keyboard_binding(div)
            if div["selected_gesture"] != "None":
                div["slider"].grid()
                div["combobox"].grid()
                div["volume_bar"].grid()
                div["tips_label"].grid()
                div["subtle_label"].grid()
                div["thres_entry"].grid()

        if self.wait_for_key_bind_id is not None:
            self.waiting_button.unbind("<KeyPress>", self.wait_for_key_bind_id)
        self.refresh_scrollbar()
        self.waiting_div = None
        self.wait_for_key_bind_id = None

    def button_click_callback(self, div_name, entry_button, event):
        """Start wait_for_key after clicked the button      
        """
        # Cancel old waiting funciontion
        if self.waiting_div is not None:
            self.wait_for_key(self.waiting_div, self.waiting_button, "cancel")

        # Start waiting for key press
        self.wait_for_key_bind_id = entry_button.bind(
            "<KeyPress>", partial(self.wait_for_key, div_name, entry_button))
        entry_button.focus_set()

        entry_button.configure(text="")
        entry_button.configure(image=self.a_button_active_image)
        self.waiting_div = div_name
        self.waiting_button = entry_button

    def dropdown_callback(self, div_name: str, target_gesture: str):

        div = self.divs[div_name]

        # Release old item
        if div["selected_gesture"] != target_gesture:
            self.shared_dropdown.enable_item(div["selected_gesture"])
        div["selected_gesture"] = target_gesture
        div["combobox"].set(t(target_gesture))

        if target_gesture != "None":
            div["slider"].grid()
            div["volume_bar"].grid()
            div["tips_label"].grid()
            div["subtle_label"].grid()
            div["thres_entry"].grid()
        else:
            div["slider"].grid_remove()
            div["volume_bar"].grid_remove()
            div["tips_label"].grid_remove()
            div["subtle_label"].grid_remove()
            div["thres_entry"].grid_remove()

        self.set_new_keyboard_binding(div)
        self.refresh_scrollbar()

    def slider_drag_callback(self, div_name: str, new_value: str):
        """Update value when slider being dragged; live-mirror to the
        numeric threshold entry beside it."""
        self.slider_dragging = True
        if div_name not in self.divs:
            return
        div = self.divs[div_name]
        thres = float(new_value) / SLIDER_FACTOR
        self._set_thres_entry(div, thres)

    def slider_mouse_down_callback(self, div_name: str, event):
        self.slider_dragging = True

    def slider_mouse_up_callback(self, div_name: str, event):
        self.slider_dragging = False
        div = self.divs[div_name]
        # Snap entry to the slider's actual settled value.
        thres = div["slider"].get() / SLIDER_FACTOR
        self._set_thres_entry(div, thres)
        self.set_new_keyboard_binding(div)

    def update_volume_preview(self):

        bs = FaceMesh().get_blendshapes()
        # Mirror Keybinder's full pipeline (curve -> global gain -> per
        # gesture gain) so the preview tracks exactly what the
        # threshold compare sees.
        from src.controllers.keybinder import RESPONSE_CURVES
        global_gain = float(
            ConfigManager().config.get("gesture_gain", 3.0))
        per_gestures = ConfigManager().config.get(
            "gesture_gains", {}) or {}
        curves = ConfigManager().config.get(
            "gesture_curves", {}) or {}

        for div in self.divs.values():

            if div["selected_gesture"] == "None":
                continue

            bs_idx = shape_list.blendshape_indices[div["selected_gesture"]]
            per_gain = float(per_gestures.get(
                div["selected_gesture"], 1.0))
            raw = float(bs[bs_idx])
            curve_fn = RESPONSE_CURVES.get(
                curves.get(div["selected_gesture"], "linear"),
                RESPONSE_CURVES["linear"])
            shaped = curve_fn(max(0.0, min(1.0, raw)))
            gained = max(0.0, shaped * global_gain * per_gain)
            # Scale to slider's range so "bar full" matches "slider at
            # the right edge". Bar still clips to [0, 1] visually.
            div["volume_bar"].set(min(1.0, gained / SLIDER_MAX_THRES))

            slider_value = div["slider"].get() / SLIDER_FACTOR
            if gained > slider_value:
                div["volume_bar"].configure(progress_color=GREEN)  # green
            else:
                div["volume_bar"].configure(progress_color=YELLOW)  # yellow

    def frame_loop(self):
        if self.is_destroyed:
            return

        if self.is_active:
            self.update_volume_preview()
            self.after(50, self.frame_loop)
        else:
            return

    def inner_refresh_profile(self):
        """Refresh the page divs to match the new profile
        """
        # Remove old divs
        self.next_empty_row = 0
        for div_name, div in self.divs.items():
            self.remove_div(div_name)
        self.divs = {}

        # Create new divs form the new profile
        self.load_initial_keybinds()

    def enter(self):
        super().enter()
        self.after(1, self.frame_loop)

    def leave(self):
        super().leave()
        self.wait_for_key(self.waiting_div, self.waiting_button, "cancel")


class PageKeyboard(SafeDisposableFrame):

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)

        self.grid_rowconfigure(3, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.is_active = False
        self.grid_propagate(False)
        self.bind_id_leave = None

        # Top label.
        self.top_label = customtkinter.CTkLabel(master=self,
                                                text=t("Keyboard"))
        self.top_label.cget("font").configure(size=24, weight="bold")
        # +6 px of top padding vs the other pages' (10, 2): every other
        # page is a CTkScrollableFrame, whose canvas is inset by the
        # frame's 6 px corner-radius spacing. This page is a plain frame,
        # so it has to add that inset itself for the titles to line up.
        self.top_label.grid(row=0,
                            column=0,
                            padx=20,
                            pady=(16, 2),
                            sticky="nw",
                            columnspan=1)

        # Description.
        des_txt = t("Select a facial gesture that you would like to bind to a specific keyboard key. Sensitivity allows you to control the extent to which you need to gesture to trigger the keyboard key press")

        # Inner frame
        self.inner_frame = FrameSelectKeyboard(
            self, logger_name="FrameSelectKeyboard")
        self.inner_frame.grid(row=3, column=0, padx=5, pady=5, sticky="nswe")

        # Add binding butotn
        self.add_binding_button = customtkinter.CTkButton(
            master=self,
            text=t("+ Add binding"),
            fg_color="white",
            text_color=BLUE,
            command=self.inner_frame.add_blank_div)
        self.add_binding_button.grid(row=2,
                                     column=0,
                                     padx=5,
                                     pady=5,
                                     sticky="nw")

    def enter(self):
        super().enter()
        self.inner_frame.enter()
        # NOTE: <Leave> auto-hide removed — the dropdown lives in a
        # separate Toplevel, so moving the pointer from the page area
        # to a dropdown item used to trigger <Leave> and close the menu
        # the user was trying to use. Dismissal is now handled by
        # click-outside in the Dropdown class.

    def refresh_profile(self):
        self.inner_frame.inner_refresh_profile()

    def leave(self):
        super().leave()
        self.inner_frame.leave()

        self.inner_frame.shared_dropdown.hide_dropdown()

    def destroy(self):
        super().destroy()
        self.inner_frame.destroy()
