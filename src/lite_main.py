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
"""GazeFace lite: the copy Windows starts on the secure desktop.

The sign-in / lock screen and UAC prompts run on the "Winlogon" desktop,
which has a small, fixed heap for window and menu objects; the full app
ran out of it while building its settings pages ("No more menus can be
allocated"). On the sign-in screen this process also runs as SYSTEM.
So this is a separate, minimal entry point:

  * head tracking, gesture clicks, face control on/off, Gaze Jump, Gaze
    move (tk arrows, no GPU), pointer reset / next monitor;
  * one status banner - no settings window, no pages, no Qt overlay,
    no sub-monitor, no launcher / task switcher / clipboard history, no
    keyboard bindings;
  * settings are read, never written;
  * nothing is ever started as a new process, and the app never restarts
    itself (a replacement would come up as the full app).

It leaves when its desktop is no longer the one in front (signed in,
prompt answered).
"""

import logging
import os
import sys
import threading
import time

from src import paths

logger = logging.getLogger("Lite")

TICK_MS = 16
STATUS_MS = 500
# Status banner, top centre: big enough to read from where the user sits.
# Opaque and not layered - the secure desktop does not draw layered
# windows properly.
BANNER_W = 520
BANNER_H = 96
BANNER_TOP = 24
BANNER_BG = "#202124"
FG = "#FFFFFF"
# State colours: tracking / face control off / no face or camera.
COL_OK = "#34A853"
COL_OFF = "#9AA0A6"
COL_NOFACE = "#EA4335"


def _setup_logging():
    from src.rotating_log import ResilientRotatingFileHandler
    fmt = "%(asctime)s %(levelname)s %(name)s: %(funcName)s: %(message)s"
    # A file of its own: next to the everyday copy's log.txt when this
    # runs as the user (UAC prompt), and easy to find on its own when it
    # runs as SYSTEM (sign-in screen, ProgramData).
    h = ResilientRotatingFileHandler(
        str(paths.user_data_dir() / "log_secure.txt"), mode="a",
        maxBytes=1024 * 1024, backupCount=2, encoding="utf-8")
    logging.basicConfig(format=fmt, level=logging.INFO, handlers=[h])


def _read_only_config():
    """Keep every settings write in memory only.

    The only runtime writer left in lite mode is the implicit gaze
    re-calibration; on the sign-in screen the settings are a copy, and on
    a UAC prompt they are the everyday copy's own files, which a second
    writer must not touch.
    """
    from src.config_manager import ConfigManager
    cm = ConfigManager
    for name in ("write_config_file", "write_mouse_bindings_file",
                 "write_keyboard_bindings_file", "write_extensions",
                 "write_app_setting", "add_profile", "remove_profile",
                 "copy_profile", "rename_profile"):
        if hasattr(cm, name):
            setattr(cm, name,
                    lambda self, *a, _n=name, **k:
                    logger.debug("settings write skipped (lite): %s", _n))


def _no_restart():
    """Recovery that would restart the app is turned into a log line.

    A restart spawns the FULL app, which cannot run here. The camera
    loop's in-process recovery (reopen / rescan) still runs.
    """
    from src import restart

    def declined(reason):
        logger.warning("restart not available in lite mode: %s", reason)
        return False
    restart.request_restart = declined


