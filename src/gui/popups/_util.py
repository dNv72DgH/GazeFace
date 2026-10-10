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
"""Shared helpers for popup window styling and geometry."""

import logging

# App background (FacePilot design: bg #F6F4F0). Set on panels so they
# don't visually pop out as a different surface.
# White background for every options panel / sub-panel in the right pane
# (was the warm off-white #F6F4F0).
POPUP_BG = "#FFFFFF"


# CTkImage leaks: customtkinter keeps every CTkImage alive in a global
# ScalingTracker, so re-creating them each time a picker opens climbed the
# live Tcl-image count to 300+ (the "gets heavier over time" report). Cache
# by (abspath, size) and reuse — bounds the count to the unique icon set.
_CTK_IMAGE_CACHE = {}


_CTK_IMAGE_FAILED = set()
_logger = logging.getLogger("GuiIcons")


def cached_ctk_image(path, size):
    """A CTkImage for `path` at `size`, reused across opens. None on error."""
    import os
    if not path:
        return None
    key = (os.path.abspath(path), tuple(size))
    img = _CTK_IMAGE_CACHE.get(key)
    if img is None:
        try:
            import customtkinter
            from PIL import Image
            img = customtkinter.CTkImage(Image.open(path), size=tuple(size))
        except Exception as e:
            # Every icon in the settings UI comes through here, and this
            # used to swallow the reason whole: a missing file, an
            # unreadable one and a corrupt PNG all came back as a blank
            # tile with nothing in the log. Report it once per path.
            if path not in _CTK_IMAGE_FAILED:
                _CTK_IMAGE_FAILED.add(path)
                _logger.warning("icon load failed: %s (exists=%s): %s",
                                path, os.path.exists(path), e)
            return None
        _CTK_IMAGE_CACHE[key] = img
    return img


