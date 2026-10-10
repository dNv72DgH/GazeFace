# Drives the real overlay subprocess over the IPC pipe (no tkinter / no
# gaze) to verify open / highlight / cells / close / quit. Stays up ~5 s.
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.gui.qt_overlay.qt_gaze_grid import OverlayClient
from src.extensions.launcher_icons import extract_file_icon, icon_for_type


def ab(p):
    return os.path.abspath(p).replace("\\", "/") if p else ""


def ic(path):
    return ab(extract_file_icon(path)) if os.path.exists(path) else ""


cells = [
    {"title": "Chrome",
     "icon": ic(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
     "app": False},
    {"title": "Explorer", "icon": ic(r"C:\Windows\explorer.exe"),
     "app": False},
    {"title": "Copy", "icon": ab(icon_for_type("key")), "app": False},
    {"title": "Open file", "icon": ab(icon_for_type("file")), "app": False},
    {"title": "Clipboard", "icon": ab(icon_for_type("ext")), "app": True},
    {"title": "Calibrate", "icon": ab(icon_for_type("calibrate")),
     "app": False},
]

c = OverlayClient.instance()
print("send open", c.send({
    "cmd": "open", "rows": 2, "cols": 3, "cells": cells, "sel": 0,
    "accent": "#4CC2FF", "gradient": 0xB0101214}))
time.sleep(1.2)
c.send({"cmd": "highlight", "sel": 4})
time.sleep(1.2)
try:
    from PIL import ImageGrab
    ImageGrab.grab().save(os.path.join(os.environ.get("TEMP", "."),
                                       "overlay_selfgrab.png"))
    print("selfgrab saved")
except Exception as e:
    print("selfgrab err", e)
time.sleep(1.8)
c.send({"cmd": "close"})
time.sleep(0.4)
c.quit()
print("IPC_TEST_DONE")
