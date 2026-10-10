# Copyright 2023 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Launcher item icons.

Phase: auto icon per item type (generated + cached as PNG), and an
extractor for a file's embedded icon. A pickable icon library comes
later."""

import ctypes
import logging
import os
from ctypes import wintypes

from src import paths

logger = logging.getLogger("LauncherIcons")

# Generated icon cache. Lives with the user's settings (see src/paths.py)
# rather than next to the exe, where it was unwritable in a real install
# location. Regenerable — paths.py deliberately skips it when seeding or
# backing up.
ICON_DIR = str(paths.configs_dir() / "launcher_icons")

# (background colour, glyph) per item type.
TYPE_STYLE = {
    "key": ("#1A73E8", "K"),
    # Was Google green (#34A853); switched to the theme accent orange so the
    # launcher stops using an off-theme green. (The calibration dot stays
    # green on purpose — its prompt says "look at the green dot".)
    "action": ("#D9622B", "M"),
    "file": ("#FBBC05", "F"),
    "sublauncher": ("#9334E6", "»"),
    "ext": ("#00ACC1", "E"),
    "calibrate": ("#E8710A", "C"),
    "back": ("#5F6368", "<"),
    "submonitor": ("#0B8043", "S"),
}

# Pickable icon library — the design-pack tool icons rendered to PNG
# under assets/gaze_icons/tools. name -> display label.
#
# Anchored to the INSTALL directory, not left relative. These were bare
# "assets/..." paths resolved against the process CWD, which works only
# for as long as the CWD is the install dir. run_app chdir()s there at
# startup, but that is one process-wide setting that anything can move
# out from under this module — a native file dialog is the usual
# culprit — and when it moves, every glyph silently stops resolving and
# the launcher fills with unlabelled blank tiles. The install dir cannot
# move, so ask paths for it.
_ASSETS = paths.install_dir() / "assets" / "gaze_icons"
GAZE_TOOLS_DIR = str(_ASSETS / "tools")
GAZE_KEYS_DIR = str(_ASSETS / "keys")
GAZE_MOUSE_DIR = str(_ASSETS / "mouse")
_GAZE_DIRS = (GAZE_TOOLS_DIR, GAZE_KEYS_DIR, GAZE_MOUSE_DIR)

LIBRARY = {
    "copy": "Copy", "cut": "Cut", "paste": "Paste",
    "paste-plain": "Paste plain", "undo": "Undo", "redo": "Redo",
    "save": "Save", "search": "Find", "replace": "Replace",
    "select-all": "Select all", "refresh": "Refresh",
    "screenshot": "Screenshot", "maximize": "Maximize",
    "minimize": "Minimize", "close": "Close", "back": "Back",
    "forward": "Forward", "go-start": "To start", "go-end": "To end",
    "launcher": "Launcher", "task-switcher": "Task switcher",
    "clipboard-history": "Clipboard", "app-switch": "App switch",
    "cycle-windows": "Cycle windows",
    # New design pack (Icon Index PNG) — gaze tool actions.
    "drag-mode": "Drag mode", "dwell-click": "Dwell click",
    "gaze-calibration": "Calibrate", "gaze-pause": "Gaze pause",
    "monitor-switch": "Monitor switch", "osk": "On-screen keyboard",
    "pointer-jump": "Pointer jump", "pointer-move": "Pointer move",
    "pointer-reset": "Pointer reset", "scroll": "Scroll",
    "sub-monitor-meter": "Sub monitor", "zoom-region": "Zoom region",
    "start": "Start",
}

# Common shortcut name -> library tool icon (for auto icons).
SHORTCUT_ICON = {
    "Copy": "copy", "Cut": "cut", "Paste": "paste",
    "Paste plain": "paste-plain", "Undo": "undo", "Redo": "redo",
    "Save": "save", "Find": "search", "Replace": "replace",
    "Select all": "select-all", "Close": "close", "Refresh": "refresh",
    "Maximize": "maximize", "Minimize": "minimize",
    "Screenshot": "screenshot", "Back": "back", "Forward": "forward",
    "To start": "go-start", "To end": "go-end",
    "App switch": "app-switch", "Start": "start",
}

# Item-type defaults that map to a library icon (rest fall back to a
# generated glyph).
TYPE_TOOL = {
    "back": "back",
    "sublauncher": "launcher",   # the 9-dot grid circle
}

# app_kind (extension item) -> tool icon.
EXT_TOOL = {
    "launcher": "launcher",
    "clipboard": "clipboard-history",
    "taskswitch": "task-switcher",
}


def _make_icon(cache_key, col, glyph):
    """Generate (and cache) a coloured-circle + glyph PNG; return path."""
    from PIL import Image, ImageDraw, ImageFont
    try:
        os.makedirs(ICON_DIR, exist_ok=True)
        p = os.path.join(ICON_DIR, f"{cache_key}.png")
        if os.path.exists(p):
            return p
        im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.ellipse((4, 4, 60, 60), fill=col)
        try:
            f = ImageFont.truetype("arial.ttf", 32)
        except Exception:
            f = ImageFont.load_default()
        bb = d.textbbox((0, 0), glyph, font=f)
        w, h = bb[2] - bb[0], bb[3] - bb[1]
        d.text((32 - w / 2 - bb[0], 32 - h / 2 - bb[1]), glyph,
               fill="white", font=f)
        im.save(p)
        return p
    except Exception as e:
        logger.error("_make_icon failed: %s", e)
        return None


_missing_glyphs = set()


def icon_for_glyph(name):
    """Path to a library icon PNG (tools / keys / dir), or None."""
    if not name:
        return None
    for d in _GAZE_DIRS:
        p = os.path.join(d, str(name) + ".png")
        if os.path.exists(p):
            return os.path.abspath(p)
    # Logged once per name: a glyph that stops resolving looks exactly
    # like a cell that was never given an icon. Extracted file-icon cache
    # keys are exempt — resolve_icon probes the glyph dirs for those on
    # its way to the cache, so a miss here is the normal path, not a
    # fault.
    if not str(name).startswith("file_") and name not in _missing_glyphs:
        _missing_glyphs.add(name)
        logger.warning("no icon file for glyph %r under %s",
                       name, GAZE_TOOLS_DIR)
    return None


def resolve_icon(stored):
    """Resolve a stored item-icon reference to an absolute path in the
    CURRENT install, or None.

    Launcher item icons used to be saved as absolute paths into a SPECIFIC
    build folder (…/dist43/…/gaze_icons/tools/copy.png). Deleting that old
    build then blanked every icon. This re-resolves by the icon's glyph name
    against the current install, so a stale build path (or a bare name) still
    finds the icon. Handles: bare glyph name, stale/valid absolute gaze-icon
    path, and extracted file-icon cache (file_*.png)."""
    if not stored:
        return None
    s = str(stored)
    if os.path.isabs(s) and os.path.exists(s):
        return s
    base = os.path.splitext(os.path.basename(s))[0]
    p = icon_for_glyph(base)               # gaze glyph by name
    if p:
        return p
    cand = os.path.join(ICON_DIR, base + ".png")   # extracted file icon
    if os.path.exists(cand):
        return os.path.abspath(cand)
    if os.path.exists(s):
        return os.path.abspath(s)
    return None


def _label_for(name):
    """A short display label for any library icon name."""
    if name in LIBRARY:
        return LIBRARY[name]
    for pre in ("key-", "num-", "mod-", "nav-", "sym-", "fn-", "arrow-"):
        if name.startswith(pre):
            return name[len(pre):].replace("-", " ").upper()
    return name.replace("-", " ").title()


def library_items():
    """All pickable library icons across tools / keys / dir as
    (name, label, png_path), tools first."""
    items = []
    seen = set()
    for name in LIBRARY:
        p = icon_for_glyph(name)
        if p:
            items.append((name, LIBRARY[name], p))
            seen.add(name)
    for d in (GAZE_MOUSE_DIR, GAZE_KEYS_DIR):
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                if not fn.lower().endswith(".png"):
                    continue
                nm = fn[:-4]
                if nm in seen:
                    continue
                items.append((nm, _label_for(nm),
                              os.path.abspath(os.path.join(d, fn))))
                seen.add(nm)
    return items


def icon_for_type(item_type):
    """Default icon for an item type: a design-pack tool icon where one
    fits, else a generated coloured-circle glyph."""
    tool = TYPE_TOOL.get(item_type)
    if tool:
        p = icon_for_glyph(tool)
        if p:
            return p
    col, glyph = TYPE_STYLE.get(item_type, ("#5F6368", "?"))
    # Cache-key suffix bumped (v2) so an existing install regenerates the
    # recoloured icons instead of serving the stale green PNGs.
    return _make_icon(f"type_{item_type}_v2", col, glyph)


def icon_for_ext(app_kind):
    """Tool icon for an extension item (launcher / clipboard / task)."""
    return icon_for_glyph(EXT_TOOL.get(app_kind))


def _crop_alpha(im):
    """Crop transparent borders so the visible glyph fills the tile."""
    try:
        bbox = im.getbbox()       # tight bounds of non-zero (visible) pixels
        if bbox:
            return im.crop(bbox)
    except Exception:
        pass
    return im


# --- Win32 icon extraction ------------------------------------------
#
# All of this is ctypes on purpose. It used to go through pywin32
# (win32ui for the GDI work, win32com.shell for SHGetFileInfo), which
# works in a source checkout and fails in the frozen build: PyInstaller
# puts win32ui.pyd under a Pythonwin\ subfolder that is not on sys.path,
# so the import raised, the except swallowed it, and every "Open file"
# item quietly had no icon -- in the launcher overlay, the settings grid
# and the editor preview alike, with nothing in the log above DEBUG. The
# Win32 calls themselves are half a dozen lines; the dependency was the
# whole risk.
#
# PRIVATE loaders: setting argtypes on ctypes.windll.<lib> rewrites the
# prototype process-wide for every other module (it has broken
# pydirectinput in this codebase before). And handle-returning functions
# MUST declare restype or a 64-bit HANDLE comes back truncated.
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_comctl32 = ctypes.WinDLL("comctl32", use_last_error=True)
_user32i = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

_user32i.GetDC.restype = wintypes.HDC
_user32i.GetDC.argtypes = [wintypes.HWND]
_user32i.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32i.DrawIconEx.restype = wintypes.BOOL
_user32i.DrawIconEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int,
                                ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                ctypes.c_uint, wintypes.HANDLE,
                                ctypes.c_uint]
_user32i.DestroyIcon.argtypes = [ctypes.c_void_p]
_user32i.PrivateExtractIconsW.restype = ctypes.c_uint
_user32i.PrivateExtractIconsW.argtypes = [
    wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint),
    ctypes.c_uint, ctypes.c_uint]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateDIBSection.restype = wintypes.HBITMAP
_gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p,
                                    ctypes.c_uint,
                                    ctypes.POINTER(ctypes.c_void_p),
                                    wintypes.HANDLE, wintypes.DWORD]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_shell32.ExtractIconExW.restype = ctypes.c_uint
_shell32.ExtractIconExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int,
                                    ctypes.POINTER(ctypes.c_void_p),
                                    ctypes.POINTER(ctypes.c_void_p),
                                    ctypes.c_uint]

_DI_NORMAL = 0x0003
_ILD_TRANSPARENT = 0x1
_SHIL_JUMBO = 0x4
_SHGFI_SYSICONINDEX = 0x4000
_SHGFI_USEFILEATTRIBUTES = 0x10
_FILE_ATTRIBUTE_NORMAL = 0x80
_BI_RGB = 0


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _SHFILEINFOW(ctypes.Structure):
    _fields_ = [("hIcon", ctypes.c_void_p), ("iIcon", ctypes.c_int),
                ("dwAttributes", wintypes.DWORD),
                ("szDisplayName", ctypes.c_wchar * 260),
                ("szTypeName", ctypes.c_wchar * 80)]


def _destroy_icon(hicon):
    try:
        if hicon:
            _user32i.DestroyIcon(ctypes.c_void_p(hicon))
    except Exception:
        pass


def _hicon_to_image(hicon, size):
    """Render an HICON into a `size`x`size` PIL RGBA image, or None.

    Drawn into a 32-bit top-down DIB section so the icon's own alpha
    channel survives; a screen-compatible bitmap would flatten it against
    whatever the display format happens to be.

    DrawIconEx, not DrawIcon: DrawIcon always draws at the system icon
    size (32px) and would silently downsample a 256px jumbo icon.
    """
    from PIL import Image
    bi = _BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    bi.biWidth = size
    bi.biHeight = -size          # negative height = top-down rows
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = _BI_RGB

    screen = _user32i.GetDC(None)
    mem = hbmp = old = None
    try:
        mem = _gdi32.CreateCompatibleDC(screen)
        bits = ctypes.c_void_p()
        hbmp = _gdi32.CreateDIBSection(screen, ctypes.byref(bi), 0,
                                       ctypes.byref(bits), None, 0)
        if not hbmp or not bits:
            return None
        old = _gdi32.SelectObject(mem, hbmp)
        if not _user32i.DrawIconEx(mem, 0, 0, ctypes.c_void_p(hicon),
                                   size, size, 0, None, _DI_NORMAL):
            return None
        buf = ctypes.string_at(bits, size * size * 4)
        return Image.frombuffer("RGBA", (size, size), buf, "raw", "BGRA",
                                0, 1).copy()
    finally:
        try:
            if old:
                _gdi32.SelectObject(mem, old)
            if hbmp:
                _gdi32.DeleteObject(hbmp)
            if mem:
                _gdi32.DeleteDC(mem)
            _user32i.ReleaseDC(None, screen)
        except Exception:
            pass


def _win_path(path):
    """A path the Win32 shell will actually accept.

    tkinter's file dialog returns Windows paths with FORWARD slashes
    ("C:/Users/.../book.xlsx"), and so every "Open file" item saved
    through Browse carries them. Python does not care -- os.path.exists
    is happy either way -- but SHGetFileInfoW does not normalise
    separators: it simply returns 0. That is the whole reason file items
    had no icon. The failure is silent and looks identical to "this file
    type has no icon", which is why it survived a cache-key fix and a
    rewrite of the extraction code before anyone looked at the string.
    """
    try:
        return os.path.normpath(str(path))
    except Exception:
        return str(path)


def _shell_icon_index(path, by_extension=False):
    """This file's index into the system image lists, or None.

    `by_extension` asks the shell to answer from the NAME alone
    (SHGFI_USEFILEATTRIBUTES) instead of touching the file. That is the
    answer for anything the shell will not open for us -- an offline
    OneDrive placeholder, a file on a disconnected share, a locked
    file -- where the registered icon for the type is still the right
    picture to show.
    """
    flags = _SHGFI_SYSICONINDEX
    attrs = 0
    if by_extension:
        flags |= _SHGFI_USEFILEATTRIBUTES
        attrs = _FILE_ATTRIBUTE_NORMAL
    info = _SHFILEINFOW()
    if not _shell32.SHGetFileInfoW(ctypes.c_wchar_p(_win_path(path)), attrs,
                                   ctypes.byref(info), ctypes.sizeof(info),
                                   flags):
        return None
    return int(info.iIcon)


def _jumbo_hicon(path):
    """A 256x256 ("jumbo") HICON for `path` via the system image list, or
    None. The shell renders the file's best embedded icon into the jumbo
    slot, so this is far crisper than ExtractIconEx's 32px large icon."""
    try:
        idx = _shell_icon_index(path)
        if idx is None:
            idx = _shell_icon_index(path, by_extension=True)
        if idx is None:
            return None
        # IID_IImageList {46EB5926-582E-4017-9FDF-E8998DAA0950}
        iid = _GUID(0x46EB5926, 0x582E, 0x4017,
                    (ctypes.c_ubyte * 8)(0x9F, 0xDF, 0xE8, 0x99,
                                         0x8D, 0xAA, 0x09, 0x50))
        himl = ctypes.c_void_p()
        shgil = _shell32.SHGetImageList
        shgil.argtypes = [ctypes.c_int, ctypes.POINTER(_GUID),
                          ctypes.POINTER(ctypes.c_void_p)]
        if shgil(_SHIL_JUMBO, ctypes.byref(iid),
                 ctypes.byref(himl)) != 0 or not himl.value:
            return None
        geticon = _comctl32.ImageList_GetIcon
        geticon.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint]
        geticon.restype = ctypes.c_void_p
        hicon = geticon(himl, idx, _ILD_TRANSPARENT)
        return int(hicon) if hicon else None
    except Exception as e:
        logger.warning("jumbo icon failed for %s: %s", path, e)
        return None


