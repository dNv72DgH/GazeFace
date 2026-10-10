# Copyright 2023 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# ...
"""In-window panel stack — the replacement for popup Toplevels.

Instead of opening a separate window, an options screen pushes a panel
onto this host. The host covers the main content area, shows a back
header + scrollable 2-column-friendly body, and pops back to the previous
screen (or the underlying page) on Back. Nested dialogs push further
panels. NO Toplevel popups.
"""
import customtkinter

from src.gui.frames.safe_disposable_frame import style_scrollbar
from src.gui.popups._util import POPUP_BG
from src.i18n import t

_HOST = None
# The launcher's own options WINDOW, while it is open. Dialogs nested
# inside it have to land in it rather than behind it in the main window,
# and they reach the host through panel_host() with no argument, so the
# routing lives here.
_OVERRIDE = None


def _active_toplevel():
    """The toplevel the user is working in right now, or None.

    The widget under the pointer first: every panel is opened by a click,
    and the thing just clicked is under the pointer (for a face-driven
    pointer as much as a mouse). Keyboard focus second, for the rare panel
    opened without one.
    """
    root = _HOST.winfo_toplevel() if _HOST is not None else None
    if root is None:
        return None
    try:
        w = root.winfo_containing(*root.winfo_pointerxy())
        if w is not None:
            return w.winfo_toplevel()
    except Exception:
        pass
    try:
        w = root.focus_get()
        if w is not None:
            return w.winfo_toplevel()
    except Exception:
        # focus_get() raises for some CTk internals (combobox popdowns).
        pass
    return None


def panel_host():
    """The PanelHost that a panel being opened now belongs in.

    The launcher window used to take EVERY push while it was open. So
    with it open, opening anything at all from the main window - gesture
    options, the clipboard settings - put that menu into the launcher
    window, and its Back button then walked from the launcher into menus
    that had nothing to do with it. The window now only receives panels
    opened from inside it; everything else goes to the main window as if
    the launcher window did not exist.
    """
    ov = _OVERRIDE
    if ov is None:
        return _HOST
    try:
        top = _active_toplevel()
        if top is not None and str(top) == str(ov.window):
            return ov
    except Exception:
        pass
    return _HOST


def set_panel_host(host):
    global _HOST
    _HOST = host


def set_host_override(host):
    """Route pushes to `host` (None restores the main-window host)."""
    global _OVERRIDE
    _OVERRIDE = host


class PanelHost(customtkinter.CTkFrame):

    def __init__(self, master, **kwargs):
        kwargs.setdefault("fg_color", POPUP_BG)
        super().__init__(master, **kwargs)
        self._stack = []        # [{frame, title, on_back}]
        # Called with True when the host becomes visible, False when it
        # hides — MainGui uses it to hide the underlying page (a sibling
        # CTkScrollableFrame in the same grid cell renders OVER the panel
        # body otherwise).
        self.on_visible = None

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = customtkinter.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        self._back_btn = customtkinter.CTkButton(
            header, text="◀ " + t("Back"), width=90, command=self._on_back)
        self._back_btn.pack(side="left", padx=(0, 12))
        self._title_lbl = customtkinter.CTkLabel(header, text="")
        self._title_lbl.cget("font").configure(size=20, weight="bold")
        self._title_lbl.pack(side="left")

        self._holder = customtkinter.CTkFrame(self, fg_color="transparent")
        self._holder.grid(row=1, column=0, sticky="nsew")
        self._holder.grid_rowconfigure(0, weight=1)
        self._holder.grid_columnconfigure(0, weight=1)

    # ---------------------------------------------------------------- #

    def push(self, title, on_close=None, on_back=None, can_back=None):
        """Open a new panel; returns its scrollable body frame to fill.
        `on_close` (optional) runs just BEFORE the panel is destroyed.

        `on_back` (optional) lets a panel that navigates INSIDE itself
        take the Back button first. It returns True if it consumed the
        press (it stepped back one internal level) and False if there is
        nothing left to unwind, in which case the panel is popped as
        usual. Without this, a panel that rebuilds itself in place — the
        launcher's sub-launcher drill-down being the case that showed it —
        has depth the host knows nothing about, so one Back from three
        levels down threw the user all the way out to the page behind.

        `can_back` (optional) answers "is there somewhere INSIDE this
        panel to go back to?" without doing it. Only the launcher window
        asks it, to decide whether to show Back at all.

        Structure: a plain CTkFrame `outer` holds a CTkScrollableFrame
        `body`. The outer-frame layer is REQUIRED — a CTkScrollableFrame
        placed directly as a sibling of the page's CTkScrollableFrame
        renders blank (CTk scrollable-frame conflict); nesting it inside a
        plain frame avoids that."""
        if self._stack:
            self._stack[-1]["outer"].grid_remove()
        outer = customtkinter.CTkFrame(self._holder, fg_color=POPUP_BG)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.grid_rowconfigure(0, weight=1)
        outer.grid_columnconfigure(0, weight=1)
        body = customtkinter.CTkScrollableFrame(outer, fg_color=POPUP_BG)
        style_scrollbar(body)
        body.grid(row=0, column=0, sticky="nsew")
        self._stack.append({"outer": outer, "body": body, "title": title,
                            "on_close": on_close, "on_back": on_back,
                            "can_back": can_back})
        self._title_lbl.configure(text=title or "")
        self._show()
        self.refresh_back()
        return body

    def refresh_back(self):
        """Re-evaluate whether Back is offered. The main-window host always
        offers it (Back returns to the page underneath)."""
        pass

    def set_title(self, body, title):
        """Retitle a panel that navigated inside itself, so the header
        says where the user actually is."""
        for e in self._stack:
            if e["body"] is body:
                e["title"] = title
                if self._stack[-1] is e:
                    self._title_lbl.configure(text=title or "")
                return

    def pop(self, body=None):
        """Close the top panel (or the one whose body is `body`, if it is
        on top). Returns to the previous panel or the underlying page."""
        if not self._stack:
            return
        if body is not None and self._stack[-1]["body"] is not body:
            return  # not the active panel; ignore stale close()
        top = self._stack.pop()
        cb = top.get("on_close")
        if cb:
            try:
                cb()
            except Exception:
                pass
        try:
            top["outer"].destroy()
        except Exception:
            pass
        if self._stack:
            self._stack[-1]["outer"].grid()
            self._title_lbl.configure(text=self._stack[-1]["title"] or "")
            self.refresh_back()
        else:
            self._hide()

    def is_top(self, body):
        return bool(self._stack) and self._stack[-1]["body"] is body

    def clear(self):
        """Dismiss all panels (e.g. when the user switches main pages)."""
        while self._stack:
            self.pop()

    def _on_back(self):
        # Let the top panel unwind its own internal depth first.
        if self._stack:
            cb = self._stack[-1].get("on_back")
            if cb:
                try:
                    if cb():
                        return
                except Exception:
                    pass
        self.pop()

    # ---------------------------------------------------------------- #

    def _show(self):
        # Cover the content area (row 0, column 1 — same cell the wizard
        # pages live in) and float above whatever page is there.
        self.grid(row=0, column=1, rowspan=2, padx=5, pady=5, sticky="nsew")
        self.lift()
        if self.on_visible is not None:
            try:
                self.on_visible(True)
            except Exception:
                pass

    def _hide(self):
        self.grid_remove()
        if self.on_visible is not None:
            try:
                self.on_visible(False)
            except Exception:
                pass


