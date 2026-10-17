# Qt overlay subprocess entry point.
#
# Runs its own QApplication + QML scene, driven by newline-delimited JSON
# commands on stdin (so the main app's tkinter loop never shares a process
# with Qt). One persistent process is reused for every gaze grid: it stays
# hidden between uses and shows instantly on "open".
#
# Protocol (one JSON object per line):
#   {"cmd":"open","rows":R,"cols":C,"cells":[{title,icon,app}],"sel":i,
#    "accent":"#RRGGBB","gradient":<int ABGR>}
#   {"cmd":"cells","rows":R,"cols":C,"cells":[...],"sel":i}   # paging
#   {"cmd":"highlight","sel":i}
#   {"cmd":"notice","cell":i,"text":s,"color":"#RRGGBB"}  # flash + message
#   {"cmd":"close"}                                            # hide
#   {"cmd":"quit"}                                             # exit process
import json
import os
import queue
import sys
import threading

# Allow running as a frozen-exe sub-mode or `python -m`.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import (                                   # noqa: E402
    QUrl, QTimer, QAbstractNativeEventFilter)
from PySide6.QtGui import QSurfaceFormat                       # noqa: E402
from PySide6.QtQuick import QQuickWindow                       # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine                # noqa: E402
from PySide6.QtWidgets import QApplication                     # noqa: E402

from src.gui.qt_overlay import win_effects                     # noqa: E402


# --- keep-on-top native filter -------------------------------------- #
# Re-asserting topmost on a timer isn't enough: when another app (e.g.
# Explorer) activates, Windows lowers our window for up to the poll
# interval. Intercepting WM_WINDOWPOSCHANGING and forcing hwndInsertAfter
# back to HWND_TOPMOST keeps the overlay above everything at all times.
import ctypes                                                  # noqa: E402
from ctypes import wintypes                                    # noqa: E402

_WM_WINDOWPOSCHANGING = 0x0046
_HWND_TOPMOST = -1


class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint),
                ("wParam", ctypes.c_void_p), ("lParam", ctypes.c_void_p),
                ("time", ctypes.c_uint), ("pt_x", ctypes.c_int),
                ("pt_y", ctypes.c_int)]


class _WINDOWPOS(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p),
                ("hwndInsertAfter", ctypes.c_ssize_t),
                ("x", ctypes.c_int), ("y", ctypes.c_int),
                ("cx", ctypes.c_int), ("cy", ctypes.c_int),
                ("flags", ctypes.c_uint)]


class _TopmostFilter(QAbstractNativeEventFilter):
    def __init__(self, hwnd_getter):
        super().__init__()
        self._hwnd_getter = hwnd_getter

    def nativeEventFilter(self, eventType, message):
        try:
            if eventType == b"windows_generic_MSG":
                msg = ctypes.cast(int(message),
                                  ctypes.POINTER(_MSG)).contents
                if (msg.message == _WM_WINDOWPOSCHANGING
                        and msg.hwnd == self._hwnd_getter()):
                    wp = ctypes.cast(msg.lParam,
                                     ctypes.POINTER(_WINDOWPOS)).contents
                    wp.hwndInsertAfter = _HWND_TOPMOST
        except Exception:
            pass
        return False, 0


# --- foreground-change hook ----------------------------------------- #
# WM_WINDOWPOSCHANGING only fires when *our* window is being reordered.
# When another app (Explorer) calls SetForegroundWindow, the system can
# slot its window into the topmost band without touching ours, so the
# filter never sees it and our overlay ends up behind. SetWinEventHook on
# EVENT_SYSTEM_FOREGROUND fires the instant *anything* takes foreground;
# we re-raise the overlay to the top of the topmost band immediately
# (vs. waiting up to 500ms for the poll).
_EVENT_SYSTEM_FOREGROUND = 0x0003
_WINEVENT_OUTOFCONTEXT = 0x0000
_WINEVENT_SKIPOWNPROCESS = 0x0002
_GW_HWNDPREV = 3

_WinEventProc = ctypes.WINFUNCTYPE(
    None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
    wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)


def _zorder_intruder(hwnd):
    """First *real* visible window stacked above `hwnd` and actually
    overlapping it, as (hwnd, class, title) — or None if the overlay is
    effectively on top. Zero-area / non-overlapping system helper windows
    (e.g. ThumbnailDeviceHelperWnd, which always rides the topmost band but
    paints nothing) are skipped so the log only flags windows that truly
    cover the overlay. Diagnostics only."""
    try:
        u = ctypes.windll.user32
        orc = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(orc))
        h = hwnd
        for _ in range(60):
            h = u.GetWindow(h, _GW_HWNDPREV)   # walks toward the top
            if not h:
                return None
            if not u.IsWindowVisible(h):
                continue
            r = wintypes.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            if (r.right - r.left) < 200 or (r.bottom - r.top) < 120:
                continue
            # Must actually overlap the overlay to count as covering it.
            iw = min(orc.right, r.right) - max(orc.left, r.left)
            ih = min(orc.bottom, r.bottom) - max(orc.top, r.top)
            if iw <= 0 or ih <= 0:
                continue
            buf = ctypes.create_unicode_buffer(64)
            u.GetClassNameW(h, buf, 64)
            tbuf = ctypes.create_unicode_buffer(64)
            u.GetWindowTextW(h, tbuf, 64)
            return (h, buf.value, tbuf.value)
    except Exception:
        return None
    return None


_WS_EX_TOPMOST = 0x00000008
_GWL_EXSTYLE = -20


