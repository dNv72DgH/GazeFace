import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer

from src.gui.qt_overlay.overlay_main import (Overlay, _real_intruder,
                                             _lost_topmost, _zorder_intruder)

OUT = open(os.path.join(os.environ.get("TEMP", "."), "duty_out.txt"), "w",
           encoding="utf-8")

o = Overlay()
stats = {"ontop": 0, "behind": 0, "who": {}, "lost": 0, "kbd": {}}


def show():
    o._on_command({"cmd": "open", "mode": "grid", "rows": 1, "cols": 3,
                   "cells": [{"title": "A", "icon": "", "app": False}],
                   "sel": 0, "accent": "#4CC2FF", "gradient": 0xB0101214})


def sample():
    if not o._hwnd:
        return
    # New semantics: "in the topmost band" = not demoted AND no non-topmost
    # window above us. A topmost on-screen keyboard above us is fine.
    real = _real_intruder(o._hwnd)
    if _lost_topmost(o._hwnd):
        stats["lost"] += 1
    if real is None:
        stats["ontop"] += 1
    else:
        stats["behind"] += 1
        stats["who"][real[1]] = stats["who"].get(real[1], 0) + 1
    raw = _zorder_intruder(o._hwnd)   # whatever is physically above (incl kbd)
    if raw is not None:
        stats["kbd"][raw[1]] = stats["kbd"].get(raw[1], 0) + 1


def done():
    n = stats["ontop"] + stats["behind"]
    pct = 100.0 * stats["ontop"] / max(1, n)
    OUT.write("IN-BAND %d/%d = %.1f%%  lost_topmost_samples=%d\n"
              % (stats["ontop"], n, pct, stats["lost"]))
    OUT.write("  real-intruders(non-topmost above us)=%s\n" % stats["who"])
    OUT.write("  physically-above (incl benign keyboards)=%s\n" % stats["kbd"])
    OUT.flush()
    o.app.quit()


def launch_explorer():
    OUT.write(">>> launching explorer now\n")
    OUT.flush()
    os.startfile("C:\\")


QTimer.singleShot(500, show)
# sample 0-1.5s (baseline), launch explorer at 1.6s, sample 1.7-4s (after)
for i in range(28):
    QTimer.singleShot(700 + i * 50, sample)
QTimer.singleShot(1600, launch_explorer)
for i in range(46):
    QTimer.singleShot(1750 + i * 50, sample)
QTimer.singleShot(4200, done)
o.run()
