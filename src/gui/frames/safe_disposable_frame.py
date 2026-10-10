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

import customtkinter


class SafeDisposableFrame(customtkinter.CTkFrame):

    def __init__(self, master, logger_name: str = "", **kwargs):
        super().__init__(master, **kwargs)
        self.is_active = False
        self.is_destroyed = False
        self.canvas_im = None
        self.new_photo = None
        self.placeholder_im = None
        self.logger = logging.getLogger(logger_name)

    def enter(self):
        self.logger.info("enter")
        self.is_active = True

    def leave(self):
        self.logger.info("leave")
        self.is_active = False

    def destroy(self):
        self.logger.info("destroy")
        self.is_active = False
        self.is_destroyed = True
        self.canvas_im = None
        self.new_photo = None
        self.placeholder_im = None


def style_scrollbar(scrollable):
    """Apply the app's one scrollbar look to any CTkScrollableFrame.

    Thick, always visible, and visually prominent, so users can see at a
    glance that a panel scrolls. Lives here rather than in the base class
    so the plain CTkScrollableFrames (panel bodies, dropdown lists) can
    match the pages instead of falling back to the 16px theme default.
    """
    try:
        scrollable._scrollbar.configure(
            width=12,
            button_color="#9AA0A6",
            button_hover_color="#5F6368",
            fg_color="#E1E4EA",
        )
        # Keep the scrollbar gridded regardless of content height — the
        # upstream CTkScrollableFrame hides it dynamically, which leaves
        # users unable to tell whether a panel scrolls.
        scrollable._scrollbar.grid()
    except Exception:
        pass


class SafeDisposableScrollableFrame(customtkinter.CTkScrollableFrame):

    def __init__(self, master, logger_name: str = "", **kwargs):
        super().__init__(master, **kwargs)
        self.is_active = False
        self.is_destroyed = False
        self.canvas_im = None
        self.new_photo = None
        self.placeholder_im = None
        self.logger = logging.getLogger(logger_name)

        style_scrollbar(self)

    def refresh_scrollbar(self):
        # Override the auto-hide behaviour from upstream: we want the
        # scrollbar always visible regardless of whether content currently
        # overflows.
        try:
            self._scrollbar.grid()
        except Exception:
            pass

    def enter(self):
        self.logger.info("enter")
        self.is_active = True

    def leave(self):
        self.logger.info("leave")
        self.is_active = False

    def destroy(self):
        self.logger.info("destroy")
        self.is_active = False
        self.is_destroyed = True
        self.canvas_im = None
        self.new_photo = None
        self.placeholder_im = None
