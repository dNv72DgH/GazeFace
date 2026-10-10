"""Low-level mouse output for macro steps: wheel, drag, long press, swipe.

Everything here goes through SendInput directly rather than through
PyDirectInput, for two reasons:

  * PyDirectInput 1.0.4 simply has no scroll/wheel function. The macro
    wheel step called `pydirectinput.scroll(...)` inside a bare
    `except: pass`, so every wheel step raised AttributeError and was
    silently swallowed — the "wheel does nothing" bug, with nothing in
    the log to show for it.
  * A held button (drag) and a timed press (long press / swipe) need
    down and up as separate events with our own timing in between, which
    is the opposite of what PyDirectInput's click() is for.

Plain clicks are deliberately NOT routed through here: those still go via
pydirectinput.click(), whose single down|up SendInput packet is what the
taskbar and other shell surfaces accept (see Launcher._press_button).
"""

import ctypes
import logging
import threading
import time
from ctypes import wintypes

from src import window_drag

logger = logging.getLogger("MouseInput")

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x01000
WHEEL_DELTA = 120
WHEEL_DIRS = ("up", "down", "left", "right")

BUTTONS = ("left", "middle", "right")
_DOWN = {"left": MOUSEEVENTF_LEFTDOWN, "right": MOUSEEVENTF_RIGHTDOWN,
         "middle": MOUSEEVENTF_MIDDLEDOWN}
_UP = {"left": MOUSEEVENTF_LEFTUP, "right": MOUSEEVENTF_RIGHTUP,
       "middle": MOUSEEVENTF_MIDDLEUP}

# Swipe distances. Fractions of screen height rather than fixed pixels so
# a swipe means the same gesture on a 1080p laptop and a 4K panel.
SWIPE_FRACS = {"small": 0.10, "medium": 0.25, "large": 0.45}
DEFAULT_SWIPE = "medium"
# Swipe travel is sent as many small moves: a single jump lands as a
# teleport and phone-mirroring apps read it as a tap, not a swipe.
SWIPE_STEPS = 24
SWIPE_MS = 220
# Gap between letting the button up and putting the pointer back, so the
# release is processed at the END of the travel rather than at the start.
SWIPE_RETURN_DELAY_S = 0.05
DEFAULT_LONG_PRESS_MS = 500