def _lost_topmost(hwnd):
    """True if our window no longer carries WS_EX_TOPMOST (something
    demoted it out of the topmost band)."""
    try:
        ex = ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        return not (ex & _WS_EX_TOPMOST)
    except Exception:
        return False


def _real_intruder(hwnd):
    """First visible, overlapping, non-trivial window stacked above `hwnd`
    that is itself NOT topmost. Such a window can only sit above us if the
    overlay has effectively dropped out of the topmost band (the "Explorer
    launches and the overlay goes behind" bug). Genuinely-topmost windows
    above us (on-screen keyboards, vendor HUDs) are deliberately skipped:
    they are *meant* to sit on top and aggressively veto z-order changes
    (e.g. Hot Virtual Keyboard's CBT hook), so fighting them is futile and
    pointless — the user wants to see them anyway."""
    try:
        u = ctypes.windll.user32
        orc = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(orc))
        h = hwnd
        # A topmost overlay only has a handful of windows stacked above it;
        # 600 was needless work to run on the animation thread on every
        # foreground event. 60 covers any realistic z-order depth above us.
        for _ in range(60):
            h = u.GetWindow(h, _GW_HWNDPREV)
            if not h:
                return None
            if not u.IsWindowVisible(h):
                continue
            if u.GetWindowLongW(h, _GWL_EXSTYLE) & _WS_EX_TOPMOST:
                continue   # a real topmost peer — leave it above us
            r = wintypes.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            if (r.right - r.left) < 200 or (r.bottom - r.top) < 120:
                continue
            iw = min(orc.right, r.right) - max(orc.left, r.left)
            ih = min(orc.bottom, r.bottom) - max(orc.top, r.top)
            if iw <= 0 or ih <= 0:
                continue
            buf = ctypes.create_unicode_buffer(64)
            u.GetClassNameW(h, buf, 64)
            tb = ctypes.create_unicode_buffer(64)
            u.GetWindowTextW(h, tb, 64)
            return (h, buf.value, tb.value)
    except Exception:
        return None
    return None


_OVL_LOG = os.path.join(os.environ.get("TEMP", "."), "GazeFace_overlay_z.log")
_OVL_CMD_LOG = os.path.join(os.environ.get("TEMP", "."),
                            "GazeFace_overlay_cmd.log")


def _zlog(msg):
    try:
        import time
        with open(_OVL_LOG, "a", encoding="utf-8") as f:
            f.write("%.3f %s\n" % (time.time(), msg))
    except Exception:
        pass


def _clog(msg):
    """Log overlay command/show activity to a file so we can tell, per
    launcher launch, whether the overlay actually received 'open' and
    showed — diagnosing 'the launcher didn't appear'."""
    try:
        import time
        with open(_OVL_CMD_LOG, "a", encoding="utf-8") as f:
            f.write("%.3f %s\n" % (time.time(), msg))
    except Exception:
        pass


def _stderr_banner():
    """Mark each overlay generation in the stderr log.

    Qt writes its own graphics warnings there (device lost, RHI failures,
    shader problems) and OverlayClient appends every generation to the same
    file, so without a divider it is impossible to tell which run a warning
    belongs to.
    """
    try:
        import time as _t
        sys.stderr.write(
            "\n===== overlay start pid=%d %s =====\n"
            % (os.getpid(), _t.strftime("%Y-%m-%d %H:%M:%S")))
        sys.stderr.flush()
    except Exception:
        pass


def _log_screens(app):
    """Record what refresh rate the render side is actually running at.

    A 60 Hz mode is what makes the fixed-interval timers beat (see the
    ring follower), and Windows can silently drop a 120 Hz display back
    to 60 across a display power-off/resume — so this is worth having on
    the record every time the overlay process starts. Since the overlay
    is now recycled on resume, that gives a fresh line per wake-up.
    """
    try:
        for s in app.screens():
            g = s.geometry()
            # Origin included on purpose: two entries for the SAME panel
            # (a TV re-enumerated after sleep) are only distinguishable by
            # where they sit, and the phantom is what the overlay can end
            # up rendering onto invisibly.
            _clog("screen %s %dx%d at %d,%d @%.3fHz dpr=%.2f%s"
                  % (s.name(), g.width(), g.height(), g.x(), g.y(),
                     s.refreshRate(), s.devicePixelRatio(),
                     " PRIMARY" if s is app.primaryScreen() else ""))
    except Exception:
        pass


# Ring-follower restart kick (ms). Only runs the follower again after the
# per-frame chain has gone quiet; deliberately far from any refresh
# period so it can never beat against one.
RING_KICK_MS = 50
# stdin drain interval (ms). The producer sends at 20 Hz.
DRAIN_MS = 20


if getattr(sys, "frozen", False):
    # Bundled data file (build.spec ships it under this relative path).
    _BASE = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    QML_PATH = os.path.join(_BASE, "src", "gui", "qt_overlay",
                            "overlay.qml")
else:
    QML_PATH = os.path.join(_HERE, "overlay.qml")


# Private user32 handle with real prototypes: setting argtypes on the
# shared ctypes.windll.user32 would rewrite them for every module.
_u32 = ctypes.WinDLL("user32", use_last_error=True)
_u32.GetCursorPos.restype = wintypes.BOOL
_u32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_u32.MonitorFromPoint.restype = wintypes.HMONITOR
_u32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


_u32.GetMonitorInfoW.restype = wintypes.BOOL
_u32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR,
                                 ctypes.POINTER(_MONITORINFO)]
