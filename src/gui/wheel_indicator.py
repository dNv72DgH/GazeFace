"""Small badge that follows the pointer while the wheel rotation mode runs.

The pointer stops moving in that mode, which on its own looks the same
as a frozen app. The badge (the launcher's scroll glyph) sits just below
and to the right of the pointer until the mode ends.

One plain Win32 layered popup (UpdateLayeredWindow, per-pixel alpha):
borderless, topmost, click-through and never activated, moved every
POLL_MS. Not a tk Toplevel: in the installed app tk's client window came
loose from its frame and stayed in the screen's top-left corner while
the empty frame followed the pointer.

Runs on the tk main thread only (tk's message loop serves the window);
the controller calls show()/hide() from its worker thread through
root.after().
"""

import ctypes
import logging
import os
from ctypes import wintypes

logger = logging.getLogger("WheelIndicator")

SIZE = 44                  # badge diameter, px
OFFSET = (22, 22)          # from the pointer's hot spot
POLL_MS = 30
RAISE_EVERY = 30           # re-assert topmost every N polls (~1 s)
DISC = (32, 33, 36, 255)
RING = (76, 194, 255, 255)

WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SW_SHOWNOACTIVATE = 4
ULW_ALPHA = 0x02
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
MONITOR_DEFAULTTONEAREST = 2
CLASS_NAME = "GazeFaceWheelBadge"

_root = None
_hwnd = None
_after = None
_polls = 0
_last_pos = None
_logged = False
_class_ok = False
_hinst = None
_U32 = None
_G32 = None


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class _CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("flags", ctypes.c_uint),
                ("hCursor", ctypes.c_void_p), ("ptScreenPos", _POINT)]


class _BLEND(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_ubyte)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_ushort),
                ("biBitCount", ctypes.c_ushort),
                ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32),
                ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", ctypes.c_uint32)]


class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("style", ctypes.c_uint),
                ("lpfnWndProc", ctypes.c_void_p), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", ctypes.c_void_p),
                ("hIcon", ctypes.c_void_p), ("hCursor", ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p),
                ("hIconSm", ctypes.c_void_p)]


def _api():
    """Private user32/gdi32 handles with full prototypes (not the shared
    windll ones, whose argtypes other modules set)."""
    global _U32, _G32
    if _U32 is None:
        u = ctypes.WinDLL("user32", use_last_error=True)
        g = ctypes.WinDLL("gdi32", use_last_error=True)
        vp, i, ui = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint
        u.CreateWindowExW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p,
                                      ctypes.c_wchar_p, ctypes.c_uint32,
                                      i, i, i, i, vp, vp, vp, vp]
        u.CreateWindowExW.restype = vp
        u.RegisterClassExW.argtypes = [ctypes.POINTER(_WNDCLASSEXW)]
        u.RegisterClassExW.restype = ctypes.c_ushort
        u.DestroyWindow.argtypes = [vp]
        u.ShowWindow.argtypes = [vp, i]
        u.SetWindowPos.argtypes = [vp, vp, i, i, i, i, ui]
        u.SetWindowPos.restype = i
        u.UpdateLayeredWindow.argtypes = [
            vp, vp, ctypes.POINTER(_POINT), ctypes.POINTER(_SIZE), vp,
            ctypes.POINTER(_POINT), ctypes.c_uint32, ctypes.POINTER(_BLEND),
            ctypes.c_uint32]
        u.UpdateLayeredWindow.restype = i
        u.GetDC.argtypes = [vp]
        u.GetDC.restype = vp
        u.ReleaseDC.argtypes = [vp, vp]
        u.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
        u.GetCursorInfo.argtypes = [ctypes.POINTER(_CURSORINFO)]
        u.MonitorFromPoint.argtypes = [_POINT, ctypes.c_uint32]
        u.MonitorFromPoint.restype = vp
        u.GetMonitorInfoW.argtypes = [vp, ctypes.POINTER(_MONITORINFO)]
        u.GetWindowRect.argtypes = [vp, ctypes.POINTER(wintypes.RECT)]
        g.CreateCompatibleDC.argtypes = [vp]
        g.CreateCompatibleDC.restype = vp
        g.CreateDIBSection.argtypes = [vp, ctypes.POINTER(_BITMAPINFOHEADER),
                                       ui, ctypes.POINTER(vp), vp,
                                       ctypes.c_uint32]
        g.CreateDIBSection.restype = vp
        g.SelectObject.argtypes = [vp, vp]
        g.SelectObject.restype = vp
        g.DeleteObject.argtypes = [vp]
        g.DeleteDC.argtypes = [vp]
        _U32, _G32 = u, g
    return _U32, _G32


