import ctypes
import os
import sys
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer

from src.gui.qt_overlay.overlay_main import Overlay, _zorder_intruder

u = ctypes.windll.user32
k = ctypes.windll.kernel32
ad = ctypes.windll.advapi32

k.OpenProcess.restype = wintypes.HANDLE
k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
ad.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                ctypes.POINTER(wintypes.HANDLE)]
ad.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                   ctypes.c_void_p, wintypes.DWORD,
                                   ctypes.POINTER(wintypes.DWORD)]
ad.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
ad.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
ad.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
ad.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]


def integrity(pid):
    PROCESS_QUERY_LIMITED = 0x1000
    h = k.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
    if not h:
        return "open?err%d" % ctypes.get_last_error()
    tok = wintypes.HANDLE()
    if not ad.OpenProcessToken(h, 0x0008, ctypes.byref(tok)):
        return "tokerr"
    size = wintypes.DWORD()
    ad.GetTokenInformation(tok, 25, None, 0, ctypes.byref(size))
    buf = ctypes.create_string_buffer(size.value)
    if not ad.GetTokenInformation(tok, 25, buf, size, ctypes.byref(size)):
        return "gtierr"
    psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
    n = ad.GetSidSubAuthorityCount(psid)[0]
    val = ad.GetSidSubAuthority(psid, n - 1)[0]
    names = {0x0000: "untrusted", 0x1000: "low", 0x2000: "medium",
             0x2100: "medium+", 0x3000: "high", 0x4000: "system"}
    return names.get(val, hex(val))


_OUT = open(os.path.join(os.environ.get("TEMP", "."), "il_out.txt"), "w",
            encoding="utf-8")


def print(*a):  # noqa: A001
    _OUT.write(" ".join(str(x) for x in a) + "\n")
    _OUT.flush()


o = Overlay()


def show():
    o._on_command({"cmd": "open", "mode": "grid", "rows": 1, "cols": 3,
                   "cells": [{"title": "A", "icon": "", "app": False}],
                   "sel": 0, "accent": "#4CC2FF", "gradient": 0xB0101214})


SWP = 0x0002 | 0x0001 | 0x0010 | 0x0040  # NOMOVE|NOSIZE|NOACTIVATE|SHOWWINDOW


def _still():
    i = _zorder_intruder(o._hwnd)
    return i[1] if i else "NONE(on-top)"


def probe():
    hwnd = o._hwnd
    print("our pid", os.getpid(), "IL", integrity(os.getpid()))
    intr = _zorder_intruder(hwnd)
    if intr:
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(intr[0], ctypes.byref(pid))
        try:
            import psutil
            pn = psutil.Process(pid.value).name()
        except Exception:
            pn = "?"
        print("intruder cls=%s pid=%s name=%s IL=%s"
              % (intr[1], pid.value, pn, integrity(pid.value)))

    # 1) plain HWND_TOPMOST
    ctypes.set_last_error(0)
    r = u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, SWP)
    print("1 HWND_TOPMOST ret=%s err=%s -> %s"
          % (r, ctypes.get_last_error(), _still()))

    # 2) BringWindowToTop
    ctypes.set_last_error(0)
    r = u.BringWindowToTop(hwnd)
    print("2 BringWindowToTop ret=%s err=%s -> %s"
          % (r, ctypes.get_last_error(), _still()))

    # 3) NOTOPMOST then TOPMOST toggle
    u.SetWindowPos(hwnd, -2, 0, 0, 0, 0, SWP)   # HWND_NOTOPMOST
    ctypes.set_last_error(0)
    r = u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, SWP)
    print("3 toggle NOTOPMOST->TOPMOST ret=%s err=%s -> %s"
          % (r, ctypes.get_last_error(), _still()))

    # 4) insert directly above the intruder (hwndInsertAfter = window above
    #    the intruder, i.e. put us right on top of it)
    if intr:
        ctypes.set_last_error(0)
        r = u.SetWindowPos(hwnd, intr[0], 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0010)
        print("4 above-intruder ret=%s err=%s -> %s"
              % (r, ctypes.get_last_error(), _still()))
    o.app.quit()


QTimer.singleShot(500, show)
QTimer.singleShot(1200, probe)
o.run()
