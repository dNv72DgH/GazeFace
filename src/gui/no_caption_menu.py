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
"""No right-click menu on the GazeFace window's title bar.

A right click on a title bar opens the window's system menu (Restore /
Move / Size / ... / Close). With a face-driven right click that is easy
to hit by accident, and the open menu then swallows the next clicks.

Tk sees nothing of the title bar (it is non-client area), so the
top-level window is subclassed and the non-client right-button messages
over the title bar are dropped before Windows turns them into the menu.
Everything else goes through untouched.
"""

import ctypes
import logging
from ctypes import wintypes

logger = logging.getLogger("NoCaptionMenu")

_WM_NCRBUTTONDOWN = 0x00A4
_WM_NCRBUTTONUP = 0x00A5
_WM_NCRBUTTONDBLCLK = 0x00A6
_RIGHT_BUTTON_MSGS = (_WM_NCRBUTTONDOWN, _WM_NCRBUTTONUP,
                      _WM_NCRBUTTONDBLCLK)
# Hit-test codes of the title bar: caption, icon, minimize, maximize,
# close, help.
_TITLE_BAR_HITS = (2, 3, 8, 9, 20, 21)
_GA_ROOT = 2
_SUBCLASS_ID = 0x47460001          # "GF" + 1, any value unique to us

_LRESULT = ctypes.c_ssize_t
_SUBCLASSPROC = ctypes.WINFUNCTYPE(
    _LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
    wintypes.LPARAM, ctypes.c_size_t, ctypes.c_size_t)

_u32 = ctypes.WinDLL("user32", use_last_error=True)
_u32.GetAncestor.restype = wintypes.HWND
_u32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_comctl = ctypes.WinDLL("comctl32", use_last_error=True)
_comctl.SetWindowSubclass.restype = wintypes.BOOL
_comctl.SetWindowSubclass.argtypes = [wintypes.HWND, _SUBCLASSPROC,
                                      ctypes.c_size_t, ctypes.c_size_t]
_comctl.DefSubclassProc.restype = _LRESULT
_comctl.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]


def _proc(hwnd, msg, wparam, lparam, _id, _data):
    try:
        if msg in _RIGHT_BUTTON_MSGS and wparam in _TITLE_BAR_HITS:
            return 0
    except Exception:
        pass
    return _comctl.DefSubclassProc(hwnd, msg, wparam, lparam)


# Must outlive the window: Windows calls it for every message.
_PROC = _SUBCLASSPROC(_proc)


def install(tk_root) -> bool:
    """Drop title-bar right clicks on `tk_root`'s window. Call on the Tk
    thread (the window's owner). True if installed."""
    try:
        tk_root.update_idletasks()
        top = _u32.GetAncestor(wintypes.HWND(tk_root.winfo_id()), _GA_ROOT)
        if not top:
            return False
        ok = bool(_comctl.SetWindowSubclass(top, _PROC, _SUBCLASS_ID, 0))
        logger.info("title-bar right click %s",
                    "blocked" if ok else "block failed")
        return ok
    except Exception as e:
        logger.warning("title-bar right-click block failed: %s", e)
        return False
