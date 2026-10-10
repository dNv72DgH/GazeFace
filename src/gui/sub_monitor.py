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
"""Floating always-on-top gesture sub-monitor.

A small frameless topmost window holding ONE thick round meter and
nothing else - no text, no needle, no scale, no face. It shows whichever
bound gesture is most active at that moment: the sweep is its level, the
colour says idle / triggered / blocked. Designed to be as light as
possible:

  * one plain tk.Canvas, two arcs created once and changed via
    itemconfig() - no per-tick widget churn
  * 30 fps update loop (33 ms), items only touched when the quantised
    value/colour actually changed
  * reads Keybinder.monitor_snapshot (already computed by the gesture
    pipeline - zero extra math here beyond picking the leader)

Drag the meter to move it (only the drawn arcs are grabbable; the rest of
the window is transparent and click-through). When the pipeline stops
feeding values (camera death / model stall) or face control is off, the
meter empties and greys out - doubling as a visible "signal lost"
indicator. Position is persisted app-wide.
"""

import logging
import time
import tkinter as tk

from src.config_manager import ConfigManager

logger = logging.getLogger("SubMonitor")

# One round meter. The window is exactly its bounding square; everything
# that is not the meter is the transparent key colour.
DIAL = 132                # window width = height
WIDTH = DIAL              # (kept: the diagnostics below size probes by it)
TOP_PAD = 8
ARC_W = 30                # stroke - the meter is the whole display, so thick
ARC_INSET = ARC_W // 2 + 2  # stroke centre-line inset from the window edge
ARC_START = 225           # degrees, Tk convention (CCW from 3 o'clock)
ARC_SWEEP = 270           # clockwise sweep, gap at the bottom

# Leader selection. The dial follows the most active gesture, measured as
# its level relative to its own threshold (1.0 = about to fire), smoothed
# so a single noisy frame cannot move the needle to another gesture.
LEAD_EMA = 0.35           # smoothing weight of the newest sample
LEAD_MARGIN = 0.15        # challenger must beat the shown one by this
LEAD_HOLD_S = 0.6         # and the shown one stays at least this long

# The window background is painted in this key colour and set as the
# window's -transparentcolor, so the background is fully transparent
# (and click-through) while the meter stays opaque. Magenta is used
# because no visible element uses it.
BG = "#FF00FF"
BAR_TRACK = "#E8EAED"
BAR_IDLE = "#EFC1A8"      # below threshold — mid accent
BAR_TRIGGER = "#D9622B"   # above threshold — accent
BAR_SUPPRESSED = "#BDC1C6"  # blocked by simultaneous-input priority
BAR_STALE = "#DADCE0"     # pipeline not feeding values
BAR_FACE_OFF = "#555555"  # face control off - the whole meter, neutral dark grey


# Snapshot older than this = pipeline stalled (camera dead, model hung,
# or app busy) — grey the bars out instead of showing frozen values.
STALE_S = 1.5

# ~30 fps. Only the fill arc is ever redrawn, and only when its
# quantised angle or colour changed, so a still meter costs nothing.
UPDATE_MS = 33