class Lite:

    def __init__(self, root):
        self.root = root
        self._closing = False
        self.dot = None
        self.canvas = None
        self._dot_id = None

    # ---- start / stop -------------------------------------------------- #

    def start(self):
        from src.camera_manager import CameraManager
        from src.config_manager import ConfigManager
        from src.controllers import Keybinder, MouseController
        from src.detectors import FaceMesh
        from src import lite_mode

        lite_mode.ACTIVE = True
        cm = ConfigManager()
        cm.start()
        # No GPU: Gaze move and the jump flourish take their tk paths.
        for d in (cm.config, getattr(cm, "temp_config", None)):
            if isinstance(d, dict):
                d["qt_overlay"] = False
        # Only the actions lite mode runs; no keyboard bindings at all.
        cm.mouse_bindings = lite_mode.filter_mouse_bindings(cm.mouse_bindings)
        cm.temp_mouse_bindings = dict(cm.mouse_bindings)
        cm.keyboard_bindings = {}
        cm.temp_keyboard_bindings = {}
        logger.info("lite bindings: %s", {
            k: v[1] for k, v in cm.mouse_bindings.items()})

        CameraManager().start()
        MouseController().start()
        MouseController().set_tk_root(self.root)
        Keybinder().start()
        FaceMesh().start()
        # Face control on: this copy exists to be used right away.
        MouseController().set_active(True)

        self._build_dot()
        self.root.after(TICK_MS, self._tick)
        self.root.after(STATUS_MS, self._status)

        from src import desktop_guard
        desktop_guard.watch_secure_desktop(
            lambda: self.root.after(0, self.stop))

    def stop(self):
        if self._closing:
            return
        self._closing = True
        logger.info("lite: exiting")
        # Hard backstop: a wedged device release must not keep a SYSTEM
        # process alive on a desktop nobody is looking at.
        threading.Thread(target=lambda: (time.sleep(4.0), os._exit(0)),
                         daemon=True).start()
        from src.camera_manager import CameraManager
        from src.controllers import Keybinder, MouseController
        from src.detectors import FaceMesh
        for part in (Keybinder, MouseController, FaceMesh, CameraManager):
            try:
                part().destroy()       # Keybinder first: lifts held input
            except Exception as e:
                logger.error("lite: %s teardown: %s", part.__name__, e)
        os._exit(0)

    # ---- pipeline ------------------------------------------------------ #

    def _tick(self):
        if self._closing:
            return
        try:
            from src.camera_manager import CameraManager
            from src.controllers import Keybinder, MouseController
            from src.detectors import FaceMesh
            FaceMesh().detect_frame(CameraManager().get_raw_frame())
            if FaceMesh().get_landmarks() is not None:
                MouseController().act(FaceMesh().get_track_loc())
                Keybinder().act(FaceMesh().get_blendshapes())
        except Exception as e:
            logger.error("lite tick: %s", e, exc_info=e)
        self.root.after(TICK_MS, self._tick)

    # ---- status banner ---------------------------------------------------- #

    def _build_dot(self):
        import tkinter as tk
        sw = self.root.winfo_screenwidth()
        top = tk.Toplevel(self.root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(bg=BANNER_BG)
        top.geometry("%dx%d+%d+%d" % (BANNER_W, BANNER_H,
                                      (sw - BANNER_W) // 2, BANNER_TOP))
        c = tk.Canvas(top, width=BANNER_W, height=BANNER_H, bg=BANNER_BG,
                      highlightthickness=0, bd=0)
        c.pack()
        # A coloured bar down the left edge, the state in words beside it.
        self._bar = c.create_rectangle(0, 0, 18, BANNER_H, fill=COL_OFF,
                                       outline="")
        c.create_text(40, 26, anchor="w", text="GazeFace", fill=FG,
                      font=("Segoe UI", 16, "bold"))
        self._state_txt = c.create_text(40, 64, anchor="w", text="",
                                        fill=COL_OFF,
                                        font=("Segoe UI", 22, "bold"))
        self.dot, self.canvas = top, c
        self._no_activate(top)

    @staticmethod
    def _no_activate(top):
        """Never take the focus from the sign-in box / UAC prompt.

        Not click-through: that needs a layered window, and layered
        windows are exactly what this desktop does not draw properly.
        The banner is small and out of the way, so it can stay
        clickable."""
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.WinDLL("user32", use_last_error=True)
            u.GetParent.restype = wintypes.HWND
            u.GetParent.argtypes = [wintypes.HWND]
            u.GetWindowLongW.restype = ctypes.c_long
            u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
            u.SetWindowLongW.restype = ctypes.c_long
            u.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int,
                                         ctypes.c_long]
            top.update_idletasks()
            hwnd = u.GetParent(top.winfo_id()) or top.winfo_id()
            GWL_EXSTYLE = -20
            ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
            u.SetWindowLongW(hwnd, GWL_EXSTYLE,
                             ex | 0x08000000)       # WS_EX_NOACTIVATE
        except Exception as e:
            logger.debug("status dot no-activate: %s", e)

    def _status(self):
        if self._closing:
            return
        try:
            from src.controllers import MouseController
            from src.detectors import FaceMesh
            from src.i18n import t
            if not MouseController().is_active.get():
                col, msg = COL_OFF, t("Face control off")
            elif FaceMesh().get_landmarks() is None:
                col, msg = COL_NOFACE, t("Face not detected")
            else:
                col, msg = COL_OK, t("Face control on")
            self.canvas.itemconfig(self._bar, fill=col)
            self.canvas.itemconfig(self._state_txt, text=msg, fill=col)
            self.dot.attributes("-topmost", True)
        except Exception as e:
            logger.debug("status dot: %s", e)
        self.root.after(STATUS_MS, self._status)


def main() -> int:
    paths.ensure_user_data()
    _setup_logging()
    from src import desktop_guard
    logger.info("===== GazeFace lite starting on desktop %s (exe=%s) =====",
                desktop_guard.own_desktop(), sys.executable)
    from src import single_instance
    single_instance.acquire(paths.configs_dir(), quiet=True,
                            scope=desktop_guard.own_desktop())
    _read_only_config()
    _no_restart()

    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    app = Lite(root)
    try:
        app.start()
        root.mainloop()
    except BaseException as e:
        logger.critical("lite failed: %s", e, exc_info=True)
    app.stop()
    return 0
