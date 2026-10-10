"""Dragging this app's own windows without entering Windows' move loop.

The deadlock this avoids
------------------------
Pressing a mouse button on a title bar makes Windows run its modal
move/size loop inside DefWindowProc, on the thread that owns the window.
For every other application that is somebody else's thread and nothing
here notices. For OUR OWN windows it is the tk main thread — the same
thread that runs the gesture pipeline.

So a face-driven drag on our own title bar wedges the pipeline while a
mouse button is held, and the button-up that would end the modal loop is
produced by... the pipeline. The app pins its own pointer down and
disables the only machinery that could lift it. The user's pointer stops
responding until they reach for a physical mouse, which for someone who
drives this app with their face is not a recovery.

The fix is to never start that loop: when a drag would begin on one of
our own captions, no button is pressed at all. The window is moved
directly with SetWindowPos as the cursor moves, from the pipeline tick
that is already running. Same gesture, same result on screen, no modal
loop, no held button.

Scope, deliberately narrow
--------------------------
ONLY our own windows, and ONLY the caption. A caption in another process
runs its modal loop on that process's thread and cannot wedge us, so it
is left completely alone and behaves as a normal drag. Resize borders
still enter the modal loop; they are rarer, and the stuck-input janitor's
deadlock breaker (see keybinder.STALL_HOLD_RELEASE_S) recovers those in
about two seconds.
"""

import ctypes
import logging
import os
from ctypes import wintypes

logger = logging.getLogger("WindowDrag")

HTCAPTION = 2
WM_NCHITTEST = 0x0084

SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010

# Private loader with real prototypes. Two reasons, both learned the hard
# way in this codebase: setting argtypes on the shared ctypes.windll
# rewrites the signature for every other module in the process (it broke
# pydirectinput once), and an HWND returned without a restype comes back
# as a truncated signed int.
_u = ctypes.WinDLL("user32", use_last_error=True)
_u.WindowFromPoint.restype = wintypes.HWND
_u.WindowFromPoint.argtypes = [wintypes.POINT]
_u.GetAncestor.restype = wintypes.HWND
_u.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
_u.GetWindowThreadProcessId.restype = wintypes.DWORD
_u.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                        ctypes.POINTER(wintypes.DWORD)]
_u.SendMessageW.restype = ctypes.c_longlong
_u.SendMessageW.argtypes = [wintypes.HWND, ctypes.c_uint,
                            ctypes.c_ulonglong, ctypes.c_longlong]
_u.GetWindowRect.restype = wintypes.BOOL
_u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_u.SetWindowPos.restype = wintypes.BOOL
_u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                            ctypes.c_int, ctypes.c_int, ctypes.c_int,
                            ctypes.c_uint]
_u.IsWindow.restype = wintypes.BOOL
_u.IsWindow.argtypes = [wintypes.HWND]
_u.GetClassNameW.restype = ctypes.c_int
_u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_u.GetCursorPos.restype = wintypes.BOOL
_u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]

_GA_ROOT = 2

# Active drag: (hwnd, window_left, window_top, cursor_x, cursor_y)
_state = None


_HIT_NAMES = {
    -2: "NOWHERE", -1: "TRANSPARENT", 0: "NOWHERE", 1: "CLIENT",
    2: "CAPTION", 3: "SYSMENU", 4: "SIZE", 5: "MENU", 6: "HSCROLL",
    7: "VSCROLL", 8: "MINBUTTON", 9: "MAXBUTTON", 10: "LEFT", 11: "RIGHT",
    12: "TOP", 13: "TOPLEFT", 14: "TOPRIGHT", 15: "BOTTOM",
    16: "BOTTOMLEFT", 17: "BOTTOMRIGHT", 18: "BORDER", 20: "CLOSE",
}

_last_decline = None       # dedupes the "why not" log


def _make_lparam(x, y):
    """MAKELPARAM for screen coordinates that may be NEGATIVE.

    A window can sit at a negative x on a multi-monitor desktop — this
    app's own main window was measured at x = -26. Packing that with
    `x & 0xFFFF` turns it into a large positive number and the hit test
    then answers about a point nowhere near the cursor, so the caption is
    never recognised. The halves have to be packed as SIGNED 16-bit.
    """
    lo = ctypes.c_ushort(int(x) & 0xFFFF).value
    hi = ctypes.c_ushort(int(y) & 0xFFFF).value
    return ctypes.c_longlong((hi << 16) | lo).value


def _describe(hwnd):
    buf = ctypes.create_unicode_buffer(64)
    _u.GetClassNameW(hwnd, buf, 64)
    pid = wintypes.DWORD()
    _u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return "hwnd=0x%X cls=%s pid=%s" % (hwnd, buf.value, pid.value)


