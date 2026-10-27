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
"""Gaze launcher options + per-cell item registration (phase-2 minimal).

Grid size (3..9 each axis), the launcher-local gesture map
(confirm/back/exit/settings), and a clickable grid where each cell opens
a small registration popup. Items so far: Key send and Mouse action;
file / macro / sub-launcher come in later phases."""

import logging
import os
import tkinter as tk

import customtkinter

from src.config_manager import ConfigManager
from src.mouse_input import (DEFAULT_LONG_PRESS_MS, DEFAULT_SWIPE,
                             DIR_ARROWS, SWIPE_FRACS)
from src.gui.popups._util import (
    apply_popup_geometry, POPUP_BG, cached_ctk_image)
from src.gui.help_icon import section_with_help
from src.i18n import t

logger = logging.getLogger("LauncherOptions")

LAUNCHER_ACTIONS = ["confirm", "back", "exit", "settings"]
GRID_MIN, GRID_MAX = 3, 9

# Fixed pixel budget for the item-cell grid in the options panel. The
# window is a fixed 1024 px, so the grid MUST NOT scale with the launcher's
# column count — cells shrink to fit this width instead, leaving room for
# the gesture map in column 2.
ITEM_GRID_W = 466          # 480 less 3%
ITEM_CELL_MAX = 78         # 80 less 3%
ITEM_CELL_MIN = 41
ITEM_CELL_PAD = 2          # must match the cells' grid(padx=...)


def item_cell_width(cols):
    """Cell width that keeps `cols` cells inside ITEM_GRID_W."""
    avail = ITEM_GRID_W // max(1, cols) - ITEM_CELL_PAD * 2
    return max(ITEM_CELL_MIN, min(ITEM_CELL_MAX, int(avail)))


# Manage/reorder panel: fixed width of its 2nd column (the launcher list).
# Sized to the list buttons, which only ever hold a short page name.
# Manage / reorder grid cell (logical px): the width the Sub-launcher page
# already came out at, now fixed for every page.
MANAGE_CELL_W = 114
MANAGE_RIGHT_W = 124
MANAGE_LIST_BTN_W = MANAGE_RIGHT_W - 40      # minus the list's 20px padx


def _fit_button_text(button, text, max_w):
    """Cut `text` with "…" until it fits max_w logical px in `button`.

    Measured with the button's own font rather than counted: a full-width
    character is about twice as wide as a Latin one, so a character limit
    either wastes half the cell on English or overflows it in Japanese.
    """
    if not text:
        return
    try:
        import tkinter.font as tkfont
        font = tkfont.Font(root=button,
                           font=button._text_label.cget("font"))
        limit = max_w * button._get_widget_scaling()
        cut = text
        if font.measure(cut) > limit:
            while cut and font.measure(cut + "…") > limit:
                cut = cut[:-1]
            button.configure(text=cut + "…")
        # The label also carries the button's inner padding, which the
        # measurement above does not see: confirm against the size the
        # button actually asks for, and trim further if it still grows.
        full = button.cget("width") * button._get_widget_scaling()
        button.update_idletasks()
        while cut and button.winfo_reqwidth() > full + 1:
            cut = cut[:-1]
            button.configure(text=cut + "…")
            button.update_idletasks()
    except Exception:
        button.configure(text=fit_label(text, max_w))


def fit_label(text, cell_w):
    """Truncate a cell caption to what fits in a cell_w-wide button."""
    if not text:
        return text
    limit = max(3, int(cell_w / 7))    # ~7 px per character at size 11-12
    return text if len(text) <= limit else text[:limit] + "…"
ITEM_TYPES = {
    "None": "none",
    "Key / Mouse / Macro": "key",
    "Open file": "file",
    "Sub-launcher": "sublauncher",
    "Extension": "ext",
    "Gaze calibration": "calibrate",
    "Sub-monitor toggle": "submonitor",
    "Back": "back",
}
MOUSE_ACTIONS = ["left", "right", "middle", "double"]
EXT_APPS = {"Clipboard history": "clipboard",
            "Task switcher": "taskswitch"}

# Common shortcut combos -> auto title (and auto icon glyph cache key).
SHORTCUTS = {
    "ctrl+c": "Copy", "ctrl+x": "Cut", "ctrl+v": "Paste",
    "ctrl+z": "Undo", "ctrl+y": "Redo", "ctrl+a": "Select all",
    "ctrl+s": "Save", "ctrl+p": "Print", "ctrl+f": "Find",
    "ctrl+w": "Close", "alt+f4": "Quit", "ctrl+n": "New",
    "ctrl+o": "Open", "ctrl+t": "New tab",
    "ctrl+esc": "Start", "ctrl+r": "Refresh",
}


def _rev_label(mapping, label, default):
    """Translated display label -> original dict key / list item.
    Falls back to the raw label (English UI) then to `default`."""
    for k in mapping:
        if t(k) == label or k == label:
            return k
    return default


# Macro presets: (i18n label key, macro line, library icon name).
# The label is shown / saved as title via t(), in the configured
# language only. `line` is the exact macro line inserted.
MACRO_PRESETS = [
    ("Undo", "key ctrl+z", "undo"),
    ("Redo", "key ctrl+y", "redo"),
    ("Select all", "key ctrl+a", "select-all"),
    ("Copy", "key ctrl+c", "copy"),
    ("Cut", "key ctrl+x", "cut"),
    ("Paste", "key ctrl+v", "paste"),
    ("Paste plain", "key ctrl+shift+v", "paste-plain"),
    ("To start", "key ctrl+home", "go-start"),
    ("To end", "key ctrl+end", "go-end"),
    ("Find", "key ctrl+f", "search"),
    ("Replace", "key ctrl+h", "replace"),
    ("Save", "key ctrl+s", "save"),
    ("Maximize", "key win+up", "maximize"),
    ("Minimize", "key win+down", "minimize"),
    ("Close window", "key alt+f4", "close"),
    ("App switch", "key win+tab", "app-switch"),
    ("Cycle windows", "key alt+esc", "cycle-windows"),
    ("Screenshot", "key win+shift+s", "screenshot"),
    ("Back", "key alt+left", "back"),
    ("Forward", "key alt+right", "forward"),
    ("Refresh", "key ctrl+r", "refresh"),
    ("Start", "key ctrl+esc", "start"),
    ("Left click", "mouse left", "left"),
    ("Right click", "mouse right", "right"),
    ("Middle click", "mouse middle", "middle"),
    ("Double click", "mouse double", "double-click"),
    ("Wheel up", "wheel up", "wheel-up"),
    ("Wheel down", "wheel down", "wheel-down"),
    ("Drag left", "drag left", "drag-left"),
    ("Long press left", "hold left %d" % DEFAULT_LONG_PRESS_MS,
     "long-press"),
] + [
    # Medium swipe in all eight directions. The icon follows both the
    # direction and the length (swipe-<dir>-s/m/l), so a hand-edited
    # swipe still gets the right picture -- see _preset_glyph.
    ("Swipe " + _name, "swipe %s medium" % DIR_ARROWS[_d],
     "swipe-%s-m" % _d.lower())
    for _d, _name in (("N", "up"), ("NE", "up-right"), ("E", "right"),
                      ("SE", "down-right"), ("S", "down"),
                      ("SW", "down-left"), ("W", "left"),
                      ("NW", "up-left"))
]

# swipe distance word -> icon length suffix
_SWIPE_SIZE = {"small": "s", "medium": "m", "large": "l"}

# tk keysym -> our modifier name.
_MOD_KEYSYMS = {
    "Control_L": "ctrl", "Control_R": "ctrl", "Shift_L": "shift",
    "Shift_R": "shift", "Alt_L": "alt", "Alt_R": "alt",
    "Super_L": "win", "Super_R": "win", "Win_L": "win", "Win_R": "win",
}
_SPECIAL_KEYSYMS = {
    "Left": "left", "Right": "right", "Up": "up", "Down": "down",
    "Home": "home", "End": "end", "Prior": "pageup", "Next": "pagedown",
    "Return": "enter", "Escape": "esc", "Tab": "tab", "space": "space",
    "BackSpace": "backspace", "Delete": "delete", "Insert": "insert",
}


def _map_keysym(ks):
    if ks in _SPECIAL_KEYSYMS:
        return _SPECIAL_KEYSYMS[ks]
    if len(ks) == 1:
        return ks.lower()
    return ks.lower()


_ARROW_TO_DIR = {v: k for k, v in DIR_ARROWS.items()}


def _preset_glyph(steps):
    """Library icon name for a macro, or None.

    Any macro that is exactly one of the presets gets that preset's
    icon, whatever the step type -- which is what gives hand-typed holds
    and swipes a picture, not just keyboard shortcuts. A single swipe
    that matches no preset (a different length) still gets the swipe
    icon for its direction and length.
    """
    try:
        text = _macro_text({"steps": steps}).strip()
    except Exception:
        return None
    if not text:
        return None
    for _label, line, icon in MACRO_PRESETS:
        if icon and _macro_text(
                {"steps": _parse_macro(line)}).strip() == text:
            return icon
    if len(steps) == 1 and "swipe" in steps[0]:
        d = str(steps[0]["swipe"]).lower()
        size = _SWIPE_SIZE.get(str(steps[0].get("dist", "")).lower())
        if size:
            return "swipe-%s-%s" % (d, size)
    if len(steps) == 1 and "hold" in steps[0]:
        return "long-press"
    return None


def _shortcut_name(steps):
    """If the macro is a single common shortcut, return its name."""
    if len(steps) == 1 and "key" in steps[0]:
        combo = "+".join(steps[0]["key"])
        return SHORTCUTS.get(combo)
    return None


def _items_root(ext):
    return (ext.setdefault("launcher", {})
            .setdefault("pages", {})
            .setdefault("root", {})
            .setdefault("items", {}))


def _page_items(ext, page):
    return (ext.setdefault("launcher", {})
            .setdefault("pages", {})
            .setdefault(page, {"title": page, "items": {}})
            .setdefault("items", {}))


