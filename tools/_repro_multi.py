import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ctypes
from ctypes import wintypes

import win32gui
import win32process
import psutil

WS_EX_TOPMOST = 0x00000008
GWL_EXSTYLE = -20
GW_HWNDPREV = 3

EXE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "dist42", "workface", "workface.exe")

OUT = open(os.path.join(os.environ.get("TEMP", "."), "repro_out.txt"), "w",
           encoding="utf-8")


def log(*a):
    OUT.write(" ".join(str(x) for x in a) + "\n")
    OUT.flush()


u = ctypes.windll.user32


class FrozenOverlay:
    def __init__(self):
        self.proc = subprocess.Popen(
            [EXE, "--qt-overlay"], stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def send(self, m):
        self.proc.stdin.write((json.dumps(m) + "\n").encode())
        self.proc.stdin.flush()


def find_overlay(pids):
    found = []

    def cb(h, _):
        if not win32gui.IsWindowVisible(h):
            return
        try:
            _, wpid = win32process.GetWindowThreadProcessId(h)
        except Exception:
            return
        if wpid in pids and "QWindowToolSaveBits" in win32gui.GetClassName(h):
            found.append(h)
    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def rect(h):
    r = wintypes.RECT()
    u.GetWindowRect(h, ctypes.byref(r))
    return r


def state(hwnd):
    """(topmost_flag, n_visible_above, first_above_cls,
        real_intruder_cls_or_None)"""
    ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
    topmost = bool(ex & WS_EX_TOPMOST)
    orc = rect(hwnd)
    h = hwnd
    n_above = 0
    first_above = None
    real = None
    for _ in range(800):
        h = u.GetWindow(h, GW_HWNDPREV)
        if not h:
            break
        if not u.IsWindowVisible(h):
            continue
        cls = win32gui.GetClassName(h)
        n_above += 1
        if first_above is None:
            first_above = cls
        # real intruder = overlapping, non-topmost, non-trivial
        if real is None and not (u.GetWindowLongW(h, GWL_EXSTYLE)
                                 & WS_EX_TOPMOST):
            r = rect(h)
            if (r.right - r.left) >= 200 and (r.bottom - r.top) >= 120:
                iw = min(orc.right, r.right) - max(orc.left, r.left)
                ih = min(orc.bottom, r.bottom) - max(orc.top, r.top)
                if iw > 0 and ih > 0:
                    real = "%s/%s" % (cls, win32gui.GetWindowText(h)[:20])
    return topmost, n_above, first_above, real


def report(hwnd, tag):
    tm, n, fa, real = state(hwnd)
    flag = "  *** BEHIND (real intruder) ***" if real else ""
    log("  [%s] topmost=%s n_above=%d first_above=%s real_intruder=%s%s"
        % (tag, tm, n, fa, real, flag))


GRID = {"cmd": "open", "mode": "grid", "rows": 1, "cols": 3,
        "cells": [{"title": "A", "icon": "", "app": False},
                  {"title": "B", "icon": "", "app": False},
                  {"title": "C", "icon": "", "app": True}],
        "sel": 0, "accent": "#4CC2FF", "gradient": 0xB0101214}
ARROWS = {"cmd": "ring_start", "accent": "#4CC2FF", "gradient": 0}

FOLDERS = [r"C:\\", r"C:\\Windows", r"C:\\Users", r"C:\\Program Files",
           r"C:\\Windows\\System32", r"C:\\Program Files (x86)"]


def open_explorers(n):
    for i in range(n):
        subprocess.Popen(["explorer.exe", "/n,", FOLDERS[i % len(FOLDERS)]])
        time.sleep(0.8)


c = FrozenOverlay()
time.sleep(3.0)
pid = c.proc.pid
pids = {pid}
try:
    for ch in psutil.Process(pid).children(recursive=True):
        pids.add(ch.pid)
except Exception:
    pass
log("=== PHASE A: launcher (grid) ===")
c.send(GRID)
time.sleep(1.5)
hwnd = find_overlay(pids)    # find once the grid is actually shown
log("overlay pid", pid, "tree", pids, "hwnd", hwnd)
if not hwnd:
    log("OVERLAY HWND NOT FOUND")
    c.send({"cmd": "quit"})
    log("DONE")
    raise SystemExit(0)
report(hwnd, "grid shown")
log("--- opening 6 NEW explorer windows ---")
for i in range(6):
    subprocess.Popen(["explorer.exe", "/n,", FOLDERS[i % len(FOLDERS)]])
    time.sleep(0.9)
    report(hwnd, "after explorer #%d" % (i + 1))
time.sleep(1.0)
report(hwnd, "settled")

log("--- re-launch grid ---")
c.send({"cmd": "close"})
time.sleep(0.6)
c.send(GRID)
time.sleep(1.5)
report(hwnd, "grid re-shown")

log("=== PHASE B: gaze-move (arrows/ring) ===")
c.send(ARROWS)
time.sleep(1.2)
report(hwnd, "ring shown")
for i in range(3):
    subprocess.Popen(["explorer.exe", "/n,", FOLDERS[i % len(FOLDERS)]])
    time.sleep(0.9)
    report(hwnd, "ring after explorer #%d" % (i + 1))
c.send({"cmd": "ring_stop"})

c.send({"cmd": "quit"})
time.sleep(0.4)
log("DONE")
