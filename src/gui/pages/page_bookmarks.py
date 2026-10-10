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
"""Bookmarks page — left-nav entry below Extensions.

Shows only the actions the user starred on the Mouse / Extensions pages,
using the exact same binding UI (FrameSelectGesture) so their favourite
settings sit in one short list. The row set is rebuilt whenever the
bookmark list changes or the page is entered."""

import logging

import customtkinter

from src.gui import bookmarks
from src.gui.pages.page_select_gestures import FrameSelectGesture
from src.gui.frames.safe_disposable_frame import SafeDisposableScrollableFrame
from src.i18n import t

logger = logging.getLogger("PageBookmarks")


class PageBookmarks(SafeDisposableScrollableFrame):

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.is_active = False
        self.configure(fg_color="white")

        top = customtkinter.CTkLabel(master=self, text=t("Bookmarks"))
        top.cget("font").configure(size=24, weight="bold")
        top.grid(row=0, column=0, padx=20, pady=(10, 2), sticky="nw")

        self.inner_frame = None
        self._rebuild()
        # Live-refresh when a star is toggled anywhere.
        bookmarks.add_listener(self._on_bookmarks_changed)

    def _rebuild(self):
        if self.inner_frame is not None:
            try:
                self.inner_frame.destroy()
            except Exception:
                pass
            self.inner_frame = None
        actions = bookmarks.load()
        if not actions:
            return
        self.inner_frame = FrameSelectGesture(
            self, action_keys=actions, logger_name="FrameBookmarkGesture")
        self.inner_frame.configure(fg_color="white")
        self.inner_frame.grid(row=2, column=0, padx=5, pady=(4, 4),
                              sticky="nw")
        if self.is_active:
            self.inner_frame.enter()

    def _on_bookmarks_changed(self):
        # Rebuild on the tk main thread (listener may fire from a widget
        # callback — safe — but keep it deferred to avoid destroying the
        # widget that is mid-callback).
        try:
            self.after(0, self._rebuild)
        except Exception:
            self._rebuild()

    # ---------------------------------------------------------------- #

    def enter(self):
        super().enter()
        # Reflect any stars toggled while the page was hidden.
        self._rebuild()
        if self.inner_frame is not None:
            self.inner_frame.enter()

    def leave(self):
        super().leave()
        if self.inner_frame is not None:
            self.inner_frame.leave()
            self.inner_frame.shared_dropdown.hide_dropdown()

    def refresh_profile(self):
        if self.inner_frame is not None:
            self.inner_frame.inner_refresh_profile()

    def destroy(self):
        super().destroy()
        if self.inner_frame is not None:
            self.inner_frame.destroy()
