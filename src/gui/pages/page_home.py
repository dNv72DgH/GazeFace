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
"""Home page — two columns: camera selection (left) and profile
selection / management (right).

The camera page and the profile menu entry were removed from the left
nav; both live here now. The camera column embeds the existing
PageSelectCamera (live preview + camera radio list). The profile column
is the existing profile editor list extended with click-to-activate."""

import logging
from functools import partial

import customtkinter

from src.config_manager import ConfigManager
from src.gui.frames.frame_profile_editor import ItemProfileEditor
from src.gui.frames.safe_disposable_frame import SafeDisposableFrame
from src.gui.pages.page_select_camera import PageSelectCamera
from src.i18n import t

logger = logging.getLogger("PageHome")

ACCENT = "#D9622B"
ACCENT_SOFT = "#F7E8DF"     # accent 10% over white


class HomeProfileList(ItemProfileEditor):
    """Profile list with click-to-activate on top of the editor's
    rename / duplicate / delete rows. The active profile row is tinted
    with the accent colour."""

    def create_div(self, row, div_id, profile_name):
        div = super().create_div(row, div_id, profile_name)
        div["wrap_label"].configure(cursor="hand2")
        # Bind BOTH the row background and the name entry: the (disabled)
        # CTkEntry sits on top of the label and swallows clicks on the
        # name text — the most natural click target.
        div["wrap_label"].bind(
            "<Button-1>", partial(self._on_row_click, div))
        div["entry"].bind(
            "<Button-1>", partial(self._on_row_click, div))
        self._paint_div(div)
        return div

    def _on_row_click(self, div, _event=None):
        if div.get("is_editing"):
            return
        name = div["profile_name"]
        if name == ConfigManager().curr_profile_name.get():
            return
        logger.info("Switch profile -> %s", name)
        ConfigManager().switch_profile(name)
        self.main_gui_callback("refresh_profiles")
        self.paint_active()

    def _paint_div(self, div):
        active = (div["profile_name"]
                  == ConfigManager().curr_profile_name.get())
        color = ACCENT_SOFT if active else "white"
        try:
            div["wrap_label"].configure(fg_color=color)
            div["entry"].configure(fg_color=color)
        except Exception:
            pass

    def paint_active(self):
        for div in self.divs.values():
            self._paint_div(div)

    def refresh_frame(self):
        super().refresh_frame()
        self.paint_active()

    def finish_rename(self, div, event):
        super().finish_rename(div, event)
        self._paint_div(div)


# Starting top padding of the Profiles row (logical px); _align_titles
# corrects it to the camera heading's real position once laid out.
PROFILE_ROW_PAD = 35

# Height of the GazeFace wordmark at the top of the page (logical px).
LOGO_H = 44