def own_caption_at(x, y, why=False):
    """HWND of OUR window whose caption is at (x, y), else None.

    The hit test is asked of the window itself (WM_NCHITTEST), rather
    than guessed from the window rect, so the system menu box, the
    minimise/maximise/close buttons and the resize borders all report
    themselves correctly and are left out of this.

    `why=True` logs the reason for a refusal (rate-limited to changes),
    because "the window will not grab" has several possible causes that
    look identical from outside.
    """
    global _last_decline

    def decline(reason):
        global _last_decline
        if why and reason != _last_decline:
            _last_decline = reason
            logger.info("caption drag declined at %d,%d: %s", x, y, reason)
        return None

    try:
        pt = wintypes.POINT(int(x), int(y))
        hwnd = _u.WindowFromPoint(pt)
        if not hwnd:
            return decline("no window under the cursor")
        top = _u.GetAncestor(hwnd, _GA_ROOT) or hwnd
        pid = wintypes.DWORD()
        _u.GetWindowThreadProcessId(top, ctypes.byref(pid))
        if int(pid.value) != os.getpid():
            return decline("not our process (%s, ours=%d)"
                           % (_describe(top), os.getpid()))
        # Our own thread's window, so this is a direct call into the
        # window procedure: no cross-thread wait, nothing to hang on.
        hit = int(_u.SendMessageW(top, WM_NCHITTEST, 0, _make_lparam(x, y)))
        if hit != HTCAPTION:
            return decline("hit test says %s (%d) on %s"
                           % (_HIT_NAMES.get(hit, "?"), hit, _describe(top)))
        _last_decline = None
        return top
    except Exception as e:
        return decline("probe raised %s" % e)


def begin(x, y):
    """Start dragging our own window if (x, y) is on its caption.

    True means "the drag is being handled here — do NOT press a physical
    button". False means this is an ordinary drag; the caller proceeds as
    usual.
    """
    global _state
    hwnd = own_caption_at(x, y, why=True)
    if not hwnd:
        return False
    rc = wintypes.RECT()
    if not _u.GetWindowRect(hwnd, ctypes.byref(rc)):
        return False
    _state = (hwnd, rc.left, rc.top, int(x), int(y))
    logger.info("window drag started on our own caption "
                "(hwnd=0x%X at %d,%d) — no button pressed, so Windows' "
                "modal move loop never runs", hwnd, rc.left, rc.top)
    return True


def active() -> bool:
    return _state is not None


_moves = 0
_fails = 0


def follow_cursor():
    """Move the dragged window to wherever the pointer actually is.

    Reads the cursor itself rather than being handed a position, and is
    driven by a loop that ALWAYS runs.

    The first version was wired into the cursor-movement path in
    mouse_controller, which looked natural and was wrong: that path
    returns early whenever motion is deliberately frozen (pointer jump,
    double-click freeze, motion-freeze, gaze move, gaze grid). With no
    physical button pressed either, the window then followed nothing at
    all — neither Windows' drag nor ours — and the window simply could
    not be grabbed. Whoever moved the pointer, the window has to follow
    it, so the pointer is the thing to read.
    """
    if _state is None:
        return
    pt = wintypes.POINT()
    try:
        if not _u.GetCursorPos(ctypes.byref(pt)):
            return
    except Exception:
        return
    update(pt.x, pt.y)


def update(x, y):
    """Move the window to follow the cursor. Safe to call every tick."""
    global _moves, _fails
    if _state is None:
        return
    hwnd, wl, wt, cx, cy = _state
    try:
        if not _u.IsWindow(hwnd):
            end("window closed")
            return
        ok = _u.SetWindowPos(hwnd, None, wl + (int(x) - cx),
                             wt + (int(y) - cy), 0, 0,
                             SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        # The return value is checked, and the first failure is logged,
        # because the first version of this ignored it: the drag started,
        # reported success, and the window never moved — with nothing in
        # the log to say so.
        if ok:
            _moves += 1
        else:
            _fails += 1
            if _fails == 1:
                logger.error("window drag: SetWindowPos refused "
                             "(hwnd=0x%X, WinError %d) — the window will "
                             "not follow the pointer",
                             hwnd, ctypes.get_last_error())
    except Exception as e:
        logger.error("window drag update failed: %s", e)
        end("update failed")


def end(why="") -> bool:
    """Finish the drag. True if one was actually in progress."""
    global _state, _moves, _fails
    if _state is None:
        return False
    hwnd = _state[0]
    moves, fails = _moves, _fails
    _moves = _fails = 0
    _state = None
    # The move count is the thing worth reporting: a drag that starts,
    # ends, and moved the window zero times looks identical in the log to
    # one that worked, which is exactly how the head-mode path went
    # unnoticed.
    logger.info("window drag ended (hwnd=0x%X, %d moves, %d refused)%s",
                hwnd, moves, fails, (" — %s" % why) if why else "")
    return True
