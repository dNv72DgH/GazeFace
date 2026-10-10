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
"""Popup with options specific to a particular bound action.

Currently only "pointer_jump" has a real option panel (the gaze-dwell
time, ported from the Advanced page). All other actions show a
"Coming soon" placeholder.
"""

import logging
import tkinter as tk

import customtkinter

from src.config_manager import ConfigManager
from src.gui.popups._util import apply_popup_geometry, POPUP_BG
from src.gui.help_icon import HelpIcon
from src.i18n import t

logger = logging.getLogger("ActionOptionsPopup")

# Dwell-time slider bounds (ms). Same numbers the Advanced page used.
JUMP_DELAY_MIN_MS = 100
JUMP_DELAY_MAX_MS = 5000
# Gaze Jump snap-to-clickable range (px).
SNAP_MIN_PX = 20
SNAP_MAX_PX = 400
SNAP_DEFAULT_PX = 120
JUMP_DELAY_DEFAULT_MS = 1000

# Gaze move: arrow-display duration (ms) and move speed (px/s).
GM_DISPLAY_MIN_MS = 500
GM_DISPLAY_MAX_MS = 5000
GM_DISPLAY_DEFAULT_MS = 2000
GM_SPEED_MIN = 1
GM_SPEED_MAX = 3000
GM_SPEED_DEFAULT = 800