def _macro_text(item):
    """Render an item's steps as editable lines."""
    steps = item.get("steps")
    if not steps:
        ks = item.get("keys")
        return ("key " + "+".join(ks)) if ks else ""
    lines = []
    for s in steps:
        if "key" in s:
            lines.append("key " + "+".join(s["key"]))
        elif "wait" in s:
            lines.append("wait %d" % int(s["wait"]))
        elif "text" in s:
            lines.append("text " + str(s["text"]))
        elif "mouse" in s:
            lines.append("mouse " + str(s["mouse"]))
        elif "wheel" in s:
            n = int(s.get("n", 1) or 1)
            lines.append("wheel %s%s" % (s["wheel"],
                                         (" %d" % n) if n != 1 else ""))
        elif "drag" in s:
            lines.append("drag " + str(s["drag"]))
        elif "hold" in s:
            lines.append("hold %s %d" % (s["hold"], int(
                s.get("ms", DEFAULT_LONG_PRESS_MS))))
        elif "swipe" in s:
            # Shown with the arrow so the line reads as a direction; the
            # parser takes either form back (see _parse_macro).
            d = str(s["swipe"]).upper()
            lines.append("swipe %s %s" % (DIR_ARROWS.get(d, d),
                                          s.get("dist", DEFAULT_SWIPE)))
    return "\n".join(lines)


def _parse_macro(text):
    """Parse macro lines: 'key ctrl+c' / 'wait 100' / 'text hello'
    (a bare 'ctrl+c' is treated as a key step)."""
    steps = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("wait "):
            try:
                steps.append({"wait": int(line[5:].strip())})
            except ValueError:
                pass
        elif low.startswith("text "):
            steps.append({"text": line[5:]})
        elif low.startswith("mouse "):
            steps.append({"mouse": line[6:].strip().lower()})
        elif low.startswith("wheel "):
            # "wheel up" / "wheel down 3"
            parts = low[6:].split()
            step = {"wheel": parts[0] if parts else "up"}
            if len(parts) > 1:
                try:
                    step["n"] = max(1, int(parts[1]))
                except ValueError:
                    pass
            steps.append(step)
        elif low.startswith("drag "):
            steps.append({"drag": line[5:].strip().lower()})
        elif low.startswith("hold "):
            # "hold left 500" — button optional, defaults to left
            parts = low[5:].split()
            btn, ms = "left", DEFAULT_LONG_PRESS_MS
            for p in parts:
                if p in ("left", "middle", "right"):
                    btn = p
                else:
                    try:
                        ms = max(0, int(p))
                    except ValueError:
                        pass
            steps.append({"hold": btn, "ms": ms})
        elif low.startswith("swipe "):
            # "swipe ↗ medium" / "swipe ne medium" / "swipe s 800" —
            # arrows are what the editor writes, compass letters are what
            # older saved macros hold, so both are accepted.
            parts = line[6:].split()
            d = parts[0] if parts else "N"
            step = {"swipe": _ARROW_TO_DIR.get(d, d.upper())}
            if len(parts) > 1:
                step["dist"] = parts[1].lower()
            steps.append(step)
        else:
            body = line[4:] if low.startswith("key ") else line
            keys = [k.strip().lower() for k in body.split("+") if k.strip()]
            if keys:
                steps.append({"key": keys})
    return steps


def _norm(v):
    if isinstance(v, dict):
        return [v]
    if isinstance(v, list):
        return list(v)
    return []


def _running_apps():
    """Executable names of apps with a visible top-level window."""
    s = set()
    try:
        import win32gui
        import win32process
        import psutil

        def cb(h, _):
            if not win32gui.IsWindowVisible(h):
                return
            if not win32gui.GetWindowText(h):
                return
            try:
                _, pid = win32process.GetWindowThreadProcessId(h)
                s.add(psutil.Process(pid).name())
            except Exception:
                pass
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    return sorted(s)


