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
"""Left navigation pane — text-only items in the FacePilot design.

Six entries (Home / Cursor / Mouse / Keyboard / Extensions / Advanced),
no icons, one shared font and indent. Camera and profile management moved
onto the Home page, so they no longer appear here.
"""

from functools import partial

import customtkinter

from src.gui.frames.safe_disposable_frame import SafeDisposableFrame
from src.i18n import t

# FacePilot design tokens.
BG = "#F6F4F0"
TEXT = "#201D1A"
ACCENT = "#D9622B"
ACCENT_SOFT = "#F3E5DC"     # accent 10% over the bg colour (tk has no alpha)

BTN_W = 176
BTN_H = 40

# (page id, i18n label) — top to bottom.
MENU_ITEMS = [
    ("page_home", "Home"),
    ("page_cursor", "Cursor"),
    ("page_gestures", "Mouse"),
    ("page_keyboard", "Keyboard"),
    ("page_extensions", "Extensions"),
    ("page_bookmarks", "Bookmarks"),
    ("page_advanced", "Advanced"),
]


class FrameMenu(SafeDisposableFrame):

    def __init__(self, master, master_callback: callable, **kwargs):
        super().__init__(master, **kwargs)

        # Rows: 1..len(MENU_ITEMS) are the nav entries, then the two
        # action items (calibration, restart), then the spacer.
        self.grid_rowconfigure(len(MENU_ITEMS) + 3, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.grid_propagate(False)
        # border_width 0 / corner_radius 0: the theme's 1 px CTkFrame border
        # and 12 px rounded corners drew a rule down the boundary between
        # this nav pane and the page area.
        self.configure(fg_color=BG, border_width=0, corner_radius=0)

        self.master_callback = master_callback

        self.btns = {}
        for idx, (page_id, label) in enumerate(MENU_ITEMS):
            btn = customtkinter.CTkButton(
                master=self,
                text=t(label),
                anchor="w",
                height=BTN_H,
                width=BTN_W,
                corner_radius=10,
                border_width=0,
                fg_color="transparent",
                hover_color=ACCENT_SOFT,
                text_color=TEXT,
                command=partial(self.master_callback,
                                function_name="change_page",
                                args={"target": page_id}))
            btn.cget("font").configure(size=14, weight="bold")
            btn.grid(row=idx + 1, column=0, padx=12, pady=2, sticky="new")
            self.btns[page_id] = btn

        # Gaze calibration — an ACTION item (runs the 16-point calibrator
        # directly, no page), styled exactly like the entries above and
        # placed right below Advanced.
        calib_btn = customtkinter.CTkButton(
            master=self,
            text=t("Gaze calibration"),
            anchor="w",
            height=BTN_H,
            width=BTN_W,
            corner_radius=10,
            border_width=0,
            fg_color="transparent",
            hover_color=ACCENT_SOFT,
            text_color=TEXT,
            command=partial(self.master_callback,
                            function_name="start_gaze_calibration",
                            args={}))
        calib_btn.cget("font").configure(size=14, weight="bold")
        calib_btn.grid(row=len(MENU_ITEMS) + 1, column=0, padx=12, pady=2,
                       sticky="new")

        # Restart — also an action item. Fires immediately with no
        # confirmation, per the user's request.
        restart_btn = customtkinter.CTkButton(
            master=self,
            text=t("Restart"),
            anchor="w",
            height=BTN_H,
            width=BTN_W,
            corner_radius=10,
            border_width=0,
            fg_color="transparent",
            hover_color=ACCENT_SOFT,
            text_color=TEXT,
            command=partial(self.master_callback,
                            function_name="restart_app",
                            args={}))
        restart_btn.cget("font").configure(size=14, weight="bold")
        restart_btn.grid(row=len(MENU_ITEMS) + 2, column=0, padx=12, pady=2,
                         sticky="new")

    def set_tab_active(self, tab_name: str):
        for page_id, btn in self.btns.items():
            if page_id == tab_name:
                btn.configure(fg_color=ACCENT_SOFT, text_color=ACCENT)
            else:
                btn.configure(fg_color="transparent", text_color=TEXT)
