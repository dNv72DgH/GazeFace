import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer

from src.gui.qt_overlay.overlay_main import (Overlay, _real_intruder,
                                             _lost_topmost)

OUT = open(os.path.join(os.environ.get("TEMP", "."), "forcetop_out.txt"), "w",
           encoding="utf-8")
o = Overlay()
stats = {"ontop": 0, "behind": 0, "lost": 0, "who": {}}
FOLDERS = [r"C:\\", r"C:\\Windows", r"C:\\Users", r"C:\\Program Files"]


def show():
    o._on_command({"cmd": "open", "mode": "grid", "rows": 1, "cols": 3,
                   "cells": [{"title": "A", "icon": "", "app": False}],
                   "sel": 0, "accent": "#4CC2FF", "gradient": 0xB0101214})


def open_exp(i):
    OUT.write(">>> explorer #%d\n" % (i + 1))
    OUT.flush()
    subprocess.Popen(["explorer.exe", "/n,", FOLDERS[i % len(FOLDERS)]])


def sample():
    if not o._hwnd:
        return
    if _lost_topmost(o._hwnd):
        stats["lost"] += 1
    r = _real_intruder(o._hwnd)
    if r is None:
        stats["ontop"] += 1
    else:
        stats["behind"] += 1
        stats["who"][r[1]] = stats["who"].get(r[1], 0) + 1


def done():
    n = stats["ontop"] + stats["behind"]
    pct = 100.0 * stats["ontop"] / max(1, n)
    OUT.write("IN-BAND %d/%d = %.1f%%  lost_topmost_samples=%d behind-by=%s\n"
              % (stats["ontop"], n, pct, stats["lost"], stats["who"]))
    OUT.flush()
    o.app.quit()


QTimer.singleShot(500, show)
for i in range(4):
    QTimer.singleShot(1200 + i * 800, lambda i=i: open_exp(i))
# sample densely from 1.3s to 6s (after all explorers, let enforcer recover)
for j in range(60):
    QTimer.singleShot(1300 + j * 80, sample)
QTimer.singleShot(6200, done)
o.run()