def _icon_path():
    from src.extensions.launcher_icons import icon_for_glyph
    return icon_for_glyph("scroll")


def _badge_bgra():
    """The badge as premultiplied BGRA bytes (top-down DIB). The scroll glyph (a blue disc with a white mouse) on
    a slightly larger dark disc, so it reads on any background."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((0, 0, SIZE - 1, SIZE - 1), fill=DISC)
    p = None
    try:
        p = _icon_path()
    except Exception:
        p = None
    if p and os.path.exists(p):
        s = SIZE - 4
        ic = Image.open(p).convert("RGBA").resize((s, s), Image.LANCZOS)
        img.alpha_composite(ic, (2, 2))
    else:
        ImageDraw.Draw(img).ellipse((3, 3, SIZE - 4, SIZE - 4), fill=RING)
    out = bytearray(SIZE * SIZE * 4)
    j = 0
    for r, g, b, a in img.getdata():
        out[j] = b * a // 255
        out[j + 1] = g * a // 255
        out[j + 2] = r * a // 255
        out[j + 3] = a
        j += 4
    return bytes(out)


def _register_class():
    global _class_ok, _hinst
    if _class_ok:
        return True
    u, _g = _api()
    k32 = ctypes.WinDLL("kernel32")
    k32.GetModuleHandleW.restype = ctypes.c_void_p
    wc = _WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(_WNDCLASSEXW)
    # DefWindowProcW itself as the window procedure: the window needs no
    # handling of its own, and no Python callback has to stay alive.
    wc.lpfnWndProc = ctypes.cast(u.DefWindowProcW, ctypes.c_void_p).value
    _hinst = k32.GetModuleHandleW(None)
    wc.hInstance = _hinst
    wc.lpszClassName = CLASS_NAME
    if not u.RegisterClassExW(ctypes.byref(wc)):
        err = ctypes.get_last_error()
        if err != 1410:            # ERROR_CLASS_ALREADY_EXISTS
            logger.warning("badge class registration failed (%d)", err)
            return False
    _class_ok = True
    return True


def _paint(hwnd, x, y):
    """Give the layered window its pixels and position."""
    u, g = _api()
    data = _badge_bgra()
    screen = u.GetDC(None)
    mem = g.CreateCompatibleDC(screen)
    bmi = _BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    bmi.biWidth = SIZE
    bmi.biHeight = -SIZE          # top-down
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bits = ctypes.c_void_p()
    dib = g.CreateDIBSection(mem, ctypes.byref(bmi), 0, ctypes.byref(bits),
                             None, 0)
    ok = False
    try:
        if dib and bits.value:
            ctypes.memmove(bits.value, data, len(data))
            old = g.SelectObject(mem, dib)
            blend = _BLEND(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
            ok = bool(u.UpdateLayeredWindow(
                hwnd, screen, ctypes.byref(_POINT(x, y)),
                ctypes.byref(_SIZE(SIZE, SIZE)), mem,
                ctypes.byref(_POINT(0, 0)), 0, ctypes.byref(blend),
                ULW_ALPHA))
            g.SelectObject(mem, old)
    finally:
        if dib:
            g.DeleteObject(dib)
        g.DeleteDC(mem)
        u.ReleaseDC(None, screen)
    if not ok:
        logger.warning("badge paint failed (%d)", ctypes.get_last_error())
    return ok


def _cursor():
    """Pointer position in physical screen px, or None."""
    u, _g = _api()
    pt = _POINT()
    try:
        if u.GetCursorPos(ctypes.byref(pt)):
            return pt.x, pt.y
    except Exception:
        pass
    ci = _CURSORINFO()
    ci.cbSize = ctypes.sizeof(_CURSORINFO)
    try:
        if u.GetCursorInfo(ctypes.byref(ci)):
            return ci.ptScreenPos.x, ci.ptScreenPos.y
    except Exception:
        pass
    return None


def _place(pos):
    """Badge position for a pointer position: below-right of it, flipped
    to the other side at the edges of the pointer's monitor."""
    u, _g = _api()
    x, y = pos[0] + OFFSET[0], pos[1] + OFFSET[1]
    try:
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        mon = u.MonitorFromPoint(_POINT(pos[0], pos[1]),
                                 MONITOR_DEFAULTTONEAREST)
        if mon and u.GetMonitorInfoW(mon, ctypes.byref(mi)):
            r = mi.rcMonitor
            if x + SIZE > r.right:
                x = pos[0] - OFFSET[0] - SIZE
            if y + SIZE > r.bottom:
                y = pos[1] - OFFSET[1] - SIZE
            x = max(r.left, min(x, r.right - SIZE))
            y = max(r.top, min(y, r.bottom - SIZE))
    except Exception:
        pass
    return x, y


