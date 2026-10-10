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
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
HWND_TOP = 0
HWND_BOTTOM = 1
SWP = 0x0002 | 0x0001 | 0x0010   # NOMOVE NOSIZE NOACTIVATE
SW_HIDE = 0
SW_SHOWNA = 8
SW_SHOWNOACTIVATE = 4
SW_MINIMIZE = 6

EXE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "dist41", "workface", "workface.exe")
OUT = open(os.path.join(os.environ.get("TEMP", "."), "recover_out.txt"), "w",
           encoding="utf-8")
u = ctypes.windll.user32


def log(*a):
    OUT.write(" ".join(str(x) for x in a) + "\n")
    OUT.flush()


def send(p, m):
    p.stdin.write((json.dumps(m) + "\n").encode())
    p.stdin.flush()


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


def real_intruder(hwnd):
    orc = rect(hwnd)
    h = hwnd
    for _ in range(800):
        h = u.GetWindow(h, GW_HWNDPREV)
        if not h:
            return None
        if not u.IsWindowVisible(h):
            continue
        if u.GetWindowLongW(h, GWL_EXSTYLE) & WS_EX_TOPMOST:
            continue
        r = rect(h)
        if (r.right - r.left) < 200 or (r.bottom - r.top) < 120:
            continue
        iw = min(orc.right, r.right) - max(orc.left, r.left)
        ih = min(orc.bottom, r.bottom) - max(orc.top, r.top)
        if iw > 0 and ih > 0:
            return win32gui.GetClassName(h)
    return None


def topmost_flag(hwnd):
    return bool(u.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)


def st(hwnd):
    return "topmost=%s real_intruder=%s" % (topmost_flag(hwnd),
                                            real_intruder(hwnd))


GRID = {"cmd": "open", "mode": "grid", "rows": 1, "cols": 3,
        "cells": [{"title": "A", "icon": "", "app": False}],
        "sel": 0, "accent": "#4CC2FF", "gradient": 0xB0101214}
FOLDERS = [r"C:\\", r"C:\\Windows", r"C:\\Users", r"C:\\Program Files"]

p = subprocess.Popen([EXE, "--qt-overlay"], stdin=subprocess.PIPE,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3.0)
pids = {p.pid}
try:
    for ch in psutil.Process(p.pid).children(recursive=True):
        pids.add(ch.pid)
except Exception:
    pass
send(p, GRID)
time.sleep(1.5)
hwnd = find_overlay(pids)
log("overlay hwnd", hwnd)
if not hwnd:
    log("NOT FOUND")
    send(p, {"cmd": "quit"})
    log("DONE")
    raise SystemExit(0)

log("initial:", st(hwnd))
log("--- open 4 explorer windows to force overlay behind ---")
for i in range(4):
    subprocess.Popen(["explorer.exe", "/n,", FOLDERS[i % len(FOLDERS)]])
    time.sleep(0.8)
log("after explorers:", st(hwnd))


def trymethod(name, fn):
    ctypes.set_last_error(0)
    try:
        ret = fn()
    except Exception as e:
        ret = "exc:%s" % e
    err = ctypes.get_last_error()
    time.sleep(0.25)
    log("  [%s] ret=%s err=%s -> %s" % (name, ret, err, st(hwnd)))


trymethod("1 SetWindowPos TOPMOST",
          lambda: u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP))
trymethod("2 NOTOPMOST then TOPMOST",
          lambda: (u.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP),
                   u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP))[-1])
trymethod("3 HWND_TOP",
          lambda: u.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0, SWP))
trymethod("4 BOTTOM then TOPMOST",
          lambda: (u.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP),
                   u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP))[-1])
trymethod("5 HIDE+SHOWNA+TOPMOST",
          lambda: (u.ShowWindow(hwnd, SW_HIDE),
                   u.ShowWindow(hwnd, SW_SHOWNA),
                   u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP))[-1])
trymethod("6 SetWindowPos SWP_SHOWWINDOW+FRAMECHANGED",
          lambda: u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                 SWP | 0x0040 | 0x0020))

# 7: ask the overlay itself to hide+reshow (fresh showFullScreen)
log("  [7 overlay close+reopen]")
send(p, {"cmd": "close"})
time.sleep(0.6)
send(p, GRID)
time.sleep(1.2)
log("    -> %s" % st(hwnd))

send(p, {"cmd": "quit"})
time.sleep(0.4)
log("DONE")
