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

import tkinter as tk
from functools import partial

import customtkinter
from PIL import Image

import src.shape_list as shape_list
from src.config_manager import ConfigManager
from src.controllers.keybinder import RESPONSE_CURVES
from src.detectors import FaceMesh
from src.gui.balloon import Balloon
from src.gui.dropdown import Dropdown
from src.gui.frames.safe_disposable_frame import SafeDisposableFrame, SafeDisposableScrollableFrame
from src.gui.help_icon import HelpIcon
from src.i18n import t

# Actions that carry a "?" help marker on the binding page.
HELP_ACTIONS = ("Gaze Jump", "Gaze move")

# Two-column layout. The first half of the actions goes in column 0 (one
# per row); the rest goes in column 1. For the Mouse page that is:
#     col 0:  left / right / middle / double  (4 click types)
#     col 1:  pause / jump / reset / cycle    (4 control actions)
# The split is computed from the action count so the shorter Extensions
# and Bookmarks lists lay out in two columns too, instead of one long one.
# The page is scrollable, so col 1 can be taller than the viewport.
#
# NOTE: do NOT switch this to `column = idx // MAX_ROWS` — that would
# silently land trailing items in a 3rd column, overflow the page width,
# and visually shuffle the layout so some items appear to be missing.
HELP_ICON_SIZE = (18, 18)
DIV_WIDTH = 240
ROW_HEIGHT = 175
DEFAULT_TRIGGER_TYPE = "single"
GREEN = "#D9622B"      # over threshold (accent)
YELLOW = "#EFC1A8"     # under threshold (mid accent)

# Threshold slider expresses a fractional blendshape value scaled by
# SLIDER_FACTOR. Range is 0.001 (slider=1) through 5.0 (slider=5000).
# 5.0 was chosen as a ~5x of the natural 0-1 range so a user with an
# aggressive sensitivity gain can still set a high threshold.
#
# For thresholds below 0.001, use the numeric entry beside the slider
# directly (it accepts down to ENTRY_MIN_THRES).
SLIDER_FACTOR = 1000
SLIDER_MIN = 1
SLIDER_MAX = 5000
# Threshold value at SLIDER_MAX (= 5.0). The volume bar above the
# slider is scaled by this so "bar full" lines up with "slider at the
# right end", making the bar a true visual analogue of the slider.
SLIDER_MAX_THRES = SLIDER_MAX / SLIDER_FACTOR  # 5.0
# Default slider position for a newly-bound gesture (= slider 1000 =
# threshold 1.0). Picked so a strong expression clears it without
# triggering on every faint twitch.
SLIDER_DEFAULT = 1000

# Numeric entry is integer-only on the slider's own 1..5000 scale —
# entry value equals slider position, so the entry and slider share
# the same units and the same precision.
ENTRY_SCALE = float(SLIDER_FACTOR)
ENTRY_MIN = SLIDER_MIN
ENTRY_MAX = SLIDER_MAX


def gesture_ceiling(gesture):
    """Per-gesture threshold-slider ceiling in slider units (1..5000).

    The threshold slider keeps its fixed width but its right end maps to
    this value, so lowering it spreads the small-threshold region across
    the whole track (finer control) and the volume / sub-monitor meters
    rescale to match. Stored per gesture in config["gesture_slider_max"];
    default is the full SLIDER_MAX."""
    try:
        m = (ConfigManager().config.get("gesture_slider_max", {})
             or {}).get(gesture)
        if m is None:
            return SLIDER_MAX
        return max(SLIDER_MIN, min(SLIDER_MAX, int(m)))
    except Exception:
        return SLIDER_MAX

# The floor must stay at least this far under the ceiling (slider units),
# or the slider would have no travel left.
MIN_SPAN = 10