def _move(pos):
    global _last_pos
    xy = _place(pos)
    if xy == _last_pos:
        return
    u, _g = _api()
    u.SetWindowPos(_hwnd, None, xy[0], xy[1], 0, 0,
                   SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
    _last_pos = xy


def _log_rect(pos):
    """Once per show: where the pointer and the badge are (diagnostics)."""
    global _logged
    if _logged:
        return
    _logged = True
    try:
        u, _g = _api()
        r = wintypes.RECT()
        u.GetWindowRect(_hwnd, ctypes.byref(r))
        logger.info("badge at %d,%d (pointer %s)", r.left, r.top, pos)
    except Exception:
        pass


def _poll():
    global _after, _polls
    _after = None
    if _hwnd is None or _root is None:
        return
    try:
        pos = _cursor()
        if pos is not None:
            _move(pos)
            if _polls == 3:
                _log_rect(pos)
        elif _polls == 3:
            logger.warning("badge: pointer position unavailable")
        _polls += 1
        if _polls % RAISE_EVERY == 0:
            try:
                from src.gui.qt_overlay.win_effects import force_top
                force_top(_hwnd)
            except Exception:
                pass
    except Exception as e:
        logger.debug("badge poll failed: %s", e)
    try:
        _after = _root.after(POLL_MS, _poll)
    except Exception:
        _after = None


def show(root, kind):
    """Show the badge (tk thread)."""
    global _root, _hwnd, _polls, _last_pos, _logged
    try:
        from src import lite_mode
        if lite_mode.ACTIVE:
            return
    except Exception:
        pass
    hide()
    try:
        if not _register_class():
            return
        u, _g = _api()
        pos = _cursor() or (0, 0)
        x, y = _place(pos)
        hwnd = u.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW
            | WS_EX_NOACTIVATE | WS_EX_TOPMOST,
            CLASS_NAME, "", WS_POPUP, x, y, SIZE, SIZE,
            None, None, _hinst, None)
        if not hwnd:
            logger.warning("badge window creation failed (%d)",
                           ctypes.get_last_error())
            return
        if not _paint(hwnd, x, y):
            u.DestroyWindow(hwnd)
            return
        u.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        _root, _hwnd = root, hwnd
        _last_pos = (x, y)
        _logged = False
        _polls = 0
        _poll()
    except Exception as e:
        logger.error("wheel badge show failed: %s", e)
        hide()


def hide():
    """Remove the badge (tk thread)."""
    global _root, _hwnd, _after
    h, root = _hwnd, _root
    _hwnd = _root = None
    try:
        if _after is not None and root is not None:
            root.after_cancel(_after)
    except Exception:
        pass
    _after = None
    if h:
        try:
            _api()[0].DestroyWindow(h)
        except Exception:
            pass