class LauncherOptionsPopup:

    def __init__(self, master):
        self.master = master
        self._window = None
        self.top = None
        self._edit_stack = ["root"]
        self._cur_page = "root"
        self._anim_id = None
        self._anim_idx = 0
        self._cells = {}

    @staticmethod
    def _open_in_window():
        try:
            e = ConfigManager().read_extensions() or {}
            return bool(e.get("launcher", {}).get("options_in_window", False))
        except Exception:
            return False

    def open(self):
        from src.gui import panel_host as ph
        title = f"{t('Gaze launcher')} {t('Options')}"
        self._own_window = False
        if self._open_in_window():
            # Already open: bring it forward instead of stacking a second
            # launcher window on top of the first.
            existing = ph._OVERRIDE
            if existing is not None:
                existing.raise_window()
                return
            # Its own window, so another page stays readable while the
            # launcher is edited. The nested dialogs find it through the
            # host override rather than through a changed call signature.
            host = ph.WindowPanelHost(self.master, title,
                                      on_closed=self._window_closed)
            ph.set_host_override(host)
            self._own_window = True
        else:
            host = ph.panel_host()
            if host is None:
                return
        self._host = host
        self._edit_stack = ["root"]
        self._cur_page = "root"
        self.top = host.push(title,
                             on_close=self._cancel_anim,
                             on_back=self._back_step,
                             can_back=lambda: len(self._edit_stack) > 1)
        self._build()

    def _window_closed(self):
        """The options window went away (the host clears its own routing)."""
        self._cancel_anim()

    def _back_step(self):
        """Back inside a sub-launcher steps UP one level instead of
        closing the whole options panel. Descending rebuilds this same
        panel in place (see _descend), so without this the host had no
        idea the user was three levels deep and Back dropped them all the
        way out to the Extensions page."""
        if len(self._edit_stack) > 1:
            self._go_up()
            return True
        return False

    def _sync_title(self):
        """Header follows the sub-launcher we are editing."""
        base = f"{t('Gaze launcher')} {t('Options')}"
        if self._cur_page != "root":
            e = self._data()
            page = e.get("launcher", {}).get("pages", {}).get(
                self._cur_page, {})
            base = "%s / %s" % (base, page.get("title") or self._cur_page)
        try:
            self._host.set_title(self.top, base)
            # Entering or leaving a sub-launcher changes whether Back has
            # anywhere to go.
            self._host.refresh_back()
        except Exception:
            pass

    # ---------------------------------------------------------------- #

    def _data(self):
        e = ConfigManager().read_extensions()
        e.setdefault("launcher", {})
        return e

    def _bound_gestures(self):
        b = ConfigManager().mouse_bindings | ConfigManager().keyboard_bindings
        return sorted(b.keys())

    def _build(self):
        self._cancel_anim()
        self._cells = {}
        for w in self.top.winfo_children():
            w.destroy()
        e = self._data()
        lc = e["launcher"]
        rows = int(lc.get("rows", 3))
        cols = int(lc.get("cols", 3))
        cur = self._cur_page
        # Anchor widget the item grid is packed BEFORE, so the item grid
        # sits directly under the grid-size row (above the launcher
        # gestures). None on non-root pages (no gesture section there).
        self._gestures_anchor = None
        pages = lc.setdefault("pages", {})
        page = pages.setdefault(
            cur, {"title": "Launcher" if cur == "root" else cur,
                  "items": {}})

        # --- breadcrumb / page header ---
        # On the ROOT page this row would only repeat the panel header, so
        # it is built for sub-launchers only (where the page name and its
        # Back / Page settings buttons are the actual navigation).
        if cur != "root":
            hrow = customtkinter.CTkFrame(self.top, fg_color="transparent")
            hrow.pack(padx=20, pady=(16, 6), anchor="w", fill="x")
            customtkinter.CTkButton(
                hrow, text="◀ " + t("Back"), width=80,
                command=self._go_up).pack(side="left", padx=(0, 8))
            hl = customtkinter.CTkLabel(hrow, text=page.get("title", cur))
            hl.cget("font").configure(size=18, weight="bold")
            hl.pack(side="left")
            customtkinter.CTkButton(
                hrow, text=t("Page settings"), width=120,
                command=self._page_settings).pack(side="left", padx=(12, 0))

        # Two columns: left = grid size / colour button / the item grid;
        # right = the launcher gesture map. Column 0 is pinned to
        # ITEM_GRID_W and the item cells below are sized to fit inside it,
        # so however many grid columns the launcher has, the left column
        # never grows and never pushes the gesture map off the right edge
        # ("表情割り当てが見切れ").
        # (NOTE: container is `optcols`, NOT `cols` — `cols` is the grid
        # COLUMN COUNT used below.)
        optcols = customtkinter.CTkFrame(self.top, fg_color="transparent")
        optcols.pack(fill="both", expand=True)
        optcols.grid_columnconfigure(0, minsize=ITEM_GRID_W + 40)
        left = customtkinter.CTkFrame(optcols, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nw", padx=(0, 8))
        right = customtkinter.CTkFrame(optcols, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nw", padx=(8, 0))
        self._opt_left, self._opt_right = left, right

        # --- root-only: grid size + launcher gesture map ---
        if cur == "root":
            srow = customtkinter.CTkFrame(left, fg_color="transparent")
            # Top pad: on the root page this is the first row in the panel
            # (the breadcrumb header is sub-launcher only now).
            srow.pack(padx=20, anchor="w", fill="x", pady=(16, 4))
            customtkinter.CTkLabel(srow, text=t("Rows")).pack(
                side="left", padx=(0, 4))
            self.rows_var = tk.StringVar(value=str(rows))
            customtkinter.CTkEntry(srow, textvariable=self.rows_var,
                                   width=50).pack(side="left", padx=(0, 12))
            customtkinter.CTkLabel(srow, text=t("Columns")).pack(
                side="left", padx=(0, 4))
            self.cols_var = tk.StringVar(value=str(cols))
            customtkinter.CTkEntry(srow, textvariable=self.cols_var,
                                   width=50).pack(side="left", padx=(0, 12))
            customtkinter.CTkButton(srow, text=t("Apply"), width=70,
                                    command=self._apply_size).pack(
                                        side="left")

            # (Background transparency moved into the shared Colour
            # settings popup — see the button below.)

            # Ignore blinks (ms): a newly gazed cell must stay stable this
            # long before it becomes the selection, so a blink's transient
            # jump reverts before it commits (0 = off). Same idea as the
            # gesture "ignore involuntary movements" option.
            brow = customtkinter.CTkFrame(left, fg_color="transparent")
            brow.pack(padx=20, anchor="w", fill="x", pady=(6, 4))
            customtkinter.CTkLabel(
                brow, text=t("Ignore blinks (ms)")).pack(
                    side="left", padx=(0, 8))
            self.blink_var = tk.IntVar(
                value=int(lc.get("ignore_blink_ms", 0) or 0))
            customtkinter.CTkSlider(
                brow, from_=0, to=500, number_of_steps=25, width=200,
                variable=self.blink_var,
                command=lambda _v: self._save_blink()).pack(
                    side="left", padx=(0, 6))
            customtkinter.CTkLabel(brow, textvariable=self.blink_var,
                                   width=44).pack(side="left")

            # Dwell-to-confirm: keep gazing at the SELECTED cell this long
            # to execute it (0 = execution by gesture only).
            crow = customtkinter.CTkFrame(left, fg_color="transparent")
            crow.pack(padx=20, anchor="w", fill="x", pady=(6, 4))
            customtkinter.CTkLabel(
                crow, text=t("Dwell confirm (ms)")).pack(
                    side="left", padx=(0, 8))
            self.confirm_var = tk.IntVar(
                value=int(lc.get("confirm_ms", 0) or 0))
            customtkinter.CTkSlider(
                crow, from_=0, to=3000, number_of_steps=30, width=200,
                variable=self.confirm_var,
                command=lambda _v: self._save_confirm()).pack(
                    side="left", padx=(0, 6))
            customtkinter.CTkLabel(crow, textvariable=self.confirm_var,
                                   width=44).pack(side="left")

            # Open these options in their own window instead of over the
            # page. Read at open time, so the change applies next time.
            self.win_mode_var = tk.BooleanVar(
                value=bool(lc.get("options_in_window", False)))
            customtkinter.CTkCheckBox(
                left, text=t("Open in a separate window"),
                variable=self.win_mode_var, onvalue=True, offvalue=False,
                command=self._save_win_mode).pack(
                    padx=20, pady=(8, 2), anchor="w")

            # Shared colour + transparency settings (launcher / clipboard /
            # task switcher share one set).
            customtkinter.CTkButton(
                left, text=t("Color settings"), width=180,
                command=self._open_color_settings).pack(
                    padx=20, pady=(12, 4), anchor="w")

            section_with_help(right, t("Launcher gestures"), "Launcher gestures",
                              pady=(14, 2))
            # Master-detail: pick a function (confirm/back/exit…) then check
            # the gestures for it. A gesture used by another function is
            # disabled (mutual exclusion). Config is gesture-keyed
            # ({gesture: slot}).
            from src.gui.popups._util import GestureMapPicker
            gestures = self._gesture_choices()
            gmap = lc.get("gestures", {}) or {}
            GestureMapPicker(
                right, LAUNCHER_ACTIONS, gmap, gestures,
                save_cb=self._save_gmap,
                label_fn=lambda x: (t(x.capitalize())
                                    if x in LAUNCHER_ACTIONS else t(x)))

        # --- item grid (current page) --- left column, under grid size.
        ilrow = customtkinter.CTkFrame(self._opt_left,
                                       fg_color="transparent")
        ilrow.pack(padx=20, pady=(14, 4), anchor="w", fill="x")
        il = customtkinter.CTkLabel(
            ilrow, text=t("Items (click a cell to edit)"))
        il.cget("font").configure(size=14, weight="bold")
        il.pack(side="left")
        customtkinter.CTkButton(
            ilrow, text=t("Manage / reorder"), width=130,
            command=self._open_manage).pack(side="left", padx=(12, 0))
        gf = customtkinter.CTkFrame(self._opt_left, fg_color="transparent")
        gf.pack(padx=20, anchor="w")
        cell_w = item_cell_width(cols)
        items = page.setdefault("items", {})
        self._cell_imgs = []
        # Build the cell buttons ONE ROW PER EVENT-LOOP TURN — a well-filled
        # 9×9 grid built synchronously froze the panel for a beat ("the
        # grid is heavy"). The generation counter invalidates pending row
        # builds when _build reruns (page switch / save).
        self._build_gen = getattr(self, "_build_gen", 0) + 1
        gen = self._build_gen

        def build_row(r):
            if gen != self._build_gen or self.top is None:
                return
            try:
                if not gf.winfo_exists():   # panel closed externally
                    return
            except Exception:
                return
            for c in range(cols):
                lst = _norm(items.get(f"{r},{c}"))
                disp = next((x for x in lst if not x.get("app")), None) \
                    or (lst[0] if lst else None)
                lbl = fit_label((disp.get("title") if disp else "") or "+",
                                cell_w)
                kw = {}
                colored = any(x.get("app") for x in lst)
                if colored:                          # app-specific cell
                    # Multi-app cells are the theme's pale orange with an
                    # orange label; hover inverts to a solid-orange fill with
                    # a white label for clear feedback (was navy/white).
                    kw["fg_color"] = "#F3E5DC"
                    kw["text_color"] = "#D9622B"
                # Show the cell's icon on the button (above its title).
                img = self._cell_image(disp)
                if img is not None:
                    kw["image"] = img
                    kw["compound"] = "top"
                # Cells shrink to keep the whole grid inside ITEM_GRID_W, so
                # a wide launcher can never push the gesture column off the
                # edge of the panel ("2列目が見切れ").
                btn = customtkinter.CTkButton(
                    gf, text=lbl, width=cell_w, height=64,
                    command=lambda r=r, c=c: self._edit(r, c), **kw)
                if colored:
                    btn.cget("font").configure(weight="bold")  # white label
                    # configure() triggers a full redraw that repaints the
                    # fill from fg_color, cancelling CTk's internal hover
                    # paint — so drive BOTH colours ourselves: light fill +
                    # black text while hovered, dark fill + white text
                    # otherwise (text_color alone left black-on-navy).
                    def _enter(_e, b=btn):
                        b.configure(fg_color="#D9622B", text_color="white")

                    def _leave(_e, b=btn):
                        b.configure(fg_color="#F3E5DC", text_color="#D9622B")
                    btn._wf_enter, btn._wf_leave = _enter, _leave
                    btn.bind("<Enter>", _enter)
                    btn.bind("<Leave>", _leave)
                btn.grid(row=r, column=c, padx=2, pady=3)
                self._cells[(r, c)] = (btn, lst)
            if r + 1 < rows:
                try:
                    self.top.after(1, lambda: build_row(r + 1))
                except Exception:
                    pass
            else:
                self._anim_idx = 0
                self._schedule_anim()

        build_row(0)

    # ---------------------------------------------------------------- #
    # Sub-launcher page navigation
    # ---------------------------------------------------------------- #

    def _cell_image(self, item):
        """Thumbnail for one launcher cell. Takes the ITEM, not its stored
        icon name: an "Open file" item's icon comes from its target, which
        only the item itself knows."""
        if not item:
            return None
        from src.extensions.launcher_icons import icon_for_item
        from PIL import Image
        try:
            p = icon_for_item(item)
            if not p or not os.path.exists(p):
                return None
            # 30px (was 40) so the icon fits the 80%-height cell while the
            # label font stays the same size.
            img = cached_ctk_image(p, (30, 30))
            if img is not None:
                self._cell_imgs.append(img)
            return img
        except Exception:
            return None

    def _descend(self, target):
        self._edit_stack.append(target)
        self._cur_page = target
        self._build()
        self._sync_title()

    def _go_up(self):
        if len(self._edit_stack) > 1:
            self._edit_stack.pop()
            self._cur_page = self._edit_stack[-1]
            self._build()
            self._sync_title()

    def _page_settings(self):
        cur = self._cur_page
        if cur == "root":
            return
        e = self._data()
        page = e["launcher"]["pages"].get(cur, {})

        def save(name):
            e2 = self._data()
            e2["launcher"]["pages"].setdefault(
                cur, {"items": {}})["title"] = name.strip() or cur
            ConfigManager().write_extensions(e2)
            self._build()
        SmallInputDialog(self.master).open(
            t("Sub-launcher name"), save, default=page.get("title", cur))

    def _apply_size(self):
        try:
            r = max(GRID_MIN, min(GRID_MAX, int(self.rows_var.get())))
            c = max(GRID_MIN, min(GRID_MAX, int(self.cols_var.get())))
        except ValueError:
            return
        e = self._data()
        e["launcher"]["rows"] = r
        e["launcher"]["cols"] = c
        ConfigManager().write_extensions(e)
        self._build()

    def _save_alpha(self):
        # alpha_var is TRANSPARENCY %; store opacity = 1 - transparency.
        e = self._data()
        e["launcher"]["alpha"] = max(
            0.1, min(1.0, 1.0 - self.alpha_var.get() / 100.0))
        ConfigManager().write_extensions(e)

    def _save_blink(self):
        e = self._data()
        e["launcher"]["ignore_blink_ms"] = int(self.blink_var.get())
        ConfigManager().write_extensions(e)

    def _save_win_mode(self):
        e = self._data()
        e.setdefault("launcher", {})["options_in_window"] = bool(
            self.win_mode_var.get())
        ConfigManager().write_extensions(e)

    def _save_confirm(self):
        e = self._data()
        e["launcher"]["confirm_ms"] = int(self.confirm_var.get())
        ConfigManager().write_extensions(e)

    def _save_gmap(self, gmap):
        e = self._data()
        e.setdefault("launcher", {})["gestures"] = dict(gmap)
        ConfigManager().write_extensions(e)

    def _open_color_settings(self):
        from src.gui.popups.ext_grid_options_popup import ColorSettingsPopup
        ColorSettingsPopup(self.master).open()

    def _gesture_choices(self):
        # Only gestures actually BOUND in the main menu. The keybinder only
        # produces values for bound gestures, so an unbound one (e.g. a stale
        # 'enabled' entry copied in from an old profile) would never fire
        # here anyway — listing it just clutters the picker.
        #
        # BOTH stores: a gesture assigned on the Keyboard page is just as
        # usable inside a gaze tool as one from the Mouse page, and looking
        # only at mouse_bindings hid every keyboard-assigned gesture from
        # this picker.
        import src.shape_list as sl
        bound = list(ConfigManager().mouse_bindings.keys()) + \
            list(ConfigManager().keyboard_bindings.keys())
        seen = []
        for g in bound:
            if g in sl.blendshape_names and g not in seen:
                seen.append(g)
        return seen

    def _edit(self, r, c):
        ItemRegisterPopup(self.master).open(
            r, c, on_saved=self._build, page=self._cur_page,
            on_descend=self._descend)

    def _open_manage(self):
        ManageReorderPopup(self.master).open(
            page=self._cur_page, on_done=self._build)

    # Cycle through an app-specific cell's slots every 2 s in the preview.
    def _schedule_anim(self):
        try:
            self._anim_id = self.top.after(2000, self._anim_tick)
        except Exception:
            self._anim_id = None

    def _anim_tick(self):
        self._anim_idx += 1
        for (_r, _c), (btn, lst) in list(self._cells.items()):
            if len(lst) > 1:
                it = lst[self._anim_idx % len(lst)]
                try:
                    # Swap the ICON along with the title — title-only
                    # cycling read as "the icon never switches".
                    img = self._cell_image(it)
                    if img is not None:
                        btn.configure(text=(it.get("title") or "+"),
                                      image=img, compound="top")
                        # configure(image=...) can create a NEW internal
                        # image label that lacks our hover handlers —
                        # attach them once (flagged so repeats don't stack
                        # duplicate binds every tick).
                        en = getattr(btn, "_wf_enter", None)
                        il = getattr(btn, "_image_label", None)
                        if (en is not None and il is not None
                                and not getattr(il, "_wf_hover", False)):
                            il._wf_hover = True
                            il.bind("<Enter>", en, add="+")
                            il.bind("<Leave>", btn._wf_leave, add="+")
                    else:
                        btn.configure(text=(it.get("title") or "+"))
                except Exception:
                    pass
        self._schedule_anim()

    def _cancel_anim(self):
        if self._anim_id is not None and self.top is not None:
            try:
                self.top.after_cancel(self._anim_id)
            except Exception:
                pass
        self._anim_id = None

    def close(self):
        h = getattr(self, "_host", None)
        if h is not None and self.top is not None:
            h.pop(self.top)
        self._window = None
        self.top = None


class ItemRegisterPopup:

    def __init__(self, master):
        self.master = master
        self._win = None

    def open(self, r, c, on_saved=None, page="root", on_descend=None):
        self.r, self.c = r, c
        self.on_saved = on_saved
        self.page = page
        self.on_descend = on_descend
        ext = ConfigManager().read_extensions()
        self._cell_list = _norm(
            ext.get("launcher", {}).get("pages", {}).get(page, {})
            .get("items", {}).get(f"{r},{c}"))

        from src.gui.panel_host import panel_host
        host = panel_host()
        if host is None:
            return
        self._host = host
        body = host.push(f"{t('Register item')} {t('Cell')} {r},{c}")
        self._win = body          # nested dialogs ignore this (use host)

        # --- common / app-specific ---
        self.app_specific_var = tk.BooleanVar(value=False)
        customtkinter.CTkCheckBox(
            body, text=t("App-specific item"),
            variable=self.app_specific_var, onvalue=True, offvalue=False,
            command=self._load_slot).pack(padx=20, anchor="w", pady=(0, 4))
        arow = customtkinter.CTkFrame(body, fg_color="transparent")
        arow.pack(padx=20, anchor="w", fill="x")
        customtkinter.CTkLabel(arow, text=t("App")).pack(
            side="left", padx=(0, 4))
        self.app_name_var = tk.StringVar(value="(select)")
        customtkinter.CTkOptionMenu(
            arow, values=["(select)"] + _running_apps(),
            variable=self.app_name_var, width=240,
            command=lambda _v: self._load_slot()).pack(side="left")

        # slot body (rebuilt when the common/app selection changes)
        self.slot_frame = customtkinter.CTkFrame(
            body, fg_color="transparent")
        self.slot_frame.pack(anchor="w", fill="x")

        brow = customtkinter.CTkFrame(body, fg_color="transparent")
        brow.pack(padx=20, pady=16, anchor="w")
        customtkinter.CTkButton(brow, text=t("OK"), command=self._save,
                                width=90).pack(side="left", padx=(0, 8))
        customtkinter.CTkButton(brow, text=t("Clear"), command=self._clear,
                                width=90).pack(side="left", padx=(0, 8))
        customtkinter.CTkButton(brow, text=t("Cancel"), command=self.close,
                                width=90).pack(side="left")
        self._load_slot()

    def _cur_app(self):
        if not self.app_specific_var.get():
            return None
        a = self.app_name_var.get()
        return a if a and a != "(select)" else None

    def _load_slot(self):
        app = self._cur_app()
        self._it = next(
            (x for x in self._cell_list
             if (x.get("app") or None) == (app or None)), {})
        sf = self.slot_frame
        for w in sf.winfo_children():
            w.destroy()
        customtkinter.CTkLabel(sf, text=t("Type")).pack(padx=20, anchor="w")
        self.type_var = tk.StringVar(
            value=t(next((k for k, v in ITEM_TYPES.items()
                          if v == self._it.get("type")), "None")))
        customtkinter.CTkOptionMenu(
            sf, values=[t(k) for k in ITEM_TYPES], variable=self.type_var,
            command=lambda _v: self._refresh()).pack(
                padx=20, anchor="w", pady=(0, 8))
        customtkinter.CTkLabel(sf, text=t("Title")).pack(padx=20, anchor="w")
        self.title_var = tk.StringVar(value=self._it.get("title", ""))
        customtkinter.CTkEntry(sf, textvariable=self.title_var,
                               width=280).pack(padx=20, anchor="w",
                                               pady=(0, 8))
        # icon: a chosen image / library glyph, or auto by type.
        cur_icon = self._it.get("icon") or ""
        self.icon_path = "" if (not cur_icon or ("launcher_icons" in cur_icon
                                and "type_" in cur_icon)) else cur_icon
        irow = customtkinter.CTkFrame(sf, fg_color="transparent")
        irow.pack(padx=20, anchor="w", fill="x", pady=(0, 8))
        customtkinter.CTkLabel(irow, text=t("Icon")).pack(
            side="left", padx=(0, 8))
        customtkinter.CTkButton(irow, text=t("Library"), width=90,
                                command=self._choose_lib).pack(
                                    side="left", padx=(0, 4))
        customtkinter.CTkButton(irow, text=t("Choose image"), width=120,
                                command=self._choose_icon).pack(
                                    side="left", padx=(0, 4))
        # Selected-icon preview to the RIGHT of the icon buttons.
        self.icon_preview = customtkinter.CTkLabel(irow, text="", width=48,
                                                   height=48)
        self.icon_preview.pack(side="left", padx=(12, 0))
        self.field = customtkinter.CTkFrame(sf, fg_color="transparent")
        self.field.pack(padx=20, anchor="w", fill="x")
        self._refresh()
        self._update_icon_preview()

    def _dialog_parent(self):
        """The toplevel this editor is showing in.

        A file dialog opened without a parent belongs to the app's root
        window, so Windows brings the ROOT forward along with it - and the
        launcher's own window, which the user was working in, dropped
        behind the main window every time Browse was pressed.
        """
        try:
            return self._win.winfo_toplevel()
        except Exception:
            return None

    def _choose_icon(self):
        from tkinter import filedialog
        p = filedialog.askopenfilename(
            parent=self._dialog_parent(),
            filetypes=[("Image", "*.png *.ico *.jpg *.jpeg *.bmp")])
        if p:
            self.icon_path = p
            self._update_icon_preview()

    def _effective_icon(self):
        """The icon path that will actually be saved/shown for the current
        selection: an explicitly chosen icon wins, else the type/app
        default."""
        if self.icon_path:
            return self.icon_path
        from src.extensions.launcher_icons import (
            icon_for_type, icon_for_ext, icon_for_glyph, extract_file_icon,
            SHORTCUT_ICON)
        try:
            tp = ITEM_TYPES[_rev_label(ITEM_TYPES,
                                        self.type_var.get(), "None")]
        except Exception:
            return None
        if tp == "file":
            # Preview the target's own icon as soon as a path is typed or
            # browsed to. Without this the editor showed the generic "F"
            # tile right up until the item was saved, so the icon the user
            # was choosing was never the icon they were shown.
            path = ""
            try:
                path = self.path_var.get().strip()
            except Exception:
                pass
            return (extract_file_icon(path) if path else None)                 or icon_for_type("file")
        if tp == "ext":
            kind = EXT_APPS.get(_rev_label(
                EXT_APPS,
                getattr(self, "ext_var", None) and self.ext_var.get(), None))
            return icon_for_ext(kind) or icon_for_type("ext")
        if tp == "key":
            try:
                steps = _parse_macro(self.macro_box.get("1.0", "end"))
                g = _preset_glyph(steps)
                if g and icon_for_glyph(g):
                    return icon_for_glyph(g)
                sc = _shortcut_name(steps)
                if sc and sc in SHORTCUT_ICON:
                    return icon_for_glyph(SHORTCUT_ICON[sc])
            except Exception:
                pass
        return icon_for_type(tp)

    def _update_icon_preview(self):
        if getattr(self, "icon_preview", None) is None:
            return
        from src.extensions.launcher_icons import resolve_icon
        p = resolve_icon(self._effective_icon())
        try:
            img = cached_ctk_image(p, (56, 56)) if (
                p and os.path.exists(p)) else None
            if img is not None:
                self._preview_img = img
                self.icon_preview.configure(image=img, text="")
            else:
                self.icon_preview.configure(image=None, text=t("Auto"))
        except Exception:
            self.icon_preview.configure(image=None, text="")

    def _autofill_title(self, name):
        """Overwrite the title with the chosen function name."""
        try:
            if name:
                self.title_var.set(name)
        except Exception:
            pass

    def _refresh(self):
        for w in self.field.winfo_children():
            w.destroy()
        tp = ITEM_TYPES[_rev_label(ITEM_TYPES, self.type_var.get(), "None")]
        if tp == "key":
            krow = customtkinter.CTkFrame(self.field, fg_color="transparent")
            krow.pack(anchor="w", fill="x")
            self.macro_box = customtkinter.CTkTextbox(
                krow, width=300, height=180)
            self.macro_box.grid(row=0, column=0, sticky="nw")
            self.macro_box.insert("1.0", _macro_text(self._it))
            bar = customtkinter.CTkFrame(krow, fg_color="transparent")
            bar.grid(row=0, column=1, sticky="n", padx=(10, 0))
            for label, cmd in ((t("Preset"), self._ins_preset),
                               ("⌨ " + t("Key"), self._ins_key),
                               (t("Mouse"), self._ins_mouse),
                               (t("Phone"), self._ins_phone),
                               (t("Text"), self._ins_text),
                               (t("Wait"), self._ins_wait)):
                customtkinter.CTkButton(bar, text=label, width=130,
                                        command=cmd).pack(pady=3, anchor="n")
            from src.gui.help_icon import HelpIcon
            HelpIcon(bar, "Key / mouse macro").pack(pady=(8, 0), anchor="n")
        elif tp == "action":
            customtkinter.CTkLabel(
                self.field, text=t("Mouse action")).pack(anchor="w")
            self.act_var = tk.StringVar(
                value=t(self._it.get("action", "left")))
            customtkinter.CTkOptionMenu(
                self.field, values=[t(a) for a in MOUSE_ACTIONS],
                variable=self.act_var,
                width=160).pack(anchor="w")
        elif tp == "file":
            customtkinter.CTkLabel(
                self.field, text=t("File path")).pack(anchor="w")
            frow = customtkinter.CTkFrame(self.field, fg_color="transparent")
            frow.pack(anchor="w", fill="x")
            self.path_var = tk.StringVar(value=self._it.get("path", ""))
            # The icon preview is derived from this path, so it has to
            # follow every edit — typed as well as browsed.
            self.path_var.trace_add(
                "write", lambda *_a: self._update_icon_preview())
            customtkinter.CTkEntry(frow, textvariable=self.path_var,
                                   width=320).pack(side="left", padx=(0, 6))
            customtkinter.CTkButton(frow, text=t("Browse"), width=80,
                                    command=self._browse).pack(side="left")
            customtkinter.CTkLabel(
                self.field, text=t("Arguments (optional)")).pack(anchor="w")
            self.args_var = tk.StringVar(value=self._it.get("args", ""))
            customtkinter.CTkEntry(self.field, textvariable=self.args_var,
                                   width=320).pack(anchor="w")
        elif tp == "sublauncher":
            customtkinter.CTkLabel(
                self.field, text=t("Sub-launcher name")).pack(anchor="w")
            self.sub_var = tk.StringVar(value=self._it.get("title", ""))
            customtkinter.CTkEntry(self.field, textvariable=self.sub_var,
                                   width=280).pack(anchor="w")
            if self._it.get("target") and self.on_descend is not None:
                customtkinter.CTkButton(
                    self.field, text=t("Open / edit sub-launcher"),
                    width=200, command=self._open_sub).pack(
                        anchor="w", pady=(6, 0))
        elif tp == "ext":
            customtkinter.CTkLabel(
                self.field, text=t("Extension")).pack(anchor="w")
            cur = next((k for k, v in EXT_APPS.items()
                        if v == self._it.get("app_kind")),
                       "Clipboard history")
            self.ext_var = tk.StringVar(value=t(cur))
            customtkinter.CTkOptionMenu(
                self.field, values=[t(k) for k in EXT_APPS],
                variable=self.ext_var,
                width=200, command=lambda v: self._on_ext_change(v)).pack(
                    anchor="w")
            # Prefill only when the title is empty (new item) — reopening
            # a saved ext cell must not clobber the user's custom title.
            if not self.title_var.get().strip():
                self._autofill_title(t(cur))
        self._update_icon_preview()

    def _on_ext_change(self, name):
        self._autofill_title(name)
        self._update_icon_preview()

    # --- macro step inserters ---------------------------------------- #

    def _macro_insert(self, line):
        if getattr(self, "macro_box", None) is None:
            return
        cur = self.macro_box.get("1.0", "end").strip("\n")
        self.macro_box.delete("1.0", "end")
        # Trailing newline so the next inserted step starts a fresh line
        # (matches the Preset insert behaviour).
        new = (cur + "\n" + line) if cur else line
        self.macro_box.insert("1.0", new + "\n")

    def _ins_key(self):
        KeyScanDialog(self._win).open(
            lambda keys: self._macro_insert("key " + "+".join(keys)))

    def _ins_mouse(self):
        MouseStepDialog(self._win).open(
            lambda line: self._macro_insert(line))

    def _ins_phone(self):
        PhoneStepDialog(self._win).open(
            lambda line: self._macro_insert(line))

    def _ins_text(self):
        SmallInputDialog(self._win).open(
            t("Text to type"), lambda s: self._macro_insert("text " + s))

    def _ins_wait(self):
        SmallInputDialog(self._win).open(
            t("Wait (ms)"), lambda s: self._macro_insert("wait " + s),
            numeric=True, default="100")

    def _ins_preset(self):
        def pick(line, label, icon):
            from src.extensions.launcher_icons import icon_for_glyph
            # Overwrite the macro box with the preset (trailing newline).
            if getattr(self, "macro_box", None) is not None:
                self.macro_box.delete("1.0", "end")
                self.macro_box.insert("1.0", line + "\n")
            try:
                self.title_var.set(t(label))   # configured language only
            except Exception:
                pass
            p = icon_for_glyph(icon) if icon else None
            if p:
                self.icon_path = p
            self._update_icon_preview()
        PresetPickPopup(self._win).open(pick)

    def _open_sub(self):
        target = self._it.get("target")
        od = self.on_descend
        self.close()
        if od and target:
            od(target)

    def _browse(self):
        from tkinter import filedialog
        p = filedialog.askopenfilename(parent=self._dialog_parent())
        if p:
            self.path_var.set(p)
            self._update_icon_preview()

    def _save(self):
        import time as _t
        e = ConfigManager().read_extensions()
        items = _page_items(e, self.page)
        tp = ITEM_TYPES[_rev_label(ITEM_TYPES, self.type_var.get(), "None")]
        title = self.title_var.get().strip()
        app = self._cur_app()
        key = f"{self.r},{self.c}"
        # Replace just this app's slot (common slot when app is None).
        lst = [x for x in _norm(items.get(key))
               if (x.get("app") or None) != (app or None)]

        new = None
        if tp == "key":
            steps = _parse_macro(self.macro_box.get("1.0", "end"))
            sc = _shortcut_name(steps)
            auto = "+".join(steps[0]["key"]) if (
                steps and "key" in steps[0]) else ""
            new = {"type": "key", "steps": steps,
                   "title": title or (t(sc) if sc else "") or auto or
                   "macro"}
        elif tp == "action":
            act = _rev_label({a: a for a in MOUSE_ACTIONS},
                             self.act_var.get(), "left")
            new = {"type": "action", "action": act,
                   "title": title or t(act)}
        elif tp == "file":
            path = self.path_var.get().strip()
            base = os.path.basename(path) if path else "file"
            new = {"type": "file", "path": path,
                   "args": self.args_var.get().strip(),
                   "title": title or base}
        elif tp == "sublauncher":
            name = self.sub_var.get().strip() or t("Sub-launcher")
            target = self._it.get("target")
            if not target:
                target = "sub_" + format(int(_t.time() * 1000), "x")
                e.setdefault("launcher", {}).setdefault("pages", {})[
                    target] = {"title": name, "items": {}}
            else:
                e["launcher"]["pages"].setdefault(
                    target, {"title": name, "items": {}})["title"] = name
            new = {"type": "sublauncher", "target": target, "title": name}
        elif tp == "ext":
            new = {"type": "ext",
                   "app_kind": EXT_APPS[_rev_label(
                       EXT_APPS, self.ext_var.get(), "Clipboard history")],
                   "title": title or self.ext_var.get()}
        elif tp == "calibrate":
            new = {"type": "calibrate",
                   "title": title or t("Gaze calibration")}
        elif tp == "back":
            new = {"type": "back", "title": title or t("Back")}
        elif tp == "submonitor":
            new = {"type": "submonitor",
                   "title": title or t("Sub-monitor")}

        if new is not None:
            if app:
                new["app"] = app
            new["icon"] = self._pick_icon(tp, new)
            lst.append(new)

        if lst:
            items[key] = lst
        else:
            items.pop(key, None)
        ConfigManager().write_extensions(e)
        self._done()

    def _pick_icon(self, tp, item):
        p = self._pick_icon_path(tp, item)
        # Persist a PORTABLE reference (glyph / basename) rather than an
        # absolute build path, so deleting an old build folder can't blank
        # the icon. resolve_icon() maps it back at display time.
        return os.path.splitext(os.path.basename(str(p)))[0] if p else None

    def _pick_icon_path(self, tp, item):
        from src.extensions.launcher_icons import (
            icon_for_type, icon_for_ext, icon_for_glyph, extract_file_icon,
            SHORTCUT_ICON)
        if self.icon_path:
            return self.icon_path
        if tp == "file" and item.get("path"):
            return extract_file_icon(item["path"]) or icon_for_type("file")
        if tp == "ext":
            return icon_for_ext(item.get("app_kind")) or icon_for_type("ext")
        if tp == "key":
            g = icon_for_glyph(_preset_glyph(item.get("steps", [])))
            if g:
                return g
            sc = _shortcut_name(item.get("steps", []))
            if sc and sc in SHORTCUT_ICON:
                g = icon_for_glyph(SHORTCUT_ICON[sc])
                if g:
                    return g
        return icon_for_type(tp)

    def _choose_lib(self):
        def pick(p, label):
            self.icon_path = p
            self._autofill_title(label)
            self._update_icon_preview()
        IconPickPopup(self._win).open(pick)

    def _clear(self):
        """Reset the editor form (does NOT save or close the window).
        Save with type None to actually empty the cell."""
        # Drop the loaded item too — otherwise picking a type after Clear
        # rebuilds its fields from the OLD item (the macro / path / action
        # reappear), which read as "the pre-clear settings came back".
        self._it = {}
        self.icon_path = ""
        try:
            self.title_var.set("")
            self.type_var.set(t("None"))
        except Exception:
            pass
        self._refresh()
        self._update_icon_preview()

    def _done(self):
        cb = self.on_saved
        self.close()
        if cb:
            try:
                cb()
            except Exception:
                pass

    def close(self):
        h = getattr(self, "_host", None)
        if h is not None and self._win is not None:
            h.pop(self._win)
        self._win = None


class IconPickPopup:
    """Pick an icon from the built-in library glyph grid."""

    def __init__(self, master):
        self.master = master
        self._win = None
        self._imgs = []

    def open(self, on_pick):
        from src.extensions.launcher_icons import library_items
        from src.gui.panel_host import panel_host
        host = panel_host()
        if host is None:
            return
        self._host = host
        self._win = host.push(t("Choose icon"))
        # Building all ~135 CTkButtons synchronously froze the UI for a
        # beat ("the library is heavy"). Render in small chunks on the
        # event loop instead — the window appears instantly and fills in.
        items = list(library_items())
        cols = 8
        chunk = 16

        def build(start):
            body = self._win
            try:
                if body is None or not body.winfo_exists():
                    return          # closed mid-build
            except Exception:
                return
            for i in range(start, min(start + chunk, len(items))):
                name, label, p = items[i]
                img = cached_ctk_image(p, (40, 40))
                customtkinter.CTkButton(
                    body, text="" if img else label, image=img,
                    width=54, height=54, fg_color="transparent",
                    command=lambda pa=p, la=label: self._pick(
                        on_pick, pa, la)).grid(
                        row=i // cols, column=i % cols, padx=3, pady=3)
            if start + chunk < len(items):
                try:
                    body.after(1, lambda: build(start + chunk))
                except Exception:
                    pass
        build(0)

    def _pick(self, on_pick, path, label):
        self.close()
        if on_pick and path:
            on_pick(path, label)

    def close(self):
        h = getattr(self, "_host", None)
        if h is not None and self._win is not None:
            h.pop(self._win)
        self._win = None
        self._imgs = []


class KeyScanDialog:
    """Capture a key chord (with modifiers) by pressing it. OK inserts the
    chord; Clear restarts the scan."""

    def __init__(self, master):
        self.master = master
        self._win = None
        self._main = None
        self._mod_vars = {}

    def open(self, on_ok):
        self.on_ok = on_ok
        self._main = None
        from src.gui.panel_host import panel_host
        host = panel_host()
        if host is None:
            return
        self._host = host
        self._win = host.push(t("Scan key"))
        body = self._win
        customtkinter.CTkLabel(
            body, text=t("Tick modifiers, then press the main key.")).pack(
                padx=20, pady=(22, 8))
        # Modifiers as checkboxes (Esc etc. can then be the main key
        # without the dialog closing).
        mrow = customtkinter.CTkFrame(body, fg_color="transparent")
        mrow.pack(pady=(0, 6))
        self._mod_vars = {}
        for m in ("ctrl", "shift", "alt", "win"):
            v = tk.BooleanVar(value=False)
            self._mod_vars[m] = v
            customtkinter.CTkCheckBox(
                mrow, text=m.capitalize(), variable=v, onvalue=True,
                offvalue=False, command=self._refresh_lbl).pack(
                    side="left", padx=6)
        self.lbl = customtkinter.CTkLabel(body, text="—")
        self.lbl.cget("font").configure(size=24, weight="bold")
        self.lbl.pack(padx=20, pady=12)
        brow = customtkinter.CTkFrame(body, fg_color="transparent")
        brow.pack(pady=14)
        customtkinter.CTkButton(brow, text=t("OK"), width=80,
                                command=self._ok).pack(side="left", padx=6)
        customtkinter.CTkButton(brow, text=t("Clear"), width=80,
                                command=self._clear).pack(side="left", padx=6)
        # Capture the main key. Esc is a normal capturable key here (NOT a
        # close shortcut). Modifier keysyms are ignored (use the boxes).
        tl = body.winfo_toplevel()
        self._tl = tl
        self._bind_id = tl.bind("<KeyPress>", self._on_key, add="+")
        try:
            tl.focus_set()
        except Exception:
            pass

    def _combo(self):
        mods = [m for m in ("ctrl", "shift", "alt", "win")
                if self._mod_vars[m].get()]
        return mods + ([self._main] if self._main else [])

    def _refresh_lbl(self):
        c = self._combo()
        self.lbl.configure(text=" + ".join(c) if c else "—")

    def _on_key(self, event):
        ks = event.keysym
        if ks in _MOD_KEYSYMS:
            return "break"   # modifiers come from the checkboxes
        self._main = _map_keysym(ks)
        self._refresh_lbl()
        return "break"

    def _clear(self):
        self._main = None
        self._refresh_lbl()

    def _ok(self):
        combo = self._combo()
        cb = self.on_ok
        self.close()
        if combo and cb:
            cb(combo)

    def close(self):
        tl = getattr(self, "_tl", None)
        bid = getattr(self, "_bind_id", None)
        if tl is not None and bid is not None:
            try:
                tl.unbind("<KeyPress>", bid)
            except Exception:
                pass
        self._tl = None
        self._bind_id = None
        h = getattr(self, "_host", None)
        if h is not None and self._win is not None:
            h.pop(self._win)
        self._win = None


class MouseStepDialog:
    """Pick a mouse click / drag / wheel step for a macro."""

    OPTIONS = [("Left click", "mouse left"), ("Right click", "mouse right"),
               ("Middle click", "mouse middle"),
               ("Double click", "mouse double")]
    # A drag holds the button down until a later click (or another drag
    # step) drops it, so the pointer can be moved by head tracking in
    # between.
    DRAG_OPTIONS = [("Drag left", "drag left"),
                    ("Drag middle", "drag middle"),
                    ("Drag right", "drag right")]
    # Vertical and horizontal wheel. Horizontal is a different event
    # (MOUSEEVENTF_HWHEEL), not a sign on the vertical one.
    WHEEL_DIRS = [("↑", "up"), ("↓", "down"), ("←", "left"), ("→", "right")]

    def __init__(self, master):
        self.master = master
        self._win = None

    def open(self, on_pick):
        from src.gui.panel_host import panel_host
        host = panel_host()
        if host is None:
            return
        self._host = host
        self._win = host.push(t("Mouse step"))
        body = self._win
        row = 0
        for group in (self.OPTIONS, self.DRAG_OPTIONS):
            for i, (label, line) in enumerate(group):
                customtkinter.CTkButton(
                    body, text=t(label), width=180,
                    command=lambda ln=line: self._pick(on_pick, ln)).grid(
                        row=row + i // 2, column=i % 2, padx=10, pady=8)
            row += (len(group) + 1) // 2

        # --- wheel: direction + how many notches ---
        customtkinter.CTkLabel(
            body, text=t("Wheel"),
            font=customtkinter.CTkFont(weight="bold")).grid(
                row=row, column=0, columnspan=2, padx=10, pady=(16, 4),
                sticky="w")
        wrow = customtkinter.CTkFrame(body, fg_color="transparent")
        wrow.grid(row=row + 1, column=0, columnspan=2, padx=10, pady=(0, 8),
                  sticky="w")
        customtkinter.CTkLabel(wrow, text=t("Notches")).pack(
            side="left", padx=(0, 6))
        self.notch_var = tk.StringVar(value="1")
        customtkinter.CTkEntry(wrow, textvariable=self.notch_var,
                               width=60).pack(side="left", padx=(0, 12))
        for arrow, d in self.WHEEL_DIRS:
            customtkinter.CTkButton(
                wrow, text=arrow, width=52,
                command=lambda dd=d: self._pick_wheel(on_pick, dd)).pack(
                    side="left", padx=3)

    def _pick_wheel(self, on_pick, d):
        try:
            n = max(1, int(self.notch_var.get().strip()))
        except ValueError:
            n = 1
        self._pick(on_pick, "wheel %s%s" % (d, (" %d" % n) if n != 1 else ""))

    def _pick(self, on_pick, line):
        self.close()
        if on_pick:
            on_pick(line)

    def close(self):
        h = getattr(self, "_host", None)
        if h is not None and self._win is not None:
            h.pop(self._win)
        self._win = None


class PhoneStepDialog:
    """Pick a phone-style gesture step: long press or swipe.

    Both are sent as real button-down / move / button-up sequences rather
    than a click, which is what phone-mirroring apps need to tell a tap
    from a hold and from a swipe.
    """

    # Laid out as a compass; the arrow IS the label (a bare "NE" made the
    # user work out which way that points).
    DIRS = [(DIR_ARROWS["NW"], "NW"), (DIR_ARROWS["N"], "N"),
            (DIR_ARROWS["NE"], "NE"),
            (DIR_ARROWS["W"], "W"), ("", ""), (DIR_ARROWS["E"], "E"),
            (DIR_ARROWS["SW"], "SW"), (DIR_ARROWS["S"], "S"),
            (DIR_ARROWS["SE"], "SE")]

    def __init__(self, master):
        self.master = master
        self._win = None

    def open(self, on_pick):
        from src.gui.panel_host import panel_host
        host = panel_host()
        if host is None:
            return
        self._host = host
        self._on_pick = on_pick
        self._win = host.push(t("Phone gesture"))
        body = self._win

        # --- long press ---
        customtkinter.CTkLabel(
            body, text=t("Long press"),
            font=customtkinter.CTkFont(weight="bold")).grid(
                row=0, column=0, columnspan=3, padx=10, pady=(16, 4),
                sticky="w")
        lp = customtkinter.CTkFrame(body, fg_color="transparent")
        lp.grid(row=1, column=0, columnspan=3, padx=10, pady=(0, 4),
                sticky="w")
        self.btn_var = tk.StringVar(value="left")
        for b in ("left", "middle", "right"):
            customtkinter.CTkRadioButton(
                lp, text=t(b.capitalize()), variable=self.btn_var,
                value=b).pack(side="left", padx=(0, 10))
        customtkinter.CTkLabel(lp, text=t("Hold (ms)")).pack(
            side="left", padx=(10, 6))
        self.ms_var = tk.StringVar(value=str(DEFAULT_LONG_PRESS_MS))
        customtkinter.CTkEntry(lp, textvariable=self.ms_var,
                               width=70).pack(side="left")
        customtkinter.CTkButton(
            body, text=t("Add long press"), width=180,
            command=self._add_hold).grid(row=2, column=0, padx=10,
                                         pady=(0, 12), sticky="w")

        # --- swipe ---
        customtkinter.CTkLabel(
            body, text=t("Swipe"),
            font=customtkinter.CTkFont(weight="bold")).grid(
                row=3, column=0, columnspan=3, padx=10, pady=(8, 4),
                sticky="w")
        dist = customtkinter.CTkFrame(body, fg_color="transparent")
        dist.grid(row=4, column=0, columnspan=3, padx=10, pady=(0, 6),
                  sticky="w")
        self.dist_var = tk.StringVar(value=DEFAULT_SWIPE)
        for key, label in (("small", "Short"), ("medium", "Medium"),
                           ("large", "Long"), ("custom", "Custom (px)")):
            customtkinter.CTkRadioButton(
                dist, text=t(label), variable=self.dist_var,
                value=key).pack(side="left", padx=(0, 10))
        self.px_var = tk.StringVar(value="600")
        customtkinter.CTkEntry(dist, textvariable=self.px_var,
                               width=70).pack(side="left")
        # The 8 directions as a compass, so the layout says which is which
        # without a label per button.
        grid = customtkinter.CTkFrame(body, fg_color="transparent")
        grid.grid(row=5, column=0, columnspan=3, padx=10, pady=(0, 16),
                  sticky="w")
        for i, (label, d) in enumerate(self.DIRS):
            if not d:
                continue
            customtkinter.CTkButton(
                grid, text=label, width=56,
                command=lambda dd=d: self._add_swipe(dd)).grid(
                    row=i // 3, column=i % 3, padx=4, pady=4)

    def _add_hold(self):
        try:
            ms = max(0, int(self.ms_var.get().strip()))
        except ValueError:
            ms = DEFAULT_LONG_PRESS_MS
        self._emit("hold %s %d" % (self.btn_var.get(), ms))

    def _add_swipe(self, d):
        dist = self.dist_var.get()
        if dist == "custom":
            try:
                dist = str(max(1, int(self.px_var.get().strip())))
            except ValueError:
                dist = DEFAULT_SWIPE
        elif dist not in SWIPE_FRACS:
            dist = DEFAULT_SWIPE
        self._emit("swipe %s %s" % (d, dist))

    def _emit(self, line):
        cb = self._on_pick
        self.close()
        if cb:
            cb(line)

    def close(self):
        h = getattr(self, "_host", None)
        if h is not None and self._win is not None:
            h.pop(self._win)
        self._win = None


class SmallInputDialog:
    """A one-field prompt (text / number)."""

    def __init__(self, master):
        self.master = master
        self._win = None

    def open(self, label, on_ok, numeric=False, default=""):
        from src.gui.panel_host import panel_host
        host = panel_host()
        if host is None:
            return
        self._host = host
        self._win = host.push(label)
        body = self._win
        customtkinter.CTkLabel(body, text=label).pack(padx=20, pady=(24, 6))
        var = tk.StringVar(value=str(default))
        ent = customtkinter.CTkEntry(body, textvariable=var, width=260)
        ent.pack(padx=20)
        ent.focus_set()

        def ok():
            v = var.get().strip()
            if numeric:
                try:
                    v = str(int(v))
                except ValueError:
                    return
            self.close()
            if v and on_ok:
                on_ok(v)
        customtkinter.CTkButton(body, text=t("OK"), width=90,
                                command=ok).pack(padx=20, pady=16, anchor="w")
        ent.bind("<Return>", lambda _e: ok())

    def close(self):
        h = getattr(self, "_host", None)
        if h is not None and self._win is not None:
            h.pop(self._win)
        self._win = None


class PresetPickPopup:
    """Grid of common-shortcut presets (icon + label)."""

    def __init__(self, master):
        self.master = master
        self._win = None
        self._imgs = []

    def open(self, on_pick):
        from PIL import Image
        from src.extensions.launcher_icons import icon_for_glyph
        from src.gui.panel_host import panel_host
        host = panel_host()
        if host is None:
            return
        self._host = host
        self._win = host.push(t("Preset"))
        body = self._win
        # Three columns: 38 presets in two columns meant scrolling through
        # nineteen rows to find one. Buttons narrowed to fit the panel.
        cols = 3
        for i, (label, line, icon) in enumerate(MACRO_PRESETS):
            p = icon_for_glyph(icon) if icon else None
            img = cached_ctk_image(p, (34, 34)) if p else None
            customtkinter.CTkButton(
                body, text="  " + t(label), image=img, anchor="w",
                compound="left", width=220, height=48,
                command=lambda ln=line, la=label, ic=icon: self._pick(
                    on_pick, ln, la, ic)).grid(
                    row=i // cols, column=i % cols, padx=5, pady=5)

    def _pick(self, on_pick, line, label, icon):
        self.close()
        if on_pick:
            on_pick(line, label, icon)

    def close(self):
        h = getattr(self, "_host", None)
        if h is not None and self._win is not None:
            h.pop(self._win)
        self._win = None
        self._imgs = []


class ManageReorderPopup:
    """Manage & reorder the launcher grids.

    Column 1: an instruction label with a "multi-select" toggle to its
    right, the page's cell grid (selection shown by a light-blue FILL, no
    border), then the operation buttons — Swap / Insert / Insert blank /
    Delete / Deselect. Swap & Insert switch to "click the destination"
    mode; Insert and Swap shift the destination-and-rightward cells right
    (error when there's no room). Insert blank inserts one blank per
    selected item, consecutively, at the first selection (error when
    there's no room); Delete empties them after a confirm. Operation
    buttons are disabled while nothing is selected.

    Selection: with the multi-select toggle OFF, a click selects just that
    one cell. With it ON, selection behaves like holding Shift in Excel —
    the first click sets an anchor, later clicks select the whole range
    from the anchor.

    Column 2: the launcher + its sub-launchers, coloured like the gesture
    function buttons (current = white/black, others = grey/white). Picking
    one switches the grid. Delete (confirm) removes a SUB-launcher —
    disabled for the root launcher."""

    _CELL_FG = "white"          # default cell fill
    _CELL_SEL = "#F2DFD3"       # selected cell fill (accent tint, no border)

    def __init__(self, master):
        self.master = master
        self._win = None
        self.page = "root"
        self.on_done = None
        self._sel = []            # selected linear indices
        self._anchor = None       # range anchor (Excel-shift multi-select)
        self._multi = False       # multi-select toggle state
        self._mode = "select"     # "select" | "swap" | "insert"
        self._cell_btns = {}
        self._op_btns = {}

    # ------------------------------------------------------------- #

    def open(self, page="root", on_done=None):
        self.page = page
        self.on_done = on_done
        from src.gui.panel_host import panel_host
        host = panel_host()
        if host is None:
            return
        self._host = host
        self._win = host.push(t("Manage / reorder"))
        self._build()

    def _data(self):
        e = ConfigManager().read_extensions()
        e.setdefault("launcher", {}).setdefault("pages", {}).setdefault(
            "root", {"title": "Launcher", "items": {}})
        return e

    def _dims(self):
        lc = self._data()["launcher"]
        return int(lc.get("rows", 3)), int(lc.get("cols", 3))

    def _linear(self):
        """The current page's items as a row-major list (None = empty)."""
        rows, cols = self._dims()
        items = (self._data()["launcher"]["pages"].get(self.page, {})
                 or {}).get("items", {}) or {}
        out = []
        for r in range(rows):
            for c in range(cols):
                out.append(items.get(f"{r},{c}"))
        return out

    def _write_linear(self, lst):
        rows, cols = self._dims()
        e = self._data()
        page = e["launcher"]["pages"].setdefault(
            self.page, {"title": self.page, "items": {}})
        # Carry over items stored OUTSIDE the current rows×cols window
        # (hidden by an earlier grid shrink) — rebuilding only from the
        # visible window would silently delete them for good.
        items = {}
        for k, v in (page.get("items", {}) or {}).items():
            try:
                r, c = (int(x) for x in k.split(","))
            except ValueError:
                items[k] = v
                continue
            if r >= rows or c >= cols:
                items[k] = v
        for i, v in enumerate(lst[:rows * cols]):
            if v:
                items[f"{i // cols},{i % cols}"] = v
        page["items"] = items
        ConfigManager().write_extensions(e)

    # ------------------------------------------------------------- #

    def _build(self):
        self._sel = []
        self._anchor = None
        self._mode = "select"
        self._cell_btns = {}
        self._op_btns = {}
        for w in self._win.winfo_children():
            w.destroy()
        rows, cols = self._dims()
        lst = self._linear()

        colsf = customtkinter.CTkFrame(self._win, fg_color="transparent")
        colsf.pack(fill="both", expand=True)
        # Pin the 2nd column so the layout doesn't jump between pages.
        # 160 = the old 200 less 20%, which is all the launcher-list
        # buttons need once they are sized to match.
        colsf.grid_columnconfigure(1, minsize=MANAGE_RIGHT_W)
        left = customtkinter.CTkFrame(colsf, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nw", padx=(0, 10))
        right = customtkinter.CTkFrame(colsf, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nw", padx=(10, 0))

        # --- column 1: instruction (+ multi toggle) + grid + operations ---
        irow = customtkinter.CTkFrame(left, fg_color="transparent")
        irow.pack(padx=20, pady=(14, 6), anchor="w", fill="x")
        self.info_lbl = customtkinter.CTkLabel(
            irow, text=t("Select items and an operation"))
        self.info_lbl.cget("font").configure(size=13, weight="bold")
        self.info_lbl.pack(side="left")
        # A SWITCH, not a button — a two-colour button read as "which state
        # am I in?"; the switch knob makes on/off unambiguous.
        self.multi_var = tk.BooleanVar(value=self._multi)
        self.multi_sw = customtkinter.CTkSwitch(
            irow, text=t("Multi-select"), variable=self.multi_var,
            onvalue=True, offvalue=False, command=self._toggle_multi)
        self.multi_sw.cget("font").configure(size=11, weight="bold")
        self.multi_sw.pack(side="left", padx=(12, 0))

        gf = customtkinter.CTkFrame(left, fg_color="transparent")
        gf.pack(padx=20, anchor="w")
        # Every cell is MANAGE_CELL_W wide on every page, and its caption
        # is cut to what fits that width. A caption wider than the button
        # used to stretch it - Japanese characters are twice as wide as a
        # character count assumes - so a page with long titles came out
        # wider than one without, and the launcher list beside it moved
        # when switching pages.
        for c in range(cols):
            gf.grid_columnconfigure(c, uniform="mcell")
        self._cell_imgs = []
        for i, v in enumerate(lst):
            lst_norm = _norm(v)
            disp = next((x for x in lst_norm if not x.get("app")), None) \
                or (lst_norm[0] if lst_norm else None)
            lbl = (disp.get("title") if disp else "") or ""
            kw = {}
            img = None
            if disp:
                from src.extensions.launcher_icons import icon_for_item
                p = icon_for_item(disp)
                if p and os.path.exists(p):
                    img = cached_ctk_image(p, (28, 28))
            if img is not None:
                kw["image"] = img
                kw["compound"] = "top"
                self._cell_imgs.append(img)
            b = customtkinter.CTkButton(
                gf, text=lbl, width=MANAGE_CELL_W, height=64,
                border_width=0, fg_color=self._CELL_FG,
                command=lambda i=i: self._cell_click(i), **kw)
            _fit_button_text(b, lbl, MANAGE_CELL_W - 12)
            b.grid(row=i // cols, column=i % cols, padx=3, pady=3)
            self._cell_btns[i] = b

        # Operation buttons wrap over multiple rows (3 per row) so column 1
        # stays narrow and the launcher list in column 2 isn't squeezed off.
        brow = customtkinter.CTkFrame(left, fg_color="transparent")
        brow.pack(padx=20, pady=(10, 4), anchor="w")
        for i, (key, label, cmd) in enumerate((
                ("swap", t("Swap"), self._op_swap),
                ("insert", t("Insert"), self._op_insert),
                ("blank", t("Insert blank"), self._op_blank),
                ("delete", t("Delete"), self._op_delete),
                ("deselect", t("Deselect"), self._op_deselect))):
            b = customtkinter.CTkButton(brow, text=label, width=104,
                                        command=cmd)
            b.grid(row=i // 3, column=i % 3, padx=(0, 6), pady=2,
                   sticky="w")
            self._op_btns[key] = b
        self._sync_state()

        # --- column 2: launcher / sub-launcher list ---
        ll = customtkinter.CTkLabel(right, text=t("Launchers"))
        ll.cget("font").configure(size=13, weight="bold")
        ll.pack(padx=20, pady=(14, 6), anchor="w")
        pages = self._data()["launcher"]["pages"]
        order = ["root"] + sorted(k for k in pages if k != "root")
        for k in order:
            title = t("Launcher") if k == "root" else (
                pages[k].get("title") or k)
            cur = k == self.page
            # Coloured like the gesture function buttons — current page
            # accent-filled, others plain surface (FacePilot style).
            b = customtkinter.CTkButton(
                right, text=fit_label(title, MANAGE_LIST_BTN_W),
                width=MANAGE_LIST_BTN_W, anchor="w",
                fg_color=("#D9622B" if cur else "white"),
                text_color=("white" if cur else "#201D1A"),
                command=lambda k=k: self._switch_page(k))
            b.cget("font").configure(weight="bold")
            b.pack(padx=20, pady=2, anchor="w")
        root_sel = self.page == "root"
        self.del_page_btn = customtkinter.CTkButton(
            right, text=t("Delete"), width=MANAGE_LIST_BTN_W,
            command=self._delete_page,
            state=("disabled" if root_sel else "normal"),
            fg_color=("#F6F4F0" if root_sel else "white"))
        self.del_page_btn.pack(padx=20, pady=(10, 4), anchor="w")

    # ------------------------------------------------------------- #

    def _switch_page(self, key):
        self.page = key
        self._build()

    def _sync_state(self):
        has = bool(self._sel)
        # Disabled: bg-coloured fill + the theme's muted disabled text.
        for b in self._op_btns.values():
            if has:
                b.configure(state="normal", fg_color="white")
            else:
                b.configure(state="disabled", fg_color="#F6F4F0")
        if self._mode == "select":
            self.info_lbl.configure(
                text=t("Select items and an operation"))
        else:
            self.info_lbl.configure(
                text=t("Click the swap / insert destination"))

    def _toggle_multi(self):
        self._multi = bool(self.multi_var.get())
        # Switching mode clears any pending selection AND cancels a pending
        # swap/insert destination pick — otherwise the popup stays in
        # destination mode with an empty selection and the next cell click
        # runs a no-op operation instead of selecting.
        self._mode = "select"
        self._sel = []
        self._anchor = None
        self._repaint_sel()
        self._sync_state()

    def _paint_cell(self, i):
        b = self._cell_btns.get(i)
        if b is not None:
            b.configure(fg_color=(self._CELL_SEL if i in self._sel
                                  else self._CELL_FG))

    def _repaint_sel(self):
        for i in self._cell_btns:
            self._paint_cell(i)

    def _cell_click(self, i):
        if self._mode in ("swap", "insert"):
            mode = self._mode
            self._mode = "select"
            if mode == "swap":
                self._do_swap(i)
            else:
                self._do_insert(i)
            return
        if not self._multi:
            # Single select.
            self._sel = [i]
            self._anchor = i
        elif self._anchor is None:
            # First click sets the range anchor.
            self._anchor = i
            self._sel = [i]
        else:
            # Excel-shift behaviour: select the whole range from the anchor
            # (anchor stays put for the next click).
            lo, hi = sorted((self._anchor, i))
            self._sel = list(range(lo, hi + 1))
        self._repaint_sel()
        self._sync_state()

    # --- operations ------------------------------------------------ #

    def _op_deselect(self):
        self._mode = "select"       # also cancels a pending swap/insert
        self._sel = []
        self._anchor = None
        self._repaint_sel()
        self._sync_state()

    def _op_swap(self):
        if not self._sel:
            return
        self._mode = "swap"
        self._sync_state()

    def _op_insert(self):
        if not self._sel:
            return
        self._mode = "insert"
        self._sync_state()

    def _do_swap(self, target):
        """Selected items (row-major) swap pairwise with the cells at
        `target` and rightward."""
        lst = self._linear()
        sel = sorted(self._sel)
        if target + len(sel) > len(lst):
            from src.gui.popups._util import ConfirmDialog
            ConfirmDialog(self.master).open(
                t("Not enough space"), cancel=False)
            self._sync_state()      # mode is back to select; refresh label
            return
        for n, i in enumerate(sel):
            j = target + n
            lst[i], lst[j] = lst[j], lst[i]
        self._write_linear(lst)
        self._build()
        if self.on_done:
            self.on_done()

    def _do_insert(self, target):
        """Move the selected items to `target`, shifting the destination
        and everything to its right further right by the selection count.
        Their old cells become blanks. Errors if items would fall off the
        end of the grid."""
        rows, cols = self._dims()
        total = rows * cols
        lst = self._linear()
        sel = sorted(set(self._sel))
        moving = [lst[i] for i in sel]
        base = [None if i in set(sel) else v for i, v in enumerate(lst)]
        result = base[:target] + moving + base[target:]
        if any(v for v in result[total:]):
            from src.gui.popups._util import ConfirmDialog
            ConfirmDialog(self.master).open(
                t("Not enough space"), cancel=False)
            self._sync_state()      # mode is back to select; refresh label
            return
        self._write_linear(result[:total])
        self._build()
        if self.on_done:
            self.on_done()

    def _op_blank(self):
        """Insert one blank per selected item, CONSECUTIVELY, at the first
        selected cell — shifting that cell and everything after it right.
        Errors when items would fall off the end of the grid."""
        if not self._sel:
            return
        rows, cols = self._dims()
        total = rows * cols
        lst = self._linear()
        p = min(self._sel)
        n = len(set(self._sel))
        new = lst[:p] + [None] * n + lst[p:]
        if any(v for v in new[total:]):
            from src.gui.popups._util import ConfirmDialog
            ConfirmDialog(self.master).open(
                t("Not enough space"), cancel=False)
            return
        self._write_linear(new[:total])
        self._build()
        if self.on_done:
            self.on_done()

    def _op_delete(self):
        if not self._sel:
            return
        from src.gui.popups._util import ConfirmDialog
        ConfirmDialog(self.master).open(
            t("Delete selected items?"), on_ok=self._do_delete)

    def _do_delete(self):
        lst = self._linear()
        for i in self._sel:
            if 0 <= i < len(lst):
                lst[i] = None
        self._write_linear(lst)
        self._build()
        if self.on_done:
            self.on_done()

    def _delete_page(self):
        if self.page == "root":
            return
        from src.gui.popups._util import ConfirmDialog
        ConfirmDialog(self.master).open(
            t("Delete this sub-launcher?"), on_ok=self._do_delete_page)

    def _do_delete_page(self):
        key = self.page
        e = self._data()
        pages = e["launcher"]["pages"]
        pages.pop(key, None)
        # Drop launcher items that pointed at the deleted page (they would
        # be dead buttons otherwise).
        for p in pages.values():
            items = p.get("items", {}) or {}
            for ck in list(items):
                lst = _norm(items[ck])
                lst = [x for x in lst
                       if not (x.get("type") == "sublauncher"
                               and x.get("target") == key)]
                if lst:
                    items[ck] = lst if len(lst) > 1 else lst[0]
                else:
                    items.pop(ck, None)
        ConfigManager().write_extensions(e)
        self.page = "root"
        self._build()
        if self.on_done:
            self.on_done()

    def close(self):
        h = getattr(self, "_host", None)
        if h is not None and self._win is not None:
            h.pop(self._win)
        self._win = None
