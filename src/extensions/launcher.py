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
"""Gaze launcher — a gaze-selectable icon grid bound to a gesture.

Item types: key (single keystroke or a multi-step macro of
key/wait/text), action (mouse click at the pre-launch pointer), file
(open with the associated app, or run with args), sublauncher (descend
into another grid page, up to 10 levels), ext (open clipboard / task
switcher), calibrate (run gaze calibration), back (go up a level / exit
at the top).

Launcher gesture actions: confirm / back / exit / settings.
'settings' opens the registration popup for the cell under the gaze.
Pointer is frozen while open and released on close. Data is app-wide
(configs/extensions.json). Per-app items and icon extraction are added
in a later phase.
"""

import logging
import os
import subprocess
import threading
import time

import pydirectinput

from src import mouse_input
from src.config_manager import ConfigManager
from src.extensions.launcher_icons import icon_for_item as _icon_for_item

logger = logging.getLogger("Launcher")

MAX_DEPTH = 10
MODS = ("ctrl", "shift", "alt", "win")
_WINMAP = {"win": "winleft"}


def _ext():
    return ConfigManager().read_extensions() or {}


def _launcher_cfg():
    return (_ext().get("launcher", {}) or {})


def launcher_gestures():
    return dict(_launcher_cfg().get("gestures", {}) or {})


def _norm(v):
    """Normalise a cell value to a list of item slots (legacy single
    dicts -> [dict])."""
    if isinstance(v, dict):
        return [v]
    if isinstance(v, list):
        return list(v)
    return []


def _fg_app_name():
    """Foreground app's executable name (lowercased), or ''."""
    try:
        import win32gui
        import win32process
        import psutil
        hwnd = win32gui.GetForegroundWindow()
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return psutil.Process(pid).name().lower()
    except Exception:
        return ""



# Private, prototyped user32 for the helpers below. Never set prototypes
# on the shared ctypes.windll.user32 (it changes them for every module),
# and HWND results must be declared or they are truncated.
_u32 = None


def _user32():
    global _u32
    if _u32 is None:
        import ctypes
        from ctypes import wintypes
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.EnumWindows.restype = wintypes.BOOL
        # ONE callback type, shared: ctypes checks the callback against
        # this exact type object.
        u.enum_proto = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        u.EnumWindows.argtypes = [u.enum_proto, wintypes.LPARAM]
        u.IsWindowVisible.restype = wintypes.BOOL
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetCursorPos.restype = wintypes.BOOL
        u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        u.WindowFromPoint.restype = wintypes.HWND
        u.WindowFromPoint.argtypes = [wintypes.POINT]
        u.GetAncestor.restype = wintypes.HWND
        u.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
        u.GetClassNameW.restype = ctypes.c_int
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                    ctypes.c_int]
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetForegroundWindow.argtypes = []
        _u32 = u
    return _u32


def _process_has_visible_window(pid):
    """True if process `pid` owns a visible top-level window."""
    import ctypes
    from ctypes import wintypes
    u = _user32()
    found = []

    def cb(hwnd, _lp):
        owner = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and u.IsWindowVisible(hwnd):
            found.append(hwnd)
            return False                 # stop enumerating
        return True

    try:
        u.EnumWindows(u.enum_proto(cb), 0)
    except Exception:
        return False
    return bool(found)


def _describe_hwnd(u, hwnd):
    import ctypes
    from ctypes import wintypes
    if not hwnd:
        return "none"
    buf = ctypes.create_unicode_buffer(64)
    u.GetClassNameW(hwnd, buf, 64)
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name = "?"
    try:
        import psutil
        name = psutil.Process(pid.value).name()
    except Exception:
        pass
    return "%s/%s" % (buf.value, name)


