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
import concurrent.futures as futures
import ctypes
from ctypes import wintypes
import logging
import threading
import time
import tkinter as tk
from collections import deque

import numpy as np
import numpy.typing as npt
import pyautogui

import src.utils as utils
from src.accel_graph import SigmoidAccel
from src.config_manager import ConfigManager
from src.detectors.facemesh import FaceMesh
from src.singleton_meta import Singleton

logger = logging.getLogger("MouseController")

pyautogui.PAUSE = 0
pyautogui.FAILSAFE = False

# Max buffer number for apply smoothing.
N_BUFFER = 100

# --- Absolute cursor positioning (gaze mode) ---
# gaze-mouse drives the cursor with user32.SetCursorPos for crisp jumps to
# absolute screen positions. pyautogui.moveTo would do the same but with
# additional overhead per call.
try:
    _user32 = ctypes.windll.user32
    # Ensure screen metrics + cursor positioning are in physical pixels on
    # high-DPI displays. Safe to call multiple times.
    try:
        _user32.SetProcessDPIAware()
    except Exception:
        pass
except Exception:
    _user32 = None


# --- Keep the monitor awake while the head/gaze tracker is actively moving
# the cursor. SetCursorPos and pyautogui move the pointer WITHOUT synthesising
# a real input event, so Windows' idle timer keeps counting and the display
# powers off mid-use even though the cursor is visibly moving. Pinging
# SetThreadExecutionState with ES_DISPLAY_REQUIRED (WITHOUT ES_CONTINUOUS)
# resets that idle timer exactly like real input would — and if the display
# has already slept, it turns it back on. One ping per actual move; stop
# moving (user looks away / leaves) and the pings stop, so the monitor sleeps
# normally instead of being pinned on forever.
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002
_last_awake_ping = 0.0
_last_cursor_xy = (-1, -1)


def _keep_display_awake() -> None:
    if _user32 is None:
        return
    global _last_awake_ping
    now = time.time()
    # The idle timer resets on each call, so once a second is ample and
    # avoids a syscall on every one of the ~30 frames/s.
    if now - _last_awake_ping < 1.0:
        return
    _last_awake_ping = now
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(
            _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED)
    except Exception:
        pass


# --- Make Windows SHOW the pointer at all ---
# After a fresh boot (or a logon where no mouse has been touched yet)
# Windows keeps the cursor hidden and only reveals it when a genuine mouse
# INPUT event arrives. SetCursorPos / pyautogui move the pointer without
# producing one, so an autostarted GazeFace could be tracking correctly
# with nothing visible on screen until the user nudged a physical mouse
# ("スタートアップ自動起動でポインタが現れない"). One relative SendInput
# move of (0, 0) is a real mouse event that reveals the cursor without
# shifting it. Done once, lazily, on the first move we make.
#
# DO NOT "IMPROVE" THIS INTO A RETRY LOOP.
#
# A version that verified the result with GetCursorInfo and re-nudged with
# a real +1/-1 delta whenever the pointer looked hidden shipped in dist104
# and BROKE THE MOUSE: the pointer vanished and the physical mouse stopped
# working, and it stayed that way across a graphics-driver reset. Reverting
# to this single (0, 0) nudge fixed it. The exact mechanism was never
# established - what is established is that this version works and that
# one did not.
#
# The wrong conclusion drawn at the time is worth recording too: because
# the broken build made even the real mouse look useless, it was taken as
# evidence of a system-level cursor fault, and a hardware HID device was
# investigated to work around it. That whole line of reasoning rested on a
# symptom this file was itself producing.
#
# What IS re-armed - still one (0, 0) nudge each time, never a loop:
#   * when our desktop comes back to the front (sign-in, unlock, a UAC
#     prompt closing), because Windows hides the pointer across those.
#     A copy started at sign-in makes its first move while the sign-in
#     screen still has the input, so its single nudge landed there and
#     the pointer stayed hidden until the physical mouse moved;
#   * when GetCursorInfo reports the pointer SUPPRESSED (hidden by
#     Windows until mouse input, not hidden by an application), at most
#     once per such episode - read-only polling, once a second.
_INPUT_MOUSE = 0
_MOUSEEVENTF_MOVE = 0x0001
_cursor_revealed = False
_revealed_gen = 0
_CURSOR_SUPPRESSED = 0x00000002
_suppress_check_ts = 0.0
_suppress_nudged = False


class _CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("flags", ctypes.c_ulong),
                ("hCursor", ctypes.c_void_p),
                ("ptScreenPos", wintypes.POINT)]


# Private loader: GetCursorInfo's prototype must not leak into the shared
# ctypes.windll.user32 (see src/uipi.py).
try:
    _u32_ci = ctypes.WinDLL("user32", use_last_error=True)
    _u32_ci.GetCursorInfo.restype = wintypes.BOOL
    _u32_ci.GetCursorInfo.argtypes = [ctypes.POINTER(_CURSORINFO)]
except Exception:
    _u32_ci = None


def _cursor_suppressed():
    if _u32_ci is None:
        return False
    ci = _CURSORINFO()
    ci.cbSize = ctypes.sizeof(ci)
    try:
        if not _u32_ci.GetCursorInfo(ctypes.byref(ci)):
            return False
    except Exception:
        return False
    return bool(ci.flags & _CURSOR_SUPPRESSED)


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]


