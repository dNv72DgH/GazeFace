# Win32 window effects for the Qt overlay: Win11 acrylic blur-behind,
# always-on-top, and optional click-through. All best-effort — every
# call degrades silently if the API is unavailable so the overlay still
# works (just flatter) on older Windows.
import ctypes
from ctypes import wintypes

import logging

logger = logging.getLogger("QtOverlayWinFx")

# --- SetWindowCompositionAttribute (undocumented but stable) ---------- #

ACCENT_DISABLED = 0
ACCENT_ENABLE_BLURBEHIND = 3
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
WCA_ACCENT_POLICY = 19


class ACCENTPOLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),   # 0xAABBGGRR
        ("AnimationId", ctypes.c_int),
    ]


class WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.POINTER(ACCENTPOLICY)),
        ("SizeOfData", ctypes.c_size_t),
    ]


def enable_acrylic(hwnd, gradient_abgr=0xB0101214):
    """Frosted-glass blur of whatever is behind the window. `gradient_abgr`
    is the tint as 0xAABBGGRR — high alpha = more tint (more opaque dim),
    low alpha = clearer glass. Default: dark, ~69% tint."""
    try:
        accent = ACCENTPOLICY()
        accent.AccentState = ACCENT_ENABLE_ACRYLICBLURBEHIND
        accent.AccentFlags = 0
        accent.GradientColor = gradient_abgr
        data = WINCOMPATTRDATA()
        data.Attribute = WCA_ACCENT_POLICY
        data.Data = ctypes.pointer(accent)
        data.SizeOfData = ctypes.sizeof(accent)
        set_wca = ctypes.windll.user32.SetWindowCompositionAttribute
        set_wca.argtypes = [wintypes.HWND, ctypes.POINTER(WINCOMPATTRDATA)]
        return bool(set_wca(int(hwnd), ctypes.byref(data)))
    except Exception as e:
        logger.debug("enable_acrylic failed: %s", e)
        return False


def disable_acrylic(hwnd):
    try:
        accent = ACCENTPOLICY()
        accent.AccentState = ACCENT_DISABLED
        data = WINCOMPATTRDATA()
        data.Attribute = WCA_ACCENT_POLICY
        data.Data = ctypes.pointer(accent)
        data.SizeOfData = ctypes.sizeof(accent)
        set_wca = ctypes.windll.user32.SetWindowCompositionAttribute
        set_wca.argtypes = [wintypes.HWND, ctypes.POINTER(WINCOMPATTRDATA)]
        set_wca(int(hwnd), ctypes.byref(data))
    except Exception as e:
        logger.debug("disable_acrylic failed: %s", e)


# --- extended-style helpers (topmost / click-through) ----------------- #

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

HWND_TOPMOST = -1
HWND_TOP = 0
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040


def _get_exstyle(hwnd):
    return ctypes.windll.user32.GetWindowLongW(int(hwnd), GWL_EXSTYLE)


def _set_exstyle(hwnd, style):
    ctypes.windll.user32.SetWindowLongW(int(hwnd), GWL_EXSTYLE, style)


def set_click_through(hwnd, on=True):
    """Make the window ignore mouse input (clicks fall through to whatever
    is underneath) and never steal focus. The gaze overlay is driven by
    gaze + gestures, never the mouse.

    NOTE: deliberately does NOT add WS_EX_LAYERED — a Qt translucent
    surface is already DWM-composited, and forcing WS_EX_LAYERED on top of
    it (without SetLayeredWindowAttributes) makes the window render blank.
    WS_EX_TRANSPARENT alone is enough for click-through on an
    alpha-channel window."""
    try:
        ex = _get_exstyle(hwnd)
        if on:
            ex |= WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
        else:
            ex &= ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
        _set_exstyle(hwnd, ex)
        return True
    except Exception as e:
        logger.debug("set_click_through failed: %s", e)
        return False


def set_topmost(hwnd):
    try:
        ctypes.windll.user32.SetWindowPos(
            int(hwnd), HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        return True
    except Exception as e:
        logger.debug("set_topmost failed: %s", e)
        return False


WS_EX_TOPMOST = 0x00000008


def ensure_topmost_flag(hwnd):
    """Restore WS_EX_TOPMOST via SetWindowLong ONLY (no z-order move).

    When a CBT-hook app (hvk) vetoes SetWindowPos(HWND_TOPMOST), the flag
    can stay cleared forever; polling code that keys off the missing flag
    then re-raises with SetWindowPos every tick, and that constant window-
    manager churn visibly degrades the overlay's animation smoothness.
    SetWindowLong is not routed through the CBT SetWindowPos filter, so the
    style bit itself can always be restored — which stops the churn while
    the WM_WINDOWPOSCHANGING filter + intruder check keep protecting the
    actual stacking. Returns True if the flag is set afterwards."""
    try:
        ex = _get_exstyle(hwnd)
        if not (ex & WS_EX_TOPMOST):
            _set_exstyle(hwnd, ex | WS_EX_TOPMOST)
        return bool(_get_exstyle(hwnd) & WS_EX_TOPMOST)
    except Exception as e:
        logger.debug("ensure_topmost_flag failed: %s", e)
        return False


_GW_HWNDPREV = 3


def covered_by_nontopmost(hwnd):
    """True if a visible NON-topmost window overlaps `hwnd` from above.

    A non-topmost window can only sit above us if we have effectively
    dropped out of the topmost band — genuinely-topmost peers (on-screen
    keyboards etc.) are meant to be on top and are skipped. Used to decide
    whether an actual z-order raise is needed (so callers don't churn
    SetWindowPos on quiet ticks)."""
    try:
        import ctypes.wintypes as wintypes
        u = ctypes.windll.user32
        orc = wintypes.RECT()
        u.GetWindowRect(int(hwnd), ctypes.byref(orc))
        h = int(hwnd)
        for _ in range(60):
            h = u.GetWindow(h, _GW_HWNDPREV)     # toward the top
            if not h:
                return False
            if not u.IsWindowVisible(h):
                continue
            if u.GetWindowLongW(h, GWL_EXSTYLE) & WS_EX_TOPMOST:
                continue                          # topmost peer — fine
            r = wintypes.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            if (r.right - r.left) < 60 or (r.bottom - r.top) < 40:
                continue                          # zero-area helper wnd
            iw = min(orc.right, r.right) - max(orc.left, r.left)
            ih = min(orc.bottom, r.bottom) - max(orc.top, r.top)
            if iw > 0 and ih > 0:
                return True
    except Exception:
        pass
    return False


def force_top(hwnd):
    """Raise to the very top of the topmost band — robust against global
    always-on-top utilities.

    Some always-on-top tools (notably Hot Virtual Keyboard) install a
    system WH_CBT hook that VETOES any `SetWindowPos(HWND_TOPMOST)` reorder
    from other apps (the call just returns FALSE), so a plain topmost
    re-assert silently fails and the overlay stays stuck behind whatever
    covered it. `HWND_TOP` is NOT filtered by that hook and still raises the
    window to the front of its band. We keep the WS_EX_TOPMOST flag (so
    HWND_TOP raises us within the *topmost* band, above normal windows like
    Explorer): re-assert the flag best-effort first, then HWND_TOP.

    Returns True if the HWND_TOP raise succeeded."""
    try:
        u = ctypes.windll.user32
        flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
        u.SetWindowPos(int(hwnd), HWND_TOPMOST, 0, 0, 0, 0, flags)
        return bool(u.SetWindowPos(int(hwnd), HWND_TOP, 0, 0, 0, 0, flags))
    except Exception as e:
        logger.debug("force_top failed: %s", e)
        return False
