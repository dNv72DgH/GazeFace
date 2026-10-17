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

import copy
import logging
import math
import threading
import time
import pydirectinput
import win32api

from src import mouse_input

# pydirectinput's FAILSAFE raises if the cursor is in a screen corner — but
# a gaze/head cursor legitimately sits in corners, which would make macro
# keystrokes, launcher clicks and clipboard paste silently abort. Disable it
# globally (mirrors pyautogui.FAILSAFE=False in mouse_controller).
pydirectinput.FAILSAFE = False

import src.shape_list as shape_list
from src.config_manager import ConfigManager
from src.controllers.mouse_controller import MouseController
from src.singleton_meta import Singleton

logger = logging.getLogger("Keybinder")

# disable lag
pydirectinput.PAUSE = 0
pydirectinput.FAILSAFE = False

# Minimum interval (seconds) between consecutive fires of the same
# "control" action (pause / reset / cycle / pointer_jump). Always on,
# independent of the opt-in chattering setting. Stops a gesture that
# noisily oscillates around its threshold from rapid-toggling global
# state (the classic "face control keeps flipping off" symptom).
CONTROL_DEBOUNCE_S = 0.7

# After a gaze app (launcher / clipboard / task switcher) closes, suspend
# the normal mouse/keyboard bindings for this long. The confirm gesture
# is usually still held and the picked item's action may still be running;
# this wait lets the gesture relax before bindings re-enable so it doesn't
# immediately fire its own binding.
GAZE_CLOSE_GRACE_S = 0.2

# Motion-freeze: max time the cursor can stay frozen waiting for a
# gesture value to return to baseline. Safety cap so a value that never
# settles can't lock the cursor forever. Per user request: 3 s (was 10).
FREEZE_MAX_HOLD_S = 3.0
# Each refresh of MouseController's time-based freeze lasts this long;
# Keybinder refreshes it every frame while the freeze should hold, so
# when it stops refreshing the cursor releases within this window.
FREEZE_REFRESH_S = 0.2

# Per-gesture response curves applied to the raw blendshape value
# (already in [0, 1]) before the sensitivity gain. Each curve maps
# [0, 1] -> [0, 1] preserving the endpoints, so threshold semantics
# stay roughly the same; only the *shape* of how quickly the signal
# rises with gesture intensity changes.
#
#   linear        : f(x) = x                      no shaping
#   slow_start    : f(x) = x^4.0  (concave up)    weak gestures stay weak,
#                                                  jumps near max — good for
#                                                  "only fire on a strong
#                                                  expression"
#   fast_start    : f(x) = x^0.25 (concave down)  sensitive to small motion,
#                                                  plateaus at the top — good
#                                                  for "tiny expression
#                                                  should already register"
#
# The exponents were 2.5 / 0.4, which barely separated the two settings in
# use: at half intensity they gave 0.18 and 0.76, close enough to linear's
# 0.5 that picking a curve felt like it did nothing. 4.0 / 0.25 give 0.06
# and 0.84 there, so the choice is actually felt. Both still map 0->0 and
# 1->1, so thresholds keep their meaning at the endpoints.
RESPONSE_CURVES = {
    "linear": lambda x: x,
    "slow_start": lambda x: x ** 4.0,
    "fast_start": lambda x: x ** 0.25,
}


# How long the tk main thread may be stalled while an input is held
# before the janitor lets go, regardless of `input_force_release_s`.
#
# This breaks a genuine deadlock, not a slow release. Pressing a mouse
# button on this app's OWN title bar (or a resize border) puts Windows
# into its modal move/size loop inside DefWindowProc — on the tk main
# thread. That is the same thread that runs the gesture pipeline, so the
# button-up that would END the loop can never be sent: the app is holding
# a button down and has disabled the only machinery that could lift it.
# The user is locked out of their pointer until they reach for a physical
# mouse, which for someone who drives this app with their face is not a
# recovery at all.
#
# 2 s is comfortably longer than any hitch a working pipeline produces
# (a real drag keeps ticking, because the app has to move the cursor to
# drag), and short enough that the lock-out is a stumble instead of an
# emergency.
STALL_HOLD_RELEASE_S = 2.0


def _foreground_note() -> str:
    """Which window we were pressing on when the thread wedged.

    The prediction is that it is one of ours, because the deadlock needs
    Windows' modal move/size loop to be running on OUR message thread.
    Logging it turns that from a theory into something the next
    occurrence either confirms or refutes.
    """
    try:
        from src import uipi
        pid, name, title, il = uipi.foreground_info()
        return "foreground=%s(pid=%s, integrity=%s) title=%r" % (
            name or "?", pid, il, (title or "")[:60])
    except Exception as e:
        return "foreground unknown (%s)" % e