def _reveal_cursor() -> None:
    """Emit one real (0, 0) mouse move so Windows un-hides the pointer."""
    global _cursor_revealed, _revealed_gen
    global _suppress_check_ts, _suppress_nudged
    if _user32 is None:
        return
    try:
        from src import desktop_guard
        ours, gen = desktop_guard.front_state()
    except Exception:
        ours, gen = True, _revealed_gen
    if not ours:
        return              # a nudge now would land on the other desktop
    if gen != _revealed_gen:
        _revealed_gen = gen
        _cursor_revealed = False
        logging.info("Our desktop is back in front - showing the pointer "
                     "again")
    if _cursor_revealed:
        now = time.monotonic()
        if now - _suppress_check_ts < 1.0:
            return
        _suppress_check_ts = now
        if not _cursor_suppressed():
            _suppress_nudged = False        # episode over
            return
        if _suppress_nudged:
            return                          # one nudge per episode
        _suppress_nudged = True
        # Windows does not take SendInput as "a mouse moved" here: the
        # (0, 0) nudge below went out after sign-in and the pointer stayed
        # hidden until the physical mouse was touched. The vmulti virtual
        # mouse IS a mouse to Windows, and this wake is the only thing the
        # app uses it for (src/vmulti_wake.py). Off the mouse thread: the
        # device lookup takes a moment. Without the driver it does
        # nothing, and the SendInput nudge still goes out as before.
        logging.info("Pointer is suppressed by Windows - waking it (vmulti "
                     "if installed, plus the (0, 0) nudge)")
        try:
            from src import vmulti_wake
            threading.Thread(target=vmulti_wake.wake_pointer,
                             name="PointerWake", daemon=True).start()
        except Exception as e:
            logging.warning("pointer wake failed to start: %s", e)
    _cursor_revealed = True
    try:
        extra = ctypes.c_ulong(0)
        inp = _INPUT()
        inp.type = _INPUT_MOUSE
        inp.mi = _MOUSEINPUT(0, 0, 0, _MOUSEEVENTF_MOVE, 0,
                             ctypes.pointer(extra))
        _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
    except Exception:
        pass


# Cursor-block reporting. SetCursorPos returning FALSE is the one place
# the app can SEE the restriction the user notices as "head tracking stops
# when that window is focused": Windows refuses synthetic input — cursor
# moves included, not just clicks — from a process whose integrity level
# is below the foreground window's. Rate-limited because a blocked cursor
# fails on every frame, and it names the blocker so the fix is decidable
# instead of guessed.
_CURSOR_BLOCK_LOG_S = 20.0
# A real block persists for as long as that window keeps focus, whereas a
# readback that disagrees for one frame is just the user's own mouse
# moving in between. Requiring a run of consecutive failures separates
# them without needing a lock.
_CURSOR_BLOCK_RUN = 10
_last_cursor_block_log = 0.0
_cursor_block_run = 0
_cursor_block_total = 0


def _note_cursor_result(ok, want_x, want_y):
    """Track whether the cursor actually went where we put it.

    Not just the return value: SetCursorPos does not reliably report the
    UIPI refusal (it can answer TRUE and do nothing), so the position is
    read back and compared. That readback is the only way the app can see
    the restriction the user experiences as "head tracking freezes while
    that window is focused".
    """
    global _last_cursor_block_log, _cursor_block_run, _cursor_block_total
    moved = True
    if ok:
        try:
            pt = wintypes.POINT()
            _user32.GetCursorPos(ctypes.byref(pt))
            moved = abs(pt.x - want_x) <= 1 and abs(pt.y - want_y) <= 1
        except Exception:
            return
    if ok and moved:
        _cursor_block_run = 0
        return
    _cursor_block_run += 1
    _cursor_block_total += 1
    if _cursor_block_run < _CURSOR_BLOCK_RUN:
        return
    now = time.time()
    if now - _last_cursor_block_log < _CURSOR_BLOCK_LOG_S:
        return
    _last_cursor_block_log = now
    try:
        from src import uipi
        logging.warning(
            "CURSOR BLOCKED: the pointer will not move (run=%d total=%d "
            "api_ok=%s) — %s",
            _cursor_block_run, _cursor_block_total, bool(ok),
            uipi.describe_block())
    except Exception as e:
        logging.warning("CURSOR BLOCKED (%s)", e)


def _drag_own_window(x=None, y=None) -> None:
    """Carry one of our own windows along with the pointer.

    Only does anything while a caption drag is in progress — see
    src/window_drag.py for why that drag moves the window from here
    instead of letting Windows run its modal move loop.

    Both cursor paths have to call this, and they do not agree on
    coordinates. Gaze mode computes an absolute target and passes it in;
    HEAD mode — the one most people actually use — steers with a relative
    pyautogui.move() and never knows where the pointer ended up. Hooking
    only the absolute path is why the first version of this started the
    drag correctly and then never moved the window a single pixel.
    Called with no arguments, it reads the live position instead.
    """
    try:
        from src import window_drag
        if not window_drag.active():
            return
        if x is None or y is None:
            if _user32 is None:
                return
            pt = wintypes.POINT()
            if not _user32.GetCursorPos(ctypes.byref(pt)):
                return
            x, y = pt.x, pt.y
        window_drag.update(x, y)
    except Exception as e:
        logging.error("own-window drag failed: %s", e)


def _set_cursor_absolute(x: int, y: int, screen_w: int, screen_h: int) -> None:
    global _last_cursor_xy
    x = max(0, min(screen_w - 1, int(x)))
    y = max(0, min(screen_h - 1, int(y)))
    _reveal_cursor()
    if _user32 is not None:
        ok = _user32.SetCursorPos(x, y)
        _note_cursor_result(ok, x, y)
    else:
        pyautogui.moveTo(x, y)
    _drag_own_window(x, y)
    # Only count a genuine position change as activity, so a perfectly
    # still gaze-follow doesn't pin the display on.
    if (x, y) != _last_cursor_xy:
        _last_cursor_xy = (x, y)
        _keep_display_awake()


