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
"""Extensions page — main menu entry under Keyboard.

The launch gestures for the gaze tools (launcher / clipboard history /
task switcher) are bound here with the SAME binding UI as the Mouse-
binding page (FrameSelectGesture over the shared mouse_bindings store,
restricted to the extension action subset). Each tool also gets an
"Options" button for its grid size and in-tool gesture map."""

import logging
import tkinter

import customtkinter

import src.shape_list as shape_list
from src.gui.pages.page_select_gestures import FrameSelectGesture
from src.gui.frames.safe_disposable_frame import SafeDisposableScrollableFrame
from src.i18n import t

logger = logging.getLogger("PageExtensions")


class PageExtensions(SafeDisposableScrollableFrame):

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.is_active = False
        # White background for the whole Extensions page (per request).
        self.configure(fg_color="white")

        top = customtkinter.CTkLabel(master=self, text=t("Extensions"))
        top.cget("font").configure(size=24, weight="bold")
        top.grid(row=0, column=0, padx=20, pady=(10, 2), sticky="nw")

        # Launch-gesture binding UI — identical to the Mouse-binding page,
        # restricted to the gaze launcher / clipboard / task-switcher
        # actions.
        self.inner_frame = FrameSelectGesture(
            self, action_keys=shape_list.EXTENSION_ACTION_KEYS,
            logger_name="FrameExtGesture")
        self.inner_frame.configure(fg_color="white")
        self.inner_frame.grid(row=2, column=0, padx=5, pady=(4, 4),
                              sticky="nw")

        # (The per-tool "Options" list that used to sit here is gone: every
        # binding row above already carries its own always-visible Action
        # options button, which opens the very same popups.)

    # ---------------------------------------------------------------- #

    def enter(self):
        super().enter()
        self.inner_frame.enter()

    def leave(self):
        super().leave()
        self.inner_frame.leave()
        self.inner_frame.shared_dropdown.hide_dropdown()

    def refresh_profile(self):
        self.inner_frame.inner_refresh_profile()

    def destroy(self):
        super().destroy()
        self.inner_frame.destroy()