def gesture_floor(gesture):
    """Per-gesture threshold-slider floor in slider units.

    The counterpart of gesture_ceiling: the slider's LEFT end. Raising it
    helps with the "strong -> weak" response curve, which pushes even a
    faint gesture high up the range - the useful thresholds then all sit
    near the top, and a raised floor spreads them across the track.
    Stored per gesture in config["gesture_slider_min"]; default the full
    range. A floor that leaves no room under the ceiling is ignored.
    """
    try:
        m = (ConfigManager().config.get("gesture_slider_min", {})
             or {}).get(gesture)
        f = SLIDER_MIN if m is None else max(SLIDER_MIN,
                                             min(SLIDER_MAX, int(m)))
    except Exception:
        f = SLIDER_MIN
    if f > gesture_ceiling(gesture) - MIN_SPAN:
        return SLIDER_MIN
    return f


BALLOON_TXT = "Set how prominent your gesture has\nto be in order to trigger the action"


class FrameSelectGesture(SafeDisposableFrame):

    def __init__(
        self,
        master,
        action_keys=None,
        show_bookmark=True,
        **kwargs,
    ):
        super().__init__(master, **kwargs)
        self.is_active = False
        # Show the per-row bookmark star (Mouse / Extensions pages). The
        # Bookmarks page itself passes True too — clicking a star there
        # removes the row from the list.
        self._show_bookmark = show_bookmark

        # Which actions this binding frame renders. Defaults to the mouse
        # actions; the Extensions page passes the gaze-launcher subset so
        # both pages reuse this same binding UI over one shared
        # mouse_bindings store.
        self._action_keys = action_keys or shape_list.MOUSE_ACTION_KEYS

        self.grid_columnconfigure(0, weight=1)

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

        # Lazy-initialised popups (one instance reused).
        self._action_popup = None
        self._gesture_popup = None

        # Divs
        self.divs = self.create_divs(self._action_keys,
                                     shape_list.available_gestures_keys)
        self.load_initial_keybinds()
        self.slider_dragging = False

    # ---------------------------------------------------------------- #
    # Option-popup launchers
    # ---------------------------------------------------------------- #

    def _open_action_options(self, action_label: str):
        from src.gui.popups import ActionOptionsPopup
        if self._action_popup is None:
            self._action_popup = ActionOptionsPopup(self)
        # action_id is the second element of the available_actions tuple
        # (e.g. "left", "pointer_jump").
        try:
            action_id = shape_list.available_actions[action_label][1]
        except KeyError:
            action_id = action_label
        self._action_popup.open(action_label, action_id)

    def _open_gesture_options(self, action_label: str):
        from src.gui.popups import GestureOptionsPopup
        div = self.divs.get(action_label)
        if div is None:
            return
        gesture = div.get("selected_gesture", "None")
        if not gesture or gesture == "None":
            return  # nothing to configure for an unbound row
        if self._gesture_popup is None:
            self._gesture_popup = GestureOptionsPopup(self)
        self._gesture_popup.open(gesture)

    def set_div_inactive(self, div):
        none_gesture = shape_list.available_gestures_keys[0]
        div["selected_gesture"] = none_gesture
        div["combobox"].set(t(none_gesture))
        div["slider"].grid_remove()
        div["subtle_label"].grid_remove()
        div["volume_bar"].grid_remove()
        div["entry"].grid_remove()
        # action_opt_btn intentionally stays visible (see create_divs).
        div["gesture_opt_btn"].grid_remove()

    def _apply_div_ceiling(self, div, gesture_name):
        """Point this row's threshold slider at the gesture's ceiling
        (keeps its width, changes only its right-end value)."""
        ceiling = gesture_ceiling(gesture_name)
        floor = gesture_floor(gesture_name)
        div["_ceiling"] = ceiling
        div["_floor"] = floor
        try:
            div["slider"].configure(
                from_=floor, to=ceiling,
                number_of_steps=max(1, ceiling - floor))
        except Exception:
            pass
        return ceiling

    def apply_gesture_ceiling(self, gesture_name):
        """Re-apply a gesture's slider ceiling to every row bound to it —
        called live from the Gesture options popup when the user changes
        the upper limit."""
        for div in self.divs.values():
            if div.get("selected_gesture") != gesture_name:
                continue
            thres = div["slider"].get() / SLIDER_FACTOR
            ceiling = self._apply_div_ceiling(div, gesture_name)
            units = max(div["_floor"],
                        min(ceiling, int(round(thres * SLIDER_FACTOR))))
            div["slider"].set(units)
            self._set_entry_value(div, units / SLIDER_FACTOR)

    def set_div_active(self, div, gesture_name, thres):
        div["selected_gesture"] = gesture_name
        div["combobox"].set(t(gesture_name))
        ceiling = self._apply_div_ceiling(div, gesture_name)
        clamped_slider = max(div["_floor"],
                             min(ceiling, int(thres * SLIDER_FACTOR)))
        div["slider"].set(clamped_slider)
        div["slider"].configure(state="normal")
        # Seed entry value without re-firing config writes.
        self._set_entry_value(div, thres)

        div["subtle_label"].grid()
        div["slider"].grid()
        div["volume_bar"].grid()
        div["entry"].grid()
        div["action_opt_btn"].grid()
        div["gesture_opt_btn"].grid()

    def _set_entry_value(self, div, thres):
        """Update entry text to match `thres` without triggering the
        write callback (so we don't ping-pong with the slider).

        Entry uses the slider's own 1..5000 integer scale.
        """
        try:
            div["entry_var"].trace_vdelete("w", div["entry_trace_id"])
        except Exception:
            pass
        entry_val = int(round(thres * ENTRY_SCALE))
        entry_val = max(ENTRY_MIN, min(ENTRY_MAX, entry_val))
        div["entry_var"].set(str(entry_val))
        div["entry_trace_id"] = div["entry_var"].trace(
            "w", div["entry_trace_fn"])

    def load_initial_keybinds(self):
        """Load default from config and set the UI
        """

        for div_name, div in self.divs.items():
            self.set_div_inactive(div)

        for gesture_name, (
                device, action_key, thres,
                trigger_type) in ConfigManager().mouse_bindings.items():
            if [device, action_key] not in shape_list.available_actions_values:
                continue
            action_idx = shape_list.available_actions_values.index(
                [device, action_key])
            target_action_name = shape_list.available_actions_keys[action_idx]
            # Skip actions owned by the OTHER page's subset (the two pages
            # share the mouse_bindings store but each renders only its own
            # rows).
            if target_action_name not in self.divs:
                continue
            div = self.divs[target_action_name]
            self.set_div_active(div, gesture_name, thres)
            self.shared_dropdown.disable_item(gesture_name)
        self.shared_dropdown.refresh_items()
        # Stars can be toggled from another page's copy of the same row.
        for action_name, div in self.divs.items():
            btn = div.get("bookmark_btn")
            if btn is not None:
                self._paint_bookmark(btn, action_name)

    def create_divs(self, action_list: list, gesture_list: list):
        out_dict = {}

        # Strict 2-column wrap: first half into col 0, the rest into col 1
        # (never a col 2, however long the action list grows).
        COL_0_COUNT = (len(action_list) + 1) // 2
        for idx, action_name in enumerate(action_list):
            if idx < COL_0_COUNT:
                row = idx
                column = 0
            else:
                row = idx - COL_0_COUNT
                column = 1

            # Action label
            label = customtkinter.CTkLabel(master=self,
                                           text=t(action_name),
                                           height=175,
                                           width=300,
                                           anchor='nw',
                                           justify=tk.LEFT)
            label.cget("font").configure(weight='bold')
            label.grid(row=row,
                       column=column,
                       padx=(20, 20),
                       pady=(0, 0),
                       sticky="nw")

            # Help marker for the actions whose behaviour is not obvious
            # from the name. Fixed offset, like the bookmark star beside
            # it: the row is a fixed-width grid cell, not a packed row.
            if action_name in HELP_ACTIONS:
                HelpIcon(self, action_name).grid(
                    row=row, column=column, padx=(242, 0), pady=(2, 0),
                    sticky="nw")

            # Bookmark star — to the right of the action name. Toggles this
            # action's membership in the Bookmarks page.
            bm_btn = None
            if self._show_bookmark:
                bm_btn = customtkinter.CTkButton(
                    master=self, text="", width=30, height=28,
                    fg_color="transparent", hover_color="#F3E5DC",
                    text_color="#D9622B",
                    command=partial(self._toggle_bookmark, action_name))
                bm_btn.cget("font").configure(size=18)
                bm_btn.grid(row=row, column=column, padx=(276, 0),
                            pady=(0, 0), sticky="nw")
                self._paint_bookmark(bm_btn, action_name)

            # Combobox — display-only (permanently disabled; the shared
            # dropdown drives it), so keep the NORMAL text colour: the
            # theme's disabled grey would make the assigned gesture name
            # look faint.
            drop = customtkinter.CTkOptionMenu(master=self,
                                               values=[t(gesture_list[0])],
                                               width=240,
                                               dynamic_resizing=False,
                                               state="disabled",
                                               text_color_disabled="#201D1A")
            drop.grid(row=row,
                      column=column,
                      padx=(20, 20),
                      pady=(28, 10),
                      sticky="nw")
            self.shared_dropdown.register_widget(drop, action_name)

            # Two compact option buttons in place of the old
            # "Gesture size" tip line — open a popup with action- or
            # gesture-specific options respectively. Each fits in the
            # ~340 px-wide row.
            action_opt_btn = customtkinter.CTkButton(
                master=self,
                text=t("Action options"),
                fg_color="#F3E5DC",
                text_color="#D9622B",
                border_width=0,
                hover_color="#EBD5C5",
                height=24, width=170,
                command=partial(self._open_action_options, action_name))
            action_opt_btn.cget("font").configure(size=11, weight="bold")
            action_opt_btn.grid(row=row, column=column,
                                padx=(20, 0), pady=(58, 10), sticky="nw")
            # NOTE: action options configure the ACTION (e.g. gaze tool
            # grid/options, gaze-move timing), not the gesture, so this
            # button stays visible even with no gesture assigned. Only the
            # gesture-options button below is gated on a bound gesture.

            gesture_opt_btn = customtkinter.CTkButton(
                master=self,
                text=t("Gesture options"),
                fg_color="#F3E5DC",
                text_color="#D9622B",
                border_width=0,
                hover_color="#EBD5C5",
                height=24, width=170,
                command=partial(self._open_gesture_options, action_name))
            gesture_opt_btn.cget("font").configure(size=11, weight="bold")
            gesture_opt_btn.grid(row=row, column=column,
                                 padx=(20 + 174, 0),
                                 pady=(58, 10), sticky="nw")
            gesture_opt_btn.grid_remove()

            # Volume bar
            volume_bar = customtkinter.CTkProgressBar(
                master=self,
                width=240,
            )
            volume_bar.grid(row=row,
                            column=column,
                            padx=(20, 20),
                            pady=(92, 10),
                            sticky="nw")
            volume_bar.grid_remove()

            # Slider — extended downward to SLIDER_MIN to allow ultra-low
            # thresholds (~0.001) so faint expressions still trigger.
            slider = customtkinter.CTkSlider(master=self,
                                             from_=SLIDER_MIN,
                                             to=SLIDER_MAX,
                                             width=250,
                                             number_of_steps=SLIDER_MAX,
                                             command=partial(
                                                 self.slider_drag_callback,
                                                 action_name))
            slider.bind("<Button-1>",
                        partial(self.slider_mouse_down_callback, action_name))
            slider.bind("<ButtonRelease-1>",
                        partial(self.slider_mouse_up_callback, action_name))
            # Seed to a usable default (threshold = 0.1) so a newly
            # picked gesture isn't stuck at the midpoint threshold of
            # the wide 1..5000 range.
            slider.set(SLIDER_DEFAULT)
            slider.configure(state="disabled", hover=False)
            slider.grid(row=row,
                        column=column,
                        padx=(15, 20),
                        pady=(112, 10),
                        sticky="nw")
            slider.grid_remove()

            # The "Subtle / Exaggerated" scale caption under the slider was
            # removed (per request). Kept as an EMPTY 1px placeholder so
            # the show/hide calls elsewhere keep working, and the row
            # tightens by the height the caption used to take.
            subtle_label = customtkinter.CTkLabel(
                master=self, text="", height=1)
            subtle_label.grid(row=row, column=column, padx=(20, 20),
                              pady=(0, 0), sticky="nw")
            subtle_label.grid_remove()

            # Numeric entry — lets the user type a precise threshold
            # below or above what the slider supports.
            entry_var = tk.StringVar()
            entry_trace_fn = partial(self._entry_changed_callback,
                                     action_name)
            entry_trace_id = entry_var.trace("w", entry_trace_fn)
            entry = customtkinter.CTkEntry(
                master=self,
                textvariable=entry_var,
                width=70,
                justify=tk.RIGHT)
            entry.grid(row=row,
                       column=column,
                       padx=(280, 5),
                       pady=(108, 10),
                       sticky="nw")
            entry.grid_remove()

            out_dict[action_name] = {
                "label": label,
                "combobox": drop,
                "action_opt_btn": action_opt_btn,
                "gesture_opt_btn": gesture_opt_btn,
                "slider": slider,
                "volume_bar": volume_bar,
                "subtle_label": subtle_label,
                "selected_gesture": gesture_list[0],
                "entry": entry,
                "entry_var": entry_var,
                "entry_trace_id": entry_trace_id,
                "entry_trace_fn": entry_trace_fn,
                "_ceiling": SLIDER_MAX,
                "bookmark_btn": bm_btn,
            }

        return out_dict

    # ---------------------------------------------------------------- #
    # Bookmarks
    # ---------------------------------------------------------------- #

    def _paint_bookmark(self, btn, action_name):
        from src.gui import bookmarks
        btn.configure(
            text="★" if bookmarks.is_bookmarked(action_name) else "☆")

    def _toggle_bookmark(self, action_name):
        from src.gui import bookmarks
        bookmarks.toggle(action_name)
        div = self.divs.get(action_name)
        if div and div.get("bookmark_btn") is not None:
            self._paint_bookmark(div["bookmark_btn"], action_name)

    # ---------------------------------------------------------------- #
    # Entry <-> slider sync
    # ---------------------------------------------------------------- #

    def _entry_changed_callback(self, action_name: str, *_args):
        """User typed into the numeric threshold field.

        Entry value is an integer on the slider's 1..5000 scale.

        The config write is DEBOUNCED (600 ms after the last keystroke):
        applying every keystroke made transient prefixes live — typing
        "1000" momentarily set threshold 1 (≈0.001), low enough for any
        face noise to fire the bound action while the user was still
        typing.
        """
        div = self.divs.get(action_name)
        if div is None:
            return
        text = div["entry_var"].get().strip()
        # Strict integer parsing — no decimals.
        try:
            entry_value = int(text)
        except ValueError:
            div["entry"].configure(fg_color="#ee9e9d")
            return
        if entry_value < ENTRY_MIN or entry_value > ENTRY_MAX:
            div["entry"].configure(fg_color="#ee9e9d")
            return
        div["entry"].configure(fg_color="white")

        threshold = entry_value / ENTRY_SCALE

        # Drag the slider to match (clamped to its representable range).
        slider_pos = max(SLIDER_MIN,
                         min(SLIDER_MAX,
                             int(round(threshold * SLIDER_FACTOR))))
        # Avoid feedback loops by setting the slider directly (no
        # write back to entry from here).
        div["slider"].set(slider_pos)

        # Debounced persist (only when the slider isn't being dragged).
        if not self.slider_dragging:
            pending = div.get("_entry_after_id")
            if pending is not None:
                try:
                    self.after_cancel(pending)
                except Exception:
                    pass
            div["_entry_after_id"] = self.after(
                600, lambda an=action_name, th=threshold:
                self._commit_entry_threshold(an, th))

    def _commit_entry_threshold(self, action_name: str, threshold: float):
        div = self.divs.get(action_name)
        if div is None:
            return
        div["_entry_after_id"] = None
        if div["selected_gesture"] == "None":
            return
        target_device, target_action = (
            shape_list.available_actions[action_name])
        ConfigManager().set_temp_mouse_binding(
            div["selected_gesture"],
            device=target_device,
            action=target_action,
            threshold=threshold,
            trigger_type=DEFAULT_TRIGGER_TYPE)
        ConfigManager().apply_mouse_bindings()

    def slider_drag_callback(self, caller_name: str, slider_value: str):
        self.slider_dragging = True
        # Live-update the numeric entry while dragging so the user sees
        # the current threshold without releasing.
        div = self.divs.get(caller_name)
        if div is not None:
            thres = float(slider_value) / SLIDER_FACTOR
            self._set_entry_value(div, thres)

    def slider_mouse_down_callback(self, caller_name: str, event):
        self.slider_dragging = True

    def slider_mouse_up_callback(self, caller_name: str, event):
        self.slider_dragging = False
        div = self.divs[caller_name]
        target_device, target_action = shape_list.available_actions[caller_name]

        # change int slider value to float
        thres_value = div["slider"].get() / SLIDER_FACTOR
        self._set_entry_value(div, thres_value)

        ConfigManager().set_temp_mouse_binding(
            div["selected_gesture"],
            device=target_device,
            action=target_action,
            threshold=thres_value,
            trigger_type=DEFAULT_TRIGGER_TYPE)
        ConfigManager().apply_mouse_bindings()

    def dropdown_callback(self, caller_name: str, target_gesture: str):
        div = self.divs[caller_name]

        # Release old item
        if div["selected_gesture"] != target_gesture:
            self.shared_dropdown.enable_item(div["selected_gesture"])
        div["selected_gesture"] = target_gesture
        div["combobox"].set(t(target_gesture))

        # Set keybind
        target_device, target_action = shape_list.available_actions[caller_name]

        # get float threshold value
        if target_gesture != "None":
            self._apply_div_ceiling(div, target_gesture)
            div["slider"].configure(state="normal")
            div["slider"].grid()
            div["volume_bar"].grid()
            div["subtle_label"].grid()
            div["entry"].grid()
            div["action_opt_btn"].grid()
            div["gesture_opt_btn"].grid()
            thres_value = div["slider"].get() / SLIDER_FACTOR
            self._set_entry_value(div, thres_value)
            ConfigManager().set_temp_mouse_binding(
                target_gesture,
                device=target_device,
                action=target_action,
                threshold=thres_value,
                trigger_type=DEFAULT_TRIGGER_TYPE)

        # Remove keybind if "None"
        else:
            div["slider"].configure(state="disabled")
            div["slider"].grid_remove()
            div["volume_bar"].grid_remove()
            div["subtle_label"].grid_remove()
            div["entry"].grid_remove()
            # action_opt_btn stays visible (see create_divs).
            div["gesture_opt_btn"].grid_remove()
            ConfigManager().remove_temp_mouse_binding(device=target_device,
                                                      action=target_action)

        ConfigManager().apply_mouse_bindings()

    def update_volume_preview(self):

        bs = FaceMesh().get_blendshapes()
        # No detection yet (camera still warming up / no face): skip
        # this tick. Indexing into None would raise and permanently
        # kill the frame_loop for this page visit.
        if bs is None:
            return
        # Mirror Keybinder's full pipeline (curve -> global gain -> per
        # gesture gain) so the volume bar matches exactly what the
        # threshold compare sees. Without the curve step the bar would
        # disagree with the popup meter the user has just adjusted.
        global_gain = float(
            ConfigManager().config.get("gesture_gain", 3.0))
        per_gestures = ConfigManager().config.get(
            "gesture_gains", {}) or {}
        curves = ConfigManager().config.get(
            "gesture_curves", {}) or {}

        for div_name, div in self.divs.items():

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
            # Scale bar to match the slider's range so "bar full" lines
            # up with "slider at its right edge". Bar still clips to
            # [0, 1] visually; the threshold compare uses the unscaled
            # gained value so a threshold above SLIDER_MAX_THRES still
            # makes sense.
            #
            # PERF: CTkProgressBar.set() and .configure() each trigger a
            # full canvas redraw even when nothing changed, which at
            # 20 Hz × 8 rows made this page noticeably heavy. Quantise
            # the bar value to 1/200 steps and only touch the widget
            # when the visible value or colour actually changes.
            # Bar full = the gesture's slider ceiling (not the global 5.0),
            # so lowering the ceiling zooms the meter into the small-value
            # region — matching the rescaled threshold slider.
            # The floor is the bar's empty end, likewise.
            lo = div.get("_floor", SLIDER_MIN) / SLIDER_FACTOR
            hi = max(div.get("_ceiling", SLIDER_MAX),
                     SLIDER_MIN) / SLIDER_FACTOR
            span = max(hi - lo, 1e-6)
            bar_q = round(min(1.0, max(0.0, (gained - lo) / span)) * 200)
            if bar_q != div.get("_last_bar_q"):
                div["_last_bar_q"] = bar_q
                div["volume_bar"].set(bar_q / 200.0)

            slider_value = div["slider"].get() / SLIDER_FACTOR
            color = GREEN if gained > slider_value else YELLOW
            if color != div.get("_last_bar_color"):
                div["_last_bar_color"] = color
                div["volume_bar"].configure(progress_color=color)

    def frame_loop(self):
        if self.is_destroyed:
            return

        if self.is_active:
            self.update_volume_preview()
            self.after(50, self.frame_loop)
        else:
            return

    def inner_refresh_profile(self):
        # Create new divs form the new profile
        self.load_initial_keybinds()

    def enter(self):
        super().enter()
        # Reload from config on every entry. The Mouse, Extensions and
        # Bookmarks pages each build their own binding frame over the ONE
        # shared mouse_bindings store, and a bookmarked action is rendered
        # by two of them at once — without this, a threshold changed on
        # the Bookmarks page still showed its old value on the Mouse page
        # (and vice versa) until the app restarted.
        self.load_initial_keybinds()
        self.after(1, self.frame_loop)

    def leave(self):
        super().leave()