class GestureMapPicker:
    """Master-detail gesture-map picker for the in-tool gesture maps
    (launcher / clipboard / task switcher).

    A row of FUNCTION buttons (confirm / back / exit …) sits on top; clicking
    one shows checkboxes of the available gestures for THAT function. A
    gesture already assigned to another function is shown disabled (mutual
    exclusion) — checking it for the active function moves it off the old one.

    `gmap` is the gesture-keyed config {gesture: function}. `save_cb` is
    called with the updated dict on every change."""

    def __init__(self, parent, actions, gmap, gestures, save_cb,
                 label_fn=None):
        import customtkinter
        self.actions = list(actions)
        self.gmap = dict(gmap or {})
        self.gestures = list(gestures)
        self.save_cb = save_cb
        self.label_fn = label_fn or (lambda x: x)
        self._active = self.actions[0] if self.actions else None
        self._btns = {}

        self.container = customtkinter.CTkFrame(parent, fg_color="transparent")
        self.container.pack(fill="x", anchor="w")
        btnrow = customtkinter.CTkFrame(self.container, fg_color="transparent")
        btnrow.pack(fill="x", anchor="w", padx=20, pady=(2, 6))
        # Wrap the function buttons over multiple rows (3 per row) so a long
        # action list doesn't run off the right edge of the panel.
        for i, a in enumerate(self.actions):
            b = customtkinter.CTkButton(
                btnrow, text=self.label_fn(a), width=82, height=26,
                command=lambda a=a: self._select(a))
            b.cget("font").configure(size=11, weight="bold")
            b.grid(row=i // 3, column=i % 3, padx=(0, 6), pady=2, sticky="w")
            self._btns[a] = b
        self.cbarea = customtkinter.CTkFrame(
            self.container, fg_color="transparent")
        self.cbarea.pack(fill="x", anchor="w", padx=20)
        self._render()

    def _select(self, a):
        self._active = a
        self._render()

    def _render(self):
        import customtkinter
        import tkinter as tk
        # Active function: accent-filled (FacePilot primary). Inactive:
        # plain surface button (theme border keeps it readable).
        for a, b in self._btns.items():
            if a == self._active:
                b.configure(fg_color="#D9622B", text_color="white")
            else:
                b.configure(fg_color="white", text_color="#201D1A")
        for w in self.cbarea.winfo_children():
            w.destroy()
        if not self._active:
            return
        for g in self.gestures:
            owner = self.gmap.get(g)
            other = owner is not None and owner != self._active
            txt = self.label_fn(g)
            if other:
                txt += "  (%s)" % self.label_fn(owner)
            bv = tk.BooleanVar(value=(owner == self._active))
            cb = customtkinter.CTkCheckBox(
                self.cbarea, text=txt, variable=bv, onvalue=True,
                offvalue=False,
                command=lambda g=g, bv=bv: self._toggle(g, bv))
            if other:
                cb.configure(state="disabled")     # mutual exclusion
            cb.pack(anchor="w", pady=1)

    def _toggle(self, g, bv):
        if bv.get():
            self.gmap[g] = self._active            # reassign (exclusive)
        elif self.gmap.get(g) == self._active:
            self.gmap.pop(g, None)
        try:
            self.save_cb(dict(self.gmap))
        except Exception:
            pass
        self._render()


class ConfirmDialog:
    """Non-modal confirm / notice panel (panel_host based).

    A native modal messagebox blocks the tk main loop — and with it the
    whole face-gesture pipeline, which can soft-lock a face-control-only
    user out of dismissing it. This panel stays on the normal event loop.
    `cancel=False` turns it into a plain OK notice."""

    def __init__(self, master):
        self.master = master
        self._win = None

    def open(self, message, on_ok=None, cancel=True):
        import customtkinter
        from src.i18n import t
        from src.gui.panel_host import panel_host
        host = panel_host()
        if host is None:
            return
        self._host = host
        self._win = host.push(t("Confirmation"))
        body = self._win
        customtkinter.CTkLabel(
            body, text=message, wraplength=460,
            justify="left").pack(padx=24, pady=(28, 12), anchor="w")
        brow = customtkinter.CTkFrame(body, fg_color="transparent")
        brow.pack(padx=24, pady=10, anchor="w")

        def ok():
            self.close()
            if on_ok:
                try:
                    on_ok()
                except Exception:
                    pass
        customtkinter.CTkButton(brow, text=t("OK"), width=90,
                                command=ok).pack(side="left", padx=(0, 8))
        if cancel:
            customtkinter.CTkButton(brow, text=t("Cancel"), width=90,
                                    command=self.close).pack(side="left")

    def close(self):
        h = getattr(self, "_host", None)
        if h is not None and self._win is not None:
            h.pop(self._win)
        self._win = None


def apply_popup_geometry(top, master_widget,
                         fraction: float = 1.0,
                         resizable: bool = False,
                         min_w: int = 480,
                         min_h: int = 320) -> None:
    """Size `top` (a Toplevel) at `fraction` of the master toplevel and
    centre it over the master. Also paints the background and toggles
    resizability.

    `fraction` defaults to 1.0 so popups match the main window's size
    exactly (and land directly over it), per the unified-size design.

    Robust to the case where the master hasn't been rendered yet — falls
    back to `winfo_reqwidth` and finally to a hard-coded default.
    """
    main = master_widget.winfo_toplevel()
    # Make sure geometry info is current.
    main.update_idletasks()

    main_w = main.winfo_width() or main.winfo_reqwidth() or 1024
    main_h = main.winfo_height() or main.winfo_reqheight() or 658
    main_x = main.winfo_rootx()
    main_y = main.winfo_rooty()

    pop_w = max(min_w, int(main_w * fraction))
    pop_h = max(min_h, int(main_h * fraction))
    pop_x = main_x + (main_w - pop_w) // 2
    pop_y = main_y + (main_h - pop_h) // 2

    top.geometry(f"{pop_w}x{pop_h}+{pop_x}+{pop_y}")
    top.configure(bg=POPUP_BG)
    top.minsize(min_w, min_h)
    top.resizable(resizable, resizable)
