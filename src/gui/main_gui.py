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
import os
import time
import tkinter as tk

import customtkinter
from PIL import Image

import src.gui.frames as frames
import src.gui.pages as pages
import src.ui_heartbeat as ui_heartbeat
from src.gui.panel_host import PanelHost, set_panel_host
from src.config_manager import ConfigManager
from src.controllers import MouseController
from src.i18n import t

customtkinter.set_appearance_mode("light")
customtkinter.set_default_color_theme("assets/themes/google_theme.json")
# Before any window exists: CustomTkinter would otherwise stamp its own
# logo on every window 200 ms after creating it (see app_icon).
from src.gui import app_icon  # noqa: E402
app_icon.install()

logger = logging.getLogger("MainGUi")


class MainGui():

    def __init__(self, tk_root):
        logger.info("Init MainGui")
        super().__init__()
        self.tk_root = tk_root

        self.tk_root.geometry("1024x658")
        # Name only. The version used to be part of the title, which put
        # a string that changes every build into the one piece of chrome
        # the user drags, reads in Alt-Tab, and identifies the app by.
        # ConfigManager().version is still the version of record.
        self.tk_root.title("GazeFace")
        app_icon.apply_default(self.tk_root)
        self.tk_root.resizable(width=False, height=False)
        # No system menu from a stray right click on the title bar.
        from src.gui import no_caption_menu
        no_caption_menu.install(self.tk_root)
        # Gaze Jump can snap onto GazeFace's own buttons too (UI
        # Automation cannot see Tk-drawn controls).
        from src import click_snap
        from src.gui import local_targets
        click_snap.set_local_provider(local_targets.provider(self.tk_root))
        # Daily update check, and the release notes after an update.
        from src import updater
        updater.start(self.tk_root)
        # Start-up: say so when the camera works but no face is found.
        self._noface_notice = None
        self.tk_root.after(1000, self._noface_watch)
        # Restore the last on-screen position (saved on exit). Size is
        # fixed, so only the +x+y offset is remembered.
        self._restore_window_pos()

        # Give MouseController a handle on the main tk root so the
        # pointer-jump worker can spawn its visual feedback Toplevel
        # via tk_root.after() (tkinter widgets are main-thread only).
        try:
            MouseController().set_tk_root(self.tk_root)
        except Exception as e:
            logger.error("Failed to register tk_root with "
                         "MouseController: %s", e)

        # Memory / leak diagnostic logging. Every 30 s, record process RSS
        # plus the live Tcl image count and pending after-callback count
        # (the two common tkinter leaks) to both the app log and a CSV in
        # the temp dir, so a build that "gets heavier over time" can be
        # traced even though the windowed exe has no visible console.
        self._exit_dlg = None

        self._mem_proc = None
        try:
            import psutil
            self._mem_proc = psutil.Process()
        except Exception:
            self._mem_proc = None
        try:
            import tempfile
            import os
            self._mem_log_path = os.path.join(
                tempfile.gettempdir(), "GazeFace_memlog.csv")
            if not os.path.exists(self._mem_log_path):
                with open(self._mem_log_path, "w", encoding="utf-8") as fh:
                    fh.write("epoch,rss_mb,tcl_images,after_cbs,threads\n")
        except Exception:
            self._mem_log_path = None
        try:
            self.tk_root.after(30000, self._mem_log_tick)
        except Exception:
            pass
        # Liveness beacon for the watchdog: if this stops ticking, the tk
        # main thread is blocked and face detection has stopped with it —
        # a state the camera/no-face probes cannot see (see ui_heartbeat).
        # Scheduled, not called: the first beat must land once mainloop is
        # actually running, so a slow UI build isn't read as a stall.
        try:
            self.tk_root.after(1000, self._ui_beat_tick)
        except Exception:
            pass

        self.tk_root.grid_rowconfigure(1, weight=1)
        self.tk_root.grid_columnconfigure(1, weight=1)

        # Create menu frame and assign callbacks
        self.frame_menu = frames.FrameMenu(self.tk_root,
                                           self.root_function_callback,
                                           height=360,
                                           width=200,
                                           logger_name="frame_menu")
        self.frame_menu.grid(row=0,
                             column=0,
                             padx=0,
                             pady=0,
                             sticky="nsew",
                             columnspan=1,
                             rowspan=3)

        # Create Preview frame
        self.frame_preview = frames.FrameCamPreview(self.tk_root,
                                                    self.cam_preview_callback,
                                                    logger_name="frame_preview")
        self.frame_preview.grid(row=1,
                                column=0,
                                padx=0,
                                pady=0,
                                sticky="sew",
                                columnspan=1)
        self.frame_preview.enter()

        # The wizard pages. Built up front, except on the secure desktop
        # (sign-in / lock screen, UAC prompt): Windows gives that desktop
        # a small, fixed heap for window and menu objects, and every
        # gesture row's dropdown is a menu - building all pages there
        # failed with "No more menus can be allocated". There only Home is
        # built, and any other page when it is first opened.
        root_cb = self.root_function_callback
        self._page_factories = {
            "page_home": lambda: pages.PageHome(
                master=self.tk_root, logger_name="page_home",
                root_callback=root_cb),
            "page_cursor": lambda: pages.PageCursor(
                master=self.tk_root, logger_name="page_cursor"),
            "page_gestures": lambda: pages.PageSelectGestures(
                master=self.tk_root, logger_name="page_gestures"),
            "page_keyboard": lambda: pages.PageKeyboard(
                master=self.tk_root, logger_name="page_keyboard"),
            "page_advanced": lambda: pages.PageAdvanced(
                master=self.tk_root, logger_name="page_advanced"),
            "page_extensions": lambda: pages.PageExtensions(
                master=self.tk_root, logger_name="page_extensions"),
            "page_bookmarks": lambda: pages.PageBookmarks(
                master=self.tk_root, logger_name="page_bookmarks"),
        }
        self.pages = {}
        # (The secure desktop never gets here - run_app hands it to the
        # lite mode - so every page is built up front.)
        for name in self._page_factories:
            self._build_page(name)

        self.page_names = list(self._page_factories.keys())
        self.curr_page_name = None

        self.change_page(self._startup_page())

        # Profile UI
        self.frame_profile_switcher = frames.FrameProfileSwitcher(
            self.tk_root, main_gui_callback=self.root_function_callback)
        self.frame_profile_editor = frames.FrameProfileEditor(
            self.tk_root, main_gui_callback=self.root_function_callback)

        # In-window panel stack (replaces popup Toplevels). Hidden until a
        # screen pushes onto it.
        self.panel_host = PanelHost(self.tk_root)
        self.panel_host.grid(row=0, column=1, rowspan=2, padx=5, pady=5,
                             sticky="nsew")
        self.panel_host.grid_remove()
        self.panel_host.on_visible = self._on_panel_visible
        set_panel_host(self.panel_host)

        # First launch: once the window is up, offer the calibration.
        self.tk_root.after(1500, self._maybe_first_calibration)

    def _ui_beat_tick(self):
        """Stamp the tk-main-thread liveness beacon once a second."""
        try:
            ui_heartbeat.beat()
        except Exception:
            pass
        # Also publish it, less often, for the out-of-process crash
        # sentinel: that is how it tells a genuinely running instance
        # from a wedged leftover before deciding whether to relaunch.
        try:
            now = time.time()
            if now - getattr(self, "_sentinel_hb_ts", 0.0) >= 15.0:
                self._sentinel_hb_ts = now
                from src import sentinel
                sentinel.note_heartbeat(os.getpid())
        except Exception:
            pass
        try:
            self.tk_root.after(1000, self._ui_beat_tick)
        except Exception:
            pass

    def _mem_log_tick(self):
        """Append one RSS / Tcl-image / after-callback sample. Reschedules
        itself every 30 s. See __init__ for why."""
        try:
            rss = (self._mem_proc.memory_info().rss / (1024.0 * 1024.0)
                   if self._mem_proc is not None else -1.0)
            try:
                n_img = len(self.tk_root.tk.call("image", "names"))
            except Exception:
                n_img = -1
            try:
                n_after = len(self.tk_root.tk.eval("after info").split())
            except Exception:
                n_after = -1
            import threading
            import time as _t
            n_thr = threading.active_count()
            # The overlay is a separate process and the only one doing GPU
            # work, so its own footprint — not ours — is what grows over a
            # long session. Track it here so the periodic recycle can be
            # shown to actually give the memory back.
            ov_rss = -1.0
            try:
                from src.gui.qt_overlay.qt_gaze_grid import OverlayClient
                pid = OverlayClient.instance().overlay_pid()
                if pid:
                    import psutil
                    ov_rss = (psutil.Process(pid).memory_info().rss
                              / (1024.0 * 1024.0))
            except Exception:
                ov_rss = -1.0
            logger.info("MEMLOG rss=%.1fMB overlay=%.1fMB images=%d "
                        "after=%d threads=%d",
                        rss, ov_rss, n_img, n_after, n_thr)
            if getattr(self, "_mem_log_path", None):
                try:
                    with open(self._mem_log_path, "a",
                              encoding="utf-8") as fh:
                        fh.write("%.0f,%.1f,%d,%d,%d,%.1f\n" % (
                            _t.time(), rss, n_img, n_after, n_thr, ov_rss))
                except Exception:
                    pass
        except Exception as e:
            logger.error("mem log failed: %s", e)
        try:
            self.tk_root.after(30000, self._mem_log_tick)
        except Exception:
            pass

    def root_function_callback(self, function_name, args: dict = {}, **kwargs):
        logger.info(f"root_function_callback {function_name} with {args}")

        # Basic page navigate
        if function_name == "change_page":
            self.change_page(args["target"])
            self.frame_menu.set_tab_active(tab_name=args["target"])

        # Gaze calibration (left-menu action item)
        elif function_name == "start_gaze_calibration":
            from src.gui.gaze_calibrator import GazeCalibrator
            GazeCalibrator(self.tk_root).start()

        elif function_name == "restart_app":
            # Deliberate user action: bypass restart.request_restart's
            # loop budget entirely. That budget exists to stop a fault
            # from restarting forever; a person clicking Restart is not
            # that, and must never be told "no" or have their click
            # trigger a config rollback.
            from src import app_status
            from src.task_killer import TaskKiller
            logger.info("Restart requested from the menu")
            app_status.set_error("Restarting", "restarting")
            TaskKiller().exit(restart=True)

        # Profiles
        elif function_name == "show_profile_switcher":
            self.frame_profile_switcher.enter()
        elif function_name == "show_profile_editor":
            self.frame_profile_editor.enter()

        elif function_name == "refresh_profiles":
            # A new or switched-to profile may have no calibration yet.
            self.tk_root.after(800, self._maybe_first_calibration)
            logger.info("refresh_profile")
            # Only pages that exist: one not built yet reads the new
            # profile when it is first opened.
            for name in ("page_gestures", "page_home", "page_cursor",
                         "page_keyboard", "page_advanced",
                         "page_extensions"):
                page = self.pages.get(name)
                if page is not None:
                    page.refresh_profile()   # home: camera + profiles

    def cam_preview_callback(self, function_name, args: dict, **kwargs):
        logger.info(f"cam_preview_callback {function_name} with {args}")

        if function_name == "toggle_switch":
            self.set_mediapipe_mouse_enable(new_state=args["switch_status"])

    # Seconds without a face (camera delivering) before the notice shows.
    NOFACE_NOTICE_S = 10

    def _noface_watch(self):
        """At start-up only: when the camera delivers but no face has been
        found for NOFACE_NOTICE_S, say so in the middle of the window, and
        take it away by itself once a face is found. Some cameras only
        find a still face after a big head movement."""
        try:
            from src.detectors import FaceMesh
            from src.gui.gaze_calibrator import camera_live
            fm = FaceMesh()
            if fm.last_face_mono:            # found: done for this run
                self._hide_noface_notice()
                return
            waited = time.monotonic() - (fm.started_mono or time.monotonic())
            if camera_live() and waited >= self.NOFACE_NOTICE_S:
                self._show_noface_notice()
            else:
                self._hide_noface_notice()
        except Exception as e:
            logger.error("no-face notice: %s", e)
            return
        self.tk_root.after(500, self._noface_watch)

    def _show_noface_notice(self):
        if self._noface_notice is not None:
            try:
                self._noface_notice.lift()
            except Exception:
                pass
            return
        f = customtkinter.CTkFrame(self.tk_root, fg_color="white",
                                   border_color="#E7E2DB", border_width=1,
                                   corner_radius=12)
        customtkinter.CTkLabel(
            f, wraplength=440, justify="left",
            text=t("No face can be detected. Try moving your head a lot or "
                   "changing your expression. A face may not be detected in "
                   "a very dark or very bright room.")).pack(
                padx=24, pady=20)
        f.place(relx=0.5, rely=0.5, anchor="center")
        f.lift()
        self._noface_notice = f
        logger.info("No-face notice shown")

    def _hide_noface_notice(self):
        if self._noface_notice is None:
            return
        try:
            self._noface_notice.destroy()
        except Exception:
            pass
        self._noface_notice = None
        logger.info("No-face notice removed")

    def set_mediapipe_mouse_enable(self, new_state: bool):
        if new_state:
            MouseController().set_active(True)
        else:
            MouseController().set_active(False)

    def _on_panel_visible(self, visible):
        # Hide the underlying page while an options panel is open (a page's
        # CTkScrollableFrame sitting under the panel can interfere).
        pg = self.pages.get(self.curr_page_name)
        if pg is None:
            return
        try:
            if visible:
                pg.grid_remove()
            else:
                pg.grid()
        except Exception:
            pass

    # --- first-run gaze calibration ---------------------------------- #

    def _maybe_first_calibration(self):
        """A profile with no gaze calibration: say so, then calibrate.

        Covers the first launch and a newly created profile alike - both
        start with no calibration, and Gaze Jump / Gaze move / the gaze
        tools are useless without one. Asked once per profile per run, so
        declining (closing the calibration) does not nag on every
        profile refresh.
        """
        try:
            cm = ConfigManager()
            key = str(getattr(cm, "curr_profile_path", "") or "")
            asked = self.__dict__.setdefault("_cal_prompted", set())
            if key in asked:
                return
            from src.controllers import Keybinder
            if Keybinder()._has_gaze_calibration():
                return
            from src.gui.gaze_calibrator import GazeCalibrator, camera_live
            if GazeCalibrator.is_active():
                return
            # No frames (yet): the camera may still be opening at start-up,
            # so look again shortly - for up to a minute. With no camera at
            # all, nothing is asked; the next start with one asks then.
            if not camera_live():
                tries = self.__dict__.get("_cal_wait", 0)
                if tries < 12:
                    self._cal_wait = tries + 1
                    self.tk_root.after(5000, self._maybe_first_calibration)
                else:
                    logger.info("No camera - first-run calibration not "
                                "offered")
                return
            asked.add(key)
        except Exception as e:
            logger.error("first-run calibration check failed: %s", e)
            return
        logger.info("No gaze calibration in this profile - prompting")

        def start():
            try:
                from src.gui.gaze_calibrator import GazeCalibrator
                # The notice below already asked.
                GazeCalibrator(self.tk_root).start(confirmed=True)
            except Exception as e:
                logger.error("first-run calibration failed: %s", e)
        from src.gui.popups._util import ConfirmDialog
        ConfirmDialog(self.tk_root).open(
            t("This is the first launch, so the gaze calibration runs now. "
              "On the next screen, look at each green dot in turn.\n"
              "If the gaze seems off, recalibrate often from the left "
              "menu."),
            on_ok=start, cancel=False)

    def _build_page(self, name):
        """Create page `name` (once) and put it in the page slot."""
        page = self.pages.get(name)
        if page is None:
            page = self._page_factories[name]()
            # All pages (home included) live right of the always-visible
            # menu.
            page.grid(row=0, column=1, padx=5, pady=5, sticky="nsew",
                      rowspan=2, columnspan=1)
            page.grid_remove()
            self.pages[name] = page
        return page

    def _startup_page(self) -> str:
        """Which page to show on launch: Home, or wherever the user was.

        App-wide (default.json), not per profile — it describes how the
        person likes the app to open, not anything about a profile.
        """
        try:
            cm = ConfigManager()
            if str(cm._read_app_setting("startup_page", "home")) != "last":
                return "page_home"
            last = str(cm._read_app_setting("last_page", "page_home"))
            # A page that no longer exists (renamed/removed in an update)
            # must not leave the window blank.
            return last if last in self._page_factories else "page_home"
        except Exception:
            return "page_home"

    def change_page(self, target_page_name: str):

        if self.curr_page_name == target_page_name:
            # Re-clicking the menu entry you are already on means "take me
            # back to the top of this section" — close whatever options
            # panel is stacked over it and scroll the page home. Without
            # this the click did nothing, leaving no way back out of a
            # deep options panel except the Back button.
            try:
                self.panel_host.clear()
            except Exception:
                pass
            self._scroll_page_top(target_page_name)
            return

        # Dismiss any open options panel when switching main pages.
        try:
            self.panel_host.clear()
        except Exception:
            pass

        if target_page_name in self._page_factories:
            self._build_page(target_page_name)
        for name, page in self.pages.items():
            if name == target_page_name:
                page.grid()
                self.pages[target_page_name].enter()
                self.curr_page_name = target_page_name

            else:
                page.grid_remove()
                page.leave()

        # Remember it for the "resume where I left off" start-up option.
        try:
            ConfigManager().write_app_setting("last_page", target_page_name)
        except Exception:
            pass

    def _scroll_page_top(self, name):
        """Scroll a page back to its top, whatever CTk wrapped it in.

        Pages are CTkScrollableFrames, whose scroll state lives on an
        internal canvas; some pages nest another one inside. Walk down to
        find whichever canvases exist rather than assuming the shape."""
        page = self.pages.get(name)
        if page is None:
            return

        def home(w, depth=0):
            cv = getattr(w, "_parent_canvas", None)
            if cv is not None:
                try:
                    cv.yview_moveto(0.0)
                except Exception:
                    pass
            if depth > 3:
                return
            try:
                for ch in w.winfo_children():
                    home(ch, depth + 1)
            except Exception:
                pass
        home(page)

    # --- window position memory ------------------------------------- #

    def _virtual_screen(self):
        """(x, y, w, h) of the whole virtual desktop (all monitors)."""
        try:
            import ctypes
            g = ctypes.windll.user32.GetSystemMetrics
            return (g(76), g(77), g(78), g(79))   # SM_*VIRTUALSCREEN
        except Exception:
            return (0, 0, self.tk_root.winfo_screenwidth(),
                    self.tk_root.winfo_screenheight())

    def _restore_window_pos(self):
        try:
            cm = ConfigManager()
            x = cm._read_app_setting("window_x", None)
            y = cm._read_app_setting("window_y", None)
            if x is None or y is None:
                return
            x, y = int(x), int(y)
            # Reject a position that would leave the window off every
            # monitor (a display that was unplugged since it was saved).
            vx, vy, vw, vh = self._virtual_screen()
            if not (vx - 50 <= x <= vx + vw - 100
                    and vy - 50 <= y <= vy + vh - 100):
                return
            self.tk_root.geometry(f"1024x658+{x}+{y}")
        except Exception as e:
            logger.error("restore window pos failed: %s", e)

    def save_window_pos(self):
        try:
            self.tk_root.update_idletasks()
            x = int(self.tk_root.winfo_x())
            y = int(self.tk_root.winfo_y())
            cm = ConfigManager()
            cm.write_app_setting("window_x", x)
            cm.write_app_setting("window_y", y)
        except Exception as e:
            logger.error("save window pos failed: %s", e)

    # --- exit confirmation ------------------------------------------ #

    def confirm_exit(self, on_yes):
        """Ask before quitting, in a small window CENTRED on the main one.

        Deliberately NOT a native messagebox: those centre on the screen,
        not on the app, and they run their own modal loop that blocks the
        tk main thread — and with it the whole face-gesture pipeline, which
        would leave a face-control-only user unable to click either answer.
        This Toplevel stays on the normal event loop.
        """
        if getattr(self, "_exit_dlg", None) is not None:
            try:
                self._exit_dlg.lift()
                self._exit_dlg.focus_force()
                return
            except Exception:
                self._exit_dlg = None

        W, H = 340, 150
        dlg = customtkinter.CTkToplevel(self.tk_root)
        self._exit_dlg = dlg
        dlg.title(t("Exit GazeFace"))
        dlg.resizable(False, False)
        dlg.transient(self.tk_root)
        self.tk_root.update_idletasks()
        x = self.tk_root.winfo_rootx() + (self.tk_root.winfo_width() - W) // 2
        y = self.tk_root.winfo_rooty() + (self.tk_root.winfo_height() - H) // 2
        dlg.geometry(f"{W}x{H}+{x}+{y}")

        customtkinter.CTkLabel(dlg, text=t("Quit GazeFace?"),
                               wraplength=W - 48).pack(padx=24, pady=(28, 16))
        brow = customtkinter.CTkFrame(dlg, fg_color="transparent")
        brow.pack()

        def dismiss():
            self._exit_dlg = None
            try:
                dlg.destroy()
            except Exception:
                pass

        def accept():
            dismiss()
            on_yes()

        customtkinter.CTkButton(brow, text=t("OK"), width=100,
                                command=accept).pack(side="left", padx=(0, 10))
        customtkinter.CTkButton(brow, text=t("Cancel"), width=100,
                                command=dismiss).pack(side="left")
        dlg.protocol("WM_DELETE_WINDOW", dismiss)
        dlg.bind("<Escape>", lambda _e: dismiss())
        try:
            dlg.lift()
            dlg.focus_force()
        except Exception:
            pass

    def del_main_gui(self):
        logger.info("Deleting MainGui instance")
        # try:
        self.frame_preview.leave()
        self.frame_preview.destroy()
        self.frame_menu.leave()
        self.frame_menu.destroy()
        for page in self.pages.values():
            page.leave()
            page.destroy()

        self.tk_root.quit()
        self.tk_root.destroy()