class PageSelectGestures(SafeDisposableScrollableFrame):

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)

        self.grid_columnconfigure(0, weight=1)
        self.is_active = False
        self.bind_id_leave = None

        # Top label.
        self.top_label = customtkinter.CTkLabel(master=self,
                                                text=t("Mouse"))
        self.top_label.cget("font").configure(size=24, weight="bold")
        self.top_label.grid(row=0,
                            column=0,
                            padx=20,
                            pady=(10, 2),
                            sticky="nw",
                            columnspan=1)

        # Description.
        des_txt = t("Select a facial gesture that you would like to bind to a specific mouse action. Sensitivity allows you to control  the extent to which you need to gesture to trigger the mouse action")

        # Inner frame
        self.inner_frame = FrameSelectGesture(self,
                                              logger_name="FrameSelectGesture")
        self.inner_frame.grid(row=2, column=0, padx=5, pady=5, sticky="nw")

    def enter(self):
        super().enter()
        self.inner_frame.enter()
        # NOTE: previously bound <Leave> to hide_dropdown. That fired the
        # moment the pointer moved from the page into the dropdown's
        # Toplevel, hiding the menu the user was trying to interact with.
        # Click-outside-dismiss in the Dropdown class handles closing now.

    def refresh_profile(self):
        self.inner_frame.inner_refresh_profile()

    def leave(self):
        super().leave()
        self.inner_frame.leave()
        self.inner_frame.shared_dropdown.hide_dropdown()

    def destroy(self):
        super().destroy()
        self.inner_frame.destroy()
