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
"""Snap a Gaze Jump onto the nearest clickable thing.

Webcam gaze lands near what the user looks at, not on it. When there is a
link, button, icon or similar within the configured range of the landing
point, the pointer goes onto it instead; with nothing in range the jump
lands where it always did.

What counts as clickable comes from UI Automation, the accessibility API
screen readers use, so it works across applications and browsers:
elements of a clickable control type (button, link, menu / list / tab /
tree item, check box, edit box ...) or any element offering Invoke.

UIA has no "everything inside this circle" query that is cheap - a
FindAll over a browser window walks thousands of elements - so the area
is sampled instead: ElementFromPoint at the landing point and on rings
around it, nearest first, each hit walked up a few levels to its
clickable ancestor (a link's text run -> the link). The search has a time
budget, and the jump never waits longer than that.

All UIA work runs on one worker thread of its own (UIA is COM: the
thread needs COM initialised, and its objects must stay on it).
"""

import logging
import math
import queue
import threading
import time

logger = logging.getLogger("ClickSnap")

# UIA control types that are clicked as a whole.
_CLICKABLE_TYPES = frozenset((
    50000,   # Button
    50002,   # CheckBox
    50003,   # ComboBox
    50004,   # Edit
    50005,   # Hyperlink
    50007,   # ListItem       (desktop icons, list rows)
    50011,   # MenuItem
    50013,   # RadioButton
    50019,   # TabItem
    50024,   # TreeItem
    50029,   # DataItem       (Explorer details rows)
    50031,   # SplitButton
))
_PROP_INVOKE_AVAILABLE = 30031    # UIA_IsInvokePatternAvailablePropertyId
# Web content (Chromium-based browsers report "Chrome", Firefox "Gecko").
_WEB_FRAMEWORKS = frozenset(("Chrome", "Gecko"))
# Row-like types that are targets in desktop apps (desktop icons, Explorer
# rows) but on a web page are mostly plain <li> / table rows.
_ROW_TYPES = frozenset((50007, 50024, 50029))  # ListItem, TreeItem, DataItem
_ANCESTOR_LEVELS = 4              # text run -> link -> list item ...
# An element covering more than this share of the screen is a panel or a
# document, not a target: snapping to its centre would be a jump away.
_MAX_AREA_FRAC = 0.20
# Screen magnifier lenses (FlexZoom, Windows Magnifier) show a PICTURE of
# what is under them; nothing in it can be found or clicked through UIA.
_LENS_CLASSES = frozenset(("MagView", "Screen Magnifier Window"))
# A big target is not snapped to its centre - only clamped into, so the
# pointer stays near where the user looked. Up to this size (px), or twice
# the range, a target counts as small and the pointer goes to its centre.
_SMALL_PX = 200
_INSET = 6


class _Worker:

    def __init__(self):
        self._q = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="ClickSnap",
                                        daemon=True)
        self._thread.start()

    def submit(self, job):
        """Run job(uia, walker) on the worker; returns a Queue for the
        result."""
        out = queue.Queue(maxsize=1)
        self._q.put((job, out))
        return out

    def _run(self):
        try:
            import comtypes
            # comtypes initialises COM itself in the thread that first
            # imports it - which may be this one, already as STA. Asking
            # for MTA on top of that fails (RPC_E_CHANGED_MODE) although
            # COM is usable, so that failure is not an error here.
            try:
                comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
            except OSError as e:
                logger.debug("COM already initialised here: %s", e)
            import comtypes.client
            try:
                from comtypes.gen import UIAutomationClient as U
            except ImportError:            # dev run: generate the wrapper
                comtypes.client.GetModule("UIAutomationCore.dll")
                from comtypes.gen import UIAutomationClient as U
            uia = comtypes.client.CreateObject(U.CUIAutomation,
                                               interface=U.IUIAutomation)
            walker = uia.ControlViewWalker
            self._U = U
        except Exception as e:
            logger.error("UI Automation unavailable - snapping off: %s", e)
            uia = walker = None
        while True:
            job, out = self._q.get()
            try:
                res = job(uia, walker) if uia is not None else None
            except Exception as e:
                logger.warning("click snap search failed: %s", e)
                res = None
            try:
                out.put_nowait(res)
            except queue.Full:
                pass


_worker = None
_worker_lock = threading.Lock()


def _get_worker():
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = _Worker()
        return _worker


def _sample_points(tx, ty, radius):
    """The landing point, then rings out to `radius`, nearest first."""
    pts = [(tx, ty)]
    rings = 4
    for i in range(1, rings + 1):
        r = radius * i / rings
        n = max(8, int(2 * math.pi * r / max(12.0, radius / 3.0)))
        for k in range(n):
            a = 2 * math.pi * k / n
            pts.append((int(round(tx + r * math.cos(a))),
                        int(round(ty + r * math.sin(a)))))
    return pts


def _rect_distance(px, py, rect):
    l, t, r, b = rect
    dx = max(l - px, 0, px - r)
    dy = max(t - py, 0, py - b)
    return math.hypot(dx, dy)