class PageHome(SafeDisposableFrame):

    def __init__(self, master, root_callback: callable, **kwargs):
        super().__init__(master, **kwargs)
        logging.info("Create PageHome")
        self.root_callback = root_callback

        # Row 0: the GazeFace wordmark across both columns. Rows 1-2: the
        # camera column and the profile heading + list, as before.
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1, uniform="home")
        self.grid_columnconfigure(1, weight=1, uniform="home")

        self._logo_img = None
        try:
            from PIL import Image
            from src import paths
            im = Image.open(paths.install_dir() / "assets" / "images"
                            / "wordmark.png")
            h = LOGO_H
            w = round(h * im.width / im.height)
            self._logo_img = customtkinter.CTkImage(
                light_image=im, dark_image=im, size=(w, h))
        except Exception as e:
            logging.error("home logo not loaded: %s", e)
        self._logo = None
        self._logo_pad = 44
        if self._logo_img is not None:
            # Left edge lines up with the Camera heading below it;
            # _align_titles() corrects this starting value by measurement.
            self._logo = customtkinter.CTkLabel(self, text="",
                                                image=self._logo_img)
            self._logo.grid(row=0, column=0, columnspan=2,
                            padx=(self._logo_pad, 16), pady=(18, 0),
                            sticky="w")

        # --- column 1: camera selection (the embedded page brings its own
        # "Camera" title, so no extra heading here) ------------------------
        self.camera = PageSelectCamera(master=self,
                                       logger_name="home_camera")
        self.camera.grid(row=1, column=0, rowspan=2, padx=(16, 8),
                         pady=(12, 16), sticky="nsew")

        # --- column 2: profile selection & management ---------------------
        prow = customtkinter.CTkFrame(self, fg_color="transparent")
        # Same title font as the Camera column's heading, and the same
        # height: _align_titles() measures where the camera heading
        # actually landed and moves this row to match. (A hand-computed
        # 28 px lived here and was off by ~7 px - the camera heading sits
        # inside a scrollable frame whose internal spacing is not what the
        # arithmetic assumed.) The title is pinned to the row's top edge so
        # the taller Add button does not centre it lower.
        self._prow_pad = PROFILE_ROW_PAD
        prow.grid(row=1, column=1, padx=(12, 24), pady=(self._prow_pad, 2),
                  sticky="new")
        self._prow = prow
        prof_title = customtkinter.CTkLabel(prow, text=t("Profiles"))
        prof_title.cget("font").configure(size=24, weight="bold")
        prof_title.pack(side="left", padx=(20, 0), pady=0, anchor="n")
        self._prof_title = prof_title
        self.after(0, self._align_titles)
        self.bind("<Map>", lambda _e: self._queue_align(), add="+")
        # Re-measure whenever either reference moves or resizes (the
        # logo's image is only sized on its first draw, and the camera
        # column settles after its preview appears). Once they agree, a
        # pass changes nothing and no further events follow.
        for w in (self._logo, getattr(self.camera, "title_label", None),
                  prof_title):
            if w is not None:
                w.bind("<Configure>", lambda _e: self._queue_align(),
                       add="+")
        add_btn = customtkinter.CTkButton(
            prow, text="＋ " + t("Add profile"), width=150, height=34,
            corner_radius=10, border_width=0,
            fg_color=ACCENT, hover_color="#C6521F", text_color="white",
            command=self._add_profile)
        add_btn.cget("font").configure(weight="bold")
        add_btn.pack(side="right")
        # (The "Click a profile to use it." caption was removed.)

        self.profiles = HomeProfileList(
            owner_frame=self, top_level=self,
            main_gui_callback=root_callback)
        self.profiles.grid(row=2, column=1, padx=(8, 16), pady=(0, 16),
                           sticky="nsew")

        # One scrollbar for the whole page. Both columns are independent
        # scrollable frames, so each shipped its own bar — two bars for
        # one screenful, side by side, disagreeing about where "the page"
        # is. Hide the camera column's and let the profile column's drive
        # both canvases. (Safe: PageSelectCamera never calls
        # refresh_scrollbar(), which would re-grid the bar it removes.)
        try:
            self.camera._scrollbar.grid_remove()
            self.profiles._scrollbar.configure(command=self._yview_both)
        except Exception as e:
            logging.error("home scroll link failed: %s", e)

    def _yview_both(self, *args):
        for frame in (self.profiles, self.camera):
            try:
                frame._parent_canvas.yview(*args)
            except Exception:
                pass

    def _queue_align(self):
        """Coalesce bursts of layout events into one alignment pass."""
        if getattr(self, "_align_pending", False):
            return
        self._align_pending = True

        def run():
            self._align_pending = False
            self._align_titles()
        self.after_idle(run)

    def _align_titles(self, tries=8):
        """Put the Profiles heading at exactly the Camera heading's height.

        Measured, not computed: both headings are placed, their screen
        positions compared, and the Profiles row's top padding corrected by
        the difference. The first measurement can be taken before the page
        has finished laying out, so it re-checks a few times until the two
        agree (the correction is linear, so it settles in one or two passes).
        Runs again whenever the page is shown.
        """
        try:
            cam = getattr(self.camera, "title_label", None)
            if cam is None:
                return
            if not (cam.winfo_ismapped() and self._prof_title.winfo_ismapped()):
                delta = None
            else:
                delta = cam.winfo_rooty() - self._prof_title.winfo_rooty()
            if delta is not None and abs(delta) >= 1:
                # grid() padding is in logical px; screen positions are not.
                scale = self._get_widget_scaling() or 1.0
                self._prow_pad = max(0, round(self._prow_pad + delta / scale))
                # Through CTk's grid(), which scales its arguments - plain
                # grid_configure() would take them as physical pixels.
                self._prow.grid(row=1, column=1, padx=(12, 24),
                                pady=(self._prow_pad, 2), sticky="new")
            dx = None
            if self._logo is not None and delta is not None                     and self._logo.winfo_ismapped():
                dx = cam.winfo_rootx() - self._logo.winfo_rootx()
                if abs(dx) >= 1:
                    scale = self._get_widget_scaling() or 1.0
                    self._logo_pad = max(0, round(self._logo_pad + dx / scale))
                    self._logo.grid(row=0, column=0, columnspan=2,
                                    padx=(self._logo_pad, 16), pady=(18, 0),
                                    sticky="w")
            unsettled = (delta is None or abs(delta) >= 1
                         or (dx is not None and abs(dx) >= 1))
            if unsettled and tries > 0:
                self.after(50, lambda: self._align_titles(tries - 1))
        except Exception as e:
            logging.debug("title alignment skipped: %s", e)

    def _add_profile(self):
        ConfigManager().add_profile()
        self.profiles.refresh_frame()

    # --- lifecycle ------------------------------------------------------ #

    def enter(self):
        super().enter()
        try:
            self.camera.enter()
        except Exception as e:
            logger.error("camera enter failed: %s", e)
        try:
            self.profiles.enter()
        except Exception as e:
            logger.error("profiles enter failed: %s", e)

    def leave(self):
        super().leave()
        try:
            self.camera.leave()
        except Exception:
            pass
        try:
            self.profiles.leave()
        except Exception:
            pass

    def refresh_profile(self):
        try:
            self.camera.refresh_profile()
        except Exception as e:
            logger.error("camera refresh failed: %s", e)
        try:
            self.profiles.refresh_frame()
        except Exception:
            pass