DIRECTIONS = {
    "N": (0.0, -1.0), "NE": (0.7071, -0.7071), "E": (1.0, 0.0),
    "SE": (0.7071, 0.7071), "S": (0.0, 1.0), "SW": (-0.7071, 0.7071),
    "W": (-1.0, 0.0), "NW": (-0.7071, -0.7071),
}
# Arrow shown in the UI for each compass point.
DIR_ARROWS = {"N": "↑", "NE": "↗", "E": "→", "SE": "↘",
              "S": "↓", "SW": "↙", "W": "←", "NW": "↖"}


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _send(flags, data=0, dx=0, dy=0):
    try:
        mi = _MOUSEINPUT(dx, dy, data, flags, 0, None)
        inp = _INPUT(INPUT_MOUSE)
        inp.mi = mi
        n = ctypes.windll.user32.SendInput(
            1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        return n == 1
    except Exception as e:
        logger.error("SendInput failed (flags=0x%x): %s", flags, e)
        return False


def cursor_pos():
    pt = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


# --- click ------------------------------------------------------------- #

def click(btn="left"):
    """One click: DOWN and UP in a single SendInput, dwExtraInfo = 0.

    Both halves of that matter, and both were measured rather than
    guessed (tools/input_capture.py, watching this app and a working
    assistive tool click the same target through one low-level hook):

      * ONE packet, so the press lasts 0 ms. That is what the taskbar and
        the other shell surfaces accept, and it is also exactly what the
        working tool emits — a split DOWN/wait/UP is NOT what it does, so
        press duration is not the thing that separates them.
      * dwExtraInfo = 0. This is the ONLY field that differed between the
        two. PyDirectInput passes `ctypes.pointer(extra)`, which puts the
        ADDRESS of a local variable in the field: a different garbage
        value on every click. Windows reads the top bits of that field to
        recognise pen/touch injection, and security and sandbox layers use
        it as their own marker, so shipping a random number there is
        indefensible even where it happens to be harmless.

    Kept separate from long_press(): that one deliberately holds the
    button, which is a different gesture, not a slower click.
    """
    b = str(btn or "left").lower()
    if b not in BUTTONS:
        b = "left"
    return _send(_DOWN[b] | _UP[b])


def button_down(btn="left"):
    """Press and keep holding. For gesture press-and-hold, not for drags
    (drag_start owns the drag state)."""
    b = str(btn or "left").lower()
    if b not in BUTTONS:
        b = "left"
    # A gesture held on our OWN title bar is the deadlock case too (this
    # is the path the user actually hit — a hold-mode left button, not
    # the drag macro). Same treatment as drag_start: move the window
    # ourselves, press nothing. Left only: the right button on a caption
    # opens the system menu, which is a different modal loop.
    if b == "left":
        x, y = cursor_pos()
        if window_drag.begin(x, y):
            return True
    return _send(_DOWN[b])


def button_up(btn="left"):
    b = str(btn or "left").lower()
    if b not in BUTTONS:
        b = "left"
    if b == "left" and window_drag.end("button up"):
        return True
    return _send(_UP[b])


# --- wheel ------------------------------------------------------------ #

def wheel(direction, notches=1):
    """Scroll `notches` notches in `direction` (up/down/left/right).

    Horizontal scrolling is a separate event (MOUSEEVENTF_HWHEEL), not a
    sign on the vertical one, so the two axes are dispatched separately.
    """
    d = str(direction or "up").strip().lower()
    if d not in WHEEL_DIRS:
        logger.error("wheel: unknown direction %r", direction)
        return False
    try:
        n = int(notches)
    except (TypeError, ValueError):
        n = 1
    if n <= 0:
        return False
    horiz = d in ("left", "right")
    flag = MOUSEEVENTF_HWHEEL if horiz else MOUSEEVENTF_WHEEL
    # Positive is up / right, negative is down / left.
    step = WHEEL_DELTA if d in ("up", "right") else -WHEEL_DELTA
    ok = True
    for _ in range(n):
        # One notch per event: apps that count notches (rather than
        # summing mouseData) miss a single large delta.
        ok = _send(flag, data=step & 0xFFFFFFFF) and ok
        time.sleep(0.008)
    return ok


# --- drag (a button held across macro steps) --------------------------- #
#
# "Drag" is not one action but a mode: the button goes down and STAYS
# down so head tracking can move the pointer, and a later step drops it.
# It therefore outlives the macro that started it and needs process-wide
# state plus a guaranteed release — a button left down would make the
# desktop unusable for someone who cannot reach for a real mouse.

_lock = threading.Lock()
_held = None            # button currently held by a drag, or None
_held_since = 0.0       # time.time() the hold began, for the stuck-input
                        # janitor: a drag is a deliberate hold, but it
                        # must still time out like any other held button.


def is_dragging():
    return _held is not None


def held_button():
    return _held


def held_since():
    """time.time() the current drag started, or 0.0 if none."""
    return _held_since if _held is not None else 0.0


def drag_start(btn):
    """Press and hold `btn`. A drag already in progress is dropped first."""
    global _held, _held_since
    btn = str(btn or "left").lower()
    if btn not in BUTTONS:
        btn = "left"
    with _lock:
        prev = _held
        if prev is not None:
            _send(_UP[prev])
            _held = None
            _held_since = 0.0
        if prev is None and window_drag.active():
            # A window drag is a drag too: a second drag command ends it,
            # matching the physical-button behaviour below.
            window_drag.end("drag command")
            return False
        if prev is not None:
            # A drag command while dragging ends the drag rather than
            # starting a new one — dragging is a mode you toggle, and
            # the alternative (drop here, immediately grab again) is not
            # something anyone would ask a macro for.
            logger.info("drag %s released (drag command)", prev)
            return False
        # Our OWN title bar must never be pressed: that starts Windows'
        # modal move loop on the tk main thread, which is the thread that
        # would later release the button. See src/window_drag.py.
        x, y = cursor_pos()
        if window_drag.begin(x, y):
            return True
        if _send(_DOWN[btn]):
            _held = btn
            _held_since = time.time()
            logger.info("drag %s started (button held)", btn)
            return True
    return False


def drag_release(why=""):
    """Release a held drag. True if one was actually held."""
    global _held, _held_since
    with _lock:
        if window_drag.end(why):
            return True
        btn = _held
        if btn is None:
            return False
        _send(_UP[btn])
        _held = None
        _held_since = 0.0
    logger.info("drag %s released%s", btn, (" (%s)" % why) if why else "")
    return True


# --- phone-style gestures --------------------------------------------- #

def long_press(btn="left", ms=DEFAULT_LONG_PRESS_MS):
    """Hold a button in place for `ms`, then release."""
    btn = str(btn or "left").lower()
    if btn not in BUTTONS:
        btn = "left"
    try:
        hold = max(0, int(ms)) / 1000.0
    except (TypeError, ValueError):
        hold = DEFAULT_LONG_PRESS_MS / 1000.0
    # A drag and a long press cannot both own the button.
    drag_release("long press")
    if not _send(_DOWN[btn]):
        return False
    try:
        time.sleep(hold)
    finally:
        _send(_UP[btn])
    return True


def swipe_distance_px(dist, screen_h=None):
    """Resolve 'small'/'medium'/'large' or a literal pixel count.

    The screen height comes from the display mode, not GetSystemMetrics:
    the latter is DPI-virtualised for a process that is not per-monitor
    aware (a 3840x2160 panel at 150% reads back as 1440 high), while the
    SetCursorPos calls the swipe is made of always move in real pixels.
    Mixing the two made every swipe 1/scale too short.
    """
    if screen_h is None:
        try:
            from src import display_info
            screen_h = display_info.primary_size()[1]
        except Exception:
            screen_h = 1080
    if isinstance(dist, (int, float)) and not isinstance(dist, bool):
        return max(1, int(dist))
    key = str(dist or DEFAULT_SWIPE).strip().lower()
    if key in SWIPE_FRACS:
        return max(1, int(screen_h * SWIPE_FRACS[key]))
    try:
        return max(1, int(float(key)))          # custom, given in pixels
    except ValueError:
        return max(1, int(screen_h * SWIPE_FRACS[DEFAULT_SWIPE]))


def swipe(direction, dist=DEFAULT_SWIPE, btn="left", ms=SWIPE_MS):
    """Press, travel `dist` in `direction`, release, return to the start.

    The pointer goes home afterwards because head tracking has no way to
    walk it back: after a swipe the cursor would sit wherever the gesture
    ended, so a second swipe would start from there and march off the
    screen. Putting it back makes swipes repeatable from one spot, which
    is how they are used (paging through a phone screen).
    """
    d = str(direction or "N").strip().upper()
    if d not in DIRECTIONS:
        logger.error("swipe: unknown direction %r", direction)
        return False
    btn = str(btn or "left").lower()
    if btn not in BUTTONS:
        btn = "left"
    vx, vy = DIRECTIONS[d]
    px = swipe_distance_px(dist)
    x0, y0 = cursor_pos()
    tx, ty = x0 + vx * px, y0 + vy * px
    drag_release("swipe")
    if not _send(_DOWN[btn]):
        return False
    try:
        try:
            per = max(0.0, float(ms) / 1000.0) / SWIPE_STEPS
        except (TypeError, ValueError):
            per = (SWIPE_MS / 1000.0) / SWIPE_STEPS
        for i in range(1, SWIPE_STEPS + 1):
            f = i / float(SWIPE_STEPS)
            ctypes.windll.user32.SetCursorPos(
                int(round(x0 + (tx - x0) * f)),
                int(round(y0 + (ty - y0) * f)))
            time.sleep(per)
    finally:
        _send(_UP[btn])
        # After the button is up, so the return trip is a plain move and
        # never gets read as part of the swipe.
        time.sleep(SWIPE_RETURN_DELAY_S)
        try:
            ctypes.windll.user32.SetCursorPos(x0, y0)
        except Exception:
            pass
    logger.info("swipe %s %dpx (returned to %d,%d)", d, px, x0, y0)
    return True
