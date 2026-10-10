# One-shot: rebuild assets/gaze_icons/{tools,mouse,dir,keys} from the
# "Icon Index PNG" design pack (already-rasterised PNGs, no SVG runtime
# needed). Clears the old icons first, then crops each PNG's transparent
# margins and caps the longest side at 512px so glyphs fill gaze tiles.
import os
import sys
import zipfile

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZIP = os.path.join(ROOT, "icon", "Icon Index PNG.zip")
DST = os.path.join(ROOT, "assets", "gaze_icons")
MAX = 512

# pack subdir -> assets/gaze_icons subdir
CATS = {
    "01-actions": "tools",
    "02-mouse": "mouse",
    "03-direction": "dir",
    "04-keytops": "keys",
}


def process(data, out_path):
    from io import BytesIO
    im = Image.open(BytesIO(data)).convert("RGBA")
    bbox = im.getbbox()                       # tight bounds of visible pixels
    if bbox:
        im = im.crop(bbox)
    w, h = im.size
    if max(w, h) > MAX:
        s = MAX / float(max(w, h))
        im = im.resize((max(1, round(w * s)), max(1, round(h * s))),
                       Image.LANCZOS)
    im.save(out_path)


def main():
    if not os.path.isfile(ZIP):
        print("MISSING ZIP", ZIP)
        return 1
    # Clear old icons in the four target dirs.
    for sub in CATS.values():
        d = os.path.join(DST, sub)
        os.makedirs(d, exist_ok=True)
        for fn in os.listdir(d):
            if fn.lower().endswith(".png"):
                os.remove(os.path.join(d, fn))
    total = 0
    counts = {}
    with zipfile.ZipFile(ZIP) as z:
        for name in z.namelist():
            if not name.lower().endswith(".png"):
                continue
            parts = name.split("/")
            # .../<cat>/<file>.png
            if len(parts) < 2:
                continue
            cat = parts[-2]
            sub = CATS.get(cat)
            if not sub:
                continue
            stem = os.path.splitext(parts[-1])[0]
            out = os.path.join(DST, sub, stem + ".png")
            process(z.read(name), out)
            counts[sub] = counts.get(sub, 0) + 1
            total += 1
    for sub, n in sorted(counts.items()):
        print("%-6s %3d" % (sub, n))
    print("TOTAL", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