# Continuous gaze-follow smoothing (used by `_gaze_tick`).
#
# This used to be a plain median over 25 samples (~800 ms at 30 fps). A
# median over a long window does NOT glide: it holds still while the
# minority of new samples pile up, then jumps the instant the window's
# majority flips — the cursor "freezes, then lurches" (the reported
# "reacts late, then suddenly darts as if a buffer overflowed"). The
# launcher's gaze grid felt snappier because it averages only ~5 samples.
#
# Now: a SMALL median rejects single-frame iris outliers, and an EMA on
# top turns the median's step-when-it-tips into a smooth follow — far more
# responsive without reintroducing jitter. Lower window / higher K = more
# responsive and jumpier; higher window / lower K = smoother and laggier.
GAZE_SMOOTH_WINDOW = 5
GAZE_EMA_K = 0.4

# Gaze Jump snap-to-clickable range (px), when the option is on.
SNAP_RADIUS_DEFAULT = 120
# A jump that snapped onto a clickable thing plays its landing animation
# in light orange (the development-time #FF8C1A, a little lighter) instead
# of white / grey, so the user can tell it landed on something (the
# "Visual cue" option).
SNAP_NOTIFY_COLOUR = "#FFA348"
SNAP_NOTIFY_KEY = "pointer_jump_snap_notify"
# Its development-time predecessor, still honoured until the new option
# has been set once.
SNAP_NOTIFY_OLD_KEY = "pointer_jump_snap_debug_color"


def snap_notify_enabled(cfg) -> bool:
    if SNAP_NOTIFY_KEY in cfg:
        return bool(cfg[SNAP_NOTIFY_KEY])
    return bool(cfg.get(SNAP_NOTIFY_OLD_KEY, True))


