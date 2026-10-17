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
"""Show a pointer Windows is hiding, with one report from a virtual
mouse (GazeFace's own UMDF driver, or vmulti). That is ALL this module
does.

After sign-in Windows keeps the pointer suppressed (CURSOR_SUPPRESSED)
until a mouse moves, and it does not count SendInput as a mouse moving:
the (0, 0) SendInput nudge in mouse_controller was logged going out and
the pointer stayed hidden until the physical mouse was touched. vmulti is
a virtual HID device - to Windows it IS a mouse - so one report from it
ends the suppression the way the physical mouse does.

Deliberately narrow (the user's condition for using the driver at all):
  * nothing else in the app sends input through vmulti - clicks, moves
    and the wheel all stay on SendInput;
  * one wake = two absolute reports: 1 px aside, then back to where the
    pointer was, no buttons. The pointer ends where it started;
  * the caller does it at most once per suppression episode;
  * the device is opened for the wake and closed again;
  * no driver, or any failure: nothing happens, and the caller carries
    on exactly as before.

Devices, both found by their control collection (usage page 0xFF00
usage 1); X and Y are 0..0x7FFF over the whole virtual desktop,
little-endian:
  * gfmouse (driver/gfmouse, UMDF - preferred; VID 0x1209 PID 0x0007):
    WriteFile of 5 bytes: [0]=0x02, [1:3] X, [3:5] Y. The driver has no
    way to press a button.
  * vmulti (xaviersykora/vmulti-win11, kernel; VID 0x00FF PID 0xBACC):
    WriteFile of 0x41 bytes: [0]=0x40 control report, [1]=7 payload
    length, [2]=0x03 mouse report, [3]=buttons, [4:6] X, [6:8] Y,
    [8]=wheel.
"""

import ctypes
import logging
import time
from ctypes import wintypes

logger = logging.getLogger("VMultiWake")

GFMOUSE = "gfmouse"
VMULTI = "vmulti"
# kind -> (VID, PID), in order of preference
DEVICES = {GFMOUSE: (0x1209, 0x0007), VMULTI: (0x00FF, 0xBACC)}
CONTROL_USAGE_PAGE = 0xFF00
CONTROL_USAGE = 0x0001
ABS_MAX = 0x7FFF

_GENERIC_RW = 0x80000000 | 0x40000000
_FILE_SHARE_RW = 0x00000003
_OPEN_EXISTING = 3
_DIGCF_PRESENT = 0x02
_DIGCF_DEVICEINTERFACE = 0x10
_INVALID_HANDLE = wintypes.HANDLE(-1).value


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", _GUID),
                ("Flags", wintypes.DWORD),
                ("Reserved", ctypes.POINTER(ctypes.c_ulong))]


class _HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", ctypes.c_ulong), ("VendorID", ctypes.c_ushort),
                ("ProductID", ctypes.c_ushort),
                ("VersionNumber", ctypes.c_ushort)]


class _HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", ctypes.c_ushort), ("UsagePage", ctypes.c_ushort),
                ("InputReportByteLength", ctypes.c_ushort),
                ("OutputReportByteLength", ctypes.c_ushort),
                ("FeatureReportByteLength", ctypes.c_ushort),
                ("Reserved", ctypes.c_ushort * 17),
                ("NumberLinkCollectionNodes", ctypes.c_ushort),
                ("NumberInputButtonCaps", ctypes.c_ushort),
                ("NumberInputValueCaps", ctypes.c_ushort),
                ("NumberInputDataIndices", ctypes.c_ushort),
                ("NumberOutputButtonCaps", ctypes.c_ushort),
                ("NumberOutputValueCaps", ctypes.c_ushort),
                ("NumberOutputDataIndices", ctypes.c_ushort),
                ("NumberFeatureButtonCaps", ctypes.c_ushort),
                ("NumberFeatureValueCaps", ctypes.c_ushort),
                ("NumberFeatureDataIndices", ctypes.c_ushort)]


# The display structures are the ones display_info already defines.
from src.display_info import (_DEVMODEW, _DISPLAY_DEVICEW,  # noqa: E402
                              _DISPLAY_DEVICE_ATTACHED_TO_DESKTOP,
                              _ENUM_CURRENT_SETTINGS)


# Private loaders with prototypes (see the note in src/uipi.py): handles
# are pointer-sized, and the shared ctypes.windll objects must not change.
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_setup = ctypes.WinDLL("setupapi", use_last_error=True)
_hid = ctypes.WinDLL("hid", use_last_error=True)
_u32 = ctypes.WinDLL("user32", use_last_error=True)