class ActionOptionsPopup:

    def __init__(self, master):
        self.master = master
        self.top = None
        self._col = None         # current target column for builders
        self._col_left = None
        self._col_right = None
        self._window = None

    def open(self, action_label: str, action_id: str):
        """`action_label` is the user-visible action name (e.g.
        "Mouse left click"). `action_id` is the internal id from
        shape_list.available_actions[label][1] (e.g. "left", "pointer_jump").
        """
        # Gaze extension actions: "Action options" opens that tool's own
        # feature options (grid size / items / in-tool gestures) instead
        # of a generic placeholder.
        if action_id in ("gaze_launcher", "gaze_clipboard",
                         "gaze_taskswitch"):
            self._open_extension_options(action_id)
            return

        # In-window panel (no Toplevel). self.top is the panel body that
        # all the existing _build_* methods build into.
        from src.gui.panel_host import panel_host
        host = panel_host()
        if host is None:
            return
        self._host = host
        self.top = host.push(f"{t(action_label)} {t('Action options')}")
        self._col = self.top

        # (No in-body title — the panel header already carries it.)

        # Two columns so controls fit with less scrolling. Builders start
        # in the left column and switch to self._col_right at their midpoint.
        cols = customtkinter.CTkFrame(self.top, fg_color="transparent")
        cols.pack(fill="both", expand=True)
        self._col_left = customtkinter.CTkFrame(cols, fg_color="transparent")
        self._col_left.grid(row=0, column=0, sticky="nw", padx=(0, 8))
        self._col_right = customtkinter.CTkFrame(cols, fg_color="transparent")
        self._col_right.grid(row=0, column=1, sticky="nw", padx=(8, 0))
        self._col = self._col_left

        if action_id == "pointer_jump":
            self._build_pointer_jump_section()
        elif action_id == "gaze_move":
            self._build_gaze_move_section()
        else:
            self._build_coming_soon()

    def _open_extension_options(self, action_id: str):
        """Route the binding row's "Action options" button straight to the
        gaze tool's own options popup."""
        if action_id == "gaze_launcher":
            from src.gui.popups.launcher_options_popup import (
                LauncherOptionsPopup)
            LauncherOptionsPopup(self.master).open()
        elif action_id == "gaze_clipboard":
            from src.gui.popups.ext_grid_options_popup import (
                ExtGridOptionsPopup)
            # The clipboard grid pages (◀ ▶) and has a pinned list, reachable
            # both from the in-grid ★ / ◀ cell and from the "switch" gesture;
            # "pin" saves the selected entry.
            ExtGridOptionsPopup(
                self.master, "clipboard", t("Gaze clipboard history"),
                ["confirm", "back", "forward", "switch", "pin", "delete",
                 "exit"],
                clipboard=True).open()
        elif action_id == "gaze_taskswitch":
            from src.gui.popups.ext_grid_options_popup import (
                ExtGridOptionsPopup)
            ExtGridOptionsPopup(
                self.master, "task_switcher", t("Gaze task switcher"),
                ["confirm", "back", "forward", "close", "exit"]).open()

    def _open_gaze_move_colors(self):
        from src.gui.popups.ext_grid_options_popup import GazeMoveColorPopup
        GazeMoveColorPopup(self.master).open()

    # ---------------------------------------------------------------- #
    # Gaze move (arrow display time + move speed)
    # ---------------------------------------------------------------- #

    def _build_gaze_move_section(self):

        # --- Arrow display time (ms) ---
        lbl = customtkinter.CTkLabel(
            master=self._col, text=t("Arrow display time (ms)"))
        lbl.cget("font").configure(size=14, weight="bold")
        lbl.pack(padx=20, pady=(8, 0), anchor="w")

        drow = customtkinter.CTkFrame(
            master=self._col, fg_color="transparent")
        drow.pack(padx=20, pady=(6, 0), anchor="w", fill="x")
        self._gm_disp_dragging = False
        self.gm_disp_slider = customtkinter.CTkSlider(
            master=drow, from_=GM_DISPLAY_MIN_MS, to=GM_DISPLAY_MAX_MS,
            width=220,
            number_of_steps=(GM_DISPLAY_MAX_MS - GM_DISPLAY_MIN_MS) // 100,
            command=self._on_gm_disp_drag)
        self.gm_disp_slider.bind(
            "<Button-1>",
            lambda _e: setattr(self, "_gm_disp_dragging", True))
        self.gm_disp_slider.bind("<ButtonRelease-1>",
                                 self._on_gm_disp_release)
        self.gm_disp_slider.pack(side="left", padx=(0, 10))
        self.gm_disp_var = tk.StringVar()
        self._gm_disp_trace = self.gm_disp_var.trace(
            "w", self._on_gm_disp_entry)
        self.gm_disp_entry = customtkinter.CTkEntry(
            master=drow, textvariable=self.gm_disp_var, width=80)
        self.gm_disp_entry.pack(side="left")
        dms = int(ConfigManager().config.get(
            "gaze_move_display_ms", GM_DISPLAY_DEFAULT_MS))
        dms = max(GM_DISPLAY_MIN_MS, min(GM_DISPLAY_MAX_MS, dms))
        self.gm_disp_slider.set(dms)
        self.gm_disp_var.trace_vdelete("w", self._gm_disp_trace)
        self.gm_disp_var.set(str(dms))
        self._gm_disp_trace = self.gm_disp_var.trace(
            "w", self._on_gm_disp_entry)

        # --- Move speed (px/s) ---
        slbl = customtkinter.CTkLabel(
            master=self._col, text=t("Move speed (px/s)"))
        slbl.cget("font").configure(size=14, weight="bold")
        slbl.pack(padx=20, pady=(16, 0), anchor="w")

        srow = customtkinter.CTkFrame(
            master=self._col, fg_color="transparent")
        srow.pack(padx=20, pady=(6, 0), anchor="w", fill="x")
        self._gm_speed_dragging = False
        self.gm_speed_slider = customtkinter.CTkSlider(
            master=srow, from_=GM_SPEED_MIN, to=GM_SPEED_MAX, width=220,
            number_of_steps=(GM_SPEED_MAX - GM_SPEED_MIN) // 50,
            command=self._on_gm_speed_drag)
        self.gm_speed_slider.bind(
            "<Button-1>",
            lambda _e: setattr(self, "_gm_speed_dragging", True))
        self.gm_speed_slider.bind("<ButtonRelease-1>",
                                  self._on_gm_speed_release)
        self.gm_speed_slider.pack(side="left", padx=(0, 10))
        self.gm_speed_var = tk.StringVar()
        self._gm_speed_trace = self.gm_speed_var.trace(
            "w", self._on_gm_speed_entry)
        self.gm_speed_entry = customtkinter.CTkEntry(
            master=srow, textvariable=self.gm_speed_var, width=80)
        self.gm_speed_entry.pack(side="left")
        spd = int(ConfigManager().config.get(
            "gaze_move_speed", GM_SPEED_DEFAULT))
        spd = max(GM_SPEED_MIN, min(GM_SPEED_MAX, spd))
        self.gm_speed_slider.set(spd)
        self.gm_speed_var.trace_vdelete("w", self._gm_speed_trace)
        self.gm_speed_var.set(str(spd))
        self._gm_speed_trace = self.gm_speed_var.trace(
            "w", self._on_gm_speed_entry)

        # --- Ignore blinks (ms) ---  keep holding the current direction
        # this long after a blink is seen, so a blink can't drop or flip the
        # arrow (0 = off). Same option as the launcher's "ignore blinks".
        sel_lbl = customtkinter.CTkLabel(
            master=self._col, text=t("Ignore blinks (ms)"))
        sel_lbl.cget("font").configure(size=14, weight="bold")
        sel_lbl.pack(padx=20, pady=(16, 0), anchor="w")
        selrow = customtkinter.CTkFrame(
            master=self._col, fg_color="transparent")
        selrow.pack(padx=20, pady=(2, 0), anchor="w", fill="x")
        self.gm_blink_var = tk.IntVar(value=int(
            ConfigManager().config.get("gaze_move_ignore_blink_ms", 200)
            or 0))
        customtkinter.CTkSlider(
            selrow, from_=0, to=500, number_of_steps=25, width=220,
            variable=self.gm_blink_var,
            command=lambda _v: self._gm_save(
                "gaze_move_ignore_blink_ms",
                int(self.gm_blink_var.get()))).pack(side="left", padx=(0, 8))
        customtkinter.CTkLabel(selrow, textvariable=self.gm_blink_var,
                               width=50).pack(side="left")

        # --- Colour settings (arrows / centre ring / pointer ring) ---
        customtkinter.CTkButton(
            master=self._col, text=t("Color settings"), width=180,
            command=self._open_gaze_move_colors).pack(
                padx=20, pady=(16, 4), anchor="w")

        # The background-transparency slider used to sit here. It did
        # nothing: the gaze-move overlay runs in "arrows" mode, and that
        # mode turns the acrylic backdrop off entirely
        # (overlay_main._apply_fx), so the gradient carrying this value is
        # discarded. Removed rather than fixed - the arrows are meant to
        # sit on the desktop, not on a dimmed one.
        self._col = self._col_right

    def _gm_save(self, key, value):
        ConfigManager().set_temp_config(key, value)
        ConfigManager().apply_config()

    def _on_gm_disp_drag(self, value):
        self._gm_disp_dragging = True
        try:
            self.gm_disp_var.trace_vdelete("w", self._gm_disp_trace)
        except Exception:
            pass
        self.gm_disp_var.set(str(int(value)))
        self._gm_disp_trace = self.gm_disp_var.trace(
            "w", self._on_gm_disp_entry)

    def _on_gm_disp_release(self, _e):
        self._gm_disp_dragging = False
        self._gm_save("gaze_move_display_ms", int(self.gm_disp_slider.get()))

    def _on_gm_disp_entry(self, *_a):
        text = self.gm_disp_var.get().strip()
        try:
            v = int(text)
        except ValueError:
            self.gm_disp_entry.configure(fg_color="#ee9e9d")
            return
        if v < GM_DISPLAY_MIN_MS or v > GM_DISPLAY_MAX_MS:
            self.gm_disp_entry.configure(fg_color="#ee9e9d")
            return
        self.gm_disp_entry.configure(fg_color="white")
        self.gm_disp_slider.set(v)
        if not self._gm_disp_dragging:
            self._gm_save("gaze_move_display_ms", v)

    def _on_gm_speed_drag(self, value):
        self._gm_speed_dragging = True
        try:
            self.gm_speed_var.trace_vdelete("w", self._gm_speed_trace)
        except Exception:
            pass
        self.gm_speed_var.set(str(int(value)))
        self._gm_speed_trace = self.gm_speed_var.trace(
            "w", self._on_gm_speed_entry)

    def _on_gm_speed_release(self, _e):
        self._gm_speed_dragging = False
        self._gm_save("gaze_move_speed", int(self.gm_speed_slider.get()))

    def _on_gm_speed_entry(self, *_a):
        text = self.gm_speed_var.get().strip()
        try:
            v = int(text)
        except ValueError:
            self.gm_speed_entry.configure(fg_color="#ee9e9d")
            return
        if v < GM_SPEED_MIN or v > GM_SPEED_MAX:
            self.gm_speed_entry.configure(fg_color="#ee9e9d")
            return
        self.gm_speed_entry.configure(fg_color="white")
        self.gm_speed_slider.set(v)
        if not self._gm_speed_dragging:
            self._gm_save("gaze_move_speed", v)

    # ---------------------------------------------------------------- #
    # Gaze-Jump dwell time
    # ---------------------------------------------------------------- #

    def _build_pointer_jump_section(self):

        lbl = customtkinter.CTkLabel(
            master=self._col, text=t("Dwell time (ms)"))
        lbl.cget("font").configure(size=14, weight="bold")
        lbl.pack(padx=20, pady=(8, 0), anchor="w")

        row = customtkinter.CTkFrame(
            master=self._col, fg_color="transparent")
        row.pack(padx=20, pady=(6, 0), anchor="w", fill="x")

        self._jump_dragging = False
        self.jump_slider = customtkinter.CTkSlider(
            master=row,
            from_=JUMP_DELAY_MIN_MS, to=JUMP_DELAY_MAX_MS,
            width=220,
            number_of_steps=(JUMP_DELAY_MAX_MS - JUMP_DELAY_MIN_MS) // 10,
            command=self._on_jump_slider_drag)
        self.jump_slider.bind("<Button-1>",
                              lambda _e: setattr(self, "_jump_dragging",
                                                 True))
        self.jump_slider.bind("<ButtonRelease-1>",
                              self._on_jump_slider_release)
        self.jump_slider.pack(side="left", padx=(0, 10))

        self.jump_var = tk.StringVar()
        self._jump_trace_id = self.jump_var.trace(
            "w", self._on_jump_entry_changed)
        self.jump_entry = customtkinter.CTkEntry(
            master=row, textvariable=self.jump_var, width=80)
        self.jump_entry.pack(side="left")

        # Seed from config
        cfg_ms = int(ConfigManager().config.get(
            "pointer_jump_delay_ms", JUMP_DELAY_DEFAULT_MS))
        cfg_ms = max(JUMP_DELAY_MIN_MS,
                     min(JUMP_DELAY_MAX_MS, cfg_ms))
        self.jump_slider.set(cfg_ms)
        self.jump_var.trace_vdelete("w", self._jump_trace_id)
        self.jump_var.set(str(cfg_ms))
        self._jump_trace_id = self.jump_var.trace(
            "w", self._on_jump_entry_changed)

        self._build_snap_options()

        # Face-control override checkbox. (right column)
        self._col = self._col_right
        self.ignore_fc_var = tk.BooleanVar(
            value=bool(ConfigManager().config.get(
                "pointer_jump_ignore_face_control", False)))
        ignore_fc_cb = customtkinter.CTkCheckBox(
            master=self._col,
            text=t("Allow Gaze Jump even when Face control is off"),
            variable=self.ignore_fc_var,
            onvalue=True, offvalue=False,
            command=self._on_ignore_fc_toggle)
        ignore_fc_cb.pack(padx=20, pady=(16, 4), anchor="w")

        # Chain into Gaze move right after the jump lands.
        self.then_gm_var = tk.BooleanVar(
            value=bool(ConfigManager().config.get(
                "pointer_jump_then_gaze_move", False)))
        # The checkbox is its own label, so its help marker sits beside
        # the control rather than after a heading.
        then_gm_row = customtkinter.CTkFrame(
            master=self._col, fg_color="transparent")
        then_gm_row.pack(padx=20, pady=(16, 4), anchor="w")
        then_gm_cb = customtkinter.CTkCheckBox(
            master=then_gm_row,
            text=t("Start Gaze move right after the jump"),
            variable=self.then_gm_var,
            onvalue=True, offvalue=False,
            command=self._on_then_gm_toggle)
        then_gm_cb.pack(side="left")
        HelpIcon(then_gm_row,
                 "Start Gaze move right after the jump").pack(
                     side="left", padx=(8, 0))

    # ---------------------------------------------------------------- #
    # Gaze Jump: snap to the nearest clickable thing
    # ---------------------------------------------------------------- #

    def _build_snap_options(self):
        cfg = ConfigManager().config or {}
        self.snap_var = tk.BooleanVar(
            value=bool(cfg.get("pointer_jump_snap", False)))
        customtkinter.CTkCheckBox(
            master=self._col,
            text=t("Jump to the nearest clickable object"),
            variable=self.snap_var, onvalue=True, offvalue=False,
            command=lambda: self._gm_save(
                "pointer_jump_snap", bool(self.snap_var.get()))).pack(
                    padx=20, pady=(18, 4), anchor="w")

        lbl = customtkinter.CTkLabel(master=self._col, text=t("Range (px)"))
        lbl.cget("font").configure(size=14, weight="bold")
        lbl.pack(padx=20, pady=(8, 0), anchor="w")
        row = customtkinter.CTkFrame(master=self._col,
                                     fg_color="transparent")
        row.pack(padx=20, pady=(6, 0), anchor="w", fill="x")
        self._snap_dragging = False
        self.snap_slider = customtkinter.CTkSlider(
            master=row, from_=SNAP_MIN_PX, to=SNAP_MAX_PX, width=220,
            number_of_steps=(SNAP_MAX_PX - SNAP_MIN_PX) // 10,
            command=self._on_snap_drag)
        self.snap_slider.bind(
            "<Button-1>", lambda _e: setattr(self, "_snap_dragging", True))
        self.snap_slider.bind("<ButtonRelease-1>", self._on_snap_release)
        self.snap_slider.pack(side="left", padx=(0, 10))
        self.snap_px_var = tk.StringVar()
        self.snap_entry = customtkinter.CTkEntry(
            master=row, textvariable=self.snap_px_var, width=80)
        self.snap_entry.pack(side="left")
        px = int(cfg.get("pointer_jump_snap_radius", SNAP_DEFAULT_PX))
        px = max(SNAP_MIN_PX, min(SNAP_MAX_PX, px))
        self.snap_slider.set(px)
        self.snap_px_var.set(str(px))
        self._snap_trace = self.snap_px_var.trace("w", self._on_snap_entry)

    def _on_snap_drag(self, value):
        self._snap_dragging = True
        try:
            self.snap_px_var.trace_vdelete("w", self._snap_trace)
        except Exception:
            pass
        self.snap_px_var.set(str(int(value)))
        self._snap_trace = self.snap_px_var.trace("w", self._on_snap_entry)

    def _on_snap_release(self, _e):
        self._snap_dragging = False
        self._gm_save("pointer_jump_snap_radius", int(self.snap_slider.get()))

    def _on_snap_entry(self, *_a):
        text = self.snap_px_var.get().strip()
        try:
            v = int(text)
        except ValueError:
            self.snap_entry.configure(fg_color="#ee9e9d")
            return
        if v < SNAP_MIN_PX or v > SNAP_MAX_PX:
            self.snap_entry.configure(fg_color="#ee9e9d")
            return
        self.snap_entry.configure(fg_color="white")
        self.snap_slider.set(v)
        if not self._snap_dragging:
            self._gm_save("pointer_jump_snap_radius", v)

    def _on_then_gm_toggle(self):
        ConfigManager().set_temp_config(
            "pointer_jump_then_gaze_move",
            bool(self.then_gm_var.get()))
        ConfigManager().apply_config()

    def _on_ignore_fc_toggle(self):
        ConfigManager().set_temp_config(
            "pointer_jump_ignore_face_control",
            bool(self.ignore_fc_var.get()))
        ConfigManager().apply_config()

    def _on_jump_slider_drag(self, value):
        self._jump_dragging = True
        v = int(value)
        # Mirror to entry without ping-ponging
        try:
            self.jump_var.trace_vdelete("w", self._jump_trace_id)
        except Exception:
            pass
        self.jump_var.set(str(v))
        self._jump_trace_id = self.jump_var.trace(
            "w", self._on_jump_entry_changed)

    def _on_jump_slider_release(self, _event):
        self._jump_dragging = False
        v = int(self.jump_slider.get())
        ConfigManager().set_temp_config("pointer_jump_delay_ms", v)
        ConfigManager().apply_config()

    def _on_jump_entry_changed(self, *_args):
        text = self.jump_var.get().strip()
        try:
            v = int(text)
        except ValueError:
            self.jump_entry.configure(fg_color="#ee9e9d")
            return
        if v < JUMP_DELAY_MIN_MS or v > JUMP_DELAY_MAX_MS:
            self.jump_entry.configure(fg_color="#ee9e9d")
            return
        self.jump_entry.configure(fg_color="white")
        self.jump_slider.set(v)
        if not self._jump_dragging:
            ConfigManager().set_temp_config("pointer_jump_delay_ms", v)
            ConfigManager().apply_config()

    # ---------------------------------------------------------------- #
    # Placeholder for actions without options yet
    # ---------------------------------------------------------------- #

    def _build_coming_soon(self):
        # Just state that there is nothing to configure. The old "Coming
        # soon." line under it promised work that is not planned, which
        # reads as an unfinished product rather than an action that simply
        # has no options.
        lbl = customtkinter.CTkLabel(
            master=self._col,
            text=t("No action-specific options here yet."),
            wraplength=460,
            justify=tk.LEFT,
            text_color="#7A736A")
        lbl.cget("font").configure(size=14)
        lbl.pack(padx=20, pady=20, anchor="w")

    # ---------------------------------------------------------------- #

    def close(self):
        h = getattr(self, "_host", None)
        if h is not None and self.top is not None:
            h.pop(self.top)
        self._window = None
        self.top = None