class MouseController(metaclass=Singleton):

    def __init__(self):
        logger.info("Intialize MouseController singleton")
        self.prev_x = 0
        self.prev_y = 0
        self.curr_track_loc = None
        self.smooth_kernel = None
        self.delay_count = 0
        self.top_count = 0
        self.is_started = False
        self.is_destroyed = False
        self.stop_flag = None
        self.is_active = None
        # Remember which tracking mode we ran last tick so we can flush
        # mode-specific buffers on transition (e.g. pointer-jump entering
        # gaze mode shouldn't median-smooth against stale samples).
        self._last_tracking_mode = None
        # EMA state for the gaze-follow glide (see GAZE_EMA_K). Re-seeded
        # whenever the median buffer is cleared (mode switch / jump).
        self._gaze_ema = None
        # Plain-bool mirror of `is_active` for the worker thread to read.
        # tkinter Variables can only be accessed from the main thread under
        # Python 3.12+, so the worker reads this flag instead.
        self._active_flag = False
        # Pointer-jump state. When _jump_locked is True the per-tick
        # head/gaze updates skip cursor movement so the cursor stays
        # frozen on its current position during the dwell, the snap
        # itself, and a short settle window afterwards.
        self._jump_locked = False
        # Timestamp when _jump_locked last went True, for the stuck-lock
        # watchdog in main_loop. None when unlocked.
        self._jump_locked_since = None
        # Dedicated freeze flag owned solely by the double-click handler.
        # Kept separate from _jump_locked so the two never race (a shared
        # flag previously got stuck True, permanently freezing tracking).
        self._click_freeze = False
        # Motion-freeze: set True by Keybinder while any gesture's value
        # is spiking (the user is starting/holding a gesture), so the
        # cursor doesn't drift from the gesture itself. Auto-expires via
        # `_motion_freeze_until` so a missed clear can't freeze forever.
        self._motion_freeze_until = 0.0
        # Gaze move: True while the directional-move interaction (arrow
        # overlay + continuous move) is running. Like _jump_locked it
        # suppresses head/gaze ticks so no other pointer control fights
        # the move. Owned by a GazeMoveSession.
        self._gaze_move_active = False
        self._gaze_move_session = None
        # True while a gaze grid (launcher / clipboard / task switcher /
        # keyboard) is open — freezes head/gaze cursor like gaze move.
        self._gaze_grid_active = False
        # Set by FrameCamPreview so the launcher can toggle the
        # sub-monitor (must be invoked on the tk main thread).
        self._submonitor_toggle = None
        # Reference to the main tk root, set by MainGui after the UI is
        # built. Needed so the worker thread can schedule the post-jump
        # visual feedback via tk_root.after().
        self._tk_root = None

    def set_tk_root(self, tk_root):
        """Called once from MainGui so begin_pointer_jump can dispatch
        the post-jump feedback Toplevel from a worker thread."""
        self._tk_root = tk_root

    def start(self):
        if not self.is_started:
            logger.info("Start MouseController singleton")
            # Trackpoint buffer x, y (head-mode velocity smoothing).
            self.buffer = np.zeros([N_BUFFER, 2])
            self.accel = SigmoidAccel()
            self.pool = futures.ThreadPoolExecutor(max_workers=1)
            self.screen_w, self.screen_h = pyautogui.size()
            self.calc_smooth_kernel()

            # gaze-mode smoothing buffer (median over the last N estimates,
            # ported from gaze-mouse). Holds screen-space (sx, sy) tuples.
            self.gaze_smooth = deque(maxlen=GAZE_SMOOTH_WINDOW)

            self.is_active = tk.BooleanVar()
            initial_active = bool(ConfigManager().config["auto_play"])
            self.is_active.set(initial_active)
            self._active_flag = initial_active
            # Keep the worker-thread mirror in sync whenever the tk var
            # changes (e.g. via the toggle switch in the UI).
            self.is_active.trace_add(
                "write", lambda *_a: self._sync_active_flag())

            self.stop_flag = threading.Event()
            self.pool.submit(self.main_loop)
            self.is_started = True

    def calc_smooth_kernel(self):
        new_pointer_smooth = ConfigManager().config["pointer_smooth"]
        if self.smooth_kernel is None:
            self.smooth_kernel = utils.calc_smooth_kernel(new_pointer_smooth)

        elif new_pointer_smooth != len(self.smooth_kernel):
            self.smooth_kernel = utils.calc_smooth_kernel(new_pointer_smooth)

        else:
            pass

    def asymmetry_scale(self, vel_x, vel_y):
        # Slider max was raised from 100 -> 1000. To keep the *effective*
        # max-speed at 5x the previous max (not 10x), the per-direction
        # value is multiplied by this coefficient before being applied.
        coef = 0.5

        if vel_x > 0:
            vel_x *= ConfigManager().config["spd_right"] * coef
        else:
            vel_x *= ConfigManager().config["spd_left"] * coef

        if vel_y > 0:
            vel_y *= ConfigManager().config["spd_down"] * coef
        else:
            vel_y *= ConfigManager().config["spd_up"] * coef

        return vel_x, vel_y

    def act(self, track_loc: npt.ArrayLike):
        self.curr_track_loc = track_loc

    def main_loop(self) -> None:
        """ Separate thread for mouse controller. Dispatches by
        `tracking_mode` to either the head-tracking velocity model or
        the gaze-tracking absolute-positioning model (gaze-mouse).
        """

        if self.is_destroyed:
            return

        # Diagnostic state for the heartbeat + transition logger.
        last_heartbeat = time.time()
        last_active = None
        last_mode = None
        last_jump_locked = None
        HEARTBEAT_S = 60.0

        while not self.stop_flag.is_set():
            try:
                # Carry a dragged window along with the pointer. Driven
                # from here, ABOVE every early return below, because this
                # loop runs unconditionally: the per-tick cursor path is
                # skipped whenever motion is deliberately frozen (pointer
                # jump, double-click freeze, motion freeze, gaze move,
                # gaze grid) and while face control is off, and a window
                # drag hung off it stopped following the moment any of
                # those happened — with no button pressed either, so
                # nothing moved the window at all.
                _drag_own_window()
                # --- Heartbeat + transition logging (helps diagnose
                # the intermittent "head tracking + gaze jump stop
                # working" freeze: the log lets us see whether this
                # loop died, got stuck on _active_flag, or hung
                # somewhere in a tick).
                now = time.time()
                if (self._active_flag != last_active
                        or last_mode != ConfigManager().config.get(
                            "tracking_mode", "head")
                        or self._jump_locked != last_jump_locked):
                    logger.info(
                        "MouseController state: active=%s mode=%s "
                        "jump_locked=%s",
                        self._active_flag,
                        ConfigManager().config.get("tracking_mode", "head"),
                        self._jump_locked)
                    last_active = self._active_flag
                    last_mode = ConfigManager().config.get(
                        "tracking_mode", "head")
                    last_jump_locked = self._jump_locked
                if now - last_heartbeat >= HEARTBEAT_S:
                    logger.info(
                        "MouseController heartbeat: alive, active=%s, "
                        "mode=%s, curr_track_loc_set=%s",
                        self._active_flag,
                        ConfigManager().config.get("tracking_mode", "head"),
                        self.curr_track_loc is not None)
                    last_heartbeat = now

                # Stuck-lock watchdog: a legit pointer jump lasts at most
                # ~dwell(5s max) + settle, so if _jump_locked has been
                # True far longer than that, a jump/double-click thread
                # must have died or raced. Force-release so head/gaze
                # tracking can resume instead of being frozen forever.
                if self._jump_locked:
                    if self._jump_locked_since is None:
                        self._jump_locked_since = now
                    elif now - self._jump_locked_since > self.MAX_JUMP_LOCK_S:
                        logger.warning(
                            "Force-releasing stuck _jump_locked after "
                            "%.1fs", now - self._jump_locked_since)
                        self._jump_locked = False
                        self._jump_locked_since = None
                        # Resume from REST: the head moved while frozen, so
                        # zero the buffers or the first tick computes a huge
                        # velocity and the cursor lurches.
                        self.delay_count = 0
                        self.prev_x = 0
                        self.prev_y = 0
                        self.buffer = np.zeros([N_BUFFER, 2])
                        try:
                            self.gaze_smooth.clear()
                        except Exception:
                            pass
                else:
                    self._jump_locked_since = None

                # Read the plain-bool mirror instead of the tk var so we
                # don't touch tkinter from this worker thread (which raises
                # under Python 3.12+).
                if not self._active_flag:
                    # Idle path: sleep longer so we don't burn CPU/battery
                    # while the user has the app open but face control off.
                    time.sleep(0.02)
                    continue

                mode = ConfigManager().config.get("tracking_mode", "head")
                if mode != self._last_tracking_mode:
                    # Transitioning mode — flush per-mode buffers so the
                    # cursor doesn't lurch from stale data.
                    self.gaze_smooth.clear()
                    self.delay_count = 0
                    self.prev_x = 0
                    self.prev_y = 0
                    self.buffer = np.zeros([N_BUFFER, 2])
                    self._last_tracking_mode = mode
                if mode == "gaze":
                    self._gaze_tick()
                else:
                    self._head_tick()
            except Exception as e:
                # Never let a transient error kill the loop — that's what
                # used to require an OS reboot to recover from.
                logger.error("MouseController loop error: %s", e, exc_info=e)
                time.sleep(0.05)

    # ------------------------------------------------------------------ #
    # Head-tracking (velocity model — unchanged behaviour)
    # ------------------------------------------------------------------ #

    def _head_tick(self) -> None:
        # While a pointer-jump is in flight, a double-click is freezing
        # the cursor, or a gesture is mid-motion (motion-freeze), the
        # cursor must stay exactly where it is — no head-driven
        # movement.
        if (self._jump_locked or self._click_freeze or self._motion_frozen()
                or self._gaze_move_active or self._gaze_grid_active):
            time.sleep(0.01)
            return
        # Skip until face tracking provides a valid location. Without
        # this guard, a None curr_track_loc on the very first tick after
        # restart raises and silently kills this thread.
        if self.curr_track_loc is None:
            time.sleep(0.01)
            return

        self.buffer = np.roll(self.buffer, shift=-1, axis=0)
        self.buffer[-1] = self.curr_track_loc

        smooth_px, smooth_py = utils.apply_smoothing(
            self.buffer, self.smooth_kernel)

        vel_x = smooth_px - self.prev_x
        vel_y = smooth_py - self.prev_y

        self.prev_x = smooth_px
        self.prev_y = smooth_py

        self.delay_count += 1
        if self.delay_count < N_BUFFER:
            time.sleep(0.005)
            return

        vel_x, vel_y = self.asymmetry_scale(vel_x, vel_y)

        if ConfigManager().config["mouse_acceleration"]:
            vel_x *= self.accel(vel_x)
            vel_y *= self.accel(vel_y)

        _reveal_cursor()
        pyautogui.move(xOffset=vel_x, yOffset=vel_y)
        # Head mode moves relatively, so the window being dragged has to
        # be told where the pointer landed rather than where we aimed.
        _drag_own_window()
        # A real (>= 1 px) move counts as activity — keeps the monitor from
        # sleeping mid-use. Sub-pixel jitter while holding still does not.
        if abs(vel_x) >= 1 or abs(vel_y) >= 1:
            _keep_display_awake()
        time.sleep(ConfigManager().config["tick_interval_ms"] / 1000)

    # ------------------------------------------------------------------ #
    # Gaze-tracking (gaze-mouse approach:
    #   polynomial calibration -> median smoothing -> absolute SetCursorPos)
    # ------------------------------------------------------------------ #

    # (Calibration loading and feature building moved to
    # src/gaze_mapping.py, which every gaze consumer now shares so the
    # ratio/ray method choice is honoured in one place.)

    def _gaze_tick(self) -> None:
        # Same lock semantics as head mode — keep the cursor still
        # during a pointer-jump, double-click freeze, or gesture motion.
        if (self._jump_locked or self._click_freeze or self._motion_frozen()
                or self._gaze_move_active or self._gaze_grid_active):
            time.sleep(0.01)
            return
        # One shared mapping for every gaze consumer: the calibrated
        # polynomial plus the implicit drift correction.
        from src import gaze_mapping
        pos = gaze_mapping.predict(self.screen_w, self.screen_h)
        if pos is None:
            time.sleep(0.01)
            return
        sx, sy = pos

        # Small median rejects single-frame iris outliers; the EMA on top
        # turns the median's step-when-it-tips into a smooth glide (see the
        # GAZE_SMOOTH_WINDOW / GAZE_EMA_K notes). Re-seed the EMA on the
        # first sample after a buffer clear so it doesn't glide in from a
        # stale position after a mode switch.
        self.gaze_smooth.append((sx, sy))
        arr = np.array(self.gaze_smooth)
        mx, my = np.median(arr, axis=0)
        if len(self.gaze_smooth) <= 1 or self._gaze_ema is None:
            self._gaze_ema = [float(mx), float(my)]
        else:
            self._gaze_ema[0] += (float(mx) - self._gaze_ema[0]) * GAZE_EMA_K
            self._gaze_ema[1] += (float(my) - self._gaze_ema[1]) * GAZE_EMA_K
        ax, ay = self._gaze_ema

        _set_cursor_absolute(int(ax), int(ay),
                             self.screen_w, self.screen_h)

        time.sleep(ConfigManager().config["tick_interval_ms"] / 1000)

    # ------------------------------------------------------------------ #
    # Pointer-jump
    #
    # Semantics (per user spec):
    #   1. Lock the cursor in place the moment the gesture fires.
    #   2. Wait `delay_ms` while collecting gaze samples internally.
    #   3. When the dwell elapses, jump the cursor once to the median
    #      gaze position.
    #   4. Hold the cursor there for a short settle window so the
    #      revert to head tracking doesn't immediately drag the cursor
    #      off the just-set target.
    #   5. Reset head-tracking state and resume.
    # ------------------------------------------------------------------ #

    # How long to keep the cursor pinned at the jump target *after* the
    # snap, so head tracking doesn't immediately move it. ~250 ms is
    # enough to let the head-buffer reset before motion resumes.
    JUMP_SETTLE_MS = 250
    # Time window before the snap whose gaze samples we feed into a
    # median, to avoid landing on a single outlier landmark frame.
    # At 30 fps a 500 ms window collects ~15 independent samples — enough
    # for the median to reject 2–3 outlier frames cleanly. Was 200 ms
    # (only ~6 samples), which left the median vulnerable to single-frame
    # iris-tracking jitter. If pointer_jump_delay_ms is shorter than this
    # window, sampling just spans the whole dwell (handled by the
    # `max(...)` below).
    JUMP_SAMPLE_WINDOW_MS = 500
    # Stuck-lock guard: if _jump_locked has been True longer than this,
    # force-release it (see main_loop) so a wedged jump/double-click thread
    # can't freeze the cursor indefinitely. Per user request: 3 s.
    MAX_JUMP_LOCK_S = 3.0

    def is_gaze_move_active(self) -> bool:
        return self._gaze_move_active

    def begin_gaze_move(self, display_ms: int, speed_px_s: float) -> None:
        """Start the directional Gaze move interaction (overlay + move).
        No-op if one is already running or the UI isn't ready."""
        if self._gaze_move_active:
            return
        if self._tk_root is None or not self.is_started:
            return
        # Blocked while a calibration is on screen — including the
        # jump-then-gaze-move chain, which reaches here directly (bypassing
        # the keybinder guard).
        try:
            from src.gui.gaze_calibrator import GazeCalibrator
            if GazeCalibrator.is_active():
                logger.info("Gaze move skipped: calibration running")
                return
        except Exception:
            pass
        try:
            use_qt = bool((ConfigManager().config or {}).get(
                "qt_overlay", True))
            if use_qt:
                try:
                    from src.gui.qt_overlay.qt_gaze_move import (
                        QtGazeMoveSession)
                    self._gaze_move_session = QtGazeMoveSession(
                        self, self._tk_root, display_ms, speed_px_s,
                        on_done=self._on_gaze_move_done)
                    self._gaze_move_session.start()
                    return
                except Exception as e:
                    logger.warning("Qt gaze move unavailable, using "
                                   "tkinter: %s", e)
            from src.gui.gaze_move import GazeMoveSession
            self._gaze_move_session = GazeMoveSession(
                self, self._tk_root, display_ms, speed_px_s,
                on_done=self._on_gaze_move_done)
            self._gaze_move_session.start()
        except Exception as e:
            logger.error("begin_gaze_move failed: %s", e, exc_info=e)
            self._gaze_move_active = False

    def cancel_gaze_move(self) -> None:
        """End any running Gaze move immediately. Releases the lock
        synchronously so a following action can take the cursor."""
        self._gaze_move_active = False
        s = self._gaze_move_session
        if s is not None:
            try:
                s.cancel()
            except Exception:
                pass

    def _on_gaze_move_done(self):
        self._gaze_move_active = False
        self._gaze_move_session = None
        # Reset head buffers so tracking resumes from rest at the new
        # cursor position instead of lurching.
        self.delay_count = 0
        self.prev_x = 0
        self.prev_y = 0
        self.buffer = np.zeros([N_BUFFER, 2])
        try:
            self.gaze_smooth.clear()
        except Exception:
            pass

    def release_gaze_grid(self) -> None:
        """Resume head/gaze tracking after a gaze grid (launcher / clipboard
        / task switcher) closed, WITHOUT the cursor lurching.

        While the grid is open the cursor is frozen (`_gaze_grid_active`),
        but the user's head keeps moving, so the head-velocity buffers go
        stale. Releasing the freeze without zeroing them makes the first
        tick compute a huge velocity from the accumulated head motion and
        the pointer snaps across the screen ("追いつこうとする" jerk). Zero
        the buffers so motion restarts from rest, exactly like
        `_on_gaze_move_done` / `set_active(True)` do."""
        self.delay_count = 0
        self.prev_x = 0
        self.prev_y = 0
        self.buffer = np.zeros([N_BUFFER, 2])
        try:
            self.gaze_smooth.clear()
        except Exception:
            pass
        # Released last so a tick can't wake on stale buffers in the gap.
        self._gaze_grid_active = False

    def begin_pointer_jump(self, delay_ms: int) -> None:
        if self._gaze_move_active:
            logger.info("Pointer jump ignored: gaze move in progress")
            return
        if self._jump_locked:
            logger.info("Pointer jump already in progress; ignoring repeat")
            return
        if not self.is_started:
            return
        logger.info("Pointer jump: dwell %d ms, then snap to gaze",
                    delay_ms)
        self._jump_locked = True

        def _run():
            target = None
            try:
                # --- Dwell with gaze sampling. The cursor is already
                # frozen because _jump_locked is True; meanwhile we
                # sample where the user is actually looking so the
                # snap target is stable even if MediaPipe drops a
                # frame or returns a momentary outlier.
                samples = []
                from src import gaze_mapping
                dwell_end = time.time() + max(0.0, delay_ms / 1000.0)
                sample_after = max(
                    dwell_end - self.JUMP_SAMPLE_WINDOW_MS / 1000.0,
                    time.time())
                while time.time() < dwell_end:
                    if time.time() >= sample_after:
                        p = gaze_mapping.predict(self.screen_w,
                                                 self.screen_h)
                        if p is not None:
                            samples.append(p)
                    time.sleep(0.02)

                # --- Snap.
                if not samples:
                    # Fall back to a single fresh sample if our sampling
                    # window caught nothing (no face for that period).
                    p = gaze_mapping.predict(self.screen_w, self.screen_h)
                    if p is None:
                        logger.warning(
                            "Pointer jump: no gaze samples available "
                            "during dwell; aborting")
                        return
                    samples.append(p)

                arr = np.array(samples)
                tx, ty = np.median(arr, axis=0)
                tx = int(max(0, min(self.screen_w - 1, tx)))
                ty = int(max(0, min(self.screen_h - 1, ty)))

                # Land on the nearest clickable thing in range, if any
                # (src/click_snap.py); otherwise exactly as before.
                cfg = ConfigManager().config or {}
                snapped = False
                if cfg.get("pointer_jump_snap", False):
                    try:
                        from src import click_snap
                        hit = click_snap.find(
                            tx, ty,
                            int(cfg.get("pointer_jump_snap_radius",
                                        SNAP_RADIUS_DEFAULT)),
                            self.screen_w, self.screen_h)
                        if hit is not None:
                            tx = int(max(0, min(self.screen_w - 1, hit[0])))
                            ty = int(max(0, min(self.screen_h - 1, hit[1])))
                            snapped = True
                    except Exception as e:
                        logger.warning("click snap skipped: %s", e)
                target = (tx, ty)

                _set_cursor_absolute(tx, ty,
                                     self.screen_w, self.screen_h)

                # --- Visual feedback (must be scheduled on tk's main
                # thread; can't touch tk widgets from a worker).
                if self._tk_root is not None:
                    try:
                        self._tk_root.after(
                            0, lambda: self._show_jump_feedback(
                                tx, ty, snapped))
                    except Exception as e:
                        logger.error("schedule feedback failed: %s", e)

                # --- Settle: keep the cursor pinned briefly, then
                # zero the head-tracking buffers so movement resumes
                # from rest at the new position instead of bursting.
                time.sleep(self.JUMP_SETTLE_MS / 1000.0)
                self.delay_count = 0
                self.prev_x = 0
                self.prev_y = 0
                self.buffer = np.zeros([N_BUFFER, 2])
                self.gaze_smooth.clear()
                # Optionally chain straight into a Gaze move so the user
                # can jump-then-glide in one gesture.
                try:
                    then_gm = bool(ConfigManager().config.get(
                        "pointer_jump_then_gaze_move", False))
                except Exception:
                    then_gm = False
            except Exception as e:
                logger.error("Pointer jump failed: %s", e, exc_info=e)
                then_gm = False
            finally:
                self._jump_locked = False
            if then_gm:
                try:
                    cfg = ConfigManager().config
                    self.begin_gaze_move(
                        int(cfg.get("gaze_move_display_ms", 2000)),
                        float(cfg.get("gaze_move_speed", 800)))
                except Exception as e:
                    logger.error("jump->gaze move failed: %s", e)

        t = threading.Thread(target=_run, name="PointerJump", daemon=True)
        t.start()

    def _show_jump_feedback(self, x: int, y: int,
                            snapped: bool = False) -> None:
        """Lightweight animated landing feedback at (x, y).

        Same overall look as before — a persistent centre dot, a
        sonar-style expanding ripple, a four-point reticle that flashes
        out and back, and a one-shot hit-confirmation ring — but drawn
        with far fewer canvas operations so the animation can't stutter
        head tracking:

          * 14 frames at ~40 ms (was 24 at 25 ms) -> ~40% fewer redraws
          * 2 ripple rings (was 3)
          * the reticle is created ONCE and animated with canvas.coords()
            each frame instead of delete+recreate (4 line creates/frame
            eliminated); only the cheap ripple/flash ovals are redrawn

        The Toplevel is click-through (magenta -transparentcolor) so it
        doesn't intercept clicks. Runs on the tk main thread.
        """
        if self._tk_root is None:
            return
        # Lite mode (secure desktop) shows no effects at all.
        try:
            from src import lite_mode
            if lite_mode.ACTIVE:
                return
        except Exception:
            pass
        # Light orange for a jump that snapped onto a clickable thing.
        orange = snapped and snap_notify_enabled(
            ConfigManager().config or {})
        # GPU path: hand the landing flourish to the Qt overlay.
        try:
            if bool((ConfigManager().config or {}).get("qt_overlay", True)):
                from src.gui.qt_overlay.qt_gaze_grid import OverlayClient
                msg = {"cmd": "jump", "x": int(x), "y": int(y),
                       "accent": "#4CC2FF"}
                if orange:
                    msg["color"] = SNAP_NOTIFY_COLOUR
                OverlayClient.instance().send(msg)
                return
        except Exception as e:
            logger.warning("Qt jump feedback failed, using tkinter: %s", e)
        try:
            size = 200
            win = tk.Toplevel(self._tk_root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            bg_colour = "#ff00ff"
            try:
                win.attributes("-transparentcolor", bg_colour)
            except Exception:
                pass
            try:
                win.attributes("-alpha", 0.95)
            except Exception:
                pass
            win.geometry(
                f"{size}x{size}+{x - size // 2}+{y - size // 2}")
            # Normal topmost only. The earlier WS_EX_TOPMOST hack to sit
            # above the Win+Tab task view was unstable.
            try:
                win.attributes("-topmost", True)
            except Exception:
                pass
            canvas = tk.Canvas(win, width=size, height=size,
                               bg=bg_colour, highlightthickness=0)
            canvas.pack()

            cx_, cy_ = size // 2, size // 2

            # Permanent inner crosshair: a small bright dot ringed by a
            # thin halo. Visible the entire animation.
            canvas.create_oval(cx_ - 8, cy_ - 8, cx_ + 8, cy_ + 8,
                               fill="", outline="#FFFFFF", width=2)
            canvas.create_oval(cx_ - 4, cy_ - 4, cx_ + 4, cy_ + 4,
                               fill="#5F6368", outline="")

            # ~14 frames @ 40ms ≈ 560ms total (was ~600ms).
            total_frames = 14
            interval_ms = 40

            RING_COUNT = 2
            RING_STAGGER = 4
            RING_LIFETIME = 10     # frames each ring animates for
            RING_R_MIN = 14
            RING_R_MAX = 90

            TICK_INNER = 14
            TICK_MAX_LEN = 36
            # Reticle active for roughly the first 60% of the animation.
            TICK_FRAMES = int(total_frames * 0.6)
            TICK_COLOUR = SNAP_NOTIFY_COLOUR if orange else "#9AA0A6"

            # Grayscale palette: fresh-bright -> mid-gray -> near-black
            # (light-orange shades for a snapped jump).
            RING_PALETTE = [
                "#FFDAB6", "#FFC891", "#FFB56D", "#FFA348",
                "#DC8E41", "#B97839", "#966232", "#724D2B",
                "#4F3823", "#2C221C",
            ] if orange else [
                "#E8EAED", "#DADCE0", "#BDC1C6", "#9AA0A6",
                "#80868B", "#5F6368", "#4D5156", "#3C4043",
                "#34373B", "#2C2F33",
            ]

            # Create the 4 reticle ticks ONCE (hidden), then just move
            # them via coords()/itemconfig each frame — far cheaper than
            # recreating four lines per frame.
            tick_ids = [
                canvas.create_line(cx_, cy_, cx_, cy_, fill=TICK_COLOUR,
                                   width=3, state="hidden")
                for _ in range(4)
            ]

            def draw(step):
                if step >= total_frames:
                    try:
                        win.destroy()
                    except Exception:
                        pass
                    return

                # Clear only the transient ripple/flash ovals; the
                # crosshair (no tag) and the reticle ticks persist.
                canvas.delete("anim")

                # --- expanding rings ---
                for ring_i in range(RING_COUNT):
                    local = step - ring_i * RING_STAGGER
                    if local < 0 or local >= RING_LIFETIME:
                        continue
                    frac = local / max(1, RING_LIFETIME - 1)
                    r = int(RING_R_MIN +
                            (RING_R_MAX - RING_R_MIN) * frac)
                    # Line thins as the ring expands (visual "fade").
                    width = max(1, 5 - int(frac * 4))
                    colour_idx = min(local, len(RING_PALETTE) - 1)
                    canvas.create_oval(cx_ - r, cy_ - r,
                                       cx_ + r, cy_ + r,
                                       outline=RING_PALETTE[colour_idx],
                                       width=width, tags="anim")

                # --- reticle ticks (N, S, E, W): extend then retract ---
                if step < TICK_FRAMES:
                    half = TICK_FRAMES / 2.0
                    if step < half:
                        frac = step / half               # extend
                    else:
                        frac = (TICK_FRAMES - 1 - step) / half  # retract
                    frac = max(0.0, min(1.0, frac))
                    tick_len = int(TICK_MAX_LEN * frac)
                    if tick_len > 0:
                        n, s, e, w = tick_ids
                        canvas.coords(n, cx_, cy_ - TICK_INNER,
                                      cx_, cy_ - TICK_INNER - tick_len)
                        canvas.coords(s, cx_, cy_ + TICK_INNER,
                                      cx_, cy_ + TICK_INNER + tick_len)
                        canvas.coords(e, cx_ + TICK_INNER, cy_,
                                      cx_ + TICK_INNER + tick_len, cy_)
                        canvas.coords(w, cx_ - TICK_INNER, cy_,
                                      cx_ - TICK_INNER - tick_len, cy_)
                        for tid in tick_ids:
                            canvas.itemconfig(tid, state="normal")
                    else:
                        for tid in tick_ids:
                            canvas.itemconfig(tid, state="hidden")
                else:
                    for tid in tick_ids:
                        canvas.itemconfig(tid, state="hidden")

                # --- one-shot hit-confirmation flash (first 3 frames) ---
                if 1 <= step <= 3:
                    r = 22 - (step - 1) * 3
                    flash_w = max(1, 4 - (step - 1))
                    canvas.create_oval(cx_ - r, cy_ - r,
                                       cx_ + r, cy_ + r,
                                       outline="#ffffff",
                                       width=flash_w, tags="anim")

                self._tk_root.after(interval_ms,
                                    lambda: draw(step + 1))

            draw(0)
        except Exception as e:
            logger.error("Jump feedback failed: %s", e)

    def set_motion_freeze(self, hold_s: float = 0.15) -> None:
        """Freeze cursor movement for `hold_s` seconds from now. Called
        repeatedly by Keybinder while a gesture's value is spiking; the
        short auto-expiry means that as soon as the gesture settles (the
        calls stop), the cursor is released within `hold_s`. The
        time-based expiry also guarantees a missed clear can never
        freeze the cursor permanently."""
        self._motion_freeze_until = time.time() + max(0.0, hold_s)

    def _motion_frozen(self) -> bool:
        return time.time() < self._motion_freeze_until

    def _sync_active_flag(self) -> None:
        try:
            self._active_flag = bool(self.is_active.get())
        except Exception:
            pass

    def set_active(self, flag: bool) -> None:
        if flag:
            # Reset transient state BEFORE publishing the active flag —
            # if the worker thread woke up between the flag write and
            # the resets, its first tick ran on stale buffers (cursor
            # lurch) and its read-modify-writes raced the resets.
            self.delay_count = 0
            self.prev_x = 0
            self.prev_y = 0
            self.buffer = np.zeros([N_BUFFER, 2])
            try:
                self.gaze_smooth.clear()
            except AttributeError:
                # start() not yet called — nothing to clear.
                pass
        self.is_active.set(flag)
        self._active_flag = bool(flag)
        if not flag:
            # Pausing face control freezes the dispatch loop for normal
            # mouse/keyboard actions, so a gesture-held button/key would
            # otherwise stay physically pressed for the entire pause.
            try:
                from src.controllers.keybinder import Keybinder
                Keybinder().release_all_held("face control paused")
            except Exception as e:
                logger.error("release on pause failed: %s", e)

    def toggle_active(self):
        logging.info("Toggle active")
        curr_state = self.is_active.get()
        self.set_active(not curr_state)

    def destroy(self):
        if self.is_active is not None:
            try:
                self.is_active.set(False)
            except Exception:
                pass
        if self.stop_flag is not None:
            self.stop_flag.set()
        # Shutdown the worker pool. Use wait=False: the worker
        # (main_loop) checks stop_flag every iteration and exits on its
        # own, but if it's wedged in a blocking call (pyautogui / a C
        # call), waiting here would hang the whole teardown and leave
        # the process alive after the window closes (the "process
        # residual" symptom). os._exit(0) at the end of TaskKiller.exit
        # hard-kills any straggler thread, so there's no need to block.
        if getattr(self, "pool", None) is not None:
            try:
                self.pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                self.pool.shutdown(wait=False)
            except Exception:
                pass
            self.pool = None
        self.is_destroyed = True