_k32.CreateFileW.restype = wintypes.HANDLE
_k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                             wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                             wintypes.DWORD, wintypes.HANDLE]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.WriteFile.restype = wintypes.BOOL
_k32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                           ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
_setup.SetupDiGetClassDevsW.restype = wintypes.HANDLE
_setup.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(_GUID),
                                        wintypes.LPCWSTR, wintypes.HWND,
                                        wintypes.DWORD]
_setup.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
_setup.SetupDiEnumDeviceInterfaces.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(_GUID),
    wintypes.DWORD, ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA)]
_setup.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
_setup.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p]
_setup.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
_hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(_GUID)]
_hid.HidD_GetAttributes.restype = wintypes.BOOLEAN
_hid.HidD_GetAttributes.argtypes = [wintypes.HANDLE,
                                    ctypes.POINTER(_HIDD_ATTRIBUTES)]
_hid.HidD_GetPreparsedData.restype = wintypes.BOOLEAN
_hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE,
                                       ctypes.POINTER(ctypes.c_void_p)]
_hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
_hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(_HIDP_CAPS)]
try:
    _u32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    _u32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
except AttributeError:
    pass
_u32.GetPhysicalCursorPos.restype = wintypes.BOOL
_u32.GetPhysicalCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_u32.EnumDisplayDevicesW.restype = wintypes.BOOL
_u32.EnumDisplayDevicesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                     ctypes.POINTER(_DISPLAY_DEVICEW),
                                     wintypes.DWORD]
_u32.EnumDisplaySettingsW.restype = wintypes.BOOL
_u32.EnumDisplaySettingsW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                      ctypes.POINTER(_DEVMODEW)]

_absent = False           # driver looked for and not found: stop looking


def _control_kind(h):
    """The DEVICES kind if `h` is a control collection, else None."""
    attrs = _HIDD_ATTRIBUTES()
    attrs.Size = ctypes.sizeof(attrs)
    if not _hid.HidD_GetAttributes(h, ctypes.byref(attrs)):
        return None
    kind = next((k for k, ids in DEVICES.items()
                 if ids == (attrs.VendorID, attrs.ProductID)), None)
    if kind is None:
        return None
    ppd = ctypes.c_void_p()
    if not _hid.HidD_GetPreparsedData(h, ctypes.byref(ppd)):
        return None
    try:
        caps = _HIDP_CAPS()
        if _hid.HidP_GetCaps(ppd, ctypes.byref(caps)) == 0:
            return None
        if (caps.UsagePage == CONTROL_USAGE_PAGE
                and caps.Usage == CONTROL_USAGE):
            return kind
        return None
    finally:
        _hid.HidD_FreePreparsedData(ppd)


def _open_control():
    """(handle, kind) of the preferred device present, or None."""
    guid = _GUID()
    _hid.HidD_GetHidGuid(ctypes.byref(guid))
    devs = _setup.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None,
        _DIGCF_PRESENT | _DIGCF_DEVICEINTERFACE)
    if devs == _INVALID_HANDLE:
        return None
    found = {}                                  # kind -> handle
    try:
        iface = _SP_DEVICE_INTERFACE_DATA()
        iface.cbSize = ctypes.sizeof(iface)
        index = 0
        while _setup.SetupDiEnumDeviceInterfaces(
                devs, None, ctypes.byref(guid), index, ctypes.byref(iface)):
            index += 1
            need = wintypes.DWORD()
            _setup.SetupDiGetDeviceInterfaceDetailW(
                devs, ctypes.byref(iface), None, 0, ctypes.byref(need), None)
            if not need.value:
                continue
            buf = ctypes.create_string_buffer(need.value)
            # cbSize = size of the fixed part: 8 on 64-bit, 6 on 32-bit.
            ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = (
                8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6)
            if not _setup.SetupDiGetDeviceInterfaceDetailW(
                    devs, ctypes.byref(iface), buf, need.value, None, None):
                continue
            path = ctypes.wstring_at(
                ctypes.addressof(buf) + ctypes.sizeof(wintypes.DWORD))
            h = _k32.CreateFileW(path, _GENERIC_RW, _FILE_SHARE_RW, None,
                                 _OPEN_EXISTING, 0x80, None)
            if not h or h == _INVALID_HANDLE:
                continue
            kind = _control_kind(h)
            if kind is None or kind in found:
                _k32.CloseHandle(h)
                continue
            found[kind] = h
            if kind == GFMOUSE:
                break                           # the preferred one
    finally:
        _setup.SetupDiDestroyDeviceInfoList(devs)
    chosen = next((k for k in DEVICES if k in found), None)
    for k, h in found.items():
        if k != chosen:
            _k32.CloseHandle(h)
    return (found[chosen], chosen) if chosen else None