class Keybinder(metaclass=Singleton):

    def __init__(self) -> None:
        logger.info("Intialize Keybinder singleton")
        self.top_count = 0
        self.triggered = False
        self.start_hold_ts = math.inf
        self.holding = False
        self.is_started = False
        self.last_know_keybinds = {}
        # Timestamp (seconds) of the last accepted *input* action (click or
        # key press). Chattering prevention uses this as a GLOBAL lockout:
        # once any input fires, no other clicks or keys are accepted for
        # `chattering_threshold_ms` regardless of which gesture caused them
        # or which gesture tries to fire next. Pointer movement is handled
        # by MouseController and is not affected.
        self.last_input_ts = None
        # Pointer-jump state: timer that will revert tracking_mode to
        # head after the gaze window expires, plus a flag to avoid
        # stacking error dialogs.
        self._jump_timer = None
        self._calib_dialog_open = False
        # Per-gesture hold-time state. _hold_start[gesture] is the
        # timestamp at which `val` first crossed `thres` on the current
        # rising edge. Cleared when val drops below thres. Used to
        # require a sustained gesture for gesture_hold_ms before firing.
        self._hold_start = {}
        # Always-on debounce for the meta "control" actions (pause,
        # reset, cycle, pointer_jump). These flip global state, so a
        # noisy gesture hovering near its threshold must not be able to
        # rapid-toggle them. Maps control state_name -> last-fire ts.
        self._control_last_ts = {}
        # Live snapshot for the floating gesture sub-monitor: list of
        # (gesture_name, action, val, thres, triggered, suppressed)
        # tuples, plus the time it was produced. Written atomically
        # (single assignment) from the pipeline thread; read from the
        # tk main thread. Stale timestamp = pipeline stopped (camera
        # death indicator for the monitor).
        self.monitor_snapshot = []
        self.monitor_ts = 0.0
        # Timestamps of currently-held physical inputs (mouseDown /
        # keyDown that haven't been released yet), keyed by state_name.
        # The stuck-input janitor force-releases anything held longer
        # than config "input_force_release_s" (default 60 s, 0 = off) —
        # a gesture pipeline that dies mid-press must never leave a
        # mouse button or key permanently pressed.
        self._press_ts = {}
        self._janitor_started = False
        # Per-binding hold-trigger state for "single" mode. The old
        # implementation used the shared self.holding/start_hold_ts
        # pair for ALL single-mode bindings, so with two bound buttons
        # gesture B's release could mouseUp() the button gesture A was
        # holding (wrong button), leaving A's button stuck down.
        self._holding_state = {}   # state_name -> bool (mouseDown sent)
        self._hold_latch = {}      # state_name -> ts of initial click
        # Presses that were spent dropping a macro drag instead of
        # clicking, so their falling edge skips the normal release.
        self._drag_consumed = set()
        # Launcher-mode rising-edge state (separate from key_states so
        # the launcher's local gesture map can't collide with normal
        # bindings). shape -> bool(currently above thres).
        self._launcher_states = {}
        # Per-gesture hold-time start ts while a gaze app is open (mirrors
        # the main loop's _hold_start, applied in _gaze_app_dispatch).
        self._lh_start = {}
        # Was a gaze app (launcher/clipboard/task) open last frame — used to
        # detect the open transition so the still-held launch gesture can be
        # seeded (so it can't immediately fire its in-launcher action).
        self._gaze_app_open = False
        # Guard so two rapid double-click gestures can't run two
        # overlapping DoubleClick threads (the first finisher would
        # clear _click_freeze mid-double-click of the second).
        self._dbl_click_active = False
        # Previous-frame gained value per gesture, for the motion-freeze
        # and simultaneous-block features (detect rapid change).
        self._prev_gesture_val = {}
        # Motion-freeze per-gesture state: shape -> dict(active, start,
        # baseline). Freeze engages on a rising spike and holds until the
        # value returns to baseline (or FREEZE_MAX_HOLD_S elapses).
        self._freeze_state = {}
        # Disable on strong reaction: gesture -> time.time() it is off
        # until.
        self._strong_off_until = {}

    def start(self):
        if not self.is_started:
            logger.info("Start Keybinder singleton")
            self.init_states()
            self.screen_w, self.screen_h = pydirectinput.size()
            self.monitors = self.get_monitors()
            self.is_started = True
            if not self._janitor_started:
                self._janitor_started = True
                threading.Thread(target=self._stuck_input_janitor,
                                 name="StuckInputJanitor",
                                 daemon=True).start()

    def _stuck_input_janitor(self):
        """Daemon loop: force-release any mouse button / key that has
        been physically held longer than `input_force_release_s`.

        This runs on its own thread (NOT the pipeline) so it still
        works when the camera/pipeline dies mid-press — exactly the
        case that used to leave a button stuck down forever."""
        while True:
            time.sleep(0.25)
            try:
                limit_s = float(ConfigManager().config.get(
                    "input_force_release_s", 60))
            except Exception:
                limit_s = 60.0

            # Is the tk main thread wedged while we hold something? Then
            # the app CANNOT release it — see STALL_HOLD_RELEASE_S. This
            # check deliberately ignores `input_force_release_s`,
            # including when it is 0/off: that setting says how long a
            # WORKING app may keep a button down, and turning it off must
            # not be able to lock the user out of their own pointer.
            deadlocked = False
            stalled_for = 0.0
            try:
                from src import ui_heartbeat
                if ui_heartbeat.started():
                    stalled_for = ui_heartbeat.age_s()
                    deadlocked = stalled_for >= STALL_HOLD_RELEASE_S
            except Exception:
                pass

            if limit_s <= 0 and not deadlocked:
                continue  # feature disabled and nothing is wedged
            now = time.time()
            # Macro drags are a deliberate hold, but they still have to
            # time out: the step that would have dropped it may never run
            # (macro aborted, app wedged, user changed their mind).
            try:
                since = mouse_input.held_since()
                if since and (deadlocked or
                              (limit_s > 0 and now - since >= limit_s)):
                    if deadlocked:
                        logger.warning(
                            "DEADLOCK: main thread stalled %.1fs with a "
                            "drag held for %.1fs — releasing so the "
                            "pointer comes back. %s",
                            stalled_for, now - since, _foreground_note())
                    else:
                        logger.warning(
                            "Force-releasing stuck drag after %.0fs held",
                            now - since)
                    mouse_input.drag_release("stuck-input janitor")
            except Exception as e:
                logger.error("drag janitor failed: %s", e)
            for state_name, ts in list(self._press_ts.items()):
                if not deadlocked and (limit_s <= 0 or now - ts < limit_s):
                    continue
                if deadlocked:
                    logger.warning(
                        "DEADLOCK: main thread stalled %.1fs with %s held "
                        "for %.1fs — releasing. %s",
                        stalled_for, state_name, now - ts,
                        _foreground_note())
                try:
                    device, action = state_name.split("_", 1)
                    logger.warning(
                        "Force-releasing stuck input %s after %.0fs "
                        "held", state_name, now - ts)
                    if device == "mouse":
                        mouse_input.button_up(action)
                    else:
                        # A combination ("ctrl+shift+t") was pressed key
                        # by key; keyUp("ctrl+shift+t") names no key and
                        # would leave Ctrl and Shift down system-wide.
                        for k in reversed(self._combo_keys(action)):
                            pydirectinput.keyUp(k)
                    self._holding_state[state_name] = False
                    self.key_states[state_name] = False
                except Exception as e:
                    logger.error("Stuck-input release of %s failed: %s",
                                 state_name, e)
                finally:
                    self._press_ts.pop(state_name, None)

    def init_states(self) -> None:
        """Re initializes the state of the keybinder.
           If new keybinds are added.
        """
        # A binding change while a button/key is physically down would
        # otherwise orphan the press (its key_states entry is reset to
        # False so the release transition never fires) — lift
        # everything we're holding first.
        self.release_all_held("bindings changed")
        # keep states for all registered keys.
        self.key_states = {}
        for _, v in (ConfigManager().mouse_bindings |
                     ConfigManager().keyboard_bindings).items():
            self.key_states[v[0] + "_" + v[1]] = False
        self.key_states["holding"] = False
        self.last_know_keybinds = copy.deepcopy(
            (ConfigManager().mouse_bindings |
             ConfigManager().keyboard_bindings))

    def release_all_held(self, reason: str = "") -> None:
        """Force-release every physically-held input this class owns
        (mouse buttons via mouseDown, keys via keyDown). Used when
        bindings change mid-press, when face control is paused, and at
        teardown — any path where the normal falling-edge release can
        no longer be reached."""
        # A macro drag holds its button outside _press_ts (it is owned by
        # src.mouse_input, not by a gesture's falling edge), so it has to
        # be dropped explicitly here. A button left down is far worse for
        # someone who cannot reach a real mouse than a lost drag.
        try:
            mouse_input.drag_release(reason or "release_all_held")
        except Exception as e:
            logger.error("drag release failed: %s", e)
        # Consumed-press marks belong to gestures that are being reset
        # here anyway; a stale one would swallow the next real release.
        self._drag_consumed.clear()
        for state_name in list(self._press_ts.keys()):
            try:
                device, action = state_name.split("_", 1)
                logger.info("Releasing held input %s (%s)",
                            state_name, reason)
                if device == "mouse":
                    mouse_input.button_up(action)
                else:
                    # `action` may be a combination ("ctrl+shift+t") —
                    # lift every one of its keys, newest first.
                    for k in reversed(self._combo_keys(action)):
                        pydirectinput.keyUp(k)
            except Exception as e:
                logger.error("release of %s failed: %s", state_name, e)
            finally:
                self._press_ts.pop(state_name, None)
                self._holding_state[state_name] = False
                if state_name in getattr(self, "key_states", {}):
                    self.key_states[state_name] = False
        self.holding = False
        self.start_hold_ts = math.inf

    def get_monitors(self) -> list[dict]:
        out_list = []
        monitors = win32api.EnumDisplayMonitors()
        for i, (_, _, loc) in enumerate(monitors):
            mon_info = {}
            mon_info["id"] = i
            mon_info["x1"] = loc[0]
            mon_info["y1"] = loc[1]
            mon_info["x2"] = loc[2]
            mon_info["y2"] = loc[3]
            mon_info["center_x"] = (loc[0] + loc[2]) // 2
            mon_info["center_y"] = (loc[1] + loc[3]) // 2
            out_list.append(mon_info)

        return out_list

    def get_curr_monitor(self):

        x, y = pydirectinput.position()
        for mon_id, mon in enumerate(self.monitors):
            if x >= mon["x1"] and x <= mon["x2"] and y >= mon[
                    "y1"] and y <= mon["y2"]:
                return mon_id
        #raise Exception("Monitor not found")
        return 0

    def _control_debounced(self, state_name: str) -> bool:
        """Return True if `state_name` fired too recently and this
        rising edge should be ignored. Records the fire timestamp when
        it allows the action through."""
        now = time.time()
        last = self._control_last_ts.get(state_name)
        if last is not None and (now - last) < CONTROL_DEBOUNCE_S:
            return True
        self._control_last_ts[state_name] = now
        return False

    def _has_gaze_calibration(self) -> bool:
        # Accept both calibration formats — len 6 (legacy, pre-head-
        # pose) and len 8 (head-pose-aware). MouseController dispatches
        # to the right polynomial based on length.
        calib = ConfigManager().config.get("gaze_calibration", None)
        if calib is None or not isinstance(calib, dict):
            return False
        if "cx" not in calib or "cy" not in calib:
            return False
        cx_len = len(calib["cx"])
        cy_len = len(calib["cy"])
        return cx_len == cy_len and cx_len in (6, 8)

    def _fire_pointer_jump(self) -> None:
        """Pointer-jump gesture handler.

        Behaviour (per user spec): freeze the cursor immediately, dwell
        for `pointer_jump_delay_ms`, then snap the cursor to where the
        user is looking, hold briefly, and resume head tracking.
        Implementation lives in MouseController.begin_pointer_jump.
        """
        # Honour the Face-control toggle: when face control is off, no
        # face-driven cursor manipulation should happen — including
        # Gaze Jump. The user can override this on a per-bind basis
        # via the action options popup (pointer_jump_ignore_face_control)
        # if they want Gaze Jump to fire even with face control off
        # (e.g. they keep face control off for ambient pose but still
        # want a deliberate gaze gesture to snap the cursor).
        try:
            ignore_fc = bool(ConfigManager().config.get(
                "pointer_jump_ignore_face_control", False))
            if (not ignore_fc) and (not MouseController().is_active.get()):
                logger.info("Gaze Jump skipped: face control is off")
                return
        except Exception:
            pass

        if self._calibration_running():
            logger.info("Gaze Jump skipped: calibration running")
            return

        if not self._has_gaze_calibration():
            logger.warning("Gaze Jump aborted: no gaze calibration")
            # Non-blocking, auto-closing notice. A modal messagebox here
            # halted the pipeline tick (act() runs on the tk main
            # thread), freezing ALL gesture processing until dismissed —
            # which a face-control-only user may not be able to do.
            try:
                from src.i18n import t
                self._show_notice(t(
                    "Gaze calibration is required. Open Advanced settings and run the 16-point calibration first."))
            except Exception as e:
                logger.error("Failed to show calibration notice: %s", e)
            return

        delay_ms = int(ConfigManager().config.get(
            "pointer_jump_delay_ms", 1000))
        MouseController().begin_pointer_jump(delay_ms)

    def _calibration_running(self) -> bool:
        """True while a gaze calibration is on screen — the gaze tools
        (move / launcher / clipboard / task switcher) must not open over
        it."""
        try:
            from src.gui.gaze_calibrator import GazeCalibrator
            return GazeCalibrator.is_active()
        except Exception:
            return False

    def _fire_gaze_move(self) -> None:
        """Gaze move handler: show the directional overlay, then move
        the cursor in the looked-at direction. Suppressed while face
        control is off (it's a face-driven cursor action)."""
        try:
            if not MouseController().is_active.get():
                logger.info("Gaze move skipped: face control is off")
                return
        except Exception:
            pass
        if self._calibration_running():
            logger.info("Gaze move skipped: calibration running")
            return
        cfg = ConfigManager().config
        display_ms = int(cfg.get("gaze_move_display_ms", 2000))
        speed = float(cfg.get("gaze_move_speed", 800))
        MouseController().begin_gaze_move(display_ms, speed)

    def _fire_gaze_launcher(self) -> None:
        """Open the gaze launcher (face-driven; suppressed when face
        control is off)."""
        try:
            if not MouseController().is_active.get():
                logger.info("Gaze launcher skipped: face control is off")
                return
        except Exception:
            pass
        if self._calibration_running():
            logger.info("Gaze launcher skipped: calibration running")
            return
        try:
            from src.extensions.launcher import Launcher
            Launcher.instance().open()
        except Exception as e:
            logger.error("open launcher failed: %s", e, exc_info=e)

    def _fire_gaze_app(self, which: str) -> None:
        """Open the clipboard-history / task-switcher gaze grid."""
        try:
            if not MouseController().is_active.get():
                logger.info("%s skipped: face control is off", which)
                return
        except Exception:
            pass
        if self._calibration_running():
            logger.info("%s skipped: calibration running", which)
            return
        try:
            if which == "clipboard":
                from src.extensions.clipboard_history import ClipboardHistory
                ClipboardHistory.instance().open()
            else:
                from src.extensions.task_switcher import TaskSwitcher
                TaskSwitcher.instance().open()
        except Exception as e:
            logger.error("open %s failed: %s", which, e, exc_info=e)

    def _gaze_app_dispatch(self, app, g_info) -> None:
        """While a gaze app is open, fire its local gesture map
        (confirm/back/forward/exit/settings) on each rising edge.

        The gesture's per-gesture sensitivity gain + response curve are
        already baked into info["val"] (the act() pre-pass). The Hold-time
        option is applied here too, so a launcher gesture honours every
        gesture-option setting just like a normal binding."""
        try:
            gmap = app.gesture_map() or {}
        except Exception:
            gmap = {}
        hold_cfg = ConfigManager().config.get("gesture_hold_ms", {}) or {}
        for shape, info in g_info.items():
            la = gmap.get(shape)
            if not la:
                continue
            above = info["val"] > info["thres"]
            if above and not self._launcher_states.get(shape, False):
                # Hold-time guard: require sustained-above-threshold for
                # gesture_hold_ms[shape] before firing (0 = immediate).
                hold_ms = float(hold_cfg.get(shape, 0))
                if hold_ms > 0:
                    started = self._lh_start.get(shape)
                    if started is None:
                        self._lh_start[shape] = time.time()
                        continue
                    if (time.time() - started) * 1000 < hold_ms:
                        continue
                self._launcher_states[shape] = True
                self._lh_start[shape] = None
                try:
                    app.on_action(la)
                except Exception as e:
                    logger.error("gaze-app on_action failed: %s", e)
            elif not above:
                self._lh_start[shape] = None
                self._launcher_states[shape] = False

    def _show_notice(self, message: str, ms: int = 4000) -> None:
        """Show a small auto-closing topmost notice. Never blocks the
        pipeline (unlike a modal messagebox). Runs on the tk main
        thread — act() is driven by the tk after() loop."""
        now = time.time()
        if now - getattr(self, "_last_notice_ts", 0.0) < 3.0:
            return  # don't stack notices while the gesture re-fires
        self._last_notice_ts = now
        root = getattr(MouseController(), "_tk_root", None)
        if root is None:
            return
        try:
            import tkinter as tk_mod
            win = tk_mod.Toplevel(root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            frame = tk_mod.Frame(win, bg="#202124", padx=14, pady=10)
            frame.pack()
            tk_mod.Label(frame, text=message, bg="#202124",
                         fg="#FFFFFF", font=("Segoe UI", 10),
                         wraplength=420, justify="left").pack()
            win.update_idletasks()
            x = (win.winfo_screenwidth() - win.winfo_reqwidth()) // 2
            win.geometry(f"+{x}+60")
            win.after(ms, win.destroy)
        except Exception as e:
            logger.error("notice failed: %s", e)

    def _is_chattering(self) -> bool:
        """Global chattering lockout check.

        Returns True if any input action fired within the past
        `chattering_threshold_ms`. While True, all clicks and key
        presses are blocked — regardless of which gesture they belong
        to. The lockout window is NOT extended or reset by additional
        gestures that arrive during the lockout (they're just dropped),
        and pointer movement does not touch it.
        """
        cfg = ConfigManager().config
        if not cfg.get("chattering_prevention_enabled", False):
            return False
        if self.last_input_ts is None:
            return False
        threshold_ms = cfg.get("chattering_threshold_ms", 1000)
        return (time.time() - self.last_input_ts) * 1000 < threshold_ms

    def _record_input(self) -> None:
        """Stamp the last-input timestamp, starting the lockout window."""
        self.last_input_ts = time.time()

    def _send_double_click(self) -> None:
        """Fire two left clicks ~40 ms apart in a background thread.

        Windows recognises a double-click only when both clicks land
        within ~4 px of each other AND within ~500 ms. The 500 ms is
        no problem (we use 40 ms), but the 4 px window IS a problem
        while head tracking is active — the cursor drifts and the OS
        then sees "single click, mouse moved, single click". So we
        freeze the cursor for the duration of the double click.

        IMPORTANT: this uses MouseController's DEDICATED `_click_freeze`
        flag, NOT the pointer-jump `_jump_locked`. An earlier version
        saved/restored `_jump_locked`, which raced with a concurrent
        pointer-jump thread: the double-click could capture a stale
        `prev_locked=True`, then restore it after the jump had already
        cleared the lock — leaving `_jump_locked` stuck True forever
        (head tracking + gaze frozen, expression gestures still
        working). A separate flag that only the double-click owns
        cannot have that race.
        """

        # One double-click at a time: a second overlapping thread would
        # share _click_freeze with the first, and the first finisher
        # would unfreeze the cursor mid-double-click of the second.
        if self._dbl_click_active:
            return
        self._dbl_click_active = True

        def _run():
            mc = None
            try:
                from src.controllers.mouse_controller import MouseController
                mc = MouseController()
                mc._click_freeze = True
            except Exception as e:
                logger.warning("could not freeze cursor for double "
                               "click: %s", e)

            try:
                mouse_input.click("left")
                time.sleep(0.04)
                mouse_input.click("left")
                # Small settle so the OS finishes processing the second
                # click before we unfreeze and head tracking resumes.
                time.sleep(0.06)
            except Exception as e:
                logger.error("double click failed: %s", e, exc_info=e)
            finally:
                if mc is not None:
                    try:
                        mc._click_freeze = False
                    except Exception:
                        pass
                self._dbl_click_active = False

        threading.Thread(target=_run, name="DoubleClick",
                         daemon=True).start()

    def mouse_action(self, val, action, thres, mode) -> None:
        state_name = "mouse_" + action

        # A click while a macro drag is held DROPS the drag instead of
        # clicking — the same rule the macro's own click step follows.
        #
        # This path was the missing half of it. The Mouse page's clicks
        # never told src.mouse_input anything, so after releasing a drag
        # with a gesture-bound click the OS had the button up but our
        # state still said "dragging". The next drag command then read as
        # "a drag command while dragging" and merely ended the drag that
        # was no longer there, so the second drag never started — the
        # reported "2回目のドラッグが反応しない".
        if (val > thres) and not self.key_states.get(state_name):
            if mouse_input.is_dragging():
                if self._is_chattering():
                    return
                mouse_input.drag_release("mouse action %s" % action)
                self._record_input()
                # Mark the gesture as consumed WITHOUT arming _hold_latch,
                # so single-mode cannot escalate this into a press-and-
                # hold while the user is still making the face.
                self.key_states[state_name] = True
                self._drag_consumed.add(state_name)
                return

        # Retire a consumed press on its falling edge WITHOUT running the
        # normal release. In hold mode that release is an unconditional
        # mouseUp, and we never sent the matching mouseDown — worse, if a
        # new drag was started in the meantime that stray mouseUp would
        # drop it, so the gesture that ends one drag could silently kill
        # the next one.
        if (val < thres) and (state_name in self._drag_consumed):
            self._drag_consumed.discard(state_name)
            self.key_states[state_name] = False
            return

        # Double-click is a momentary trigger, not a press-and-hold
        # event — short-circuit before the hold/single state machine.
        if action == "double":
            if (val > thres) and (self.key_states[state_name] is False):
                if self._is_chattering():
                    return
                self._send_double_click()
                self._record_input()
                self.key_states[state_name] = True
            elif (val < thres) and (self.key_states[state_name] is True):
                self.key_states[state_name] = False
            return

        mode = "hold" if self.key_states["holding"] else "single"

        if mode == "hold":
            if (val > thres) and (self.key_states[state_name] is False):
                # Chattering lockout: drop the rising edge entirely and
                # leave the gesture state untouched. When the lockout
                # expires the next frame will re-evaluate and the press
                # will go through if the user is still holding the
                # gesture.
                if self._is_chattering():
                    return
                mouse_input.button_down(action)
                self._record_input()
                self._press_ts[state_name] = time.time()
                self.key_states[state_name] = True

            elif (val < thres) and (self.key_states[state_name] is True):
                # Releases are always allowed — we have to lift what we
                # already pressed. Releases do NOT extend the lockout.
                mouse_input.button_up(action)
                self._press_ts.pop(state_name, None)
                self.key_states[state_name] = False

        elif mode == "single":
            # Per-binding hold state (NOT the old shared self.holding —
            # see __init__ comment: with two single-mode bindings the
            # shared flag caused wrong-button releases / stuck buttons).
            if (val > thres):
                if not self.key_states[state_name]:
                    if self._is_chattering():
                        # Drop the click and don't set state — the next
                        # frame will retry once the lockout expires.
                        return
                    mouse_input.click(action)
                    self._record_input()
                    self._hold_latch[state_name] = time.time()
                    self.key_states[state_name] = True

                if (not self._holding_state.get(state_name)) and (
                    ((time.time() - self._hold_latch.get(
                        state_name, math.inf)) * 1000) >=
                        ConfigManager().config["hold_trigger_ms"]):

                    # The hold-trigger transition is also a click-like
                    # event for chattering purposes.
                    if self._is_chattering():
                        return
                    mouse_input.button_down(action)
                    self._record_input()
                    self._press_ts[state_name] = time.time()
                    self._holding_state[state_name] = True

            elif (val < thres) and (self.key_states[state_name] is True):

                self.key_states[state_name] = False

                if self._holding_state.get(state_name):
                    mouse_input.button_up(action)
                    self._press_ts.pop(state_name, None)
                    self._holding_state[state_name] = False
                self._hold_latch.pop(state_name, None)

    @staticmethod
    def _combo_keys(keysym):
        """Split a binding into the physical keys to press, in order.

        A binding is either a single key ("a", "escape") or a combination
        joined with '+' ("ctrl+shift+t"). Returning a list lets the press /
        release paths treat both the same way: press left-to-right, release
        right-to-left."""
        if "+" in keysym and len(keysym) > 1:
            parts = [p for p in keysym.split("+") if p]
            if parts:
                return parts
        return [keysym]

    def keyboard_action(self, val, keysym, thres, mode):

        state_name = "keyboard_" + keysym
        keys = self._combo_keys(keysym)

        if (self.key_states[state_name] is False) and (val > thres):
            if self._is_chattering():
                # Drop the press; leave state False so the next frame
                # re-evaluates after the lockout expires.
                return
            # Modifiers first, then the key — the order a real keyboard
            # produces for a combination.
            for k in keys:
                pydirectinput.keyDown(k)
            self._record_input()
            self._press_ts[state_name] = time.time()
            self.key_states[state_name] = True

        elif (self.key_states[state_name] is True) and (val < thres):
            # Release always allowed; does not extend the lockout.
            for k in reversed(keys):
                pydirectinput.keyUp(k)
            self._press_ts.pop(state_name, None)
            self.key_states[state_name] = False

    def _update_motion_freeze(self, g_info, cfg) -> None:
        """Engage/maintain the cursor motion-freeze.

        Per gesture (when its freeze is enabled): a sharp RISING spike
        (per-frame delta >= sensitivity) starts a freeze that is held
        until the value returns to its pre-spike baseline, or until
        FREEZE_MAX_HOLD_S elapses (safety). Falling edges do NOT freeze.

        Optional grace period ("ignore involuntary movements"): when
        enabled, a detected spike enters a PENDING phase and the cursor
        is NOT frozen yet. If the value falls back to baseline within
        `gesture_freeze_grace_ms`, the spike is treated as an
        involuntary twitch/blink and discarded (no freeze). Only a
        spike that stays elevated past the grace window commits to an
        actual freeze. This keeps a reflexive blink from freezing the
        cursor while still catching deliberate sustained gestures.

        While any gesture's freeze is active we refresh MouseController's
        time-based freeze each frame; when none are active the refresh
        stops and the cursor releases within FREEZE_REFRESH_S.
        """
        freeze_enabled = cfg.get("gesture_freeze_enabled", {}) or {}
        freeze_sens = cfg.get("gesture_freeze_sensitivity", {}) or {}
        ignore_invol = cfg.get("gesture_freeze_ignore_involuntary", {}) or {}
        grace_map = cfg.get("gesture_freeze_grace_ms", {}) or {}
        now = time.time()
        any_active = False

        for shape_name, info in g_info.items():
            if not freeze_enabled.get(shape_name, False):
                self._freeze_state.pop(shape_name, None)
                continue
            # Sensitivity is on the 0..5000 display scale (gained × 1000).
            sens_gained = float(
                freeze_sens.get(shape_name, 100)) / 1000.0
            use_grace = bool(ignore_invol.get(shape_name, False))
            grace_s = max(
                0.0, float(grace_map.get(shape_name, 100)) / 1000.0)
            val = info["val"]
            prev = info["prev"]
            delta = info["delta"]
            st = self._freeze_state.get(shape_name)
            return_level = None

            if st is None:
                # Idle — trigger only on a sharp RISE.
                if prev is not None and delta >= sens_gained:
                    if use_grace and grace_s > 0:
                        # Hold off: wait out the grace window to see if
                        # this is a brief involuntary spike.
                        self._freeze_state[shape_name] = {
                            "phase": "pending", "start": now,
                            "baseline": prev}
                    else:
                        self._freeze_state[shape_name] = {
                            "phase": "active", "start": now,
                            "baseline": prev}
                        any_active = True
            elif st["phase"] == "pending":
                return_level = st["baseline"] + sens_gained * 0.5
                if val <= return_level:
                    # Returned within grace -> involuntary. Discard.
                    self._freeze_state.pop(shape_name, None)
                elif now - st["start"] >= grace_s:
                    # Still elevated past the grace window -> deliberate
                    # gesture. Commit the freeze (reset start so the 3 s
                    # safety counts from the commit).
                    st["phase"] = "active"
                    st["start"] = now
                    any_active = True
                # else: still within grace — keep waiting, don't freeze.
            else:  # "active"
                return_level = st["baseline"] + sens_gained * 0.5
                if val <= return_level:
                    self._freeze_state.pop(shape_name, None)  # released
                elif now - st["start"] > FREEZE_MAX_HOLD_S:
                    self._freeze_state.pop(shape_name, None)  # safety
                    logger.info(
                        "Motion-freeze for %s auto-released after %.0fs",
                        shape_name, FREEZE_MAX_HOLD_S)
                else:
                    any_active = True

        if any_active:
            try:
                MouseController().set_motion_freeze(FREEZE_REFRESH_S)
            except Exception:
                pass

    def _compute_block_priority(self, g_info, cfg):
        """Return the highest priority among gestures that are (a)
        currently ACTIVE and (b) have simultaneous-input block enabled,
        or None if none qualify. Gestures with priority below this are
        suppressed in the dispatch loop.

        A block-enabled gesture counts as "active" when ANY of:
          * it is rising sharply        (delta >=  sensitivity)
          * it is dropping sharply      (delta <= -sensitivity)   ← 急降下
          * its value is above its own input threshold (val >= thres)
            — i.e. the gesture is being held/triggered.
        So the block holds for the whole life of a gesture (rise → hold
        → fall), not just the instant its value is changing."""
        block_enabled = cfg.get("gesture_block_enabled", {}) or {}
        block_sens = cfg.get("gesture_block_sensitivity", {}) or {}
        priority_map = cfg.get("gesture_priority", {}) or {}
        block_priority = None
        for shape_name, info in g_info.items():
            if not block_enabled.get(shape_name, False):
                continue
            sens_gained = float(
                block_sens.get(shape_name, 100)) / 1000.0
            moving = abs(info["delta"]) >= sens_gained
            above_thres = info["val"] >= info.get("thres", float("inf"))
            if moving or above_thres:
                p = float(priority_map.get(shape_name, 0))
                if block_priority is None or p > block_priority:
                    block_priority = p
        return block_priority

    def _update_strong_off(self, g_info, cfg) -> set:
        """Disable on strong reaction. Returns the gestures that are off.

        A gesture with the option on whose value reaches its strong level
        (a sudden laugh, say) is turned off for its disabled time. The
        level is relative to the gesture's own threshold: sensitivity 1 =
        threshold x 1.05, each step +0.05 (100 = x 6.0). The time restarts
        on every frame the value is still that high, so it counts from
        the moment the reaction ends.
        """
        enabled = cfg.get("gesture_strong_off_enabled", {}) or {}
        sens = cfg.get("gesture_strong_off_sens", {}) or {}
        secs = cfg.get("gesture_strong_off_secs", {}) or {}
        now = time.time()
        off = set()
        for shape_name, info in g_info.items():
            if not enabled.get(shape_name, False):
                self._strong_off_until.pop(shape_name, None)
                continue
            try:
                s = max(1, min(100, int(sens.get(shape_name, 20))))
                hold = max(1, min(120, int(secs.get(shape_name, 2))))
            except (TypeError, ValueError):
                continue
            level = info["thres"] * (1.05 + (s - 1) * 0.05)
            until = self._strong_off_until.get(shape_name, 0.0)
            if info["val"] >= level:
                if now >= until:
                    logger.info("Strong reaction on %s (%.2f >= %.2f): "
                                "off for %d s", shape_name, info["val"],
                                level, hold)
                until = now + hold
                self._strong_off_until[shape_name] = until
            if now < until:
                off.add(shape_name)
        return off

    def act(self, blendshape_values) -> dict:
        """Trigger devices action base on blendshape values

        Args:
            blendshape_values (npt.ArrayLike): blendshape values from tflite model

        Returns:
            dict: debug states
        """

        if blendshape_values is None:
            return

        if (ConfigManager().mouse_bindings |
                ConfigManager().keyboard_bindings) != self.last_know_keybinds:
            self.init_states()

        cfg = ConfigManager().config
        bindings = (ConfigManager().mouse_bindings |
                    ConfigManager().keyboard_bindings)

        # --- Pre-pass: compute the gained value + per-frame delta for
        # every bound gesture once, so motion-freeze, simultaneous-block
        # and the action dispatch all share the same numbers. ---
        global_gain = float(cfg.get("gesture_gain", 3.0))
        per_gestures = cfg.get("gesture_gains", {}) or {}
        curves = cfg.get("gesture_curves", {}) or {}
        g_info = {}
        for shape_name, v in bindings.items():
            if shape_name not in shape_list.blendshape_names:
                continue
            idx = shape_list.blendshape_indices[shape_name]
            raw = blendshape_values[idx]
            curve_fn = RESPONSE_CURVES.get(
                curves.get(shape_name, "linear"),
                RESPONSE_CURVES["linear"])
            shaped = curve_fn(max(0.0, min(1.0, float(raw))))
            per_gain = float(per_gestures.get(shape_name, 1.0))
            val = max(0.0, shaped * global_gain * per_gain)
            prev = self._prev_gesture_val.get(shape_name)
            delta = (val - prev) if prev is not None else 0.0
            # v = (device, action, thres, mode); thres is in the same
            # gained units as val, so the block logic can compare them.
            thres = float(v[2])
            g_info[shape_name] = {"val": val, "prev": prev,
                                  "delta": delta, "thres": thres}

        # --- Motion-freeze (rising-spike trigger, hold-until-return). ---
        self._update_motion_freeze(g_info, cfg)

        # --- Simultaneous-input block: while a block-enabled gesture is
        # moving, suppress lower-priority gestures. ---
        block_priority = self._compute_block_priority(g_info, cfg)
        priority_map = cfg.get("gesture_priority", {}) or {}

        # --- Disable on strong reaction: that gesture off for a while. ---
        strong_off = self._update_strong_off(g_info, cfg)

        # Update prev-values for next frame now that the pre-pass is done.
        for shape_name, info in g_info.items():
            self._prev_gesture_val[shape_name] = info["val"]

        # Publish a snapshot for the floating sub-monitor (atomic single
        # assignment; cheap tuples only). Ordered to match the Mouse page's
        # top-to-bottom row order (by action), NOT the arbitrary order the
        # gestures happened to be bound in — otherwise the meters reshuffle
        # whenever a binding is added ("並び順が勝手に変わる").
        # Keyed by action id ("left", "pointer_jump") - what a binding
        # holds in v[1] - not by the page's display labels.
        action_order = {v[1]: i for i, v in enumerate(
            shape_list.available_actions_values)}
        slider_max = cfg.get("gesture_slider_max", {}) or {}
        slider_min = cfg.get("gesture_slider_min", {}) or {}
        snap = []
        for shape_name, v in bindings.items():
            info = g_info.get(shape_name)
            if info is None:
                continue
            suppressed = shape_name in strong_off or (
                block_priority is not None and
                float(priority_map.get(shape_name, 0)) < block_priority)
            # Per-gesture meter full-scale (threshold units) = the slider
            # ceiling / 1000; default 5.0. The sub-monitor bar uses it so
            # its deflection matches the rescaled threshold slider.
            try:
                ceil = max(1, min(5000, int(slider_max.get(
                    shape_name, 5000)))) / 1000.0
            except Exception:
                ceil = 5.0
            # Floor (threshold units): the meter's empty end. Ignored when
            # it leaves no room under the ceiling (same rule as the page).
            try:
                floor = max(1, min(5000, int(slider_min.get(
                    shape_name, 1)))) / 1000.0
            except Exception:
                floor = 0.001
            if floor > ceil - 0.01:
                floor = 0.001
            snap.append((shape_name, v[1], info["val"], float(v[2]),
                         info["val"] > float(v[2]), suppressed, ceil, floor))
        snap.sort(key=lambda s: action_order.get(s[1], len(action_order)))
        self.monitor_snapshot = snap
        self.monitor_ts = time.time()

        # A gesture that is off reads as 0 from here on (after the meters,
        # which still show the real value): no new presses, and anything
        # it holds is released through the normal falling edge.
        for shape_name in strong_off:
            g_info[shape_name]["val"] = 0.0

        # Gaze-app mode: while a gaze grid (launcher / clipboard / task
        # switcher) is open, ONLY its local gesture map drives it;
        # normal mouse/keyboard bindings are suspended.
        try:
            from src.extensions import active_gaze_app
            app = active_gaze_app()
            if app is not None:
                if not self._gaze_app_open:
                    # Just opened. Seed every gesture ALREADY held at open
                    # time (notably the gesture that launched the app) to
                    # "already down" in the launcher's own rising-edge map,
                    # so it must be RELEASED before it can trigger an
                    # in-launcher action. Without this, a launch gesture
                    # that's ALSO bound to an in-launcher action (e.g. the
                    # same eyebrow raise = "open launcher" AND "exit") fires
                    # that action on the very next frame, so the launcher
                    # flashes open then instantly shuts — the "launcher only
                    # opens every other time" bug.
                    self._gaze_app_open = True
                    try:
                        _gmap = app.gesture_map() or {}
                    except Exception:
                        _gmap = {}
                    for _sh, _inf in g_info.items():
                        if _sh in _gmap and _inf["val"] > _inf["thres"]:
                            self._launcher_states[_sh] = True
                self._gaze_was_active = True
                self._gaze_app_dispatch(app, g_info)
                return
            self._gaze_app_open = False
        except Exception as e:
            logger.error("gaze-app dispatch failed: %s", e)

        # A gaze app just closed (e.g. the confirm gesture both picked an
        # item AND closed the launcher). That gesture is usually still
        # held; without this it would immediately fire its NORMAL mouse
        # binding. Seed every still-held gesture's binding state to "down"
        # so it must be released before it can trigger again.
        if getattr(self, "_gaze_was_active", False):
            self._gaze_was_active = False
            self._gaze_close_ts = time.time()
            # Don't hold the launch gestures off on the control debounce
            # right after a gaze app closes, so re-opening is responsive.
            for _act in ("mouse_gaze_launcher", "mouse_gaze_clipboard",
                         "mouse_gaze_taskswitch"):
                self._control_last_ts.pop(_act, None)

        # Grace wait: for a short time after the app closed, keep the
        # normal bindings suspended and treat every still-held gesture as
        # already-down, so when the wait ends a fresh press is required
        # before anything fires.
        if (time.time() - getattr(self, "_gaze_close_ts", 0.0)
                < GAZE_CLOSE_GRACE_S):
            # Re-sync EVERY binding's rising-edge state to the gesture's
            # current value — BOTH directions. Held -> True (a release is
            # required before it can fire again, so a gesture still held
            # from closing the grid can't immediately trigger its normal
            # binding). Released -> False (ready for a fresh rising edge).
            #
            # The False direction is the bug fix: while the grid is open the
            # dispatch returns early (gaze-app mode), so the LAUNCH
            # gesture's falling edge is never processed and its key_state is
            # left stuck True from the launch. If the user has already let
            # the launch gesture go by the time the grid closes, that stale
            # True makes the NEXT raise produce no rising edge — the
            # "launcher only opens every other time" symptom. Syncing to
            # False here clears it so the next raise reliably re-opens.
            for _sn, _v in bindings.items():
                if _sn in g_info:
                    self.key_states[_v[0] + "_" + _v[1]] = (
                        g_info[_sn]["val"] > float(_v[2]))
            return

        # Gaze move: if a directional move is running, ANY gesture fire
        # ends it (and then runs that gesture's own action below).
        gm_active = MouseController().is_gaze_move_active()

        for shape_name, v in bindings.items():
            if shape_name not in shape_list.blendshape_names:
                continue
            device, action, thres, mode = v
            # Self-heal: a binding applied between the staleness check
            # at the top of act() and here can reference a state that
            # init_states hasn't created yet — without this a KeyError
            # aborts the whole frame.
            self.key_states.setdefault(device + "_" + action, False)
            val = g_info[shape_name]["val"]

            # Suppress this gesture's input if a higher-priority gesture
            # is currently active (simultaneous-input block). The active
            # gesture itself (at block_priority) and any equal/higher
            # priority gestures are NOT suppressed.
            #
            # IMPORTANT: suppression must never swallow RELEASES — if
            # this gesture already holds a button/key down (key_states
            # True) and we simply `continue`d here, the falling edge
            # would be skipped and the input left stuck pressed until
            # the block cleared. Instead treat the suppressed gesture as
            # val=0: new presses are impossible (0 < thres) while
            # release transitions fire naturally.
            if block_priority is not None:
                my_priority = float(priority_map.get(shape_name, 0))
                if my_priority < block_priority:
                    val = 0.0

            # Hold-time guard. If the gesture requires sustained-above-
            # threshold for `gesture_hold_ms[shape_name]` ms (e.g. eye
            # close = 100ms by default), defer firing until the hold
            # has elapsed. Below threshold resets the hold timer.
            hold_cfg = ConfigManager().config.get(
                "gesture_hold_ms", {}) or {}
            hold_required_ms = float(hold_cfg.get(shape_name, 0))
            if hold_required_ms > 0:
                if val > thres:
                    started = self._hold_start.get(shape_name)
                    if started is None:
                        self._hold_start[shape_name] = time.time()
                        # Still in the hold window — skip this frame.
                        continue
                    if (time.time() - started) * 1000 < hold_required_ms:
                        continue
                    # Hold satisfied — fall through with val > thres so
                    # the existing rising-edge logic fires.
                else:
                    # Released before the hold elapsed (or just idle).
                    self._hold_start[shape_name] = None

            # Gaze move cancel-on-any-gesture. A rising edge while a move
            # is in progress ends the move. Gaze move and Gaze Jump
            # gestures just END it (consume the edge, run nothing);
            # any other gesture ends it AND runs its own action.
            gm_state = device + "_" + action
            if gm_active and (val > thres) and (
                    self.key_states.get(gm_state) is False):
                MouseController().cancel_gaze_move()
                gm_active = False
                if action in ("gaze_move", "pointer_jump"):
                    self.key_states[gm_state] = True
                    continue

            if (device == "mouse") and (action == "pause"):
                state_name = "mouse_" + action

                if (val > thres) and (self.key_states[state_name] is False):
                    # Mark the edge as consumed regardless, so we don't
                    # spin on it while debounced.
                    self.key_states[state_name] = True
                    if self._control_debounced(state_name):
                        logger.debug("pause toggle debounced")
                    else:
                        mon_id = self.get_curr_monitor()
                        if mon_id is None:
                            return
                        MouseController().toggle_active()
                elif (val < thres) and (self.key_states[state_name] is True):
                    self.key_states[state_name] = False

            elif (device == "mouse") and (action == "pointer_jump"):
                # Switch to gaze mode for `pointer_jump_delay_ms`, let the
                # cursor settle on where the user is looking, then revert
                # to head tracking. Fires once per rising edge.
                state_name = "mouse_" + action
                if (val > thres) and (self.key_states[state_name] is False):
                    self.key_states[state_name] = True
                    if self._control_debounced(state_name):
                        logger.debug("pointer_jump debounced")
                    else:
                        self._fire_pointer_jump()
                elif (val < thres) and (self.key_states[state_name] is True):
                    self.key_states[state_name] = False

            elif (device == "mouse") and (action == "gaze_move"):
                # Show the 9-direction overlay, then move the cursor in
                # the looked-at direction. (If a move is already running,
                # the cancel-on-any-gesture check above already ended it
                # and consumed this edge, so we only reach here to START
                # a new move.) Fires once per rising edge.
                state_name = "mouse_" + action
                if (val > thres) and (self.key_states[state_name] is False):
                    self.key_states[state_name] = True
                    if self._control_debounced(state_name):
                        logger.debug("gaze_move debounced")
                    else:
                        self._fire_gaze_move()
                elif (val < thres) and (self.key_states[state_name] is True):
                    self.key_states[state_name] = False

            elif (device == "mouse") and (action == "gaze_launcher"):
                # Open the gaze launcher grid. Once open, the launcher's
                # own gesture map takes over (see _launcher_dispatch).
                state_name = "mouse_" + action
                # Diagnostic: trace the launch gesture's value vs threshold
                # near the crossing, throttled. Lets us tell a sub-threshold
                # miss (the brow never reaches thres / its resting value sits
                # near thres) from a stuck latch (state) — the two need
                # different fixes.
                if val > 0.6 * thres:
                    _nt = time.time()
                    if _nt - getattr(self, "_gl_trace_ts", 0.0) > 0.15:
                        self._gl_trace_ts = _nt
                        logger.info("gaze_launcher TRACE val=%.3f thres=%.3f "
                                    "latch=%s", val, thres,
                                    self.key_states[state_name])
                if (val > thres) and (self.key_states[state_name] is False):
                    self.key_states[state_name] = True
                    self._gl_latched_logged = False
                    if self._control_debounced(state_name):
                        logger.info("gaze_launcher rising edge DEBOUNCED "
                                    "(val=%.2f thres=%.2f)", val, thres)
                    else:
                        logger.info("gaze_launcher FIRE (val=%.2f thres=%.2f)",
                                    val, thres)
                        self._fire_gaze_launcher()
                elif (val > thres) and (self.key_states[state_name] is True):
                    # Above threshold but the latch is already True, so no
                    # rising edge is produced — if the user means to
                    # re-open, this is the "every other time" miss. Log once
                    # per latched episode to diagnose without spamming.
                    if not getattr(self, "_gl_latched_logged", False):
                        logger.info("gaze_launcher above thres but latch "
                                    "still True -> NO rising edge "
                                    "(val=%.2f thres=%.2f)", val, thres)
                        self._gl_latched_logged = True
                elif (val < thres) and (self.key_states[state_name] is True):
                    logger.info("gaze_launcher latch cleared (val=%.2f "
                                "thres=%.2f)", val, thres)
                    self.key_states[state_name] = False
                    self._gl_latched_logged = False

            elif (device == "mouse") and (action in ("gaze_clipboard",
                                                     "gaze_taskswitch")):
                state_name = "mouse_" + action
                if (val > thres) and (self.key_states[state_name] is False):
                    self.key_states[state_name] = True
                    if self._control_debounced(state_name):
                        logger.debug("%s debounced", action)
                    elif action == "gaze_clipboard":
                        self._fire_gaze_app("clipboard")
                    else:
                        self._fire_gaze_app("taskswitch")
                elif (val < thres) and (self.key_states[state_name] is True):
                    self.key_states[state_name] = False

            elif MouseController().is_active.get():

                if device == "mouse":

                    if action == "reset":
                        state_name = "mouse_" + action
                        if (val > thres) and (self.key_states[state_name] is
                                              False):
                            self.key_states[state_name] = True
                            if self._control_debounced(state_name):
                                logger.debug("reset debounced")
                            else:
                                mon_id = self.get_curr_monitor()
                                if mon_id is None:
                                    return
                                pydirectinput.moveTo(
                                    self.monitors[mon_id]["center_x"],
                                    self.monitors[mon_id]["center_y"])
                        elif (val < thres) and (self.key_states[state_name] is
                                                True):
                            self.key_states[state_name] = False

                    elif action == "cycle":
                        state_name = "mouse_" + action
                        if (val > thres) and (self.key_states[state_name] is
                                              False):
                            self.key_states[state_name] = True
                            if self._control_debounced(state_name):
                                logger.debug("cycle debounced")
                            else:
                                mon_id = self.get_curr_monitor()
                                next_mon_id = (mon_id + 1) % len(self.monitors)
                                pydirectinput.moveTo(
                                    self.monitors[next_mon_id]["center_x"],
                                    self.monitors[next_mon_id]["center_y"])
                        elif (val < thres) and (self.key_states[state_name] is
                                                True):
                            self.key_states[state_name] = False

                    else:
                        self.mouse_action(val, action, thres, mode)

                elif device == "keyboard":
                    self.keyboard_action(val, action, thres, mode)

    def destroy(self):
        """Destroy the keybinder.

        Releasing held input here is the whole point: os._exit() at the
        end of teardown kills the process with any gesture-held mouse
        button or key still physically DOWN at the OS level, and the
        janitor thread that would have rescued it dies with us. That
        stranded a held button across the user's whole desktop — and
        across a restart, since the new process starts with an empty
        _press_ts and no idea anything is down.
        """
        try:
            self.release_all_held("app teardown")
        except Exception as e:
            logger.error("release on teardown failed: %s", e)