class WindowPanelHost(PanelHost):
    """A PanelHost that lives in its own top-level window.

    Same push/pop/set_title contract as the in-window host, so every panel
    and nested dialog works here unchanged — that is the whole point of
    reusing the class rather than writing a second options UI.

    It exists so the launcher can be edited while another page stays
    visible: the normal host covers the content area, which makes
    "compare this against the Mouse page" impossible.
    """

    # The main window's ground. Its content sits on it as a white rounded
    # card inset by 5 px (see PanelHost._show); this window used to be
    # white edge to edge, which is why it did not look like the same app.
    GROUND = "#F6F4F0"

    def __init__(self, master, title, on_closed=None, size="980x760"):
        self.window = customtkinter.CTkToplevel(master, fg_color=self.GROUND)
        self.window.title(title)
        self.window.geometry(size)
        self.window.minsize(720, 520)
        try:
            from src.gui import app_icon
            self.window.iconbitmap(app_icon.icon_path())
        except Exception:
            pass
        super().__init__(self.window)
        self._on_closed = on_closed
        self.pack(fill="both", expand=True, padx=5, pady=5)
        # Closing this window must NEVER reach the main window's
        # WM_DELETE_WINDOW handler, which asks whether to quit the whole
        # app. Closing an options window is not quitting, so it just
        # closes — no confirmation.
        self.window.wm_protocol("WM_DELETE_WINDOW", self.close_window)
        try:
            self.window.after(120, self.window.lift)
        except Exception:
            pass

    # The window IS the host, so there is nothing to grid in or out.
    def _show(self):
        pass

    def refresh_back(self):
        """Offer Back only when it leads somewhere inside the launcher.

        At the launcher's top level there is nothing behind it in this
        window, and a Back button there read as "go to the previous menu"
        - which is exactly how this window used to wander off into menus
        that are not the launcher. The title bar's close box closes it.
        """
        show = len(self._stack) > 1
        if not show and self._stack:
            fn = self._stack[-1].get("can_back")
            if fn:
                try:
                    show = bool(fn())
                except Exception:
                    show = False
        try:
            if show:
                if not self._back_btn.winfo_manager():
                    self._back_btn.pack(side="left", padx=(0, 12),
                                        before=self._title_lbl)
            else:
                self._back_btn.pack_forget()
        except Exception:
            pass

    def raise_window(self):
        try:
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
        except Exception:
            pass

    def _hide(self):
        self.close_window()

    def close_window(self):
        """Tear down every panel, then the window itself."""
        # Stop routing pushes here FIRST, and do it from the host rather
        # than relying on whoever opened it: a leaked override sends every
        # later panel into a destroyed window, and the app then looks
        # frozen because nothing can be opened at all.
        if _OVERRIDE is self:
            set_host_override(None)
        cb, self._on_closed = getattr(self, "_on_closed", None), None
        try:
            while self._stack:
                top = self._stack.pop()
                fn = top.get("on_close")
                if fn:
                    try:
                        fn()
                    except Exception:
                        pass
                try:
                    top["outer"].destroy()
                except Exception:
                    pass
        finally:
            try:
                self.window.destroy()
            except Exception:
                pass
            if cb:
                try:
                    cb()
                except Exception:
                    pass