def _describe_point():
    """'x,y <top-level class>/<exe> (fg <class>/<exe>)' for the log - what
    a click at the cursor is going to hit, and who is in the foreground."""
    try:
        import ctypes
        from ctypes import wintypes
        u = _user32()
        pt = wintypes.POINT()
        u.GetCursorPos(ctypes.byref(pt))
        hit = u.WindowFromPoint(pt)
        top = u.GetAncestor(hit, 2) if hit else None      # GA_ROOT
        return "%d,%d %s [%s] (fg %s)" % (
            pt.x, pt.y, _describe_hwnd(u, top), _describe_hwnd(u, hit),
            _describe_hwnd(u, u.GetForegroundWindow()))
    except Exception as e:
        return "? (%s)" % e

class Launcher:
    _inst = None

    @classmethod
    def instance(cls):
        if cls._inst is None:
            cls._inst = Launcher()
        return cls._inst

    @classmethod
    def is_active(cls):
        return cls._inst is not None and cls._inst._active

    def __init__(self):
        self._active = False
        self.grid = None
        self._saved_cursor = (0, 0)
        self._page_stack = ["root"]
        self._rows = 3
        self._cols = 3
        self._root = None
        self._fg_app = ""

    # ---------------------------------------------------------------- #

    def gesture_map(self):
        return launcher_gestures()

    def open(self):
        if self._active:
            logger.info("Launcher.open IGNORED: already active "
                        "(grid=%s)", self.grid is not None)
            return
        from src.controllers.mouse_controller import MouseController
        mc = MouseController()
        if getattr(mc, "_tk_root", None) is None:
            logger.warning("Launcher: no tk root; cannot open")
            return
        logger.info("Launcher.open: proceeding")
        self._root = mc._tk_root
        try:
            import win32api
            self._saved_cursor = win32api.GetCursorPos()
        except Exception:
            self._saved_cursor = (0, 0)

        cfg = _launcher_cfg()
        self._rows = int(cfg.get("rows", 3))
        self._cols = int(cfg.get("cols", 3))
        self._page_stack = ["root"]
        self._fg_app = _fg_app_name()
        self._active = True
        mc._gaze_grid_active = True

        from src.gui.gaze_grid_factory import make_gaze_grid
        self.grid = make_gaze_grid(self._root)
        logger.info("Launcher.open: grid created (%s)",
                    type(self.grid).__name__)
        cells = self._page_cells()
        g = self.grid
        try:
            self._root.after(0, lambda: self._do_grid_open(g, cells))
        except Exception as e:
            logger.error("Launcher open failed: %s", e)
            self.close()

    def _do_grid_open(self, g, cells):
        """The deferred grid.open(). Runs on the tk main loop one tick after
        open(). Logged + guarded so a silent failure here (which looks like
        'the launcher didn't appear') is captured."""
        try:
            if g is None or g is not self.grid:
                logger.warning("Launcher: grid changed/cleared before "
                               "deferred open — skipping (g=%s self.grid=%s)",
                               g is not None, self.grid is not None)
                return
            g.open(self._rows, self._cols, cells, on_exit=self.close,
                   idle_timeout_s=60.0,
                   alpha=float(_launcher_cfg().get("alpha", 0.2)),
                   confirm_ms=int(_launcher_cfg().get("confirm_ms", 0) or 0),
                   on_confirm=lambda: self.on_action("confirm"),
                   ignore_blink_ms=int(
                       _launcher_cfg().get("ignore_blink_ms", 200) or 0))
            logger.info("Launcher: deferred grid.open dispatched")
        except Exception as e:
            logger.error("Launcher deferred grid.open FAILED: %s", e,
                         exc_info=e)

    def _page(self, page_id=None):
        pid = page_id or self._page_stack[-1]
        return (_launcher_cfg().get("pages", {}) or {}).get(pid, {}) or {}

    def _page_cells(self):
        items = self._page(self._page_stack[-1]).get("items", {}) or {}
        cells = {}
        for key in items:
            it = self._resolve(items, key)
            if not it:
                continue
            lst = _norm(items.get(key))
            cells[key] = {
                "title": it.get("title", "") or "",
                "icon": _icon_for_item(it),
                "app_specific": any(x.get("app") for x in lst),
            }
        return cells

    def _resolve(self, items, key):
        """Pick the item slot to show/run for a cell: an app-specific
        slot matching the foreground app wins; else the common slot."""
        lst = _norm(items.get(key))
        for it in lst:
            a = (it.get("app") or "").lower()
            if a and a == self._fg_app:
                return it
        for it in lst:
            if not it.get("app"):
                return it
        return None

    def _refresh_grid(self):
        if self.grid:
            self.grid.update_cells(self._page_cells())

    # --- gesture actions ---

    def on_action(self, action):
        if not self._active or self.grid is None:
            return
        self.grid.note_action()
        if action == "confirm":
            self._confirm()
        elif action == "back":
            self._go_back()
        elif action == "exit":
            self.close()
        elif action == "settings":
            self._open_register()

    def _go_back(self):
        if len(self._page_stack) > 1:
            self._page_stack.pop()
            self._refresh_grid()
        else:
            self.close()

    def _confirm(self):
        r, c = self.grid.current_cell()
        items = self._page(self._page_stack[-1]).get("items", {}) or {}
        item = self._resolve(items, f"{r},{c}")
        if not item:
            return
        # A real, filled cell was executed — free ground truth for the
        # implicit gaze re-calibration.
        try:
            self.grid.note_confirmed_cell()
        except Exception:
            pass
        kind = item.get("type")
        if kind == "sublauncher":
            target = item.get("target")
            if target and len(self._page_stack) < MAX_DEPTH:
                self._page_stack.append(target)
                self._refresh_grid()
            return
        if kind == "back":
            self._go_back()
            return
        # Everything else closes the grid and runs in the real app.
        # Key / mouse-action items must land at the PRE-LAUNCH pointer, so
        # keep the cursor frozen (release_pointer=False) until _execute has
        # sent the input, then release it there. Otherwise head tracking
        # resumes the instant the grid closes and drags the cursor off the
        # saved position before the click fires.
        hold = kind in ("key", "action")
        self.close(release_pointer=not hold)
        threading.Thread(target=self._execute, args=(item, hold),
                         name="LauncherExec", daemon=True).start()

    def _open_register(self):
        page = self._page_stack[-1]
        r, c = self.grid.current_cell()
        root = self._root
        self.close()
        if root is not None:
            root.after(0, lambda: self._do_register(root, page, r, c))

    def _do_register(self, root, page, r, c):
        try:
            from src.gui.popups.launcher_options_popup import (
                ItemRegisterPopup)
            ItemRegisterPopup(root).open(r, c, page=page)
        except Exception as e:
            logger.error("open register failed: %s", e)

    # --- execution ---

    @staticmethod
    def _wait_overlay_gone(timeout_s=1.2):
        """Block until the gaze overlay has actually left the screen.
        Returns (waited_ms, still_visible) for the log.

        The gaze grid is a fullscreen topmost window in a SEPARATE process;
        close() only sends it an IPC message, and it then fades out for
        about 320 ms before hiding. Input sent during that time reaches the
        app underneath while our fullscreen topmost window is still over
        it - and a shell popup (the taskbar's right-click menu) opened in
        that moment did not survive it.

        The previous version waited for "the window under the cursor is no
        longer ours". That test can never succeed at waiting: the overlay
        is click-through (WS_EX_LAYERED | WS_EX_TRANSPARENT), and
        WindowFromPoint skips click-through windows exactly as mouse input
        does - verified - so it reported the overlay gone at once, every
        time, and the click went out mid-fade. What matters is whether the
        overlay is VISIBLE, so that is what is polled now.
        """
        t0 = time.time()
        pid = None
        try:
            from src.gui.qt_overlay.qt_gaze_grid import OverlayClient
            proc = OverlayClient.instance().proc
            if proc is not None and proc.poll() is None:
                pid = proc.pid
        except Exception:
            pass
        visible = False
        if pid is not None:
            deadline = t0 + timeout_s
            while True:
                visible = _process_has_visible_window(pid)
                if not visible or time.time() >= deadline:
                    break
                time.sleep(0.02)
        # Short settle even once it is gone, so the app underneath has
        # finished repainting / re-activating before input arrives.
        time.sleep(0.06)
        return int((time.time() - t0) * 1000), visible

    def _execute(self, item, release_after=False):
        # Wait for the overlay grid to actually leave the screen before we
        # drive input into the real app.
        waited, still = self._wait_overlay_gone()
        kind = item.get("type")
        logger.info("Launcher exec %s %r: overlay %s after %d ms; "
                    "under cursor %s",
                    kind, item.get("title", ""),
                    "STILL VISIBLE" if still else "hidden", waited,
                    _describe_point())
        try:
            if kind == "key":
                self._send_macro(item)
            elif kind == "action":
                self._do_mouse_action(item.get("action"))
            elif kind == "file":
                self._open_file(item)
            elif kind == "ext":
                self._open_ext(item.get("app_kind"))
            elif kind == "calibrate":
                self._open_calibrate()
            elif kind == "submonitor":
                self._toggle_submonitor()
        except Exception as e:
            logger.error("Launcher execute failed: %s", e)
        finally:
            if release_after:
                # Let the keystrokes / click fully land, then release the
                # frozen cursor so head tracking resumes from rest. Guard:
                # if the launcher was re-opened while we waited, the new
                # session owns the freeze now — don't clobber it.
                time.sleep(0.12)
                if not self._active:
                    self._release_pointer()

    def _toggle_submonitor(self):
        from src.controllers.mouse_controller import MouseController
        cb = MouseController()._submonitor_toggle
        if cb and self._root is not None:
            self._root.after(0, cb)

    def _send_macro(self, item):
        steps = item.get("steps")
        if not steps:
            ks = item.get("keys")          # legacy single-combo
            if ks:
                steps = [{"key": ks}]
            else:
                return
        for s in steps:
            if "key" in s:
                self._send_keys(s["key"])
            elif "wait" in s:
                try:
                    time.sleep(max(0, int(s["wait"])) / 1000.0)
                except Exception:
                    pass
            elif "text" in s:
                self._paste_text(str(s["text"]))
            elif "mouse" in s:
                self._macro_click(str(s["mouse"]))
            elif "wheel" in s:
                self._macro_wheel(s)
            elif "drag" in s:
                mouse_input.drag_start(str(s["drag"]))
            elif "hold" in s:
                mouse_input.long_press(
                    str(s["hold"]),
                    s.get("ms", mouse_input.DEFAULT_LONG_PRESS_MS))
            elif "swipe" in s:
                mouse_input.swipe(
                    str(s["swipe"]),
                    s.get("dist", mouse_input.DEFAULT_SWIPE))

    @staticmethod
    def _macro_wheel(s):
        """One wheel step. `wheel` is up/down/left/right, `n` the notch
        count (default 1)."""
        mouse_input.wheel(s.get("wheel", "up"), s.get("n", 1))

    def _paste_text(self, txt):
        """Send a literal string via clipboard + Ctrl+V.

        pydirectinput.typewrite emits raw scancodes; with a Japanese (or any)
        IME active those get eaten/transformed and the text never arrives —
        the "macro string isn't sent" bug. A clipboard paste bypasses the IME
        and handles arbitrary Unicode."""
        if not txt:
            return
        try:
            from src.extensions.clipboard_history import _set_clipboard_text
            _set_clipboard_text(txt)
            time.sleep(0.03)
            pydirectinput.keyDown("ctrl")
            pydirectinput.press("v")
            pydirectinput.keyUp("ctrl")
        except Exception as e:
            logger.error("macro text paste failed: %s", e)

    @staticmethod
    def _press_button(btn):
        """One click, sent EXACTLY the way Keybinder.mouse_action sends the
        Mouse page's own click actions.

        Down and up go in one SendInput event
        (MOUSEEVENTF_*DOWN | MOUSEEVENTF_*UP), which is what the taskbar
        and the other shell surfaces accept. Splitting it into separate
        mouseDown / mouseUp calls — even with a hold in between — is a
        different event sequence and got dropped intermittently.

        Routed through mouse_input.click() rather than pydirectinput so
        dwExtraInfo is 0; see that function for why the difference was
        worth chasing."""
        mouse_input.click(btn)

    def _macro_click(self, btn):
        """A click at the CURRENT cursor position (macro step), unlike a
        launcher 'action' item which clicks at the pre-launch cursor."""
        try:
            # A click while a drag is held DROPS it instead of clicking.
            # That is the whole point of the drag mode: grab, move the
            # pointer by head tracking, then click to let go — so the
            # click that ends the drag must not also fire a fresh one.
            if mouse_input.drag_release("click step"):
                return
            if btn == "double":
                self._press_button("left")
                time.sleep(0.04)
                self._press_button("left")
            elif btn in ("left", "right", "middle"):
                self._press_button(btn)
            logger.info("Launcher macro click %s at %s", btn,
                        _describe_point())
        except Exception as e:
            logger.error("macro click failed: %s", e)

    def _send_keys(self, keys):
        if not keys:
            return
        mods = [k for k in keys if k in MODS]
        main = [k for k in keys if k not in MODS]
        for m in mods:
            pydirectinput.keyDown(_WINMAP.get(m, m))
        try:
            for k in main:
                pydirectinput.press(k)
        finally:
            for m in reversed(mods):
                pydirectinput.keyUp(_WINMAP.get(m, m))

    def _do_mouse_action(self, action):
        if action in ("left", "right", "middle", "double"):
            x, y = self._saved_cursor
            try:
                pydirectinput.moveTo(x, y)
                time.sleep(0.03)   # let the move settle before clicking
            except Exception:
                pass
            # Same rule as the macro click step and the Mouse page: any
            # click ends a held drag rather than starting a new one. This
            # one moves to the saved cursor FIRST, so the drop lands where
            # the user was looking when they opened the launcher.
            if mouse_input.drag_release("launcher action"):
                return
            if action == "double":
                self._press_button("left")
                time.sleep(0.04)
                self._press_button("left")
            else:
                self._press_button(action)

    def _open_file(self, item):
        path = item.get("path")
        args = (item.get("args") or "").strip()
        if not path:
            return
        if args:
            subprocess.Popen([path] + args.split())
        else:
            os.startfile(path)

    def _open_ext(self, app):
        if app == "clipboard":
            from src.extensions.clipboard_history import ClipboardHistory
            ClipboardHistory.instance().open()
        elif app == "taskswitch":
            from src.extensions.task_switcher import TaskSwitcher
            TaskSwitcher.instance().open()
        elif app == "launcher":
            # Re-open the launcher root, deferred so the current grid has
            # fully closed first.
            try:
                from src.controllers.mouse_controller import MouseController
                root = MouseController()._tk_root
                if root is not None:
                    root.after(50, self.open)
            except Exception:
                pass

    def _open_calibrate(self):
        root = self._root
        if root is None:
            return

        def go():
            try:
                from src.gui.gaze_calibrator import GazeCalibrator
                GazeCalibrator(root).start()
            except Exception as e:
                logger.error("calibrate failed: %s", e)
        root.after(0, go)

    # ---------------------------------------------------------------- #

    def close(self, release_pointer=True):
        self._active = False
        # For runnable items the cursor is kept frozen until the action has
        # been sent (see _confirm/_execute); those callers pass
        # release_pointer=False and release it themselves afterwards.
        if release_pointer:
            self._release_pointer()
        g = self.grid
        self.grid = None
        if g is not None:
            try:
                g.close()
            except Exception:
                pass

    def _release_pointer(self):
        """Resume head/gaze tracking from rest so the cursor doesn't lurch
        to catch up with head motion accumulated while the grid was open."""
        try:
            from src.controllers.mouse_controller import MouseController
            MouseController().release_gaze_grid()
        except Exception:
            pass