def _clickable(el):
    """A clickable control type, or anything else offering Invoke that is
    not a whole window.

    The window exception matters: Windows' default accessibility proxy
    offers Invoke on plain windows (measured: FlexZoom's 751x360 lens
    window said Invoke=True), and treating those as targets made a
    magnifier pull the jump away from a real button. In-page clickable
    elements (a browser's clickable <div>) have no window of their own.

    Web pages need a stricter rule, measured on a shopping page where
    snaps kept landing on things that do nothing when clicked:
      * <li> list items come through as ListItem (61 of them there: point
        balances, labels) - only a row that takes keyboard focus or
        offers Invoke is a target;
      * Chromium offers Invoke on any element with a JavaScript click
        listener, and pages attach those to plain blocks for analytics
        (58 nameless Groups there) - on the web, Invoke alone is not
        enough: the element must also take keyboard focus, as real
        links, buttons and controls do.
    """
    try:
        ct = el.CurrentControlType
        web = el.CurrentFrameworkId in _WEB_FRAMEWORKS
        if ct in _CLICKABLE_TYPES:
            if web and ct in _ROW_TYPES:
                return bool(el.CurrentIsKeyboardFocusable or
                            el.GetCurrentPropertyValue(
                                _PROP_INVOKE_AVAILABLE))
            return True
        if not el.GetCurrentPropertyValue(_PROP_INVOKE_AVAILABLE):
            return False
        if el.CurrentNativeWindowHandle:
            return False
        return bool(el.CurrentIsKeyboardFocusable) if web else True
    except Exception:
        return False


def _describe(el):
    """Short "framework type 'name'" for the log: what a snap landed on."""
    try:
        name = (el.CurrentName or "").replace("\n", " ")[:30]
        return "%s %s '%s'" % (el.CurrentFrameworkId or "?",
                               el.CurrentLocalizedControlType or
                               el.CurrentControlType, name)
    except Exception:
        return "?"


def _search(uia, walker, tx, ty, radius, screen_area, deadline):
    from ctypes import wintypes
    best = None                       # (distance, area, rect, what)
    seen = set()
    for px, py in _sample_points(tx, ty, radius):
        if time.monotonic() > deadline:
            break
        sd = math.hypot(px - tx, py - ty)
        # Nothing reached from here on can beat a hit this much closer.
        if best is not None and sd > best[0] + radius / 4.0:
            break
        try:
            el = uia.ElementFromPoint(wintypes.POINT(px, py))
            if el is None or el.CurrentClassName in _LENS_CLASSES:
                continue
        except Exception:
            continue
        for _ in range(_ANCESTOR_LEVELS + 1):
            if el is None:
                break
            if _clickable(el):
                break
            try:
                el = walker.GetParentElement(el)
            except Exception:
                el = None
        if el is None or not _clickable(el):
            continue
        try:
            if el.CurrentIsOffscreen:
                continue
            rc = el.CurrentBoundingRectangle
            rect = (rc.left, rc.top, rc.right, rc.bottom)
        except Exception:
            continue
        if rect in seen:
            continue
        seen.add(rect)
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if w <= 0 or h <= 0 or w * h > _MAX_AREA_FRAC * screen_area:
            continue
        d = _rect_distance(tx, ty, rect)
        if d > radius:
            continue
        cand = (d, w * h, rect, _describe(el))
        if best is None or cand[:2] < best[:2]:
            best = cand
    return best


# GazeFace's own window: its buttons are drawn by Tk (CustomTkinter paints
# them on a canvas), so UI Automation sees one plain pane and offers
# nothing to snap to. The app registers a provider that lists the screen
# rectangles of its visible clickable widgets instead (see
# src/gui/local_targets.py).
_local_provider = None


def set_local_provider(fn):
    """fn() -> [(left, top, right, bottom), ...] in screen pixels."""
    global _local_provider
    _local_provider = fn


def _local_best(tx, ty, radius, screen_area):
    fn = _local_provider
    if fn is None:
        return None
    try:
        rects = fn() or []
    except Exception as e:
        logger.debug("local targets failed: %s", e)
        return None
    best = None
    for rect in rects:
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if w <= 0 or h <= 0 or w * h > _MAX_AREA_FRAC * screen_area:
            continue
        d = _rect_distance(tx, ty, rect)
        if d > radius:
            continue
        cand = (d, w * h, tuple(rect), "GazeFace widget")
        if best is None or cand[:2] < best[:2]:
            best = cand
    return best


def find(tx, ty, radius, screen_w, screen_h, budget_s=0.35):
    """Where the jump should land: (x, y) on the nearest clickable thing
    within `radius` px of (tx, ty), or None to keep (tx, ty)."""
    radius = max(1, int(radius))
    t0 = time.monotonic()
    deadline = t0 + budget_s
    area = max(1, screen_w * screen_h)

    def job(uia, walker):
        return _search(uia, walker, tx, ty, radius, area, deadline)

    out = _get_worker().submit(job)
    # GazeFace's own widgets, collected while UIA searches the rest.
    local = _local_best(tx, ty, radius, area)
    try:
        # A little past the budget: the last UIA call may still be out.
        best = out.get(timeout=budget_s + 0.25)
    except queue.Empty:
        logger.info("click snap: no answer in time")
        best = None
    if local is not None and (best is None or local[:2] < best[:2]):
        best = local
    ms = (time.monotonic() - t0) * 1000
    if best is None:
        logger.info("click snap: nothing clickable within %d px (%.0f ms)",
                    radius, ms)
        return None
    d, _area, (l, t, r, b), what = best
    w, h = r - l, b - t
    small = max(_SMALL_PX, 2 * radius)
    if w <= small and h <= small:
        x, y = (l + r) // 2, (t + b) // 2           # small: its centre
    else:                                           # big: just inside it
        x = min(max(tx, l + _INSET), r - _INSET)
        y = min(max(ty, t + _INSET), b - _INSET)
    logger.info("click snap: %d,%d -> %d,%d (%s, %dx%d, %.0f px off, "
                "%.0f ms)", tx, ty, x, y, what, w, h, d, ms)
    return int(x), int(y)
