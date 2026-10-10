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
"""Gaze clipboard history.

A background monitor records clipboard TEXT (deduped, capped, persisted
in configs/extensions.json so it survives restarts). The gaze grid shows
recent entries; the centre cells of the left/right edges page through
history. Confirm pastes the chosen text into the foreground app (set
clipboard + restore foreground + Ctrl+V) and closes. Gesture actions:
confirm / back / forward / exit.
"""

import logging
import threading
import time

import pydirectinput

from src.config_manager import ConfigManager

logger = logging.getLogger("ClipboardHistory")

# Serialise ALL clipboard access. The background monitor thread reads the
# clipboard every second while the paste thread writes it; two threads in
# OpenClipboard/CloseClipboard at once corrupts the clipboard state (and can
# hard-crash the process). This lock keeps every open/close pairing atomic.
_CLIP_LOCK = threading.Lock()


def _tool_icon(name):
    """Absolute path of a library icon, or None if it is missing."""
    try:
        from src.extensions.launcher_icons import icon_for_glyph
        return icon_for_glyph(name)
    except Exception:
        return None


def _cb_cfg():
    return (ConfigManager().read_extensions() or {}).get(
        "clipboard", {}) or {}


def _get_clipboard_text():
    import win32clipboard
    import win32con
    with _CLIP_LOCK:
        try:
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(
                        win32con.CF_UNICODETEXT):
                    return win32clipboard.GetClipboardData(
                        win32con.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            pass
    return None


def _set_clipboard_text(txt):
    import win32clipboard
    import win32con
    with _CLIP_LOCK:
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                # SetClipboardText (high-level) takes a Python string and
                # allocs the global memory itself. SetClipboardData(
                # CF_UNICODETEXT, str) is the LOW-level form and expects a
                # HANDLE, not a string — passing a str fails with WinError 6
                # (invalid handle) and can corrupt the clipboard / crash the
                # app. (This was the cause of "pasting an item crashed the
                # app".)
                win32clipboard.SetClipboardText(
                    str(txt), win32con.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
        except Exception as e:
            logger.error("set clipboard failed: %s", e)


# ---------------------------------------------------------------------- #
#                              Monitor                                    #
# ---------------------------------------------------------------------- #

class ClipboardMonitor:
    _inst = None

    @classmethod
    def instance(cls):
        if cls._inst is None:
            cls._inst = ClipboardMonitor()
        return cls._inst

    def __init__(self):
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._loop, name="ClipboardMonitor",
                         daemon=True).start()
        logger.info("Clipboard monitor started")

    def stop(self):
        self._running = False

    def _loop(self):
        last = None
        while self._running:
            try:
                txt = _get_clipboard_text()
                if txt and txt != last:
                    last = txt
                    self._record(txt)
            except Exception:
                pass
            time.sleep(1.0)

    def _record(self, txt):
        e = ConfigManager().read_extensions()
        cb = e.setdefault("clipboard", {})
        hist = cb.setdefault("history", [])
        if txt in hist:
            hist.remove(txt)
        hist.insert(0, txt)
        mx = int(cb.get("max", 100))
        if mx > 0:
            del hist[mx:]
        ConfigManager().write_extensions(e)


def maybe_start_monitor():
    """Called at app startup: start monitoring if enabled, and honour
    the 'clear on start' option."""
    cfg = _cb_cfg()
    if cfg.get("clear_on_start"):
        e = ConfigManager().read_extensions()
        e.setdefault("clipboard", {})["history"] = []
        ConfigManager().write_extensions(e)
    if cfg.get("enabled"):
        ClipboardMonitor.instance().start()


# ---------------------------------------------------------------------- #
#                          Gaze grid app                                 #
# ---------------------------------------------------------------------- #

class ClipboardHistory:
    _inst = None

    @classmethod
    def instance(cls):
        if cls._inst is None:
            cls._inst = ClipboardHistory()
        return cls._inst

    @classmethod
    def is_active(cls):
        return cls._inst is not None and cls._inst._active

    def __init__(self):
        self._active = False
        self.grid = None
        self._history = []
        self._pinned = []
        self._mode = "history"     # "history" | "pinned"
        self._page = 0
        self._rows = 3
        self._cols = 3
        self._cellmap = {}
        self._saved_fg = None

    def gesture_map(self):
        # Stored gesture-keyed: {gesture_name: slot}. Several gestures can
        # already map to the same slot (e.g. confirm on both mouth-open
        # and eye-close) since each gesture is its own key. Returned as-is
        # because the keybinder looks up by gesture name.
        return dict(_cb_cfg().get("gestures", {}) or {})

    def open(self):
        if self._active:
            return
        from src.controllers.mouse_controller import MouseController
        mc = MouseController()
        if getattr(mc, "_tk_root", None) is None:
            return
        try:
            import win32gui
            self._saved_fg = win32gui.GetForegroundWindow()
        except Exception:
            self._saved_fg = None
        cfg = _cb_cfg()
        self._rows = int(cfg.get("rows", 3))
        self._cols = int(cfg.get("cols", 3))
        self._history = list(cfg.get("history", []) or [])
        self._pinned = list(cfg.get("pinned", []) or [])
        self._mode = "history"
        self._page = 0
        self._active = True
        mc._gaze_grid_active = True
        from src.gui.gaze_grid_factory import make_gaze_grid
        self.grid = make_gaze_grid(mc._tk_root)
        cells = self._page_cells()
        try:
            mc._tk_root.after(
                0, lambda: self.grid.open(
                    self._rows, self._cols, cells, on_exit=self.close,
                    idle_timeout_s=60.0,
                    alpha=float(_cb_cfg().get("alpha", 0.2)),
                    confirm_ms=int(_cb_cfg().get("confirm_ms", 0) or 0),
                    on_confirm=lambda: self.on_action("confirm"),
                    ignore_blink_ms=int(
                        _cb_cfg().get("ignore_blink_ms", 200) or 0)))
        except Exception as e:
            logger.error("clipboard open failed: %s", e)
            self.close()

    def _show_page_btns(self):
        return bool(_cb_cfg().get("page_btns", True))

    def _show_pin_cell(self):
        return bool(_cb_cfg().get("show_pin_cell", True))

    def _cur_list(self):
        return self._pinned if self._mode == "pinned" else self._history

    def _special_cells(self):
        """Non-entry cells keyed 'r,c' -> role: the list-switch cell
        (first cell) plus the row-middle edge page cells."""
        special = {}
        if self._show_pin_cell():
            special["0,0"] = ("history" if self._mode == "pinned"
                              else "pinlist")
        if self._show_page_btns():
            mid = self._rows // 2
            for key in (f"{mid},0", f"{mid},{self._cols - 1}"):
                if key not in special:
                    special[key] = "prev" if key.endswith(",0") else "next"
        return special

    def _per_page(self):
        return max(1, self._rows * self._cols - len(self._special_cells()))

    def _page_cells(self):
        cells = {}
        self._cellmap = {}
        for k, role in self._special_cells().items():
            if role == "prev":
                cells[k] = {"title": "◀", "app_specific": True}
            elif role == "next":
                cells[k] = {"title": "▶", "app_specific": True}
            elif role == "pinlist":
                # Icons rather than ★ / ◀: the cell says what it opens.
                cells[k] = {"title": "", "app_specific": True,
                            "icon": _tool_icon("pinned-list")}
            elif role == "history":
                cells[k] = {"title": "", "app_specific": True,
                            "icon": _tool_icon("clipboard-history")}
            self._cellmap[k] = role
        lst = self._cur_list()
        start = self._page * self._per_page()
        slot = 0
        for r in range(self._rows):
            for c in range(self._cols):
                k = f"{r},{c}"
                if k in self._cellmap:
                    continue
                hi = start + slot
                if hi < len(lst):
                    txt = lst[hi].replace("\n", " ").replace("\r", " ")
                    cells[k] = {"title": txt[:48]}
                    self._cellmap[k] = hi
                slot += 1
        return cells

    def _npages(self):
        per = self._per_page()
        return max(1, (len(self._cur_list()) + per - 1) // per)

    def on_action(self, a):
        if not self._active or self.grid is None:
            return
        self.grid.note_action()
        if a == "confirm":
            self._confirm()
        elif a == "forward":
            self._flip(1)
        elif a == "back":
            self._flip(-1)
        elif a == "pin":
            self._pin_current()
        elif a == "switch":
            # Toggle history <-> pinned list (same destination as the ★ / ◀
            # in-grid cell, without having to gaze at it).
            self._switch_mode(
                "history" if self._mode == "pinned" else "pinned")
        elif a == "delete":
            self._delete_current()
        elif a == "exit":
            self.close()

    def _delete_current(self):
        """Drop the selected entry from whichever list is showing.

        Works in both views: in the pinned list it unpins, in the history
        it forgets the entry. The grid stays open so several can be
        cleared in a row."""
        if self.grid is None:
            return
        r, c = self.grid.current_cell()
        target = self._cellmap.get(f"{r},{c}")
        lst = self._cur_list()
        if not isinstance(target, int) or not (0 <= target < len(lst)):
            return                       # a paging / list-switch cell
        txt = lst[target]
        e = ConfigManager().read_extensions()
        cb = e.setdefault("clipboard", {})
        key = "pinned" if self._mode == "pinned" else "history"
        stored = cb.setdefault(key, [])
        try:
            stored.remove(txt)
        except ValueError:
            pass
        ConfigManager().write_extensions(e)
        if self._mode == "pinned":
            self._pinned = list(stored)
        else:
            # The monitor keeps no copy of its own (it re-reads the config
            # on every capture), so the config write above plus this local
            # snapshot is the whole story.
            self._history = list(stored)
        # Deleting the last entry of a page can leave the view past the
        # end; walk back rather than showing an empty grid.
        self._page = min(self._page, self._npages() - 1)
        self.grid.update_cells(self._page_cells())

    def _flip(self, d):
        self._page = (self._page + d) % self._npages()
        if self.grid:
            self.grid.update_cells(self._page_cells())

    def _switch_mode(self, mode):
        self._mode = mode
        self._page = 0
        if self.grid:
            self.grid.update_cells(self._page_cells())

    def _pin_current(self):
        """The pin gesture, in whichever list is showing.

        History view: pin the selected entry.
        Pinned view: UNPIN it - the same gesture takes it back off the
        list - and put it back at the top of the clipboard history, so it
        is still there to be pinned again. Dropping it entirely is what
        the delete gesture is for.
        """
        if self.grid is None:
            return
        if self._mode == "pinned":
            self._unpin_current()
            return
        r, c = self.grid.current_cell()
        target = self._cellmap.get(f"{r},{c}")
        if not isinstance(target, int) or not (
                0 <= target < len(self._history)):
            return
        txt = self._history[target]
        e = ConfigManager().read_extensions()
        cb = e.setdefault("clipboard", {})
        pinned = cb.setdefault("pinned", [])
        if txt in pinned:
            pinned.remove(txt)
        pinned.insert(0, txt)
        ConfigManager().write_extensions(e)
        self._pinned = list(pinned)
        if self.grid:
            self.grid.update_cells(self._page_cells())

    def _unpin_current(self):
        """Take the selected entry off the pinned list and return it to
        the clipboard history (newest first, honouring the history cap)."""
        r, c = self.grid.current_cell()
        target = self._cellmap.get(f"{r},{c}")
        if not isinstance(target, int) or not (
                0 <= target < len(self._pinned)):
            return                       # a paging / list-switch cell
        txt = self._pinned[target]
        e = ConfigManager().read_extensions()
        cb = e.setdefault("clipboard", {})
        pinned = cb.setdefault("pinned", [])
        try:
            pinned.remove(txt)
        except ValueError:
            pass
        hist = cb.setdefault("history", [])
        if txt in hist:
            hist.remove(txt)
        hist.insert(0, txt)
        mx = int(cb.get("max", 100))
        if mx > 0:
            del hist[mx:]
        ConfigManager().write_extensions(e)
        self._pinned = list(pinned)
        self._history = list(hist)
        # Unpinning the last entry of a page can leave the view past the
        # end; walk back rather than showing an empty grid.
        self._page = min(self._page, self._npages() - 1)
        if self.grid:
            self.grid.update_cells(self._page_cells())

    def _confirm(self):
        r, c = self.grid.current_cell()
        target = self._cellmap.get(f"{r},{c}")
        if target == "prev":
            self._flip(-1)
            return
        if target == "next":
            self._flip(1)
            return
        if target == "pinlist":
            self._switch_mode("pinned")
            return
        if target == "history":
            self._switch_mode("history")
            return
        if not isinstance(target, int):
            return
        # A real content cell was executed — free ground truth for the
        # implicit gaze re-calibration.
        try:
            self.grid.note_confirmed_cell()
        except Exception:
            pass
        lst = self._cur_list()
        txt = lst[target] if 0 <= target < len(lst) else None
        fg = self._saved_fg
        self.close()
        if txt is not None:
            threading.Thread(target=self._do_paste, args=(txt, fg),
                             name="ClipPaste", daemon=True).start()

    def _do_paste(self, txt, fg):
        time.sleep(0.05)
        _set_clipboard_text(txt)
        if fg:
            # Verified + escalating focus restore — a bare
            # SetForegroundWindow from this background process is routinely
            # rejected, leaving Ctrl+V to land nowhere (the "paste
            # sometimes does nothing" bug).
            try:
                from src.utils.win_focus import focus_window
                focus_window(fg)
            except Exception:
                pass
        time.sleep(0.08)
        try:
            pydirectinput.keyDown("ctrl")
            pydirectinput.press("v")
            pydirectinput.keyUp("ctrl")
        except Exception as e:
            logger.error("paste failed: %s", e)

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
