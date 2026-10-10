# Robust foreground-window activation.
#
# A bare SetForegroundWindow from a background process FAILS silently in
# most cases (Windows only grants it to the process that owns the current
# foreground window or received recent input) — that was the "task switch /
# paste sometimes does nothing" bug: the gaze grids run click-through and
# never own the foreground, so their activation calls were routinely
# rejected. This helper verifies the switch and escalates through the two
# classic workarounds (Alt-key nudge, AttachThreadInput).
import ctypes
import logging
import time

logger = logging.getLogger("WinFocus")

SW_SHOW = 5
SW_RESTORE = 9
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002


def focus_window(hwnd, timeout_s=0.8):
    """Bring `hwnd` to the foreground; True once it really is foreground.

    Restores only when minimised (SW_RESTORE on an already-maximised
    window would knock it back to 'normal' size)."""
    u = ctypes.windll.user32
    try:
        hwnd = int(hwnd)
        if not u.IsWindow(hwnd):
            return False
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, SW_RESTORE)
        else:
            u.ShowWindow(hwnd, SW_SHOW)
        deadline = time.monotonic() + max(0.1, float(timeout_s))
        attempt = 0
        while True:
            u.SetForegroundWindow(hwnd)
            time.sleep(0.03)
            if u.GetForegroundWindow() == hwnd:
                return True
            if time.monotonic() >= deadline:
                break
            attempt += 1
            if attempt == 2:
                # A synthetic Alt tap marks this thread as "recent input",
                # which makes Windows grant the next SetForegroundWindow.
                u.keybd_event(VK_MENU, 0, 0, 0)
                u.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
            elif attempt == 4:
                # Attach to the current foreground thread's input queue —
                # from "inside" the queue the switch is always allowed.
                fg = u.GetForegroundWindow()
                if fg:
                    cur = ctypes.windll.kernel32.GetCurrentThreadId()
                    tid = u.GetWindowThreadProcessId(fg, None)
                    if tid and u.AttachThreadInput(tid, cur, True):
                        u.SetForegroundWindow(hwnd)
                        u.AttachThreadInput(tid, cur, False)
            time.sleep(0.05)
        ok = u.GetForegroundWindow() == hwnd
        if not ok:
            logger.warning("focus_window: could not raise hwnd=%s", hwnd)
        return ok
    except Exception as e:
        logger.error("focus_window failed: %s", e)
        return False