# Files whose icon lives in the file itself and can be read from it.
_RESOURCE_ICON_EXTS = (".exe", ".dll", ".ico")


def _resource_hicon(path, size=256):
    """The file's own first icon at `size` px, read from the file.

    For an exe this goes around the shell's icon cache, which the jumbo
    path below reads through: after an app is rebuilt with a new icon at
    the same path, the shell keeps handing out the OLD picture until its
    cache happens to be refreshed - so the task switcher kept showing
    GazeFace's previous icon after an update. Reading the resource
    directly always gets the icon the file has now.
    """
    try:
        h = ctypes.c_void_p()
        got = _user32i.PrivateExtractIconsW(
            _win_path(path), 0, size, size, ctypes.byref(h), None, 1, 0)
        if got and got != 0xFFFFFFFF and h.value:
            return int(h.value)
    except Exception as e:
        logger.warning("PrivateExtractIcons failed for %s: %s", path, e)
    return None


def _large_hicon(path):
    """The file's own 32px large icon, or None. Fallback for anything the
    shell image list will not serve."""
    try:
        large = ctypes.c_void_p()
        if _shell32.ExtractIconExW(ctypes.c_wchar_p(_win_path(path)), 0,
                                   ctypes.byref(large), None, 1) and \
                large.value:
            return int(large.value)
    except Exception as e:
        logger.warning("ExtractIconEx failed for %s: %s", path, e)
    return None


