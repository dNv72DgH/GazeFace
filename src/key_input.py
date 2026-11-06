"""Key presses with the extended-key flag where Windows needs it.

pydirectinput sends scan codes and only marks the four arrows as
extended keys. Page Up / Down, Home, End, Insert, Delete (and the right
Ctrl / Alt, Win, Apps, numpad /, Print Screen) share their scan code
with a numpad key and are told apart ONLY by that flag. Without it a
macro's Page Down arrived as numpad 3 - which with NumLock on types a
"3" instead of scrolling. Its mapping stores those keys as 0x4xx /
0x8x+ values whose low 7 bits are the scan code.

install() replaces pydirectinput.keyDown / keyUp (press() calls them
through the module, so it is covered too). Every caller keeps using
pydirectinput as before.
"""

import ctypes
import logging

import pydirectinput

logger = logging.getLogger("KeyInput")

_ARROWS = ("up", "down", "left", "right")
_VK_PAUSE = 0x13
_installed = False


def _scan(key):
    """(scan code, extended) for a pydirectinput key name, or None."""
    v = pydirectinput.KEYBOARD_MAPPING.get(key)
    if v is None:
        return None
    if v > 0x7F:
        return v & 0x7F, True
    return v, key in _ARROWS


def _send(key, up):
    if key == "pause":
        # Pause is the E1 1D 45 sequence, which SendInput cannot express
        # as a scan code; the virtual key gets it right.
        flags = 0x0002 if up else 0                     # KEYEVENTF_KEYUP
        ki = pydirectinput.KeyBdInput(_VK_PAUSE, 0, flags, 0,
                                      ctypes.pointer(ctypes.c_ulong(0)))
    else:
        sc = _scan(key)
        if sc is None:
            return False
        flags = pydirectinput.KEYEVENTF_SCANCODE
        if sc[1]:
            flags |= pydirectinput.KEYEVENTF_EXTENDEDKEY
        if up:
            flags |= pydirectinput.KEYEVENTF_KEYUP
        ki = pydirectinput.KeyBdInput(0, sc[0], flags, 0,
                                      ctypes.pointer(ctypes.c_ulong(0)))
    ii = pydirectinput.Input_I()
    ii.ki = ki
    x = pydirectinput.Input(ctypes.c_ulong(1), ii)
    return pydirectinput.SendInput(1, ctypes.pointer(x),
                                   ctypes.sizeof(x)) == 1


def key_down(key, logScreenshot=None, _pause=True):
    return _send(key, False)


def key_up(key, logScreenshot=None, _pause=True):
    return _send(key, True)


def install():
    global _installed
    if _installed:
        return
    _installed = True
    pydirectinput.keyDown = key_down
    pydirectinput.keyUp = key_up


install()
