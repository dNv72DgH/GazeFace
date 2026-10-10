# Visual preview of the Qt Quick gaze overlay. Run it and LOOK:
#     .venv\Scripts\python.exe tools\qt_overlay_preview.py
# Esc closes. Set QT_PREVIEW_AUTOQUIT=1 for a non-interactive smoke test.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication

from src.extensions.launcher_icons import icon_for_type, extract_file_icon
from src.gui.qt_overlay import win_effects

HERE = os.path.dirname(os.path.abspath(__file__))
QML = os.path.join(os.path.dirname(HERE), "src", "gui", "qt_overlay",
                   "overlay.qml")


def _abs(p):
    return os.path.abspath(p).replace("\\", "/") if p else ""


def build_cells():
    # A mix: real app icons (jumbo) + generated type icons, to show how
    # both render at tile size.
    apps = [r"C:\Windows\System32\notepad.exe", r"C:\Windows\explorer.exe"]
    cells = []
    for p in apps:
        cells.append({"title": os.path.basename(p), "icon": _abs(
            extract_file_icon(p)), "app": False})
    for tp, name in (("key", "Copy"), ("file", "Open file"),
                     ("ext", "Clipboard"), ("calibrate", "Calibrate")):
        cells.append({"title": name, "icon": _abs(icon_for_type(tp)),
                      "app": tp == "ext"})
    return cells


def main():
    fmt = QSurfaceFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    QQuickWindow.setDefaultAlphaBuffer(True)

    app = QApplication(sys.argv)
    engine = QQmlApplicationEngine()
    engine.load(QUrl.fromLocalFile(QML))
    roots = engine.rootObjects()
    if not roots:
        print("QML_LOAD_FAIL")
        return 2
    win = roots[0]

    cells = build_cells()
    win.setProperty("rows", 2)
    win.setProperty("cols", 3)
    win.setProperty("cells", cells[:6])
    win.setProperty("sel", 0)
    win.escPressed.connect(app.quit)
    win.showFullScreen()

    # Win11 frosted-glass backdrop + always-on-top, applied once the
    # native window exists.
    def apply_fx():
        hwnd = int(win.winId())
        win_effects.enable_acrylic(hwnd, 0xB0101214)
        win_effects.set_topmost(hwnd)
    QTimer.singleShot(0, apply_fx)

    # Cycle the selection so the glide animation is visible.
    def cyc():
        win.setProperty("sel", (win.property("sel") + 1) % 6)
    t = QTimer()
    t.timeout.connect(cyc)
    t.start(900)

    if os.environ.get("QT_PREVIEW_AUTOQUIT"):
        QTimer.singleShot(2000, app.quit)
    app.exec()
    print("PREVIEW_DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