def _file_cache_name(path):
    """Cache file name for `path`'s extracted icon.

    The key covers the target's mtime and size, not just its path. The
    first version keyed on the path alone and cached forever, so any
    extraction that happened to come back with the shell's generic blank
    page -- an exe still being written, a cold icon cache, a target that
    was briefly missing -- stayed the item's icon for good, with no way
    to refresh it short of deleting the cache by hand. Rebuilding the
    target now produces a new key and the icon is re-read.
    """
    import hashlib
    try:
        st = os.stat(path)
        stamp = "%d-%d" % (int(st.st_mtime), int(st.st_size))
    except OSError:
        stamp = "0-0"
    # Normalised, so "C:/x/y.exe" and "C:\\x\\y.exe" share one cache
    # entry instead of extracting the same icon twice.
    h = hashlib.md5((os.path.normpath(path) + "|" + stamp)
                    .encode("utf-8", "ignore")).hexdigest()[:12]
    # "_rs" marks the generation that reads exe / dll / ico icons from the
    # file itself; earlier "_hi" entries may hold a stale shell-cache
    # picture and are simply no longer looked up.
    return "file_%s_rs.png" % h


def icon_for_item(item):
    """The icon a launcher item should show, as an absolute path or None.

    "Open file" items show the TARGET's own icon, looked up from the
    target every time rather than from whatever was extracted when the
    item was first saved. Two things went wrong with the stored
    snapshot: a target whose icon could not be read at that moment kept
    the generic blank page permanently, and rebuilding the target (a new
    exe with a new icon) left the launcher showing the old one.

    An icon the user picked explicitly still wins -- those are stored
    under a glyph or image name, never the extractor's "file_..." cache
    key, so the two are told apart by the stored value itself.
    """
    if not isinstance(item, dict):
        return resolve_icon(item)
    stored = item.get("icon")
    if item.get("type") == "file" and item.get("path"):
        if not stored or str(stored).startswith("file_"):
            live = extract_file_icon(item["path"])
            if live:
                return live
            # Target unreadable right now (removed drive, deleted file):
            # fall back to the last extraction, then the type glyph, so
            # the cell does not go blank.
            return resolve_icon(stored) or icon_for_type("file")
    return resolve_icon(stored)