class GestureSubMonitor:
    """One reusable instance; show()/hide() toggle visibility."""

    def __init__(self, tk_root, on_hidden=None):
        self._root = tk_root
        self._on_hidden = on_hidden
        self._win = None
        self._canvas = None
        self._items = {}         # canvas item ids of the dial
        self._last = {}          # last value written per item (dedupe)
        self._lead = None        # gesture currently on the dial
        self._lead_since = 0.0
        self._ema = {}           # gesture -> smoothed activation
        self._drag_off = None
        self._alpha = None       # last applied window opacity
        self._visible = False
        self._last_state = None  # last logged window state (dedupes SUBMON)
        self._state_ticks = 0
        self._deep_probed = False  # the once-only dig when the wrapper is gone

    # ------------------------------------------------------------- #
    # Visibility
    # ------------------------------------------------------------- #

    def _sync_alpha(self):
        """Read the configured opacity and apply it if it changed.
        Cheap enough to call each tick — only touches the window when
        the value actually differs, so the Advanced-page slider updates
        the live window without any direct reference."""
        try:
            a = float(ConfigManager()._read_app_setting(
                "submonitor_alpha", 1.0))
        except Exception:
            a = 1.0
        a = max(0.2, min(1.0, a))
        if a != self._alpha and self._win is not None:
            self._alpha = a
            try:
                self._win.attributes("-alpha", a)
            except Exception:
                pass

    def show(self):
        if self._win is None:
            self._build()
        self._win.deiconify()
        self._sync_alpha()
        self._visible = True
        try:
            ConfigManager().write_app_setting("submonitor_visible", True)
        except Exception:
            pass
        self._tick()

    def hide(self):
        self._visible = False
        if self._win is not None:
            self._win.withdraw()
        try:
            ConfigManager().write_app_setting("submonitor_visible", False)
        except Exception:
            pass

    def is_visible(self) -> bool:
        return self._visible

    # ------------------------------------------------------------- #
    # Window construction
    # ------------------------------------------------------------- #

    def _build(self):
        self._win = tk.Toplevel(self._root)

        # Realise the window BEFORE asking for -topmost. Measured, not
        # guessed (the variant sweep in _deep_probe): with UIAccess on,
        # "-topmost" applied to an unrealised toplevel loses the window.
        #
        # Why: Tk gives a toplevel a TkTopLevel *wrapper* and reparents
        # the real TkChild into it. A UIAccess process's topmost windows
        # are placed in the ZBID_UIACCESS z-order band (2) while the
        # child is still in ZBID_DESKTOP (1) — and Windows REFUSES
        # SetParent across bands. Tk does not check, so the wrapper ends
        # up empty and correctly styled while the orphaned child sits on
        # the desktop at 0,0 with no styles: the magenta key never
        # applies (hence the pink window) and the position never applies
        # (hence the top-left corner). Both were seen exactly like that.
        #
        # Mapping it first means the wrapper already owns the child when
        # the topmost request moves it, and the child rides along.
        #
        # Off-screen so the brief unstyled, captioned window is never
        # visible; the real position is set at the end of this method.
        self._win.geometry("%dx%d+%d+%d" % (DIAL, DIAL, -3000, -3000))
        try:
            self._win.update_idletasks()
            self._win.update()
        except Exception as e:
            logger.error("sub-monitor realise failed: %s", e)

        self._win.overrideredirect(True)
        # Applied explicitly. Tk used to fold WS_EX_TOOLWINDOW in when it
        # built the override-redirect wrapper from scratch; doing that
        # after the window is realised does not, and without it a
        # frameless status readout turns up in the taskbar and Alt-Tab.
        try:
            self._win.attributes("-toolwindow", True)
        except Exception:
            pass
        self._win.attributes("-topmost", True)
        # Magenta background -> fully transparent + click-through, so
        # only the (opaque) icons/bars/numbers show. No title bar.
        try:
            self._win.attributes("-transparentcolor", BG)
        except Exception:
            pass
        # Initial opacity (kept in sync each tick via _sync_alpha).
        try:
            a = float(ConfigManager()._read_app_setting(
                "submonitor_alpha", 1.0))
        except Exception:
            a = 1.0
        self._alpha = max(0.2, min(1.0, a))
        try:
            self._win.attributes("-alpha", self._alpha)
        except Exception:
            pass

        x = int(ConfigManager()._read_app_setting("submonitor_x", 40))
        y = int(ConfigManager()._read_app_setting("submonitor_y", 40))
        # Clamp on-screen in case the saved position is from another
        # monitor layout.
        x = max(0, min(self._win.winfo_screenwidth() - DIAL, x))
        y = max(0, min(self._win.winfo_screenheight() - DIAL, y))
        self._win.geometry(f"{DIAL}x{DIAL}+{x}+{y}")

        self._canvas = tk.Canvas(self._win, width=DIAL, height=DIAL,
                                 bg=BG, highlightthickness=0, bd=0)
        self._canvas.pack(fill="both", expand=True)
        self._build_dial()

        # Drag from ANYWHERE (no title bar). Clicks land on the opaque
        # content (icons/bars/numbers); the transparent background is
        # click-through. Close is via the Sub-monitor switch in the
        # main window.
        self._canvas.bind("<Button-1>", self._press)
        self._canvas.bind("<B1-Motion>", self._drag_move)
        self._canvas.bind("<ButtonRelease-1>", self._drag_end)


        # Record what was asked for next to what Windows actually did, so
        # a silent no-op is visible in the log instead of only on screen.
        logger.info("SUBMON built: asked pos=%d,%d size=%dx%d "
                    "transparentcolor=%s alpha=%.2f",
                    x, y, DIAL, DIAL, BG, self._alpha)
        self._win.update_idletasks()
        self._log_state("after build")

    # ------------------------------------------------------------- #
    # Dragging
    # ------------------------------------------------------------- #

    def _press(self, e):
        self._drag_off = (e.x, e.y)

    def _drag_move(self, e):
        if self._drag_off is None:
            return
        nx = self._win.winfo_x() + e.x - self._drag_off[0]
        ny = self._win.winfo_y() + e.y - self._drag_off[1]
        self._win.geometry(f"+{nx}+{ny}")

    def _drag_end(self, _e):
        if self._drag_off is None:
            return
        self._drag_off = None
        try:
            ConfigManager().write_app_setting(
                "submonitor_x", int(self._win.winfo_x()))
            ConfigManager().write_app_setting(
                "submonitor_y", int(self._win.winfo_y()))
        except Exception:
            pass

    # ------------------------------------------------------------- #
    # Row management
    # ------------------------------------------------------------- #


    def _build_dial(self):
        """Create the meter once: a track arc and a fill arc on top of it.
        Updates only change the fill's extent and colour."""
        c = self._canvas
        c.delete("dial")
        self._last = {}
        a0, a1 = ARC_INSET, DIAL - ARC_INSET
        self._items = {
            "track": c.create_arc(a0, a0, a1, a1, start=ARC_START,
                                  extent=-ARC_SWEEP, style=tk.ARC,
                                  outline=BAR_TRACK, width=ARC_W,
                                  tags="dial"),
            # extent is set per update; a zero sweep draws nothing
            "fill": c.create_arc(a0, a0, a1, a1, start=ARC_START,
                                 extent=0, style=tk.ARC, outline=BAR_IDLE,
                                 width=ARC_W, tags="dial"),
        }

    def _set(self, key, **kw):
        """itemconfig only when something actually changed."""
        sig = tuple(sorted(kw.items()))
        if self._last.get(key) == sig:
            return
        self._last[key] = sig
        self._canvas.itemconfig(self._items[key], **kw)

    def _pick_leader(self, snap, now):
        """The gesture the dial should show: the most active one, with
        smoothing and a little stickiness so it does not flicker between
        two gestures hovering at the same level."""
        seen = set()
        for entry in snap:
            name, val, thres = entry[0], float(entry[2]), float(entry[3])
            act = val / thres if thres > 1e-6 else 0.0
            prev = self._ema.get(name, act)
            self._ema[name] = prev + LEAD_EMA * (act - prev)
            seen.add(name)
        for gone in [k for k in self._ema if k not in seen]:
            del self._ema[gone]
        if not seen:
            self._lead = None
            return None
        best = max(seen, key=lambda k: self._ema[k])
        cur = self._lead
        if cur not in seen:
            self._lead, self._lead_since = best, now
        elif best != cur:
            held = now - self._lead_since >= LEAD_HOLD_S
            ahead = self._ema[best] > self._ema[cur] + LEAD_MARGIN
            if held and ahead:
                self._lead, self._lead_since = best, now
        return self._lead

    # ------------------------------------------------------------- #
    # Update loop
    # ------------------------------------------------------------- #

    def _hwnds(self):
        """(child, root) HWNDs of this window, or (None, None)."""
        try:
            import ctypes
            child = self._win.winfo_id()
            root = ctypes.windll.user32.GetAncestor(child, 2)   # GA_ROOT
            return child, (root or child)
        except Exception:
            return None, None

    def _window_state(self):
        """What Windows actually thinks this window is.

        Reported rather than assumed because the visible faults - the
        magenta key showing through instead of being transparent, and the
        window sitting at the top-left instead of its saved position -
        both happen with no exception raised anywhere: every call in
        _build "succeeds". The only way to tell which of them did not
        take effect is to read the result back.
        """
        import ctypes
        from ctypes import wintypes

        # Private loader with real prototypes: GetLayeredWindowAttributes
        # takes three out-pointers, and ctypes' default int-sized
        # arguments would corrupt them on 64-bit.
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.GetWindowRect.argtypes = [wintypes.HWND,
                                    ctypes.POINTER(wintypes.RECT)]
        u.GetWindowRect.restype = wintypes.BOOL
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetWindowLongW.restype = ctypes.c_long
        u.GetLayeredWindowAttributes.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.COLORREF),
            ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(wintypes.DWORD)]
        u.GetLayeredWindowAttributes.restype = wintypes.BOOL

        # HWND-returning functions MUST be prototyped: without a restype
        # ctypes hands back a signed C int, and a fallback of "0 means
        # failure" then cannot be told apart from a real handle.
        u.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
        u.GetAncestor.restype = wintypes.HWND
        u.GetParent.argtypes = [wintypes.HWND]
        u.GetParent.restype = wintypes.HWND
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                    ctypes.c_int]
        u.GetClassNameW.restype = ctypes.c_int
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.IsWindowVisible.restype = wintypes.BOOL

        # Undocumented but exported since Win8. Windows refuses
        # SetParent() across z-order bands, and a UIAccess process gets
        # ZBID_UIACCESS (2) instead of the ordinary ZBID_DESKTOP (1) —
        # which is the leading explanation for the missing Tk wrapper:
        # Tk creates the child on the desktop, creates the wrapper, then
        # reparents. If that reparent is refused the child stays where we
        # find it, and Tk carries on believing it worked.
        try:
            u.GetWindowBand.argtypes = [wintypes.HWND,
                                        ctypes.POINTER(wintypes.DWORD)]
            u.GetWindowBand.restype = wintypes.BOOL
            has_band = True
        except AttributeError:
            has_band = False

        def band_of(hwnd):
            if not has_band or not hwnd:
                return "?"
            d = wintypes.DWORD()
            return str(d.value) if u.GetWindowBand(hwnd,
                                                   ctypes.byref(d)) else "?"

        def describe(hwnd, tag):
            if not hwnd:
                return "%s=<none>" % tag
            buf = ctypes.create_unicode_buffer(64)
            u.GetClassNameW(hwnd, buf, 64)
            rc = wintypes.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(rc))
            ex = int(u.GetWindowLongW(hwnd, -20)) & 0xFFFFFFFF  # GWL_EXSTYLE
            names = []
            for bit, nm in ((0x00080000, "LAYERED"), (0x00000008, "TOPMOST"),
                            (0x00000020, "TRANSPARENT"),
                            (0x00000080, "TOOLWIN"),
                            (0x08000000, "NOACTIVATE")):
                if ex & bit:
                    names.append(nm)
            key = wintypes.COLORREF()
            alpha = ctypes.c_ubyte()
            flags = wintypes.DWORD()
            ok = u.GetLayeredWindowAttributes(hwnd, ctypes.byref(key),
                                              ctypes.byref(alpha),
                                              ctypes.byref(flags))
            # dwFlags: 1 = LWA_COLORKEY (the magenta key), 2 = LWA_ALPHA.
            lay = ("key=#%06X alpha=%d lwa=%d" % (
                ((key.value & 0xFF) << 16) | (key.value & 0xFF00)
                | ((key.value >> 16) & 0xFF), alpha.value, flags.value)
                if ok else "noLWA(err=%d)" % ctypes.get_last_error())
            return ("%s=0x%X cls=%s band=%s vis=%d rect=%d,%d %dx%d "
                    "ex=0x%08X[%s] %s"
                    % (tag, hwnd, buf.value, band_of(hwnd),
                       1 if u.IsWindowVisible(hwnd) else 0,
                       rc.left, rc.top, rc.right - rc.left,
                       rc.bottom - rc.top, ex, "|".join(names) or "-", lay))

        try:
            child = self._win.winfo_id()
        except Exception as e:
            return "winfo_id failed: %s" % e
        root = u.GetAncestor(child, 2)          # GA_ROOT
        parent = u.GetParent(child)

        # What Tk believes it set, next to what Windows did. Separates
        # "Tk never made the call" from "Windows refused it" — the two
        # need completely different fixes and look identical on screen.
        try:
            tk_view = "tk[geom=%s override=%s topmost=%s transp=%s alpha=%s]" % (
                self._win.geometry(),
                self._win.overrideredirect(),
                self._win.attributes("-topmost"),
                self._win.attributes("-transparentcolor"),
                self._win.attributes("-alpha"))
        except Exception as e:
            tk_view = "tk[query failed: %s]" % e

        # The MAIN window, for comparison. This is the fork in the road:
        # if it has its TkTopLevel wrapper while this one does not, then
        # UIAccess is not breaking every Tk toplevel and the cause is
        # something specific to how THIS window is built (overrideredirect
        # / the attribute order / when it is created). If neither has one,
        # the reparent is being refused process-wide.
        try:
            mroot = self._root.winfo_id()
            mroot = u.GetAncestor(mroot, 2) or mroot
            main = describe(mroot, "MAINWIN")
        except Exception as e:
            main = "MAINWIN=<probe failed: %s>" % e

        return "  ".join((
            tk_view,
            describe(child, "child"),
            describe(root, "root"),
            describe(parent, "parent"),
            main,
        ))

    def _log_state(self, why):
        try:
            state = self._window_state()
        except Exception as e:
            state = "state probe failed: %s" % e
        if state != self._last_state:
            self._last_state = state
            logger.info("SUBMON %s: %s", why, state)
        # Our wrapper is missing: dig once, while the evidence is fresh.
        if "root=" in state and "cls=TkChild" in state.split("root=", 1)[1][:40] \
                and not self._deep_probed:
            self._deep_probed = True
            try:
                self._deep_probe()
            except Exception as e:
                logger.error("SUBMON deep probe failed: %s", e)

    def _deep_probe(self):
        """Two questions, asked only when the TkTopLevel wrapper is gone.

        1. Where did it go? Tk creates the wrapper and then reparents the
           child into it. If the reparent was refused, the wrapper still
           exists — orphaned, empty, on this thread. Enumerating the
           thread's top-level windows finds it, and its z-order band says
           whether a band mismatch is what refused the SetParent (Windows
           will not reparent across bands; a UIAccess process's topmost
           windows can land in ZBID_UIACCESS=2 while the child sits in
           ZBID_DESKTOP=1).

        2. Which attribute triggers it? Build throwaway toplevels with one
           attribute each, plus the full combination, plus the full
           combination applied only AFTER the window is realised. Whichever
           variants lose their wrapper name the culprit.
        """
        import ctypes
        import tkinter as tk
        from ctypes import wintypes

        u = ctypes.WinDLL("user32", use_last_error=True)
        u.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
        u.GetAncestor.restype = wintypes.HWND
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetWindowLongW.restype = ctypes.c_long
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
        u.GetWindow.restype = wintypes.HWND
        try:
            u.GetWindowBand.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            u.GetWindowBand.restype = wintypes.BOOL
        except AttributeError:
            pass
        k = ctypes.WinDLL("kernel32")
        k.GetCurrentThreadId.restype = wintypes.DWORD

        def cls(h):
            b = ctypes.create_unicode_buffer(64)
            u.GetClassNameW(h, b, 64)
            return b.value

        def band(h):
            try:
                d = wintypes.DWORD()
                return d.value if u.GetWindowBand(h, ctypes.byref(d)) else -1
            except Exception:
                return -1

        # --- 1. every top-level window on this thread -------------------
        rows = []
        CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def each(h, _l):
            c = cls(h)
            if c.startswith("Tk"):
                rc = wintypes.RECT()
                u.GetWindowRect(h, ctypes.byref(rc))
                ex = u.GetWindowLongW(h, -20) & 0xFFFFFFFF
                kid = u.GetWindow(h, 5)          # GW_CHILD
                rows.append("0x%X %s band=%d vis=%d rect=%d,%d %dx%d ex=0x%08X child=%s"
                            % (h, c, band(h), 1 if u.IsWindowVisible(h) else 0,
                               rc.left, rc.top, rc.right - rc.left,
                               rc.bottom - rc.top, ex,
                               cls(kid) if kid else "none"))
            return True

        u.EnumThreadWindows(k.GetCurrentThreadId(), CB(each), 0)
        logger.info("SUBMON deep: %d Tk top-level window(s) on this thread:",
                    len(rows))
        for r in rows:
            logger.info("SUBMON deep:   %s", r)

        # --- 2. which attribute loses the wrapper --------------------------
        def build(name, steps, realise_first=False):
            w = tk.Toplevel(self._root)
            try:
                if realise_first:
                    w.geometry("10x10+0+0")
                    w.update_idletasks()
                    w.update()
                for fn in steps:
                    fn(w)
                w.geometry("10x10+0+0")
                w.update_idletasks()
                w.update()
                h = w.winfo_id()
                root = u.GetAncestor(h, 2) or h
                verdict = "OK wrapper=%s band=%d" % (cls(root), band(root)) \
                    if cls(root) == "TkTopLevel" else \
                    "LOST root=%s band=%d" % (cls(root), band(root))
            except Exception as e:
                verdict = "error %s" % e
            finally:
                try:
                    w.destroy()
                except Exception:
                    pass
            logger.info("SUBMON deep: variant %-28s %s", name, verdict)

        OR = lambda w: w.overrideredirect(True)
        TM = lambda w: w.attributes("-topmost", True)
        TC = lambda w: w.attributes("-transparentcolor", BG)
        AL = lambda w: w.attributes("-alpha", 0.99)
        build("plain", [])
        build("overrideredirect", [OR])
        build("topmost", [TM])
        build("transparentcolor", [TC])
        build("alpha", [AL])
        build("override+topmost", [OR, TM])
        build("override+transparent", [OR, TC])
        build("full (as sub-monitor)", [OR, TM, TC, AL])
        build("full, realised first", [OR, TM, TC, AL], realise_first=True)

    def _tick(self):
        if not self._visible or self._win is None:
            return
        try:
            self._update_once()
        except Exception as e:
            logger.error("sub-monitor tick failed: %s", e)
        self._force_top()
        # Only logs when something actually changed, so a healthy window
        # produces one line and a window being altered behind our back
        # produces a trail showing what changed and when.
        self._state_ticks += 1
        if self._state_ticks % 20 == 0:
            self._log_state("tick")
        self._win.after(UPDATE_MS, self._tick)

    def _force_top(self):
        """Re-assert top-of-z-order via HWND_TOP. Tk's '-topmost' uses
        SetWindowPos(HWND_TOPMOST), which aggressive always-on-top tools
        (Hot Virtual Keyboard) veto with a CBT hook — so the sub-monitor
        drifts behind them. HWND_TOP is not vetoed (same fix as the gaze
        overlay's force_top).

        winfo_id() returns a CHILD HWND for a tk Toplevel — the WS_EX_TOPMOST
        window is the ROOT ancestor, so resolve it with GetAncestor.

        Only re-raises when the window has actually LOST WS_EX_TOPMOST —
        an unconditional SetWindowPos every tick churns the window manager
        (it measurably degrades the GPU overlay's animation smoothness).
        On quiet ticks this is a single cheap GetWindowLong."""
        try:
            import ctypes
            from src.gui.qt_overlay.win_effects import (
                force_top, ensure_topmost_flag, covered_by_nontopmost,
                _get_exstyle, WS_EX_TOPMOST)
            child = self._win.winfo_id()
            root = ctypes.windll.user32.GetAncestor(child, 2)  # GA_ROOT
            hwnd = root or child
            flag_ok = bool(_get_exstyle(hwnd) & WS_EX_TOPMOST)
            covered = covered_by_nontopmost(hwnd)
            if flag_ok and not covered:
                return                      # quiet tick — nothing to do
            if covered:
                force_top(hwnd)             # raise (HWND_TOP beats the veto)
            if not flag_ok:
                ensure_topmost_flag(hwnd)   # restore the bit so we go quiet
        except Exception:
            pass

    def _update_once(self):
        self._sync_alpha()
        from src.controllers.keybinder import Keybinder
        from src.controllers.mouse_controller import MouseController
        kb = Keybinder()
        snap = list(getattr(kb, "monitor_snapshot", []) or [])
        ts = float(getattr(kb, "monitor_ts", 0.0))
        stale = (time.time() - ts) > STALE_S if ts > 0 else True
        # When Face control is OFF, the meters don't reflect anything
        # actionable — show them greyed out and frozen (no animation).
        try:
            face_off = not bool(MouseController()._active_flag)
        except Exception:
            face_off = False
        # "inactive" = nothing live to show -> static grey, no animation.
        inactive = stale or face_off

        # Face control off: the whole meter goes dark grey, so "off" reads
        # at a glance and is not mistaken for a stalled pipeline (light).
        self._set("track", outline=BAR_FACE_OFF if face_off else BAR_TRACK)
        if not snap:
            self._set("fill", extent=0)
            return

        lead = self._pick_leader(snap, time.monotonic())
        entry = next((e for e in snap if e[0] == lead), snap[0])
        # 8-tuple; tolerate older shorter ones (ceil 5.0, floor 0).
        _name, _action, val, _thres, triggered, suppressed = entry[:6]
        ceil = entry[6] if len(entry) > 6 else 5.0
        floor = entry[7] if len(entry) > 7 else 0.0
        if not ceil > 0:
            ceil = 5.0
        if not 0 <= floor < ceil:
            floor = 0.0

        if inactive:
            frac, color = 0.0, BAR_STALE
        else:
            frac = min(1.0, max(0.0, (val - floor) / (ceil - floor)))
            if suppressed:
                color = BAR_SUPPRESSED
            elif triggered:
                color = BAR_TRIGGER
            else:
                color = BAR_IDLE

        # 0.5-degree steps: finer than the eye can see on a 130 px dial,
        # coarse enough that a steady gesture does not redraw every tick.
        extent = -round(ARC_SWEEP * frac * 2) / 2.0
        self._set("fill", extent=extent, outline=color)

    def destroy(self):
        self._visible = False
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None
