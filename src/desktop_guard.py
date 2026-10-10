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
"""Which desktop we are on, and which one the user is looking at.

With the Windows startup options on (src/at_startup.py), Windows starts a
SECOND copy of GazeFace on the secure desktop - the sign-in / lock screen
and the UAC prompt - while the everyday copy keeps running on the normal
desktop. Only one of them can hold the webcam, so:

  * the everyday copy lets go of the camera while another desktop is in
    front, and takes it back when its own desktop returns;
  * the secure-desktop copy ends itself once its desktop is no longer
    the one in front (signed in, prompt answered).

"Default" is the name of the normal desktop; the sign-in screen, the lock
screen and the UAC prompt all run on "Winlogon".
"""

import ctypes
import logging
import threading
import time
from ctypes import wintypes

logger = logging.getLogger("DesktopGuard")

NORMAL_DESKTOP = "Default"
_CACHE_S = 0.5

_UOI_NAME = 2
_DESKTOP_READOBJECTS = 0x0001

# Private loaders with prototypes: HDESK is a pointer-sized handle, and
# the shared ctypes.windll objects must not have their prototypes changed
# (see the note in src/uipi.py).
_u32 = ctypes.WinDLL("user32", use_last_error=True)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_u32.GetThreadDesktop.restype = wintypes.HANDLE
_u32.GetThreadDesktop.argtypes = [wintypes.DWORD]
_u32.OpenInputDesktop.restype = wintypes.HANDLE
_u32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                  wintypes.DWORD]
_u32.CloseDesktop.restype = wintypes.BOOL
_u32.CloseDesktop.argtypes = [wintypes.HANDLE]
_u32.GetUserObjectInformationW.restype = wintypes.BOOL
_u32.GetUserObjectInformationW.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD)]
_k32.GetCurrentThreadId.restype = wintypes.DWORD
_k32.GetCurrentThreadId.argtypes = []


def _name_of(h):
    buf = ctypes.create_unicode_buffer(256)
    need = wintypes.DWORD(0)
    if not _u32.GetUserObjectInformationW(
            h, _UOI_NAME, buf, ctypes.sizeof(buf), ctypes.byref(need)):
        return None
    return buf.value or None


_own = None


def own_desktop():
    """The desktop this process was started on (cached; it never
    changes - no thread here calls SetThreadDesktop)."""
    global _own
    if _own is None:
        try:
            h = _u32.GetThreadDesktop(_k32.GetCurrentThreadId())
            _own = (_name_of(h) if h else None) or NORMAL_DESKTOP
        except Exception as e:
            logger.error("own desktop query failed: %s", e)
            _own = NORMAL_DESKTOP
    return _own


def on_secure_desktop() -> bool:
    return own_desktop().lower() != NORMAL_DESKTOP.lower()


def input_desktop():
    """Name of the desktop receiving input, or None if we may not see it.

    A normal user process is refused access to the Winlogon desktop, so
    from the everyday copy None means "the secure desktop is in front".
    """
    try:
        h = _u32.OpenInputDesktop(0, False, _DESKTOP_READOBJECTS)
    except Exception:
        return None
    if not h:
        return None
    try:
        return _name_of(h)
    finally:
        _u32.CloseDesktop(h)


# ---- our desktop coming back to the front --------------------------- #

_ret_gen = 0
_ret_ours = None
_ret_ts = 0.0


def front_state():
    """(ours, generation), re-read at most every half second.

    `ours` is True when our desktop receives input. `generation` counts
    the times it has come back to the front after something else had it
    (sign-in, lock screen, UAC prompt) - Windows hides the pointer across
    those, so the mouse code shows it again once per return.
    """
    global _ret_gen, _ret_ours, _ret_ts
    now = time.monotonic()
    if now - _ret_ts >= _CACHE_S:
        _ret_ts = now
        inp = input_desktop()
        ours = inp is not None and inp.lower() == own_desktop().lower()
        if ours and _ret_ours is False:
            _ret_gen += 1
        _ret_ours = ours
    return bool(_ret_ours), _ret_gen


# ---- the everyday copy: hand the camera over ------------------------- #

_yield_enabled = False
_cache = (0.0, False)


def set_yield_enabled(on: bool) -> None:
    """Hand the camera over while another desktop is in front?

    Only when a secure-desktop copy can exist (a Windows startup option
    is on). Otherwise nothing would pick the camera up, and releasing and
    reopening a webcam on every lock / UAC prompt is a needless risk with
    the flaky drivers this app already has to recover from.
    """
    global _yield_enabled, _cache
    _yield_enabled = bool(on) and not on_secure_desktop()
    _cache = (0.0, False)
    logger.info("camera hand-over on desktop switch: %s", _yield_enabled)


def camera_yielded() -> bool:
    """True while another desktop is in front and the camera is theirs."""
    global _cache
    if not _yield_enabled:
        return False
    now = time.monotonic()
    ts, val = _cache
    if now - ts < _CACHE_S:
        return val
    inp = input_desktop()
    val = inp is None or inp.lower() != own_desktop().lower()
    _cache = (now, val)
    return val


# ---- the secure-desktop copy: leave with the desktop ------------------ #

LEAVE_AFTER_S = 3.0


def watch_secure_desktop(on_gone) -> None:
    """Call `on_gone()` once the user has left this desktop for good.

    Only a DEFINITE answer counts: the input desktop is known and it is
    not ours. If it cannot be read at all we stay - Windows ends a copy it
    started itself when it tears the desktop down.
    """
    if not on_secure_desktop():
        return

    def loop():
        away_since = None
        while True:
            time.sleep(0.5)
            inp = input_desktop()
            if inp is not None and inp.lower() != own_desktop().lower():
                if away_since is None:
                    away_since = time.monotonic()
                elif time.monotonic() - away_since >= LEAVE_AFTER_S:
                    logger.info("secure desktop %s left (now on %s) - "
                                "exiting", own_desktop(), inp)
                    try:
                        on_gone()
                    finally:
                        return
            else:
                away_since = None

    threading.Thread(target=loop, name="secure-desktop-watch",
                     daemon=True).start()
