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

import tkinter

import customtkinter

from src import app_status
from src.config_manager import ConfigManager
from src.controllers import MouseController
from src.gui.frames.safe_disposable_frame import SafeDisposableFrame
from src.i18n import t

# Matches the left menu pane's button width.
PANE_W = 176
LIGHT_BLUE = "#F6F4F0"
TEXT = "#201D1A"
MUTED = "#7A736A"
ACCENT = "#D9622B"
# Error texts are set in the app's ordinary black (was red #C5221F), at
# the user's request. The word "Error" itself still says what it is.
ERROR_TEXT = TEXT
# Switch widget width — the visible switch is only `switch_width` px;
# the rest is empty clickable area, so keep it small so it doesn't pad
# the left column out wider than the menu.
SWITCH_W = 80
# Status polling. Nothing pushes state changes (there is no error->GUI
# channel), so the readout polls — cheap: a few label reads per tick.
STATUS_TICK_MS = 500


class FrameCamPreview(SafeDisposableFrame):

    def __init__(self, master, master_callback: callable, **kwargs):
        super().__init__(master, **kwargs)

        # Rows: 0-3 status, 4 Face control, 5 spacer, 6 Sub-monitor.
        self.grid_rowconfigure(5, weight=1)
        self.grid_columnconfigure(0, weight=1)
        # border_width 0 / corner_radius 0: the theme gives CTkFrame a 1 px
        # border and 12 px rounded corners, which drew a visible rule along
        # the top of the status block and down the pane edge. The left pane
        # is meant to read as one flat surface.
        self.configure(fg_color=LIGHT_BLUE, border_width=0, corner_radius=0)

        # Status readout — replaces the live camera preview that used to
        # sit here. The preview cost a PIL resize + PhotoImage rebuild on
        # the tk main thread ~30x/s (the same thread that runs face
        # detection) to show something the Home page already shows, and
        # it still could not tell the user what was wrong when tracking
        # stopped. This says so in words instead. See src/app_status.py.
        status_head = customtkinter.CTkLabel(master=self,
                                             text=t("Status"),
                                             text_color=TEXT,
                                             anchor="w")
        status_head.cget("font").configure(size=14, weight="bold")
        status_head.grid(row=0, column=0, padx=(22, 0), pady=(10, 0),
                         sticky="nw")

        self.status_value = customtkinter.CTkLabel(master=self,
                                                   text=t("Disabled"),
                                                   text_color=MUTED,
                                                   anchor="w")
        self.status_value.cget("font").configure(size=14, weight="bold")
        self.status_value.grid(row=1, column=0, padx=(22, 0), pady=(0, 2),
                               sticky="nw")

        # Only populated on error: what went wrong, and what the app is
        # doing about it.
        self.status_detail = customtkinter.CTkLabel(
            master=self, text="", text_color=MUTED, anchor="w",
            justify=tkinter.LEFT, wraplength=PANE_W)
        self.status_detail.cget("font").configure(size=11)
        self.status_detail.grid(row=2, column=0, padx=(22, 0), pady=(0, 0),
                                sticky="nw")

        self.status_action = customtkinter.CTkLabel(
            master=self, text="", text_color=MUTED, anchor="w",
            justify=tkinter.LEFT, wraplength=PANE_W)
        self.status_action.cget("font").configure(size=11)
        self.status_action.grid(row=3, column=0, padx=(22, 0), pady=(0, 6),
                                sticky="nw")

        # Toggle label
        self.toggle_label = customtkinter.CTkLabel(master=self,
                                                   compound='right',
                                                   text=t("Face control"),
                                                   text_color="black",
                                                   justify=tkinter.LEFT)
        self.toggle_label.cget("font").configure(size=14, weight="bold")
        self.toggle_label.grid(row=4,
                               column=0,
                               padx=(22, 0),
                               pady=5,
                               sticky="nw")

        # Toggle switch
        self.toggle_switch = customtkinter.CTkSwitch(
            master=self,
            text="",
            width=SWITCH_W,
            border_color="transparent",
            switch_height=18,
            switch_width=32,
            variable=MouseController().is_active,
            command=lambda: master_callback(
                "toggle_switch", {"switch_status": self.toggle_switch.get()}),
            onvalue=1,
            offvalue=0,
        )
        self.toggle_switch.grid(row=4,
                                column=0,
                                padx=(112, 0),
                                pady=5,
                                sticky="nw")

        # (The "Allow facial gestures…" description under the Face
        # control switch was removed at the user's request.)

        # Floating gesture sub-monitor toggle — same switch design as
        # the Face control toggle above. State is app-wide and restored
        # on launch.
        self.submonitor = None
        self.submonitor_var = tkinter.BooleanVar(value=False)
        self.submonitor_label = customtkinter.CTkLabel(
            master=self,
            compound='right',
            text=t("Sub-monitor"),
            text_color="black",
            justify=tkinter.LEFT)
        self.submonitor_label.cget("font").configure(size=14, weight="bold")
        # Extra BOTTOM padding on the last row lifts the whole Face control
        # / Sub-monitor pair by half a row: this frame is bottom-anchored
        # (sticky="sew") and sized to its content, so growing the last row
        # downward pushes everything above it up off the window edge.
        self.submonitor_label.grid(row=6, column=0, padx=(22, 0),
                                   pady=(5, 20), sticky="nw")
        self.submonitor_switch = customtkinter.CTkSwitch(
            master=self,
            text="",
            width=SWITCH_W,
            border_color="transparent",
            switch_height=18,
            switch_width=32,
            variable=self.submonitor_var,
            command=self._on_submonitor_toggle,
            onvalue=True,
            offvalue=False)
        self.submonitor_switch.grid(row=6, column=0, padx=(112, 0),
                                    pady=(5, 20), sticky="nw")

        self.after(1, self.status_loop)

        # Restore monitor visibility from the previous session (after
        # the main window is fully up, so the Toplevel parents cleanly).
        try:
            if bool(ConfigManager()._read_app_setting(
                    "submonitor_visible", False)):
                self.after(500, self._restore_submonitor)
        except Exception:
            pass

        # Let the gaze launcher toggle the sub-monitor via this same
        # switch (so state stays in sync with the GUI).
        try:
            MouseController()._submonitor_toggle = (
                self._toggle_submonitor_external)
        except Exception:
            pass

    def _toggle_submonitor_external(self):
        self.submonitor_var.set(not self.submonitor_var.get())
        self._on_submonitor_toggle()

    def _restore_submonitor(self):
        if self.is_destroyed:
            return
        self.submonitor_var.set(True)
        self._on_submonitor_toggle()


    def _on_submonitor_toggle(self):
        from src.gui.sub_monitor import GestureSubMonitor
        if self.submonitor is None:
            self.submonitor = GestureSubMonitor(
                self.winfo_toplevel(),
                on_hidden=lambda: self.submonitor_var.set(False))
        if self.submonitor_var.get():
            self.submonitor.show()
        else:
            self.submonitor.hide()

    def status_loop(self):
        """Repaint the status readout. Error state wins over on/off:
        face control being "on" means nothing if tracking is broken."""
        if self.is_destroyed:
            return
        if self.is_active:
            try:
                self._paint_status()
            except Exception as e:
                self.logger.error("status paint failed: %s", e)
            self.after(STATUS_TICK_MS, self.status_loop)

    def _paint_status(self):
        state, err, action = app_status.read()
        if state == app_status.ERROR:
            self.status_value.configure(text=t("Error"),
                                        text_color=ERROR_TEXT)
            self.status_detail.configure(text=t(err) if err else "",
                                         text_color=ERROR_TEXT)
            self.status_action.configure(text=t(action) if action else "",
                                         text_color=ERROR_TEXT)
            return
        on = bool(MouseController()._active_flag)
        # Until face detection delivers (and whenever it falls behind) the
        # meters and the pointer do not move. Say so, rather than looking
        # like it is on and working.
        try:
            from src.detectors import FaceMesh
            fm = FaceMesh().results_state()
        except Exception:
            fm = "ok"
        if on and fm != "ok":
            value, detail = {
                "starting": (t("Preparing"), t("Starting face tracking...")),
                "stalled": (t("Processing"),
                            t("Face tracking is catching up...")),
                "noface": (t("Searching"), t("Looking for your face...")),
            }.get(fm, (t("Processing"), ""))
            self.status_value.configure(text=value, text_color=MUTED)
            self.status_detail.configure(text=detail, text_color=MUTED)
            self.status_action.configure(text="")
            return
        self.status_value.configure(
            text=t("Enabled") if on else t("Disabled"),
            text_color=ACCENT if on else MUTED)
        self.status_detail.configure(text="")
        self.status_action.configure(text="")

    def enter(self):
        super().enter()
        self.after(1, self.status_loop)

    def destroy(self):
        super().destroy()