_MONITOR_DEFAULTTONEAREST = 2


def _pointer_monitor_rect():
    """(left, top, right, bottom) of the monitor under the pointer, in
    native pixels, straight from Windows. None if it cannot be read."""
    try:
        pt = wintypes.POINT()
        if not _u32.GetCursorPos(ctypes.byref(pt)):
            return None
        hmon = _u32.MonitorFromPoint(pt, _MONITOR_DEFAULTTONEAREST)
        if not hmon:
            return None
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if not _u32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return None
        r = mi.rcMonitor
        return (r.left, r.top, r.right, r.bottom)
    except Exception:
        return None


# Exit code meaning "this overlay can no longer draw - replace me". Any
# exit makes the main process start a fresh overlay (OverlayClient); the
# distinct code just makes the reason readable in its log.
RENDER_RESTART_EXIT = 75

# A display topology change arrives as a burst of screen add / remove /
# primary-change signals. Wait for it to settle before acting on it.
TOPOLOGY_SETTLE_MS = 2000


class Overlay:

    def __init__(self):
        _stderr_banner()
        fmt = QSurfaceFormat()
        fmt.setAlphaBufferSize(8)
        QSurfaceFormat.setDefaultFormat(fmt)
        QQuickWindow.setDefaultAlphaBuffer(True)

        self.app = QApplication(sys.argv)
        self.engine = QQmlApplicationEngine()
        self.engine.load(QUrl.fromLocalFile(QML_PATH))
        roots = self.engine.rootObjects()
        if not roots:
            raise SystemExit(3)
        self.win = roots[0]
        # Show/hide via the QWindow methods. QML starts the window hidden;
        # we showFullScreen() on open.
        self.win.hide()
        self._fx_done = False
        # Native origin of the monitor the window was last anchored to.
        self._origin = (0, 0)
        self._hide_timer = None
        self._want_visible = False
        self._hwnd = 0
        # Always-on-top enforcer (beats other apps stealing the z-order).
        self._topfilter = _TopmostFilter(lambda: self._hwnd)
        try:
            self.app.installNativeEventFilter(self._topfilter)
        except Exception:
            pass
        # Re-raise the instant any other window takes foreground (e.g.
        # Explorer launching) — the poll alone let us drift behind.
        # _last_fg_t must exist before the hook can fire: the callback
        # reads it for its debounce. (Safe today because OUTOFCONTEXT
        # callbacks only dispatch once the event loop pumps, but don't
        # let correctness hinge on that.)
        self._last_fg_t = 0.0
        self._winevent_cb = _WinEventProc(self._on_foreground_change)
        self._winevent_hook = 0
        try:
            self._winevent_hook = ctypes.windll.user32.SetWinEventHook(
                _EVENT_SYSTEM_FOREGROUND, _EVENT_SYSTEM_FOREGROUND, 0,
                self._winevent_cb, 0, 0,
                _WINEVENT_OUTOFCONTEXT | _WINEVENT_SKIPOWNPROCESS)
        except Exception:
            pass
        _log_screens(self.app)

        # Recycle on display topology changes (see _on_topology).
        self._topology_dirty = False
        self._topology_timer = QTimer()
        self._topology_timer.setSingleShot(True)
        self._topology_timer.timeout.connect(self._topology_settled)
        for sig in (self.app.screenAdded, self.app.screenRemoved,
                    self.app.primaryScreenChanged):
            try:
                sig.connect(lambda *_a: self._on_topology())
            except Exception:
                pass

        # Cursor-ring follower (gaze-move glide). Reads the OS cursor and
        # feeds the ring position to QML (logical px).
        #
        # This used to be a flat 16 ms QTimer, i.e. 62.5 Hz, which is close
        # to but not equal to a 60 Hz display's 16.667 ms. The two beat at
        # 2.5 Hz, so roughly every 0.4 s a produced frame had no vsync slot
        # to land in and the ring visibly hitched. No integer millisecond
        # can express 16.667, so a timer cannot be tuned out of this — the
        # follower has to be paced BY the display instead.
        #
        # afterAnimating fires once per frame on the GUI thread (unlike
        # frameSwapped, which fires on the render thread and could not
        # touch QML properties safely), so it is exactly one update per
        # presented frame at any refresh rate. It only fires while frames
        # are being produced: if the cursor stops, the property write is a
        # no-op, nothing is dirtied, no frame is drawn and the chain goes
        # quiet — which is what we want, and is strictly cheaper than the
        # old unconditional 62.5 Hz. RING_KICK_MS restarts it when the
        # cursor moves again while the chain is idle; it is deliberately
        # nowhere near any refresh period.
        self._ring_on = False
        try:
            self.win.afterAnimating.connect(self._poll_ring)
        except Exception:
            # No per-frame signal: the kick timer alone still drives it.
            pass
        self._ring_timer = QTimer()
        self._ring_timer.timeout.connect(self._poll_ring)

        self._install_render_watch()

        # Commands arrive on a reader THREAD; hand them to the GUI thread
        # via a plain queue drained by a timer. (A cross-thread Signal(dict)
        # does not marshal reliably, which silently dropped commands.)
        self._q = queue.Queue()
        self._stdin_open = True
        threading.Thread(target=self._reader, daemon=True).start()

        self._poll = QTimer()
        self._poll.timeout.connect(self._drain)
        # Also off the refresh neighbourhood (see the ring follower above):
        # this one does not draw anything by itself, but a 62.5 Hz wakeup
        # train on the GUI thread still competes with the render sync for
        # no benefit — the main process only sends at 20 Hz (TICK_MS = 50),
        # so 20 ms here costs nothing in latency and halves the wakeups.
        self._poll.start(DRAIN_MS)

        # If the parent dies its pipe closes, the reader exits, and this
        # watchdog quits us so no orphan overlay lingers.
        self._wd = QTimer()
        self._wd.timeout.connect(self._watchdog)
        self._wd.start(500)

        # Fast topmost enforcer. Other always-on-top apps (on-screen
        # keyboards, vendor overlays) re-assert their own topmost on a
        # timer and slip above us; a 500ms poll loses that race long enough
        # that the overlay looks like it "drifts behind and disappears". We
        # re-raise only when a real window has actually covered the overlay,
        # fast enough to win the fight without constant SetWindowPos churn.
        self._last_intr = None
        self._last_intr_t = 0.0
        self._top_timer = QTimer()
        self._top_timer.timeout.connect(self._enforce_top)
        self._top_timer.start(80)

        # Materialise the native surface once (transparent, content hidden)
        # so the first real open doesn't flash a default-cleared frame.
        QTimer.singleShot(60, self._warm)

    # -- render health --------------------------------------------------- #
    #
    # The reported failure is "everything the GPU draws stops appearing":
    # the overlay process is alive, it receives its commands and it calls
    # showFullScreen (the command log proves all three), but the screen
    # stays empty. Replacing the process every 15 minutes does not prevent
    # it, so whatever is broken is not state this process accumulates.
    #
    # That leaves the graphics side, and these are the three things worth
    # knowing when it next happens:
    #   * which backend was in use at all (D3D11 / OpenGL / software),
    #   * whether Qt reported a scene-graph error or lost the device
    #     (sceneGraphInvalidated fires on a lost D3D device),
    #   * whether frames were still being PRESENTED while nothing was
    #     visible, which separates "rendering stopped" from "rendering
    #     fine, the window is just not composited".
    # None of this changes behaviour; it only makes the next occurrence
    # readable instead of guesswork.

    def _install_render_watch(self):
        self._frames = 0
        self._last_frame_report = 0
        self._sg_ready = False
        self._last_fx = "none"
        try:
            self.win.afterAnimating.connect(self._count_frame)
        except Exception:
            pass
        for sig, cb in (("sceneGraphInitialized", self._on_sg_init),
                        ("sceneGraphInvalidated", self._on_sg_gone),
                        ("sceneGraphError", self._on_sg_error)):
            try:
                getattr(self.win, sig).connect(cb)
            except Exception as e:
                _clog("render: cannot watch %s (%s)" % (sig, e))
        self._render_timer = QTimer()
        self._render_timer.timeout.connect(self._check_render)
        self._render_timer.start(2000)

    def _count_frame(self):
        self._frames += 1

    def _on_sg_init(self):
        self._sg_ready = True
        api = "?"
        try:
            # PySide6's GraphicsApi is a Python enum with no __int__, so
            # read .name rather than mapping numbers (an earlier numeric
            # table was also simply wrong: Direct3D11 is 4, not 3).
            g = self.win.rendererInterface().graphicsApi()
            api = getattr(g, "name", None) or str(g)
        except Exception as e:
            api = "unreadable (%s)" % e
        _clog("render: scene graph initialized, backend=%s" % api)

    def _on_sg_gone(self):
        # Fires on a lost/reset device as well as on a normal shutdown, so
        # it is only alarming when we still want to be on screen.
        self._sg_ready = False
        _clog("render: SCENE GRAPH INVALIDATED (want_visible=%s frames=%d)"
              % (self._want_visible, self._frames))

    def _on_sg_error(self, error, message):
        _clog("render: SCENE GRAPH ERROR %s: %s" % (error, message))

    def _check_render(self):
        """Flag 'should be animating, but nothing is being drawn'.

        Scoped to ring mode ON PURPOSE. A settled grid or arrow panel is a
        static scene, and Qt Quick correctly draws zero frames for one, so
        a general "no frames while visible" test reports a healthy overlay
        as broken every two seconds. The cursor-follow ring is the one
        state that must repaint continuously, which makes a gap there a
        real signal rather than noise.
        """
        try:
            if not (self._ring_on and self.win.isVisible()):
                self._last_frame_report = self._frames
                return
            if self._frames != self._last_frame_report:
                self._last_frame_report = self._frames
                return
            _clog("render: NO FRAMES for 2s while the ring is active "
                  "(frames=%d sg_ready=%s)" % (self._frames, self._sg_ready))
        except Exception:
            pass

    def _warm(self):
        try:
            self.win.setProperty("contentOpacity", 0.0)
            self._anchor_to_pointer_screen()
            self.win.showFullScreen()
            QTimer.singleShot(40, self._warm_hide)
        except Exception:
            pass

    def _warm_hide(self):
        # Don't hide if a real open already raced in ahead of the warm.
        if not self._want_visible:
            try:
                self.win.hide()
            except Exception:
                pass

    # -- stdin ---------------------------------------------------------- #

    def _stream(self):
        s = sys.stdin
        if s is not None:
            return s
        # Windowed frozen exes have sys.stdin = None; the parent still
        # wired a pipe onto fd 0.
        try:
            return os.fdopen(0, "r", encoding="utf-8")
        except Exception:
            return None

    def _reader(self):
        stream = self._stream()
        if stream is None:
            self._stdin_open = False
            return
        try:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    self._q.put(json.loads(line))
                except Exception:
                    continue
        except Exception:
            pass
        self._stdin_open = False

    def _drain(self):
        while True:
            try:
                msg = self._q.get_nowait()
            except queue.Empty:
                return
            try:
                self._on_command(msg)
            except Exception:
                pass

    def _on_foreground_change(self, hook, event, hwnd, idobj, idchild,
                              thread, ts):
        # Fired on our own (GUI/animation) thread whenever the foreground
        # window changes. Normally we re-raise immediately rather than
        # waiting for the next enforcer tick. BUT apps like Excel storm
        # this hook — in-cell editors, autocomplete dropdowns, tooltips
        # flip the foreground many times a second — and each call runs a
        # full z-order walk on the same thread that drives the QML
        # animation, stalling frames precisely while Excel is in use.
        # Debounce so a burst collapses to a single re-assert; the 80ms
        # _top_timer still catches anything skipped here within one tick.
        import time
        now = time.monotonic()
        if now - self._last_fg_t < 0.2:
            return
        self._last_fg_t = now
        self._enforce_top()

    def _enforce_top(self):
        # Re-raise ONLY when the overlay has genuinely dropped out of the
        # topmost band — i.e. it lost WS_EX_TOPMOST, or a *non-topmost*
        # window (Explorer, a normal app) is sitting above it. Real topmost
        # peers above us (on-screen keyboards etc.) are left alone, so this
        # neither churns SetWindowPos nor spams the log against windows that
        # are supposed to be on top.
        if not (self._want_visible and self._hwnd and self.win.isVisible()):
            return
        lost = _lost_topmost(self._hwnd)
        intr = _real_intruder(self._hwnd)
        if not lost and intr is None:
            return
        ok = False
        try:
            if intr is not None:
                # Something actually covers us — a real z-order raise is
                # needed (SetWindowPos churn is justified here).
                ok = win_effects.force_top(self._hwnd)
            if lost:
                # Flag-only loss: hvk's CBT hook vetoes the SetWindowPos
                # flag restore, so `lost` would stay True forever and this
                # enforcer would churn SetWindowPos every tick — which
                # visibly degrades the QML animation over time. Restore the
                # style bit via SetWindowLong (not routed through the CBT
                # SetWindowPos filter); with the bit back, quiet ticks stay
                # quiet.
                win_effects.ensure_topmost_flag(self._hwnd)
        except Exception:
            pass
        import time
        now = time.time()
        sig = ("LOST|" if lost else "") + (intr[1] if intr else "")
        if sig != (self._last_intr or "") or now - self._last_intr_t > 1.0:
            _zlog("reassert lost_topmost=%s covered_by=%s force_top_ok=%s"
                  % (lost, (intr[1:] if intr else None), ok))
            self._last_intr = sig
            self._last_intr_t = now

    def _watchdog(self):
        # Parent died -> its pipe closed -> reader exited -> quit so we
        # don't linger as an orphan. (Topmost is handled by _enforce_top.)
        if not self._stdin_open:
            self.app.quit()

    # -- commands ------------------------------------------------------- #

    def _on_command(self, msg):
        cmd = msg.get("cmd")
        if cmd in ("open", "close", "ring_start", "quit"):
            try:
                _clog("recv cmd=%s mode=%s vis=%s want=%s"
                      % (cmd, msg.get("mode"), self.win.isVisible(),
                         self._want_visible))
            except Exception:
                pass
        if cmd == "open":
            self._apply_model(msg)
            self._show(msg.get("gradient"))
        elif cmd == "cells":
            self._apply_model(msg)
        elif cmd == "highlight":
            self.win.setProperty("sel", int(msg.get("sel", 0)))
        elif cmd == "arrow":
            self.win.setProperty("arrowDir", str(msg.get("dir", "")))
        elif cmd == "ring_start":
            self._want_visible = True
            if msg.get("accent"):
                self.win.setProperty("accent", str(msg["accent"]))
            self.win.setProperty("mode", "ring")
            if not self.win.isVisible():
                self._show(msg.get("gradient"))
            else:
                # Already up (arrows -> ring): keep content shown, no re-fade.
                if self._hide_timer is not None:
                    self._hide_timer.stop()
                    self._hide_timer = None
            self._ring_start()
        elif cmd == "ring_stop":
            self._ring_stop()
            self._fade_out()
        elif cmd == "jump":
            self._do_jump(msg)
        elif cmd == "notice":
            self.win.setProperty("noticeCell", int(msg.get("cell", -1)))
            self.win.setProperty("noticeText", str(msg.get("text", "")))
            if msg.get("color"):
                self.win.setProperty("noticeColor", str(msg["color"]))
            self.win.setProperty(
                "noticeTick", int(self.win.property("noticeTick") or 0) + 1)
        elif cmd == "close":
            self._ring_stop()
            self._fade_out()
        elif cmd == "quit":
            self.app.quit()

    def _apply_model(self, msg):
        if "mode" in msg:
            self.win.setProperty("mode", str(msg["mode"]))
        if "rows" in msg:
            self.win.setProperty("rows", int(msg["rows"]))
        if "cols" in msg:
            self.win.setProperty("cols", int(msg["cols"]))
        if "accent" in msg and msg["accent"]:
            self.win.setProperty("accent", str(msg["accent"]))
        if msg.get("cellColor"):
            self.win.setProperty("cellColor", str(msg["cellColor"]))
        if msg.get("hlColor"):
            self.win.setProperty("hlColor", str(msg["hlColor"]))
        for _k in ("arrowColor", "centerColor", "pointerColor"):
            if msg.get(_k):
                self.win.setProperty(_k, str(msg[_k]))
        if "arrowDir" in msg:
            self.win.setProperty("arrowDir", str(msg["arrowDir"]))
        if "cells" in msg:
            self.win.setProperty("cells", msg.get("cells") or [])
        if "sel" in msg:
            self.win.setProperty("sel", int(msg.get("sel", 0)))

    def _anchor_to_pointer_screen(self):
        """Put the window on the monitor the pointer is on, right before it
        is shown. Returns nothing; records the native origin in
        self._origin for everything that positions content by absolute
        screen coordinates (the ring follower, the jump ripple).

        History. showFullScreen() fills whichever screen the window is
        currently associated with, and Windows re-enumerates displays
        whenever one sleeps, wakes, changes mode or a mirroring display
        appears. On this machine the one physical TV shows up in three
        shapes that the overlay log has recorded repeatedly:

            HISENSE-TV|HISENSE-TV  2560x1440 at 0,0     (clone, 120 Hz)
            HISENSE-TV (2)         2560x1440 at 0,0     PRIMARY, 59.94 Hz
            HISENSE-TV (1)         2560x1440 at 3840,0  60 Hz

        The window kept being left on whichever entry it had last been
        given, drawing perfectly and invisibly. The first fix pinned it to
        the PRIMARY screen, and the log shows that still re-anchoring over
        and over ("was at 3840,0") -- because "primary" is just as much a
        label Windows reassigns as the rest. It is not evidence of which
        entry the TV is actually displaying.

        The pointer is. The user can see it; they are moving it with their
        face. Whatever monitor it is on is, by construction, a real and
        visible one. So that is the one to draw on.

        The monitor is identified through Win32, not Qt, because Qt's own
        list is exactly the thing that goes stale across a re-enumeration.
        Qt screens report their NATIVE origin (sizes are logical, origins
        are not), so the two are matched by origin. If Qt has no screen at
        the pointer's monitor origin -- its list is behind -- this falls
        back to the primary screen and says so in the log.

        Gaze cells stay correct on any screen: the main process maps gaze
        to cells as FRACTIONS of the grid, so it does not care which
        monitor the grid is on, only that the user is looking at it --
        which they are, since it is where their pointer is.
        """
        try:
            from PySide6.QtGui import QGuiApplication
            mon = _pointer_monitor_rect()
            scr = None
            if mon is not None:
                for cand in QGuiApplication.screens():
                    g = cand.geometry()
                    if (g.x(), g.y()) == (mon[0], mon[1]):
                        scr = cand
                        break
            why = "pointer"
            if scr is None:
                scr = QGuiApplication.primaryScreen()
                why = ("primary - Qt knows no screen at the pointer's "
                       "monitor %s" % (mon,))
            if scr is None:
                return
            g = scr.geometry()
            was = (self.win.x(), self.win.y())      # read BEFORE moving
            moved = False
            if self.win.screen() is not scr:
                self.win.setScreen(scr)
                moved = True
            if was != (g.x(), g.y()):
                self.win.setGeometry(g)
                moved = True
            self._origin = (g.x(), g.y())
            if moved or why != "pointer":
                _clog("render: ANCHORED to %s screen %s at %d,%d "
                      "(was at %d,%d)"
                      % (why, scr.name(), g.x(), g.y(), was[0], was[1]))
        except Exception as e:
            _clog("render: anchor failed: %s" % e)

    def _show(self, gradient):
        self._want_visible = True
        if self._hide_timer is not None:
            self._hide_timer.stop()
            self._hide_timer = None
        # Start hidden (instant) then fade the CONTENT in. Window opacity is
        # NOT used to fade — that needs WS_EX_LAYERED, which conflicts with
        # the acrylic backdrop and blanks the window.
        self.win.setProperty("contentOpacity", 0.0)
        # Make the window click-through + no-activate BEFORE showing so it
        # never steals foreground focus from the app the user is in.
        try:
            win_effects.set_click_through(int(self.win.winId()), True)
            self._fx_done = True
        except Exception:
            pass
        self._anchor_to_pointer_screen()
        self.win.showFullScreen()
        try:
            _clog("_show: showFullScreen called, vis=%s" % self.win.isVisible())
        except Exception:
            pass
        # winId is valid once the platform window exists; defer effects to
        # the next loop turn so the window has actually mapped.
        QTimer.singleShot(0, lambda: self._apply_fx(gradient))
        QTimer.singleShot(0, lambda: self.win.setProperty(
            "contentOpacity", 1.0))
        # Did this show actually paint anything? A fade-in from 0 to 1 over
        # 300 ms cannot produce zero frames, so if it does, the window went
        # up over a dead renderer — which is the "nothing appears" report.
        before = self._frames
        QTimer.singleShot(1200, lambda: self._after_show_check(before))

    def _window_state(self):
        """Everything between 'a frame was drawn' and 'the user saw it'.

        The frame counter alone turned out not to answer the question: on
        2026-08-17, through the whole period the overlay was invisible,
        every show still reported 37-77 frames. Frames are counted per
        presented frame whatever their contents, so a fully transparent
        scene, a blanked layered window and a covered window all look
        identical to it. These are the fields that tell those apart.
        """
        bits = []
        try:
            u = ctypes.windll.user32
            hwnd = self._hwnd or int(self.win.winId())
            ex = u.GetWindowLongW(hwnd, win_effects.GWL_EXSTYLE)
            names = []
            for flag, nm in ((win_effects.WS_EX_LAYERED, "LAYERED"),
                             (win_effects.WS_EX_TRANSPARENT, "TRANSPARENT"),
                             (win_effects.WS_EX_NOACTIVATE, "NOACTIVATE"),
                             (win_effects.WS_EX_TOOLWINDOW, "TOOLWINDOW"),
                             (0x00000008, "TOPMOST")):
                if ex & flag:
                    names.append(nm)
            bits.append("exstyle=0x%X[%s]" % (ex & 0xFFFFFFFF,
                                              ",".join(names)))
            # WS_EX_LAYERED without SetLayeredWindowAttributes blanks a Qt
            # translucent surface — the exact failure win_effects warns
            # about — so its alpha is worth having on the record.
            if ex & win_effects.WS_EX_LAYERED:
                key = wintypes.DWORD()
                alpha = ctypes.c_ubyte()
                flags = wintypes.DWORD()
                ok = u.GetLayeredWindowAttributes(
                    hwnd, ctypes.byref(key), ctypes.byref(alpha),
                    ctypes.byref(flags))
                bits.append("layered_alpha=%s(ok=%s)" % (alpha.value, ok))
            r = wintypes.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(r))
            bits.append("rect=%d,%d %dx%d"
                        % (r.left, r.top, r.right - r.left,
                           r.bottom - r.top))
            bits.append("visible=%s iconic=%s"
                        % (bool(u.IsWindowVisible(hwnd)),
                           bool(u.IsIconic(hwnd))))
            # Is anything actually stacked above us? NOT WindowFromPoint:
            # that skips WS_EX_TRANSPARENT windows by design, so on a
            # click-through overlay it answers "someone else" every single
            # time, healthy or not. _zorder_intruder walks the z-order and
            # is what the topmost enforcer already trusts.
            intr = _zorder_intruder(hwnd)
            bits.append("covered_by=%s" % ("none" if intr is None
                                           else "%s/%s" % (intr[1], intr[2])))
        except Exception as e:
            bits.append("win-state failed: %s" % e)
        try:
            # A transparent scene still counts as frames, so the content
            # opacity is the difference between "drew" and "showed".
            #
            # want_visible separates the two ways it reads 0: a tool that
            # was closed inside the check window (normal - almost every
            # arrows/jump row is exactly that) from one that is still
            # meant to be on screen and simply never faded in. Without it
            # the log cannot tell them apart, and the 83 suspicious rows
            # in the 08-17..08-22 history are mostly the harmless kind.
            bits.append("want_visible=%s" % self._want_visible)
            bits.append("contentOpacity=%s" % self.win.property(
                "contentOpacity"))
            bits.append("mode=%s" % self.win.property("mode"))
            bits.append("winOpacity=%s" % self.win.opacity())
        except Exception:
            pass
        try:
            comp = wintypes.BOOL()
            ctypes.windll.dwmapi.DwmIsCompositionEnabled(ctypes.byref(comp))
            bits.append("dwm=%s" % bool(comp.value))
        except Exception:
            pass
        bits.append("fx_ok=%s" % self._last_fx)
        return "  ".join(bits)

    def _after_show_check(self, before):
        try:
            drawn = self._frames - before
            dead = drawn <= 0 and self._want_visible
            tag = "SHOW DREW NOTHING" if dead \
                else "show drew %d frames" % drawn
            # Always dump the window state, not only on the failure branch:
            # without healthy rows to compare against, the failing ones say
            # nothing.
            _clog("render: %s | %s" % (tag, self._window_state()))
        except Exception:
            return
        if dead:
            self._restart_process(
                "the renderer produced no frame for a visible show")

    def _restart_process(self, why):
        """Leave, so the main process starts a fresh overlay.

        Measured on 2026-09-19: an overlay process started while the TV was
        mid re-enumeration (two entries, 59.94 Hz) was still showing its
        window correctly placed, visible and opaque 20 minutes later, after
        the display had settled into a single 120 Hz output - and drew zero
        frames for every show, every time, while Qt reported no device loss
        and no scene-graph error. Nothing inside the process recovers from
        that; a new process, created against the display as it is now,
        draws normally. So the only useful response to a dead renderer is
        to be replaced, immediately, instead of leaving the user with a
        grid that answers their gaze but cannot be seen.
        """
        _clog("render: RESTARTING overlay process - %s" % why)
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        # TerminateProcess on ourselves - NOT os._exit, and not a Qt
        # shutdown. os._exit ends in ExitProcess, which first calls every
        # loaded DLL's detach routine; with the D3D device in this state the
        # GPU driver's never returned. Measured 2026-09-19: the overlay
        # logged this line at 15:58:02 and was found still "running" at
        # 16:00:28 - all threads gone, window up, nothing drawn, and because
        # the process had not exited the main app never replaced it. Every
        # gesture in between opened into that corpse. TerminateProcess is
        # done by the kernel and runs no user-mode code in this process.
        try:
            import ctypes
            from ctypes import wintypes
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.GetCurrentProcess.restype = wintypes.HANDLE
            k.GetCurrentProcess.argtypes = []
            k.TerminateProcess.restype = wintypes.BOOL
            k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            k.TerminateProcess(k.GetCurrentProcess(), RENDER_RESTART_EXIT)
        except Exception:
            pass
        os._exit(RENDER_RESTART_EXIT)     # only if the above failed

    def _on_topology(self):
        """A screen was added / removed or the primary changed.

        Rendering resources are built for the display layout that existed
        when this process started; after a layout change they can keep
        "working" while nothing reaches the screen (see _restart_process).
        Replace the process once the change has settled - but never while
        a tool is on screen: that waits for the next hide.
        """
        self._topology_dirty = True
        self._topology_timer.start(TOPOLOGY_SETTLE_MS)

    def _topology_settled(self):
        if not self._topology_dirty:
            return
        _log_screens(self.app)
        if self._want_visible or self.win.isVisible():
            # Busy - checked again when the window hides.
            return
        self._restart_process("the display layout changed")

    def _hidden_now(self):
        """The window just hid; act on a layout change that waited for it."""
        try:
            self.win.hide()
        finally:
            if self._topology_dirty and not self._want_visible:
                self._restart_process(
                    "the display layout changed while a tool was open")

    def _do_jump(self, msg):
        # One-shot landing ripple at the snap point. Uses a transparent
        # (un-frosted) window so it doesn't flash the whole screen, and
        # auto-hides when the ripple finishes.
        self._want_visible = True
        # Anchor BEFORE converting: the snap point is in absolute screen
        # pixels and has to be made relative to whichever monitor the
        # window ends up on.
        if not self.win.isVisible():
            self._anchor_to_pointer_screen()
        try:
            dpr = self.win.screen().devicePixelRatio() or 1.0
        except Exception:
            dpr = 1.0
        ox, oy = self._origin
        self.win.setProperty("mode", "jump")
        if msg.get("accent"):
            self.win.setProperty("accent", str(msg["accent"]))
        # Ring / dot colour for THIS jump: set every time, so a snapped
        # (orange) jump does not leave the next plain one orange.
        col = msg.get("color")
        self.win.setProperty("jumpRingColor", str(col or "#FFFFFF"))
        self.win.setProperty("jumpDotColor", str(col or "#80868B"))
        self.win.setProperty("jumpX", (float(msg.get("x", 0)) - ox) / dpr)
        self.win.setProperty("jumpY", (float(msg.get("y", 0)) - oy) / dpr)
        self.win.setProperty(
            "jumpTick", int(self.win.property("jumpTick") or 0) + 1)
        self._ring_stop()
        if not self.win.isVisible():
            self.win.showFullScreen()
        QTimer.singleShot(0, lambda: self._apply_fx(None))
        if self._hide_timer is not None:
            self._hide_timer.stop()
        t = QTimer()
        t.setSingleShot(True)
        t.timeout.connect(self._jump_done)
        t.start(720)
        self._hide_timer = t

    def _jump_done(self):
        # The ripple is over and the window hides: nothing is on screen
        # any more. Left True, a display change seen meanwhile would keep
        # waiting for a "next hide" (see _topology_settled) that already
        # happened.
        self._want_visible = False
        self._hidden_now()

    def _ring_start(self):
        self._ring_on = True
        self._ring_timer.start(RING_KICK_MS)
        self._poll_ring()               # seed now; afterAnimating takes over

    def _ring_stop(self):
        self._ring_on = False
        self._ring_timer.stop()

    def _poll_ring(self):
        # Called once per presented frame (afterAnimating) AND from the
        # kick timer, so it must be cheap and safe to run twice in a row.
        if not self._ring_on:
            return
        try:
            pt = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            dpr = self.win.devicePixelRatio() or 1.0
            # Unchanged values are dropped by QML, so a still cursor draws
            # no frame at all — that is what keeps this from costing more
            # than the old fixed-rate poll.
            # Relative to the window's own monitor: it is no longer always
            # the one at 0,0.
            ox, oy = self._origin
            self.win.setProperty("ringX", (pt.x - ox) / dpr)
            self.win.setProperty("ringY", (pt.y - oy) / dpr)
        except Exception:
            pass

    def _fade_out(self):
        self._want_visible = False
        if not self.win.isVisible():
            return
        self.win.setProperty("contentOpacity", 0.0)   # animates 1 -> 0
        # Hide for real once the fade finishes (timer so a re-open mid-fade
        # cancels it).
        t = QTimer()
        t.setSingleShot(True)
        t.timeout.connect(self._hidden_now)
        t.start(320)
        self._hide_timer = t

    def _apply_fx(self, gradient):
        try:
            hwnd = int(self.win.winId())
        except Exception:
            return
        self._hwnd = hwnd
        # Gaze move (arrows / ring) and jump want a fully transparent
        # (un-frosted) window — no background blur, just the arrows / ripple
        # over the desktop. The grid tools (launcher / clipboard / task) get
        # the acrylic tint, re-applied on EVERY open so the per-tool
        # transparency slider takes effect.
        # SetWindowCompositionAttribute is undocumented and CAN fail; its
        # result was being thrown away, so a window that came up invisible
        # gave no clue whether the backdrop had been applied at all.
        if self.win.property("mode") in ("jump", "arrows", "ring"):
            win_effects.disable_acrylic(hwnd)
            self._last_fx = "acrylic-off"
        else:
            try:
                grad = int(gradient) if gradient is not None else 0xB0101214
            except Exception:
                grad = 0xB0101214
            ok = win_effects.enable_acrylic(hwnd, grad)
            self._last_fx = "acrylic=%s grad=0x%X" % (ok, grad)
            if not ok:
                _clog("render: enable_acrylic FAILED (grad=0x%X)" % grad)
        if not self._fx_done:
            win_effects.set_click_through(hwnd, True)
            self._fx_done = True
        win_effects.force_top(hwnd)

    def run(self):
        self.app.exec()


def main():
    try:
        Overlay().run()
    except SystemExit:
        raise
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
