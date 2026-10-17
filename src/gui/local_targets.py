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
"""GazeFace's own clickable widgets, as Gaze Jump snap targets.

CustomTkinter draws its buttons, check boxes and so on onto canvases, so
UI Automation sees GazeFace's window as one plain pane and the snap
(src/click_snap.py) could never land on the app's own controls. This
lists their screen rectangles straight from Tk instead.

Tk may only be touched on its own thread and the snap runs on the jump's
worker, so the walk is handed to the Tk thread and waited for briefly; if
that thread is busy, the jump just goes without these targets.
"""

import ctypes
import logging
import threading
import tkinter

logger = logging.getLogger("LocalTargets")

WAIT_S = 0.15
_GA_ROOT = 2

# Widget classes that are clicked as a whole (by class name, so the
# CustomTkinter import stays out of this module).
_CLICKABLE = frozenset((
    "CTkButton", "CTkCheckBox", "CTkRadioButton", "CTkSwitch",
    "CTkOptionMenu", "CTkComboBox", "CTkEntry", "CTkSegmentedButton",
    "Button", "Checkbutton", "Radiobutton", "Entry",
))

_u32 = ctypes.WinDLL("user32")
_u32.GetAncestor.restype = ctypes.c_void_p
_u32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
_u32.WindowFromPoint.restype = ctypes.c_void_p


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _top_hwnd(widget):
    try:
        return _u32.GetAncestor(widget.winfo_id(), _GA_ROOT)
    except Exception:
        return None


def _on_top(widget, cx, cy):
    """Is `widget`'s window the one actually showing at (cx, cy)? A
    button of a page hidden behind another app's window is no target."""
    try:
        pt = _POINT(int(cx), int(cy))
        hit = _u32.WindowFromPoint(pt)
        return bool(hit) and _u32.GetAncestor(hit, _GA_ROOT) == \
            _top_hwnd(widget)
    except Exception:
        return False


def _clickable(widget):
    cls = type(widget).__name__
    if cls in _CLICKABLE:
        try:
            return str(widget.cget("state")) != "disabled"
        except Exception:
            return True
    # Clickable labels such as the "?" help markers show a hand cursor.
    try:
        return str(widget.cget("cursor")) == "hand2"
    except Exception:
        return False


def _collect(tk_root):
    """[(l, t, r, b)] of visible clickable widgets. Tk thread only."""
    rects = []
    stack = list(tk_root.winfo_children())
    while stack:
        w = stack.pop()
        try:
            if not w.winfo_ismapped():
                continue                 # hidden page: skip its subtree
        except tkinter.TclError:
            continue
        if _clickable(w):
            try:
                x, y = w.winfo_rootx(), w.winfo_rooty()
                r, b = x + w.winfo_width(), y + w.winfo_height()
                if r > x and b > y and _on_top(w, (x + r) / 2, (y + b) / 2):
                    rects.append((x, y, r, b))
            except tkinter.TclError:
                pass
            continue                     # a button's own parts are not
        try:                             # separate targets
            stack.extend(w.winfo_children())
        except tkinter.TclError:
            pass
    return rects


def provider(tk_root):
    """A click_snap local provider for `tk_root` (callable from any
    thread)."""

    def get():
        done = threading.Event()
        box = []

        def run():
            try:
                box.extend(_collect(tk_root))
            except Exception as e:
                logger.debug("collect failed: %s", e)
            finally:
                done.set()

        try:
            tk_root.after(0, run)
        except Exception:
            return []
        if not done.wait(WAIT_S):
            return []
        return box

    return get
