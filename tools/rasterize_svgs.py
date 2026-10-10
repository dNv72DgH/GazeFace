# One-shot: rasterize the design-pack SVGs to high-res PNGs under
# assets/gaze_icons/{dir,tools,keys}/ so the app can load them without an
# SVG runtime dependency.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "icon", "new_design")
DST = os.path.join(ROOT, "assets", "gaze_icons")

SETS = {
    "dir":   os.path.join(SRC, "New page design options4", "icons", "dir"),
    "tools": os.path.join(SRC, "New page design options２", "icons", "final"),
    "keys":  os.path.join(SRC, "New page design options３", "icons", "keys"),
    "mouse": os.path.join(ROOT, "icon", "nd3", "icons", "mouse-blue"),
}

SIZE = 512


def render(svg_path, png_path, size=SIZE):
    r = QSvgRenderer(svg_path)
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    r.render(p)
    p.end()
    img.save(png_path)
    # Crop transparent margins so the glyph fills the PNG — otherwise the
    # design padding makes library icons look smaller than the
    # tight-cropped task-switcher app icons.
    try:
        from PIL import Image
        im = Image.open(png_path).convert("RGBA")
        bbox = im.getbbox()
        if bbox:
            im.crop(bbox).save(png_path)
    except Exception:
        pass


def main():
    QGuiApplication(sys.argv)
    total = 0
    for cat, src_dir in SETS.items():
        if not os.path.isdir(src_dir):
            print("MISSING", cat, src_dir)
            continue
        out_dir = os.path.join(DST, cat)
        os.makedirs(out_dir, exist_ok=True)
        n = 0
        for fn in os.listdir(src_dir):
            if not fn.lower().endswith(".svg"):
                continue
            stem = os.path.splitext(fn)[0]
            render(os.path.join(src_dir, fn),
                   os.path.join(out_dir, stem + ".png"))
            n += 1
        print("%-6s %3d -> %s" % (cat, n, out_dir))
        total += n
    print("TOTAL", total)


if __name__ == "__main__":
    main()