def _virtual_bounds():
    """(left, top, width, height) of the whole desktop in real pixels -
    the rectangle vmulti's 0..0x7FFF range spans."""
    left = top = right = bottom = None
    i = 0
    while True:
        dd = _DISPLAY_DEVICEW()
        dd.cb = ctypes.sizeof(dd)
        if not _u32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
            break
        i += 1
        if not (dd.StateFlags & _DISPLAY_DEVICE_ATTACHED_TO_DESKTOP):
            continue
        dm = _DEVMODEW()
        dm.dmSize = ctypes.sizeof(dm)
        if not _u32.EnumDisplaySettingsW(dd.DeviceName,
                                         _ENUM_CURRENT_SETTINGS,
                                         ctypes.byref(dm)):
            continue
        w, h = int(dm.dmPelsWidth), int(dm.dmPelsHeight)
        if w <= 0 or h <= 0:
            continue
        x, y = int(dm.dmPositionX), int(dm.dmPositionY)
        left = x if left is None else min(left, x)
        top = y if top is None else min(top, y)
        right = x + w if right is None else max(right, x + w)
        bottom = y + h if bottom is None else max(bottom, y + h)
    if left is None or right - left < 2 or bottom - top < 2:
        return None
    return left, top, right - left, bottom - top


def _to_abs(x, y, b):
    vl, vt, vw, vh = b
    return (max(0, min(ABS_MAX, int(round((x - vl) * ABS_MAX / (vw - 1))))),
            max(0, min(ABS_MAX, int(round((y - vt) * ABS_MAX / (vh - 1))))))


def _report_bytes(kind, ax, ay):
    pos = bytes((ax & 0xFF, (ax >> 8) & 0xFF, ay & 0xFF, (ay >> 8) & 0xFF))
    if kind == GFMOUSE:
        return b"\x02" + pos
    # vmulti: control, payload length, mouse report, no buttons, ever
    return (b"\x40\x07\x03\x00" + pos).ljust(0x41, b"\x00")


def _report(h, kind, ax, ay):
    data = _report_bytes(kind, ax, ay)
    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    written = wintypes.DWORD()
    return bool(_k32.WriteFile(h, buf, len(data), ctypes.byref(written),
                               None))


def wake_pointer() -> bool:
    """One tiny real-mouse move and back. True if it was sent."""
    global _absent
    if _absent:
        return False
    try:
        opened = _open_control()
    except Exception as e:
        logger.warning("virtual mouse lookup failed: %s", e)
        return False
    if opened is None:
        _absent = True
        logger.info("no virtual mouse installed - pointer wake unavailable")
        return False
    h, kind = opened
    # Cursor position and display sizes must be in the same (physical)
    # pixels as the absolute range. In a DPI-unaware thread they are not,
    # and the "back to where it was" report put the pointer a third of
    # the way toward the corner on this 150 % display. So the whole wake
    # runs per-monitor aware, whatever the process is.
    prev = None
    try:
        prev = _u32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass
    try:
        p = wintypes.POINT()
        b = _virtual_bounds()
        if b is None or not _u32.GetPhysicalCursorPos(ctypes.byref(p)):
            return False
        x, y = p.x, p.y
        # One absolute unit is often less than a pixel; step a whole pixel
        # inward so it is a real move even at the desktop's right edge.
        step = 1 if x + 1 < b[0] + b[2] else -1
        ok = _report(h, kind, *_to_abs(x + step, y, b))
        time.sleep(0.015)
        ok = _report(h, kind, *_to_abs(x, y, b)) and ok
        logger.info("%s pointer wake at %d,%d: %s", kind, x, y,
                    "sent" if ok else "WriteFile failed (%d)"
                    % ctypes.get_last_error())
        return ok
    except Exception as e:
        logger.warning("%s pointer wake failed: %s", kind, e)
        return False
    finally:
        _k32.CloseHandle(h)
        if prev:
            try:
                _u32.SetThreadDpiAwarenessContext(ctypes.c_void_p(prev))
            except Exception:
                pass
