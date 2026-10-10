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
"""Gaze task switcher — Alt+Tab by gaze.

Enumerates the open top-level windows into a gaze grid; confirm raises
the one under the gaze. back/forward page through windows when there are
more than rows×cols. Gesture actions: confirm / back / forward / exit.
"""

import fnmatch
import logging

from src.config_manager import ConfigManager

logger = logging.getLogger("TaskSwitcher")


def _cfg():
    return (ConfigManager().read_extensions() or {}).get(
        "task_switcher", {}) or {}


def hidden_rules():
    """The user's "hide task" rules, as stored under task_switcher.hidden.

    Each rule is {"proc": str, "title": str, "by_proc": bool,
    "by_title": bool}. A rule matches a window when EVERY enabled
    condition matches (so ticking both is an AND, which is what lets one
    rule target a single window of a multi-window app). `title` must
    match the window title EXACTLY, or as a wildcard pattern (* and ?);
    `proc` is compared case-insensitively on the executable's file name.
    """
    out = []
    for r in (_cfg().get("hidden", []) or []):
        if not isinstance(r, dict):
            continue
        proc = str(r.get("proc", "") or "")
        title = str(r.get("title", "") or "")
        by_proc = bool(r.get("by_proc", bool(proc)))
        by_title = bool(r.get("by_title", False))
        if not (by_proc and proc) and not (by_title and title):
            continue            # nothing enabled -> rule does nothing
        out.append({"proc": proc, "title": title,
                    "by_proc": by_proc, "by_title": by_title})
    return out


def rule_matches(rule, proc_name, title):
    """True when every ENABLED condition of `rule` matches the window."""
    if rule["by_proc"] and rule["proc"]:
        if (proc_name or "").lower() != rule["proc"].lower():
            return False
    if rule["by_title"] and rule["title"]:
        pat = rule["title"]
        # Exact, case-sensitive: no substring match, so a rule written for
        # one window ("Inbox") no longer hides every window whose title
        # merely contains those letters. Wildcards (* ? [..]) still work,
        # and they too must cover the WHOLE title.
        if any(ch in pat for ch in "*?["):
            if not fnmatch.fnmatchcase(title or "", pat):
                return False
        elif (title or "") != pat:
            return False
    return True