def extract_file_icon(path):
    """Extract a file's high-resolution icon to a cached PNG; return its
    path or None. Tries the 256px "jumbo" system image-list icon first
    (what the Win11 Start menu shows), then the file's own 32px large
    icon. The visible glyph is auto-cropped so it fills a gaze tile."""
    if not path or not os.path.exists(path):
        return None
    out = None
    hicon = None
    try:
        os.makedirs(ICON_DIR, exist_ok=True)
        out = os.path.join(ICON_DIR, _file_cache_name(path))
        if os.path.exists(out):
            return out

        size = 256
        hicon = None
        if path.lower().endswith(_RESOURCE_ICON_EXTS):
            hicon = _resource_hicon(path, size)
        if hicon is None:
            hicon = _jumbo_hicon(path)
        if hicon is None:
            hicon = _large_hicon(path)
            size = 32
        if hicon is None:
            logger.warning("no icon available for %s", path)
            return None
        im = _hicon_to_image(hicon, size)
        if im is None:
            logger.warning("could not render the icon of %s", path)
            return None
        im = _crop_alpha(im)
        im.save(out)
        return out
    except Exception as e:
        # Was logger.debug, so a frozen build that could not import its
        # icon machinery failed in complete silence.
        logger.warning("extract_file_icon failed for %s: %s", path, e)
        return None
    finally:
        _destroy_icon(hicon)
