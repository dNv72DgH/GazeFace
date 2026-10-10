"""Current display mode (resolution + refresh rate) without Qt.

The main process has no Qt, but the refresh rate is what decides whether
the overlay's frame pacing beats against vsync: a 62.5 Hz producer on a
60 Hz display drops a frame every 0.4 s, on a 120 Hz display it never
does. Windows can also put a 120 Hz display back to 60 across a display
power-off/resume, which looks exactly like "the GPU went bad after I left
it alone". Logging the mode at startup and at every overlay recycle makes
that visible in log.txt instead of guesswork.
"""

import ctypes
import logging
from ctypes import wintypes

logger = logging.getLogger(__name__)

_ENUM_CURRENT_SETTINGS = -1
_DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001
_DISPLAY_DEVICE_PRIMARY_DEVICE = 0x00000004


class _DEVMODEW(ctypes.Structure):
    # Only the tail fields are read; the layout must still match in full.
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmPositionX", ctypes.c_long),
        ("dmPositionY", ctypes.c_long),
        ("dmDisplayOrientation", wintypes.DWORD),
        ("dmDisplayFixedOutput", wintypes.DWORD),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]


def modes(primary_first=False):
    """[(device_name, width, height, hz), ...] for attached displays."""
    out = []
    try:
        user32 = ctypes.windll.user32
        i = 0
        while True:
            dd = _DISPLAY_DEVICEW()
            dd.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
            if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
                break
            i += 1
            if not (dd.StateFlags & _DISPLAY_DEVICE_ATTACHED_TO_DESKTOP):
                continue
            dm = _DEVMODEW()
            dm.dmSize = ctypes.sizeof(_DEVMODEW)
            if not user32.EnumDisplaySettingsW(
                    dd.DeviceName, _ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
                continue
            row = (dd.DeviceName, int(dm.dmPelsWidth),
                   int(dm.dmPelsHeight), int(dm.dmDisplayFrequency))
            if primary_first and (dd.StateFlags &
                                  _DISPLAY_DEVICE_PRIMARY_DEVICE):
                out.insert(0, row)
            else:
                out.append(row)
    except Exception as e:
        logger.debug("display mode query failed: %s", e)
    return out


def primary_size():
    """(width, height) of the primary display in REAL pixels.

    GetSystemMetrics(SM_CXSCREEN/SM_CYSCREEN) is not usable for this: it
    reports the DPI-VIRTUALISED size unless the calling process is
    per-monitor DPI aware, so on a 3840x2160 panel at 150% it answers
    2560x1440. Anything that then feeds the number to SetCursorPos — which
    always works in real pixels — comes out 1.5x short. EnumDisplaySettings
    reports the actual display mode regardless of the caller's awareness,
    so it is the same answer for everyone.
    """
    got = modes(primary_first=True)
    if got:
        _name, w, h, _hz = got[0]
        if w > 0 and h > 0:
            return w, h
    try:
        g = ctypes.windll.user32.GetSystemMetrics
        return int(g(0)) or 1920, int(g(1)) or 1080
    except Exception:
        return 1920, 1080


def log_modes(why=""):
    """Log every display's current mode; flag the ones still at 60 Hz."""
    try:
        got = modes()
        if not got:
            return
        parts = []
        for name, w, h, hz in got:
            # 60 Hz is the mode that makes the overlay's pacing beat, so
            # call it out rather than leaving it to be spotted by eye.
            parts.append("%s %dx%d@%dHz%s"
                         % (name, w, h, hz, " <-60Hz" if hz <= 60 else ""))
        logger.info("DISPLAY%s %s", (" " + why) if why else "",
                    " | ".join(parts))
    except Exception as e:
        logger.debug("display mode logging failed: %s", e)