class TaskSwitcher:
    _inst = None

    @classmethod
    def instance(cls):
        if cls._inst is None:
            cls._inst = TaskSwitcher()
        return cls._inst

    @classmethod
    def is_active(cls):
        return cls._inst is not None and cls._inst._active

    def __init__(self):
        self._active = False
        self.grid = None
        self._wins = []
        self._page = 0
        self._rows = 3
        self._cols = 3
        self._cellmap = {}      # "r,c" -> window index | "prev" | "next"

    def gesture_map(self):
        # Stored gesture-keyed: {gesture_name: slot}. Multiple gestures
        # may map to the same slot natively (each gesture is a key).
        # Returned as-is; the keybinder looks up by gesture name.
        return dict(_cfg().get("gestures", {}) or {})

    # ---------------------------------------------------------------- #

    def open(self):
        if self._active:
            return
        from src.controllers.mouse_controller import MouseController
        mc = MouseController()
        if getattr(mc, "_tk_root", None) is None:
            return
        c = _cfg()
        self._rows = int(c.get("rows", 3))
        self._cols = int(c.get("cols", 3))
        self._alpha = float(c.get("alpha", 0.2))
        self._wins = self._enum_windows()
        self._page = 0
        self._active = True
        mc._gaze_grid_active = True
        from src.gui.gaze_grid_factory import make_gaze_grid
        self.grid = make_gaze_grid(mc._tk_root)
        cells = self._page_cells()
        try:
            mc._tk_root.after(
                0, lambda: self.grid.open(
                    self._rows, self._cols, cells,
                    on_exit=self.close,
                    idle_timeout_s=60.0,
                    alpha=self._alpha,
                    confirm_ms=int(_cfg().get("confirm_ms", 0) or 0),
                    on_confirm=lambda: self.on_action("confirm"),
                    ignore_blink_ms=int(
                        _cfg().get("ignore_blink_ms", 200) or 0)))
        except Exception as e:
            logger.error("task switcher open failed: %s", e)
            self.close()

    @staticmethod
    def window_proc_name(hwnd):
        """The exe file name that owns `hwnd` (e.g. "chrome.exe")."""
        try:
            import win32process
            import psutil
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            return psutil.Process(pid).name()
        except Exception:
            return ""

    def _enum_windows(self):
        import win32gui
        import win32con
        out = []
        rules = hidden_rules()

        # GazeFace's own window is listed like any other. It used to be
        # filtered out by pid, which meant the one window a user driving
        # the app by face most needs to reach — its own settings — was the
        # only one the switcher would not switch to. The gaze overlay is a
        # WS_EX_TOOLWINDOW, so it is still excluded by the filter below,
        # and anyone who does not want the app listed can hide it through
        # the hide rules like anything else.

        def cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if not title:
                return
            try:
                ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                if ex & win32con.WS_EX_TOOLWINDOW:
                    return
            except Exception:
                pass
            if rules:
                # Only pay for the process lookup when a rule needs it.
                need_proc = any(r["by_proc"] and r["proc"] for r in rules)
                proc_name = self.window_proc_name(hwnd) if need_proc else ""
                for r in rules:
                    if rule_matches(r, proc_name, title):
                        return
            out.append((hwnd, title))

        try:
            win32gui.EnumWindows(cb, None)
        except Exception as e:
            logger.error("EnumWindows failed: %s", e)
        return out

    @classmethod
    def list_open_windows(cls):
        """(proc_name, title) for every switchable window, for the options
        UI's "pick from the running processes" list. Unfiltered — the user
        is choosing what to hide."""
        import win32gui
        import win32con
        out = []

        def cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if not title:
                return
            try:
                ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                if ex & win32con.WS_EX_TOOLWINDOW:
                    return
            except Exception:
                pass
            # Our own window is offered here too — it is switchable now,
            # so it must also be hideable.
            out.append((cls.window_proc_name(hwnd), title))

        try:
            win32gui.EnumWindows(cb, None)
        except Exception as e:
            logger.error("EnumWindows failed: %s", e)
        out.sort(key=lambda t: (t[0].lower(), t[1].lower()))
        return out

    def _show_page_btns(self):
        return bool(_cfg().get("page_btns", True))

    def _per_page(self):
        per = self._rows * self._cols
        if self._show_page_btns():
            per -= 2
        return max(1, per)

    def _page_cells(self):
        cells = {}
        self._cellmap = {}
        # Optional ◀ ▶ page cells at the middle of the left/right edges
        # (same layout as the clipboard grid), toggled in the options.
        if self._show_page_btns():
            mid = self._rows // 2
            pk, nk = f"{mid},0", f"{mid},{self._cols - 1}"
            cells[pk] = {"title": "◀", "app_specific": True}
            self._cellmap[pk] = "prev"
            cells[nk] = {"title": "▶", "app_specific": True}
            self._cellmap[nk] = "next"
        start = self._page * self._per_page()
        slot = 0
        for r in range(self._rows):
            for c in range(self._cols):
                k = f"{r},{c}"
                if k in self._cellmap:
                    continue
                wi = start + slot
                if wi < len(self._wins):
                    hwnd, title = self._wins[wi]
                    cell = {"title": title[:48]}
                    icon = self._win_icon(hwnd)
                    if icon:
                        cell["icon"] = icon
                    cells[k] = cell
                    self._cellmap[k] = wi
                slot += 1
        return cells

    def _win_icon(self, hwnd):
        """The window's app icon as a cached PNG (via its exe), or None.

        Reuses the launcher's exe-icon extractor. A true live window
        preview needs DWM thumbnail compositing, which tkinter can't host;
        a large app icon is the practical fallback."""
        try:
            import win32process
            import psutil
            from src.extensions.launcher_icons import extract_file_icon
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            exe = psutil.Process(pid).exe()
            return extract_file_icon(exe)
        except Exception:
            return None

    def _npages(self):
        per = self._per_page()
        return max(1, (len(self._wins) + per - 1) // per)

    # --- gesture actions ---

    def on_action(self, a):
        if not self._active or self.grid is None:
            return
        self.grid.note_action()
        if a == "confirm":
            self._activate()
        elif a == "forward":
            self._flip(1)
        elif a == "back":
            self._flip(-1)
        elif a == "close":
            self._close_selected()
        elif a == "exit":
            self.close()

    def _close_selected(self):
        """Ask the selected window to close (the same thing its X button
        does), then drop it from the grid.

        WM_CLOSE, not TerminateProcess: the app gets to run its own
        shutdown and prompt about unsaved work. If it refuses, the window
        simply stays open — which is the correct outcome for a gesture
        that cannot be confirmed."""
        r, c = self.grid.current_cell()
        target = self._cellmap.get(f"{r},{c}")
        if not isinstance(target, int) or not (
                0 <= target < len(self._wins)):
            return                       # a paging cell, or nothing there
        hwnd, title = self._wins[target]
        if self._is_own_window(hwnd):
            # Closing our own window by gesture would shut down the one
            # thing the user is controlling the computer with - and the
            # exit confirmation it raises would then have to be answered
            # with the input it just took away. Other GazeFace processes
            # are ordinary targets.
            logger.info("task switcher: refused to close our own window "
                        "%r", title)
            return
        try:
            self.grid.note_confirmed_cell()
        except Exception:
            pass
        import win32con
        import win32gui
        try:
            # PostMessage, not SendMessage: a window that is busy or
            # showing a save prompt would block us for as long as it
            # takes, and this runs on the gaze-grid thread.
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            logger.info("task switcher: asked %r to close", title)
        except Exception as e:
            logger.error("close window failed: %s", e)
            return
        # Refresh the list so the closed window stops being offered. Its
        # own teardown is asynchronous, so filter by hwnd rather than
        # re-enumerating into a race.
        self._wins = [w for w in self._wins if w[0] != hwnd]
        if not self._wins:
            self.close()
            return
        self._page = min(self._page, self._npages() - 1)
        self.grid.update_cells(self._page_cells())

    @staticmethod
    def _is_own_window(hwnd):
        """True for a window of THIS GazeFace (this process, or the gaze
        overlay it spawned). Another running GazeFace is not ours."""
        try:
            import os
            import win32process
            _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return False
        if pid == os.getpid():
            return True
        try:
            from src.gui.qt_overlay.qt_gaze_grid import OverlayClient
            proc = OverlayClient.instance().proc
            return proc is not None and proc.pid == pid
        except Exception:
            return False

    def _flip(self, d):
        self._page = (self._page + d) % self._npages()
        if self.grid:
            self.grid.update_cells(self._page_cells())

    def _activate(self):
        r, c = self.grid.current_cell()
        target = self._cellmap.get(f"{r},{c}")
        if target == "prev":
            self._flip(-1)
            return
        if target == "next":
            self._flip(1)
            return
        if not isinstance(target, int):
            return
        # A real window cell was executed — free ground truth for the
        # implicit gaze re-calibration.
        try:
            self.grid.note_confirmed_cell()
        except Exception:
            pass
        hwnd = self._wins[target][0] \
            if 0 <= target < len(self._wins) else None
        self.close()
        if hwnd is not None:
            # focus_window verifies + escalates — a bare
            # SetForegroundWindow from a background process is routinely
            # rejected, which was the "switch sometimes does nothing" bug.
            from src.utils.win_focus import focus_window
            import threading
            threading.Thread(target=focus_window, args=(hwnd,),
                             name="TaskSwitchFocus", daemon=True).start()

    def close(self):
        self._active = False
        try:
            from src.controllers.mouse_controller import MouseController
            # release_gaze_grid() also resets the head-velocity buffers —
            # a raw flag clear makes the cursor lurch toward the head
            # motion accumulated while the grid was open.
            MouseController().release_gaze_grid()
        except Exception:
            pass
        g = self.grid
        self.grid = None
        if g is not None:
            try:
                g.close()
            except Exception:
                pass
