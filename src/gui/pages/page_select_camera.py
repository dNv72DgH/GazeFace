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
import time
import tkinter

import customtkinter
from PIL import Image, ImageTk

from src.camera_manager import CameraManager
from src.config_manager import ConfigManager
from src.gui.frames.safe_disposable_frame import SafeDisposableScrollableFrame
from src.i18n import t

logger = logging.getLogger("PageSelectCamera")

CANVAS_WIDTH = 320
CANVAS_HEIGHT = 240

MAX_ROWS = 10
# The placeholder is also what shows for a moment at start-up and on a
# camera swap; only a placeholder that lasts this long means "no camera".
NO_CAMERA_AFTER_S = 2.5


class PageSelectCamera(SafeDisposableScrollableFrame):

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)

        # Single-column stack (title / preview / radio list) so the page
        # also fits when embedded in the Home page's half-width column.
        self.grid_rowconfigure(MAX_ROWS + 3, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Top text
        top_label = customtkinter.CTkLabel(master=self, text=t("Camera"))
        top_label.cget("font").configure(size=24, weight="bold")
        top_label.grid(row=0,
                       column=0,
                       padx=20,
                       pady=(10, 2),
                       sticky="nw")
        # The Home page lines its Profiles heading up with this one.
        self.title_label = top_label

        # Empty radio buttons
        self.radio_var = tkinter.IntVar(value=0)
        self.prev_radio_value = None
        self.radios = []

        # Camera canvas (below the title, above the radio list)
        self.placeholder_im = Image.new(
            "RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0))
        self.placeholder_im = ImageTk.PhotoImage(self.placeholder_im)
        self.canvas = tkinter.Canvas(master=self,
                                     width=CANVAS_WIDTH,
                                     height=CANVAS_HEIGHT,
                                     bg="#000000")
        self.canvas.grid(row=1,
                         column=0,
                         padx=20,
                         pady=10,
                         sticky="nw")

        # Label
        self.label = customtkinter.CTkLabel(master=self,
                                            text=t("Select a Camera"))
        self.label.cget("font").configure(size=16, weight="bold")
        self.label.grid(row=2, column=0, padx=20, pady=(10, 4), sticky="nw")

        # Set first image. Until a frame arrives the canvas is plain black,
        # and a camera that never delivers gets a message instead (see
        # page_loop).
        self.canvas_im = self.canvas.create_image(0,
                                                  0,
                                                  image=self.placeholder_im,
                                                  anchor=tkinter.NW,
                                                  state="hidden")
        self.no_cam_txt = self.canvas.create_text(
            CANVAS_WIDTH // 2, CANVAS_HEIGHT // 2,
            text=t("Camera not found.\nIt is not connected, or another "
                   "app is using it."),
            fill="#FFFFFF", width=CANVAS_WIDTH - 20, justify="center",
            font=("IBM Plex Sans JP", 10, "bold"), state="hidden")
        self._placeholder_since = None
        self.new_photo = None
        self.latest_camera_list = []

    def load_initial_config(self):
        """ Update radio buttons to match CameraManager
        """
        logger.info("Refresh radio buttons")
        for old_radio in self.radios:
            old_radio.destroy()

        new_camera_list = CameraManager().get_camera_list()
        logger.info(f"Get camera list {new_camera_list}")
        self.latest_camera_list = list(new_camera_list)
        radios = []
        for row_i, cam_id in enumerate(new_camera_list):

            radio = customtkinter.CTkRadioButton(master=self,
                                                 text="%s %s" % (
                                                     t("Camera"), cam_id),
                                                 command=self.radiobutton_event,
                                                 variable=self.radio_var,
                                                 value=cam_id)

            radio.grid(row=row_i + 3, column=0, padx=30, pady=8, sticky="w")
            radios.append(radio)

        # Set radio select
        target_id = ConfigManager().config["camera_id"]
        self.radios = radios
        for radio in self.radios:
            # By value, not by the (translated) label.
            if radio.cget("value") == target_id:
                radio.select()
                self.prev_radio_value = self.radio_var.get()
                logger.info(f"Set initial camera to {target_id}")
                break

    def radiobutton_event(self):
        # Open new camera.
        new_radio_value = self.radio_var.get()
        if new_radio_value == self.prev_radio_value:
            return
        logger.info(f"Change cameara: {new_radio_value}")
        CameraManager().pick_camera(new_radio_value)
        ConfigManager().set_temp_config("camera_id", new_radio_value)
        ConfigManager().apply_config()
        self.prev_radio_value = new_radio_value

    def page_loop(self):
        if self.is_destroyed:
            return

        if self.is_active:

            if CameraManager().showing_placeholder():
                now = time.monotonic()
                if self._placeholder_since is None:
                    self._placeholder_since = now
                no_cam = now - self._placeholder_since >= NO_CAMERA_AFTER_S
                self.canvas.itemconfig(self.canvas_im, state="hidden")
                self.canvas.itemconfig(
                    self.no_cam_txt,
                    state=("normal" if no_cam else "hidden"))
            else:
                self._placeholder_since = None
                frame_rgb = CameraManager().get_raw_frame()
                # Assign ref to avoid garbage collected
                self.new_photo = ImageTk.PhotoImage(
                    image=Image.fromarray(frame_rgb).resize((CANVAS_WIDTH,
                                                             CANVAS_HEIGHT)))
                self.canvas.itemconfig(self.canvas_im, image=self.new_photo,
                                       state="normal")
                self.canvas.itemconfig(self.no_cam_txt, state="hidden")
            self.canvas.update()

            self.after(ConfigManager().config["tick_interval_ms"],
                       self.page_loop)

    def _cameras_poll(self):
        """Rebuild the radio list when the async camera enumeration adds
        cameras after we first painted. On startup the Home page enters
        within the first second, racing the enumeration thread (one
        blocking cv2 open per index) — without this the list would stay
        empty/incomplete until the user left and re-entered the page."""
        if self.is_destroyed or not self.is_active:
            return
        try:
            lst = list(CameraManager().get_camera_list())
            if lst != self.latest_camera_list:
                logger.info("Camera list changed %s -> %s — refreshing",
                            self.latest_camera_list, lst)
                self.load_initial_config()
        except Exception as e:
            logger.error("camera poll failed: %s", e)
        self.after(700, self._cameras_poll)

    def enter(self):
        super().enter()
        self.load_initial_config()
        self.after(1, self.page_loop)
        self.after(700, self._cameras_poll)

    def refresh_profile(self):
        self.load_initial_config()
        new_camera_id = ConfigManager().config["camera_id"]
        CameraManager().pick_camera(new_camera_id)
