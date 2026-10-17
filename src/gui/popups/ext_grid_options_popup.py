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
"""Shared options popup for the simpler gaze-grid apps (clipboard
history, task switcher): grid size + the app's local gesture map. The
clipboard app adds record/cap/clear controls."""

import logging
import tkinter as tk

import customtkinter

from src.config_manager import ConfigManager
from src.gui.popups._util import apply_popup_geometry, POPUP_BG
from src.gui.help_icon import section_with_help
from src.i18n import t

logger = logging.getLogger("ExtGridOptions")

GRID_MIN, GRID_MAX = 3, 9


class ExtGridOptionsPopup:

    def __init__(self, master, cfg_key, title, actions, clipboard=False):
        self.master = master
        self.cfg_key = cfg_key          # "clipboard" / "task_switcher"
        self.title = title
        self.actions = actions          # e.g. ["confirm","back","forward","exit"]
        self.clipboard = clipboard
        self._window = None
        self.top = None

    def _data(self):
        e = ConfigManager().read_extensions()
        e.setdefault(self.cfg_key, {})
        return e

    def _bound_gestures(self):
        b = ConfigManager().mouse_bindings | ConfigManager().keyboard_bindings
        return sorted(b.keys())

    def open(self):
        from src.gui.panel_host import panel_host
        host = panel_host()
        if host is None:
            return
        self._host = host
        self.top = host.push(f"{self.title} {t('Options')}")
        self._build()

    def _build(self):
        for w in self.top.winfo_children():
            w.destroy()
        e = self._data()
        cfg = e[self.cfg_key]

        # (No in-body title — the panel header already carries it.)

        # Two columns so most settings fit without scrolling: left holds the
        # grid size / transparency (and clipboard controls); right holds the
        # (tall) gesture map.
        cols = customtkinter.CTkFrame(self.top, fg_color="transparent")
        cols.pack(fill="both", expand=True)
        left = customtkinter.CTkFrame(cols, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nw", padx=(0, 8))
        right = customtkinter.CTkFrame(cols, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nw", padx=(8, 0))

        # --- clipboard-specific: record / cap / clear ---
        if self.clipboard:
            self.rec_var = tk.BooleanVar(value=bool(cfg.get("enabled")))
            customtkinter.CTkCheckBox(
                left, text=t("Record clipboard (start monitoring)"),
                variable=self.rec_var, onvalue=True, offvalue=False,
                command=self._save_clip).pack(padx=20, pady=(2, 6),
                                              anchor="w")
            self.clr_start_var = tk.BooleanVar(
                value=bool(cfg.get("clear_on_start")))
            customtkinter.CTkCheckBox(
                left, text=t("Clear history on app start"),
                variable=self.clr_start_var, onvalue=True, offvalue=False,
                command=self._save_clip).pack(padx=20, pady=(0, 6),
                                              anchor="w")
            mrow = customtkinter.CTkFrame(left, fg_color="transparent")
            mrow.pack(padx=20, anchor="w", fill="x", pady=2)
            customtkinter.CTkLabel(
                mrow, text=t("Max entries (0 = unlimited)")).pack(
                    side="left", padx=(0, 6))
            self.max_var = tk.StringVar(value=str(int(cfg.get("max", 100))))
            customtkinter.CTkEntry(mrow, textvariable=self.max_var,
                                   width=70).pack(side="left", padx=(0, 8))
            customtkinter.CTkButton(
                mrow, text=t("Apply"), width=70,
                command=self._save_clip).pack(side="left")
            customtkinter.CTkButton(
                left, text=t("Clear now"), width=120,
                command=self._clear_now).pack(
                    padx=20, pady=(4, 8), anchor="w")

        # --- grid size ---
        srow = customtkinter.CTkFrame(left, fg_color="transparent")
        srow.pack(padx=20, anchor="w", fill="x", pady=(8, 4))
        customtkinter.CTkLabel(srow, text=t("Rows")).pack(
            side="left", padx=(0, 4))
        self.rows_var = tk.StringVar(value=str(int(cfg.get("rows", 3))))
        customtkinter.CTkEntry(srow, textvariable=self.rows_var,
                               width=50).pack(side="left", padx=(0, 12))
        customtkinter.CTkLabel(srow, text=t("Columns")).pack(
            side="left", padx=(0, 4))
        self.cols_var = tk.StringVar(value=str(int(cfg.get("cols", 3))))
        customtkinter.CTkEntry(srow, textvariable=self.cols_var,
                               width=50).pack(side="left", padx=(0, 12))
        customtkinter.CTkButton(srow, text=t("Apply"), width=70,
                                command=self._save_size).pack(side="left")

        # --- clipboard: manage / pin list (above transparency) ---
        # One button — the manage screen has its own history/pinned toggle.
        if self.clipboard:
            crow2 = customtkinter.CTkFrame(left, fg_color="transparent")
            crow2.pack(padx=20, anchor="w", fill="x", pady=(10, 2))
            customtkinter.CTkButton(
                crow2, text=t("Manage / pin"), width=180,
                command=lambda: self._open_clip_list("history")).pack(
                    side="left")

        # (Background transparency moved into the shared Colour settings
        # popup — see the button at the bottom of this column.)

        # Ignore blinks (ms): a newly gazed cell must stay stable this long
        # before it becomes the selection, so a blink's transient jump
        # reverts before it commits (0 = off). Same idea as the gesture
        # "ignore involuntary movements" option.
        brow = customtkinter.CTkFrame(left, fg_color="transparent")
        brow.pack(padx=20, anchor="w", fill="x", pady=(6, 4))
        customtkinter.CTkLabel(
            brow, text=t("Ignore blinks (ms)")).pack(side="left", padx=(0, 8))
        self.blink_var = tk.IntVar(
            value=int(cfg.get("ignore_blink_ms", 200) or 0))
        customtkinter.CTkSlider(
            brow, from_=0, to=500, number_of_steps=25, width=200,
            variable=self.blink_var,
            command=lambda _v: self._save_blink()).pack(
                side="left", padx=(0, 6))
        customtkinter.CTkLabel(brow, textvariable=self.blink_var,
                               width=44).pack(side="left")

        # Dwell-to-confirm: keep gazing at the SELECTED cell this long to
        # execute it (0 = execution by gesture only).
        crow = customtkinter.CTkFrame(left, fg_color="transparent")
        crow.pack(padx=20, anchor="w", fill="x", pady=(6, 4))
        customtkinter.CTkLabel(
            crow, text=t("Dwell confirm (ms)")).pack(side="left", padx=(0, 8))
        self.confirm_var = tk.IntVar(value=int(cfg.get("confirm_ms", 0) or 0))
        customtkinter.CTkSlider(
            crow, from_=0, to=3000, number_of_steps=30, width=200,
            variable=self.confirm_var,
            command=lambda _v: self._save_confirm()).pack(
                side="left", padx=(0, 6))
        customtkinter.CTkLabel(crow, textvariable=self.confirm_var,
                               width=44).pack(side="left")

        # ◀ ▶ page cells on/off.
        self.pgbtn_var = tk.BooleanVar(value=bool(cfg.get("page_btns", True)))
        customtkinter.CTkCheckBox(
            left, text=t("Show page buttons"), variable=self.pgbtn_var,
            onvalue=True, offvalue=False,
            command=self._save_pgbtn).pack(padx=20, pady=(6, 4), anchor="w")

        # ★ pinned-list cell (first cell) on/off.
        if self.clipboard:
            self.pincell_var = tk.BooleanVar(
                value=bool(cfg.get("show_pin_cell", True)))
            customtkinter.CTkCheckBox(
                left, text=t("Show pinned-list cell"),
                variable=self.pincell_var, onvalue=True, offvalue=False,
                command=self._save_pincell).pack(padx=20, pady=(0, 4),
                                                 anchor="w")

        # Task switcher only: rules for windows to leave out of the grid.
        if self.cfg_key == "task_switcher":
            customtkinter.CTkButton(
                left, text=t("Hide tasks"), width=180,
                command=self._open_hidden_tasks).pack(
                    padx=20, pady=(10, 2), anchor="w")

        # Shared colour + transparency settings (launcher / clipboard /
        # task switcher all share one set). Bottom of the left column.
        customtkinter.CTkButton(
            left, text=t("Color settings"), width=180,
            command=self._open_color_settings).pack(
                padx=20, pady=(12, 4), anchor="w")

        # --- gesture map (right column) ---
        # Topic per tool: the heading is the same in both, the help
        # text names the tool it belongs to.
        section_with_help(right, t("Gestures"),
                          "Gestures:%s" % self.cfg_key, pady=(14, 2))
        # Master-detail: pick a function (slot) then check the gestures for
        # it. A gesture used by another function is disabled (mutual
        # exclusion). Config is gesture-keyed ({gesture: slot}).
        from src.gui.popups._util import GestureMapPicker
        gestures = self._gesture_choices()
        gmap = cfg.get("gestures", {}) or {}
        acts = list(self.actions)
        # Prune gestures bound to slots this grid no longer exposes (e.g.
        # a clipboard config that still holds a "back"/"forward" entry from
        # before paging was removed). Left in place they keep firing at
        # runtime AND lock their row in the picker — the owner slot has no
        # button, so mutual exclusion disables the checkbox and the user
        # can never unassign it. Clean them on open.
        stale = [g for g, slot in gmap.items() if slot not in acts]
        if stale:
            for g in stale:
                gmap.pop(g, None)
            self._save_gmap(gmap)
        GestureMapPicker(
            right, acts, gmap, gestures, save_cb=self._save_gmap,
            label_fn=lambda x: (t(x.capitalize()) if x in acts else t(x)))

    def _gesture_choices(self):
        # Only gestures actually BOUND in the main menu, from BOTH the
        # mouse and keyboard stores (see launcher popup).
        import src.shape_list as sl
        bound = list(ConfigManager().mouse_bindings.keys()) + \
            list(ConfigManager().keyboard_bindings.keys())
        seen = []
        for g in bound:
            if g in sl.blendshape_names and g not in seen:
                seen.append(g)
        return seen

    def _save_size(self):
        try:
            r = max(GRID_MIN, min(GRID_MAX, int(self.rows_var.get())))
            c = max(GRID_MIN, min(GRID_MAX, int(self.cols_var.get())))
        except ValueError:
            return
        e = self._data()
        e[self.cfg_key]["rows"] = r
        e[self.cfg_key]["cols"] = c
        ConfigManager().write_extensions(e)

    def _save_alpha(self):
        # alpha_var is TRANSPARENCY %; store opacity = 1 - transparency.
        e = self._data()
        e[self.cfg_key]["alpha"] = max(
            0.1, min(1.0, 1.0 - self.alpha_var.get() / 100.0))
        ConfigManager().write_extensions(e)

    def _save_blink(self):
        e = self._data()
        e.setdefault(self.cfg_key, {})["ignore_blink_ms"] = int(
            self.blink_var.get())
        ConfigManager().write_extensions(e)

    def _save_confirm(self):
        e = self._data()
        e.setdefault(self.cfg_key, {})["confirm_ms"] = int(
            self.confirm_var.get())
        ConfigManager().write_extensions(e)

    def _save_pgbtn(self):
        e = self._data()
        e.setdefault(self.cfg_key, {})["page_btns"] = bool(
            self.pgbtn_var.get())
        ConfigManager().write_extensions(e)

    def _save_pincell(self):
        e = self._data()
        e.setdefault(self.cfg_key, {})["show_pin_cell"] = bool(
            self.pincell_var.get())
        ConfigManager().write_extensions(e)

    def _open_clip_list(self, mode):
        ClipListPopup(self.master, mode).open()

    def _open_color_settings(self):
        ColorSettingsPopup(self.master).open()

    def _open_hidden_tasks(self):
        from src.gui.popups.hidden_tasks_popup import HiddenTasksPopup
        HiddenTasksPopup(self.master).open()

    def _save_gmap(self, gmap):
        e = self._data()
        e.setdefault(self.cfg_key, {})["gestures"] = dict(gmap)
        ConfigManager().write_extensions(e)

    def _save_clip(self):
        e = self._data()
        cfg = e[self.cfg_key]
        cfg["enabled"] = bool(self.rec_var.get())
        cfg["clear_on_start"] = bool(self.clr_start_var.get())
        try:
            cfg["max"] = max(0, min(9999, int(self.max_var.get())))
        except ValueError:
            pass
        ConfigManager().write_extensions(e)
        # Start/stop the live monitor to match.
        try:
            from src.extensions.clipboard_history import ClipboardMonitor
            if cfg["enabled"]:
                ClipboardMonitor.instance().start()
            else:
                ClipboardMonitor.instance().stop()
        except Exception as e2:
            logger.error("monitor toggle failed: %s", e2)

    def _clear_now(self):
        from src.extensions import clip_store
        clip_store.clear_history()

    # (The hide-task rule editor lives in hidden_tasks_popup.py.)

    def close(self):
        h = getattr(self, "_host", None)
        if h is not None and self.top is not None:
            h.pop(self.top)
        self._window = None
        self.top = None


def _plain_color(c):
    """A colour CTk will take back through configure(). The app theme
    gives one-item lists (["white"]), which configure() rejects - it
    wants a string or a (light, dark) pair."""
    if isinstance(c, (list, tuple)) and len(c) == 1:
        return c[0]
    return c


class ClipListPopup:
    """A 3-column, multi-select list of clipboard entries with two views —
    the history and the pinned list — reachable from each other (the same
    ⇄ the runtime grid offers). History view: pin or delete the selected
    entries. Pinned view: unpin (back to the history) or delete (drop from
    both). Clicking a cell toggles its selection.

    Entries come from the clipboard database one page at a time, filtered
    by the search box, so a long history opens as fast as a short one.
    The selection is kept by text, so it survives paging and searching."""

    COLS = 3
    ROWS = 9                   # rows per page; sized to fit the panel
    ROW_H = 40                 # button height (+ 2 x 3 px padding per row)
    PAGE_SIZE = COLS * ROWS
    SEARCH_DELAY_MS = 250
    SEL_FILL = "#F3E5DC"       # selected: pale orange fill + orange label
    SEL_TEXT = "#D9622B"

    def __init__(self, master, mode="history"):
        self.master = master
        self.mode = mode if mode in ("history", "pinned") else "history"
        self._host = None
        self.top = None
        self._sel = set()          # selected entry texts
        self._buttons = {}         # text -> (button, default_fg, default_text)
        self._query = ""
        self._page = 0
        self._search_after = None
        self._list_frame = None
        self._pager = None
        self._pool = []
        self._empty = None
        self._default_colors = None

    def open(self):
        from src.gui.panel_host import panel_host
        host = panel_host()
        if host is None:
            return
        self._host = host
        self.top = host.push(f"{t('Clipboard')} {t('Manage')}")
        self._build()

    # --- data ---
    def _kind(self):
        from src.extensions import clip_store
        return (clip_store.PINNED if self.mode == "pinned"
                else clip_store.HISTORY)

    def _cap(self):
        e = ConfigManager().read_extensions() or {}
        try:
            return int((e.get("clipboard") or {}).get("max", 100))
        except (TypeError, ValueError):
            return 100

    # --- build ---
    def _build(self):
        for w in self.top.winfo_children():
            w.destroy()
        self._buttons = {}

        # View toggle — the two lists are reachable from each other. Tab
        # style: the ACTIVE view is orange with a bold white label, the
        # inactive one plain white. The search box sits to their right.
        trow = customtkinter.CTkFrame(self.top, fg_color="transparent")
        trow.pack(padx=20, anchor="w", pady=(16, 6))

        def _tab(text, mode):
            on = (self.mode == mode)
            b = customtkinter.CTkButton(
                trow, text=text, width=110,
                fg_color="#D9622B" if on else "#FFFFFF",
                text_color="white" if on else "#3C4043",
                hover_color="#B85222" if on else "#EDEDED",
                border_width=0 if on else 1, border_color="#C5C5C5",
                command=lambda: self._switch(mode))
            b.cget("font").configure(weight="bold")   # both tabs bold
            return b

        _tab(t("History"), "history").pack(side="left", padx=(0, 8))
        _tab(t("Pinned"), "pinned").pack(side="left", padx=(0, 16))
        self._search = customtkinter.CTkEntry(
            trow, width=240, placeholder_text=t("Search"))
        self._search.pack(side="left")
        if self._query:
            self._search.insert(0, self._query)
        self._search.bind("<KeyRelease>", self._on_search_key)

        # Operations on the selected entries.
        orow = customtkinter.CTkFrame(self.top, fg_color="transparent")
        orow.pack(padx=20, anchor="w", pady=(0, 8))
        if self.mode == "pinned":
            customtkinter.CTkButton(orow, text=t("Unpin"), width=120,
                                    command=self._unpin_selected).pack(
                                        side="left", padx=(0, 8))
        else:
            customtkinter.CTkButton(orow, text=t("Pin"), width=120,
                                    command=self._pin_selected).pack(
                                        side="left", padx=(0, 8))
        customtkinter.CTkButton(orow, text=t("Delete"), width=120,
                                command=self._delete_selected).pack(
                                    side="left", padx=(0, 8))
        customtkinter.CTkButton(orow, text=t("Clear selection"), width=140,
                                command=self._clear_sel).pack(side="left")

        # One page = ROWS rows, sized to fit the panel: no scrollbar, the
        # pager below moves between pages.
        self._list_frame = customtkinter.CTkFrame(
            self.top, fg_color="transparent")
        self._list_frame.pack(padx=16, pady=(0, 4), anchor="nw", fill="x")
        # Fixed, equal-width columns so a wide entry can never push the grid
        # past the panel edge (the "3rd column cut off" case). Buttons fill
        # their column and clip their (truncated, newline-flattened) text.
        for col in range(self.COLS):
            self._list_frame.grid_columnconfigure(col, weight=1,
                                                  uniform="clipcol")
        self._pool = []            # entry buttons, reused across pages
        self._empty = None

        prow = customtkinter.CTkFrame(self.top, fg_color="transparent")
        prow.pack(padx=20, anchor="w", pady=(0, 12))
        customtkinter.CTkButton(prow, text="◀", width=48,
                                command=lambda: self._flip(-1)).pack(
                                    side="left")
        self._pager = customtkinter.CTkLabel(prow, text="", width=160)
        self._pager.pack(side="left", padx=8)
        customtkinter.CTkButton(prow, text="▶", width=48,
                                command=lambda: self._flip(1)).pack(
                                    side="left")
        self._render()

    def _render(self):
        """Fill the list with the current page of the current view."""
        from src.extensions import clip_store
        if self._list_frame is None:
            return
        self._buttons = {}
        total = clip_store.count(self._kind(), self._query)
        npages = max(1, (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self._page = max(0, min(self._page, npages - 1))
        items = clip_store.items(self._kind(), self._query,
                                 limit=self.PAGE_SIZE,
                                 offset=self._page * self.PAGE_SIZE)
        self._pager.configure(text=t("%d / %d  (%d items)")
                              % (self._page + 1, npages, total))
        if self._empty is None:
            self._empty = customtkinter.CTkLabel(self._list_frame,
                                                 text=t("(empty)"))
        if items:
            self._empty.grid_remove()
        else:
            self._empty.grid(row=0, column=0, padx=8, pady=20, sticky="w")
        # Buttons are made once and relabelled after that: creating a
        # CTkButton costs ~10x relabelling one, so paging and searching
        # stay quick.
        for i, txt in enumerate(items):
            if i >= len(self._pool):
                b = customtkinter.CTkButton(self._list_frame, text="",
                                            width=100, height=self.ROW_H,
                                            anchor="w")
                self._pool.append(b)
                self._default_colors = (_plain_color(b.cget("fg_color")),
                                        _plain_color(b.cget("text_color")))
            b = self._pool[i]
            r, c = divmod(i, self.COLS)
            disp = txt.replace("\n", " ").replace("\r", " ")[:30] or "(空)"
            on = txt in self._sel
            b.configure(text=disp, command=lambda x=txt: self._toggle(x),
                        fg_color=self.SEL_FILL if on
                        else self._default_colors[0],
                        text_color=self.SEL_TEXT if on
                        else self._default_colors[1])
            b.grid(row=r, column=c, padx=3, pady=3, sticky="ew")
            self._buttons[txt] = (b,) + self._default_colors
        for b in self._pool[len(items):]:
            b.grid_remove()

    # --- search / paging ---
    def _on_search_key(self, _e=None):
        if self._search_after is not None:
            try:
                self.top.after_cancel(self._search_after)
            except Exception:
                pass
        self._search_after = self.top.after(self.SEARCH_DELAY_MS,
                                            self._apply_search)

    def _apply_search(self):
        self._search_after = None
        q = self._search.get().strip()
        if q != self._query:
            self._query = q
            self._page = 0
            self._render()

    def _flip(self, d):
        self._page += d
        self._render()

    # --- selection ---
    def _toggle(self, txt):
        b, dfg, dtext = self._buttons[txt]
        if txt in self._sel:
            self._sel.discard(txt)
            b.configure(fg_color=dfg, text_color=dtext)
        else:
            self._sel.add(txt)
            b.configure(fg_color=self.SEL_FILL, text_color=self.SEL_TEXT)

    def _clear_sel(self):
        for txt in list(self._sel):
            if txt in self._buttons:
                b, dfg, dtext = self._buttons[txt]
                b.configure(fg_color=dfg, text_color=dtext)
        self._sel.clear()

    def _switch(self, mode):
        if mode != self.mode:
            self.mode = mode
            self._sel.clear()
            self._page = 0
            self._build()

    # --- operations ---
    def _pin_selected(self):
        from src.extensions import clip_store
        if self._sel:
            clip_store.pin(list(self._sel))
            self._sel.clear()
            self._render()

    def _unpin_selected(self):
        from src.extensions import clip_store
        if self._sel:
            clip_store.unpin(list(self._sel), self._cap())
            self._sel.clear()
            self._render()

    def _delete_selected(self):
        from src.extensions import clip_store
        if self._sel:
            clip_store.delete(self._kind(), list(self._sel))
            self._sel.clear()
            self._render()

    def close(self):
        if self._search_after is not None and self.top is not None:
            try:
                self.top.after_cancel(self._search_after)
            except Exception:
                pass
        if self._host is not None and self.top is not None:
            self._host.pop(self.top)
        self.top = None


class ColorSettingsPopup:
    """Shared colour + transparency settings for ALL gaze grids (launcher /
    clipboard / task switcher) — one set, saved under gaze_colors. A swatch
    button per colour opens the OS colour chooser; a slider sets the shared
    backdrop transparency (moved here from the per-tool options)."""

    FIELDS = [("background", "Background"), ("cell", "Mount"),
              ("highlight", "Highlight")]

    def __init__(self, master):
        self.master = master
        self._host = None
        self.top = None
        self._swatch = {}

    def open(self):
        from src.gui.panel_host import panel_host
        host = panel_host()
        if host is None:
            return
        self._host = host
        self.top = host.push(t("Color settings"))
        self._build()

    def _build(self):
        for w in self.top.winfo_children():
            w.destroy()
        self._swatch = {}
        from src.gui.gaze_colors import gaze_colors
        c = gaze_colors()

        # (No in-body title — the panel header already carries it.)
        for i, (key, label) in enumerate(self.FIELDS):
            row = customtkinter.CTkFrame(self.top, fg_color="transparent")
            row.pack(padx=20, anchor="w", fill="x",
                     pady=((16, 4) if i == 0 else 4))
            customtkinter.CTkLabel(row, text=t(label), width=120,
                                   anchor="w").pack(side="left")
            sw = customtkinter.CTkButton(
                row, text=str(c[key]), width=150, fg_color=c[key],
                hover=False, text_color=self._contrast(c[key]),
                command=lambda k=key: self._pick(k))
            sw.pack(side="left")
            self._swatch[key] = sw

        # Backdrop transparency (moved here from the per-tool options).
        arow = customtkinter.CTkFrame(self.top, fg_color="transparent")
        arow.pack(padx=20, anchor="w", fill="x", pady=(14, 4))
        customtkinter.CTkLabel(
            arow, text=t("Background transparency (%)")).pack(
                side="left", padx=(0, 8))
        self.alpha_var = tk.IntVar(value=int(c.get("transparency", 80)))
        customtkinter.CTkSlider(
            arow, from_=10, to=100, number_of_steps=18, width=220,
            variable=self.alpha_var,
            command=lambda _v: self._save_alpha()).pack(
                side="left", padx=(0, 6))
        customtkinter.CTkLabel(arow, textvariable=self.alpha_var,
                               width=34).pack(side="left")

    def _pick(self, key):
        from tkinter import colorchooser
        from src.gui.gaze_colors import gaze_colors, save_gaze_colors
        cur = gaze_colors().get(key, "#000000")
        try:
            res = colorchooser.askcolor(color=cur, parent=self.top,
                                        title=t("Color settings"))
        except Exception:
            res = None
        if res and res[1]:
            hexv = str(res[1])
            save_gaze_colors({key: hexv})
            sw = self._swatch.get(key)
            if sw is not None:
                sw.configure(fg_color=hexv, text=hexv,
                             text_color=self._contrast(hexv))

    def _save_alpha(self):
        from src.gui.gaze_colors import save_gaze_colors
        save_gaze_colors({"transparency": int(self.alpha_var.get())})

    @staticmethod
    def _contrast(hexcol):
        try:
            h = str(hexcol).lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return "black" if (r * 299 + g * 587 + b * 114) / 1000 > 140 \
                else "white"
        except Exception:
            return "white"

    def close(self):
        if self._host is not None and self.top is not None:
            self._host.pop(self.top)
        self.top = None


class GazeMoveColorPopup(ColorSettingsPopup):
    """Colours for the gaze-MOVE overlay: the 8 compass arrows, the centre
    ring target, and the cursor-follow (pointer) ring shown during the
    glide. Separate palette from the grids — no transparency here."""

    FIELDS = [("arrow", "Arrow"), ("center", "Center ring"),
              ("pointer", "Pointer ring")]

    def _build(self):
        for w in self.top.winfo_children():
            w.destroy()
        self._swatch = {}
        from src.gui.gaze_colors import gaze_move_colors
        c = gaze_move_colors()
        # (No in-body title — the panel header already carries it.)
        for i, (key, label) in enumerate(self.FIELDS):
            row = customtkinter.CTkFrame(self.top, fg_color="transparent")
            row.pack(padx=20, anchor="w", fill="x",
                     pady=((16, 4) if i == 0 else 4))
            customtkinter.CTkLabel(row, text=t(label), width=120,
                                   anchor="w").pack(side="left")
            sw = customtkinter.CTkButton(
                row, text=str(c[key]), width=150, fg_color=c[key],
                hover=False, text_color=self._contrast(c[key]),
                command=lambda k=key: self._pick(k))
            sw.pack(side="left")
            self._swatch[key] = sw

    def _pick(self, key):
        from tkinter import colorchooser
        from src.gui.gaze_colors import (gaze_move_colors,
                                         save_gaze_move_colors)
        cur = gaze_move_colors().get(key, "#FFFFFF")
        try:
            res = colorchooser.askcolor(color=cur, parent=self.top,
                                        title=t("Color settings"))
        except Exception:
            res = None
        if res and res[1]:
            hexv = str(res[1])
            save_gaze_move_colors({key: hexv})
            sw = self._swatch.get(key)
            if sw is not None:
                sw.configure(fg_color=hexv, text=hexv,
                             text_color=self._contrast(hexv))
