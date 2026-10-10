import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.gui.qt_overlay.qt_gaze_grid import OverlayClient

icon_dir = "file:///" + os.path.abspath(
    os.path.join("assets", "gaze_icons", "dir")).replace("\\", "/")

c = OverlayClient.instance()
c.send({"cmd": "open", "mode": "arrows", "iconDir": icon_dir,
        "arrowDir": "NE", "accent": "#FFD23F", "gradient": 0xB0101214})
time.sleep(1.5)
c.send({"cmd": "arrow", "dir": "NE"})
time.sleep(1.0)
try:
    from PIL import ImageGrab
    ImageGrab.grab().save(os.path.join(os.environ.get("TEMP", "."),
                                       "overlay_arrows.png"))
    print("arrows grab saved")
except Exception as e:
    print("grab err", e)
time.sleep(0.5)
c.send({"cmd": "quit"})
time.sleep(0.3)
print("ARROWS_TEST_DONE")
