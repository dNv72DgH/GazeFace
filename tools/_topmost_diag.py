import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import subprocess

import win32gui
import win32con
import win32process
import psutil

WS_EX_TOPMOST = 0x00000008

_OUT = open(os.path.join(os.environ.get("TEMP", "."), "topdiag.txt"), "w",
            encoding="utf-8")


def _p(*a):
    s = " ".join(str(x) for x in a)
    _OUT.write(s + "\n")
    _OUT.flush()


print = _p  # noqa

EXE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "dist41", "workface", "workface.exe")


class FrozenOverlay:
    def __init__(self):
        self.proc = subprocess.Popen(
            [EXE, "--qt-overlay"], stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def send(self, m):
        self.proc.stdin.write((json.dumps(m) + "\n").encode())
        self.proc.stdin.flush()

    def ensure(self):
        pass


def dump_large(label, pids):
    print("---", label, "--- (pids of interest:", pids, ")")
    items = []

    def cb(h, _):
        if not win32gui.IsWindowVisible(h):
            return
        r = win32gui.GetWindowRect(h)
        w, ht = r[2] - r[0], r[3] - r[1]
        if w < 1400 or ht < 900:
            return
        try:
            _, wpid = win32process.GetWindowThreadProcessId(h)
        except Exception:
            wpid = 0
        ex = win32gui.GetWindowLong(h, win32con.GWL_EXSTYLE)
        items.append((h, wpid, win32gui.GetClassName(h),
                      bool(ex & WS_EX_TOPMOST), w, ht,
                      win32gui.GetWindowText(h)[:24]))
    win32gui.EnumWindows(cb, None)
    # EnumWindows returns top z-order first.
    for i, (h, wpid, cls, top, w, ht, txt) in enumerate(items):
        mark = " <== OVERLAY" if wpid in pids else ""
        try:
            pname = psutil.Process(wpid).name()
        except Exception:
            pname = "?"
        print("  z%-2d hwnd=%-9d pid=%-6d(%s) TOPMOST=%s %dx%d cls=%s %r%s"
              % (i, h, wpid, pname, top, w, ht, cls, txt, mark))


c = FrozenOverlay()
time.sleep(3.0)        # frozen cold start
pid = c.proc.pid
kids = set([pid])
try:
    for ch in psutil.Process(pid).children(recursive=True):
        kids.add(ch.pid)
except Exception:
    pass
print("overlay pid", pid, "tree", kids)
c.send({"cmd": "open", "mode": "grid", "rows": 1, "cols": 3,
        "cells": [{"title": "A", "icon": "", "app": False},
                  {"title": "B", "icon": "", "app": False},
                  {"title": "C", "icon": "", "app": True}],
        "sel": 0, "accent": "#4CC2FF", "gradient": 0xB0101214})
time.sleep(2.0)
dump_large("before explorer", kids)
os.startfile("C:\\")
time.sleep(2.5)
dump_large("after explorer", kids)
time.sleep(1.2)
dump_large("after +1.2s", kids)
c.send({"cmd": "quit"})
time.sleep(0.4)
print("DONE")
