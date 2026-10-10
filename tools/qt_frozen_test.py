import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "dist28", "workface", "workface.exe")

cells = [{"title": "Chrome", "icon": "", "app": False},
         {"title": "Files", "icon": "", "app": False},
         {"title": "Clip", "icon": "", "app": True}]

p = subprocess.Popen([EXE, "--qt-overlay"], stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def send(m):
    p.stdin.write((json.dumps(m) + "\n").encode())
    p.stdin.flush()


send({"cmd": "open", "rows": 1, "cols": 3, "cells": cells, "sel": 0,
      "accent": "#4CC2FF", "gradient": 0xB0101214})
time.sleep(6.0)            # allow frozen PySide6 cold start
send({"cmd": "highlight", "sel": 2})
time.sleep(0.8)
try:
    from PIL import ImageGrab
    ImageGrab.grab().save(os.path.join(os.environ.get("TEMP", "."),
                                       "overlay_frozen.png"))
    print("frozen selfgrab saved")
except Exception as e:
    print("grab err", e)
time.sleep(0.5)
send({"cmd": "quit"})
try:
    _o, err = p.communicate(timeout=5)
except subprocess.TimeoutExpired:
    p.kill()
    _o, err = p.communicate()
print("RC", p.returncode)
e = err.decode("utf-8", "ignore")
if e.strip():
    print("STDERR:\n", e[-1500:])
print("FROZEN_TEST_DONE")
