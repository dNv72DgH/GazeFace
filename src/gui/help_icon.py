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
"""The "?" help marker that sits at the end of a setting's row.

Hovering shows it larger, at once (no in-between frames); clicking opens
a small popup with the help for that setting. The larger icon floats
over the row instead of resizing the marker, so the row's height never
changes - it may cover the text beside it while the pointer is on it.

The help TEXT is filled in later: every topic that has no entry in
HELP_TEXTS shows a "coming soon" line, so the markers can be placed and
the wording written afterwards without touching this file's users.

Written for a face-driven pointer: the marker is a 22 px target that
grows to 30 px, the popup closes on a click anywhere outside it or on
Escape, and nothing here depends on a precise double-click or a hover
that has to be held.
"""

import logging

import customtkinter

from src import paths
from src.i18n import t

logger = logging.getLogger("HelpIcon")

ICON = 22                 # resting size (px)
ICON_HOVER = 30           # size under the pointer

POPUP_W = 380
POPUP_BG = "#FFFFFF"
POPUP_BORDER = "#E7E2DB"
TEXT = "#201D1A"
# The body is set in the ordinary text colour at the app's normal size:
# 12 px in the muted grey read as small and faint.
TITLE_SIZE = 16
BODY_SIZE = 14

# topic -> (heading, body), both as source strings that go through t().
# A topic with no entry here shows the "coming soon" placeholder, so a
# marker can be placed before its wording exists.
HELP_TEXTS = {
    # ---- gesture options ----
    "Response curve": (
        "Response curve",
        "Change this when the slider moves unsteadily, or falls back too "
        "slowly."),
    "curve:slow_start": (
        "Slow start, fast end",
        "Weak gestures barely register; strong ones respond a lot."),
    "curve:fast_start": (
        "Fast start, slow end",
        "Even a weak gesture responds strongly from the start."),
    "Hold time (ms)": (
        "Hold time (ms)",
        "Holding the gesture for this long switches it on. Depending on "
        "the lighting, short, large noise-like inputs can repeat. Setting "
        "the hold time to about 200 ms suppresses them."),
    "Slider upper limit": (
        "Slider upper limit",
        "Set this when the slider barely moves and is hard to read."),
    "Slider lower limit": (
        "Slider lower limit",
        "Mainly used when the response curve is set to \"strong -> "
        "weak\"."),
    "Freeze pointer on gesture": (
        "Freeze pointer on gesture",
        "With some gestures head tracking reacts to the movement, so the "
        "pointer drifts while you make them. This holds it still, which "
        "makes it easier to click exactly where you aimed."),
    "Block simultaneous input": (
        "Block simultaneous input",
        "Set this when gestures other than the one you mean respond at "
        "the same time."),
    "Disable on strong reaction": (
        "Disable on strong reaction",
        "Keeps a gesture from misfiring when you laugh suddenly or "
        "react in some other way you didn't mean."),
    "Ignore blinks / involuntary movements": (
        "Ignore blinks / involuntary movements",
        "Keeps a blink or an involuntary movement from stopping the "
        "pointer by accident."),
    # ---- cursor page ----
    "Smooth pointer": (
        "Smooth pointer",
        "Larger values make the pointer move more smoothly, at the cost "
        "of more delay."),
    "Hold trigger delay(ms)": (
        "Hold trigger delay(ms)",
        "After the first click, keeping the gesture up for this long "
        "turns it into a held press (drag)."),
    # ---- Advanced ----
    "Chattering prevention": (
        "Chattering prevention",
        "Suppresses chattering, where a click or other input is entered "
        "several times in a row without you meaning it. Recommended on."),
    "Auto recovery": (
        "Auto recovery",
        "When an error occurs in the app, it tries to restart itself "
        "automatically so you are not left unable to operate it. If the "
        "settings are broken, it restarts with an earlier backup "
        "profile. Normally there is no need to change this setting."),
    # ---- Advanced: startup ----
    "At sign-in": (
        "At sign-in (starts on the sign-in screen etc.)",
        "Starts an ultra-light version just for signing in to Windows, "
        "meant only for typing with the on-screen keyboard. Only mouse "
        "operation, Gaze Jump and Gaze move can be used. The Gaze Jump and "
        "Gaze move animations are not drawn."),
    # ---- task switcher: hidden tasks ----
    "Window title": (
        "Window title",
        "Exact match only. Wildcards (*, ?) can be used."),
    # ---- mouse page ----
    "Gaze Jump": (
        "Gaze Jump",
        "Moves the pointer near where you are looking. Webcam gaze "
        "detection has limited accuracy, so it can only take you to the "
        "rough area; used together with Gaze move or head tracking it "
        "gets you there precisely, and quickly."),
    "Jump to the nearest button or link": (
        "Jump to the nearest button or link",
        "If there is a button or link near where the jump lands, the "
        "pointer goes onto it. In some apps and sites this does not work, "
        "or picks the wrong thing."),
    "Visual cue": (
        "Visual cue",
        "When the jump lands on a button or link, its animation is "
        "coloured to tell you."),
    "Start Gaze move right after the jump": (
        "Start Gaze move right after the jump",
        "Makes the fine movement quick. Recommended on."),
    "Gaze move": (
        "Gaze move",
        "Look at one of the arrows on screen to move the pointer that "
        "way. While it is moving, the Gaze move gesture again stops the "
        "pointer, and any other gesture immediately runs the action "
        "assigned to it."),
    # ---- launcher item editor ----
    "Key / mouse macro": (
        "Key / mouse macro",
        "With a macro, even a complicated operation takes nothing but "
        "your gaze. Choosing the launcher button runs the key and mouse "
        "operations below in order. One line is one step, run from the top; the buttons "
        "on the right add lines.\n"
        "key ctrl+c: press keys (+ for together, e.g. ctrl+shift+s, "
        "win+up, alt+f4, enter, esc, f5)\n"
        "text Hello: type text (pasted through the clipboard, so its "
        "content is replaced)\n"
        "wait 500: wait this many milliseconds\n"
        "mouse left / right / middle / double: click where the pointer is\n"
        "wheel up 3: scroll (up / down / left / right, and how many "
        "notches)\n"
        "drag left: keep the button pressed; move the pointer, and the "
        "next click lets go\n"
        "hold left 800: press and hold in place for this many "
        "milliseconds\n"
        "swipe ↑ medium: press, move that way, let go, and return to the "
        "start (small / medium / large = 10 / 25 / 45% of the screen "
        "height, or a number of pixels)"),
    # ---- per-tool gesture maps ----
    "Launcher gestures": (
        "Launcher gestures",
        "This assignment is used only while the gaze launcher is open."),
    "Gestures:task_switcher": (
        "Gestures",
        "This assignment is used only while the gaze task switcher is "
        "open."),
    "Gestures:clipboard": (
        "Gestures",
        "This assignment is used only while the gaze clipboard history "
        "is open."),
}

_images = {}              # size -> CTkImage (shared; CTk keeps its own)
_dismiss_bound = set()    # toplevels whose dismiss handlers are installed


def _install_dismiss(root):
    """Bind click-outside / Escape dismissal ONCE per window.

    Installed once and never removed, dispatching to whichever popup is
    open: binding per popup and unbinding on close would mean calling
    unbind_all, which drops every <Button-1> binding in the app, not just
    this one.
    """
    key = str(root)
    if key in _dismiss_bound:
        return
    _dismiss_bound.add(key)

    def on_click(event):
        p = HelpPopup._open
        if p is not None:
            p._maybe_close(event)

    def on_esc(_event):
        p = HelpPopup._open
        if p is not None:
            p.close()

    try:
        root.bind_all("<Button-1>", on_click, add="+")
        root.bind_all("<Escape>", on_esc, add="+")
    except Exception as e:
        logger.debug("help dismiss binding: %s", e)


def _image(size):
    img = _images.get(size)
    if img is None:
        try:
            from PIL import Image
            pil = Image.open(paths.install_dir() / "assets" / "images"
                             / "help_q.png")
        except Exception as e:
            logger.error("help icon not loaded: %s", e)
            return None
        img = customtkinter.CTkImage(light_image=pil, dark_image=pil,
                                     size=(size, size))
        _images[size] = img
    return img


def section_with_help(parent, text, topic, size=14, padx=20,
                      pady=(16, 2), variable=None, command=None):
    """A bold section heading followed by its "?" marker.

    With `variable`, an on/off checkbox (bound to it, calling `command`)
    sits in front of the heading, for a section that is itself a switch.

    Returns the heading label. The two live in a transparent row so the
    marker sits right after the words however long they are - a fixed
    offset would drift with the translation.
    """
    row = customtkinter.CTkFrame(parent, fg_color="transparent")
    row.pack(padx=padx, pady=pady, anchor="w")
    if variable is not None:
        customtkinter.CTkCheckBox(
            row, text="", width=24, variable=variable,
            onvalue=True, offvalue=False, command=command).pack(
                side="left", padx=(0, 6))
    label = customtkinter.CTkLabel(row, text=text)
    label.cget("font").configure(size=size, weight="bold")
    label.pack(side="left")
    HelpIcon(row, topic).pack(side="left", padx=(8, 0))
    return label


class HelpIcon(customtkinter.CTkLabel):
    """A "?" marker for `topic`. Place it like any other widget."""

    def __init__(self, master, topic, **kwargs):
        kwargs.setdefault("text", "")
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, image=_image(ICON), **kwargs)
        self.topic = topic
        self._big = None          # the enlarged copy, created on first hover
        self.configure(cursor="hand2")
        self.bind("<Enter>", self._show_big)
        self.bind("<Button-1>", self._open)

    # -- hover ------------------------------------------------------ #

    def _show_big(self, _event=None):
        """Lay the enlarged icon over this one.

        It is a child of the window, not of this row, so it is not clipped
        by the row and does not push the row taller. Once it is up it owns
        the pointer: its own Leave hides it, and a click on it opens the
        help as a click on the marker would.
        """
        if self._big is None:
            img = _image(ICON_HOVER)
            if img is None:
                return
            try:
                self._big = customtkinter.CTkLabel(
                    self.winfo_toplevel(), text="", image=img,
                    width=ICON_HOVER, height=ICON_HOVER,
                    fg_color="transparent", bg_color=self.cget("bg_color"),
                    cursor="hand2")
            except Exception as e:
                logger.debug("help icon hover: %s", e)
                self._big = None
                return
            self._big.bind("<Leave>", self._maybe_hide)
            self._big.bind("<Button-1>", self._open)
        try:
            self._big.place(in_=self, relx=0.5, rely=0.5, anchor="center")
            self._big.lift()
        except Exception as e:
            logger.debug("help icon hover: %s", e)

    def _maybe_hide(self, _event=None):
        # Leave also fires moving between the label's own canvas and its
        # inner tk label, so hide only once the pointer is really outside.
        big = self._big
        if big is None:
            return
        try:
            px, py = big.winfo_pointerxy()
            x, y = big.winfo_rootx(), big.winfo_rooty()
            if (x <= px < x + big.winfo_width()
                    and y <= py < y + big.winfo_height()):
                return
            big.place_forget()
        except Exception:
            pass

    def destroy(self):
        # The enlarged copy belongs to the window, not to this row, so it
        # would outlive a rebuilt page unless taken down here.
        if self._big is not None:
            try:
                self._big.destroy()
            except Exception:
                pass
            self._big = None
        super().destroy()

    # -- popup ------------------------------------------------------ #

    def _open(self, _event=None):
        heading, body = HELP_TEXTS.get(
            self.topic,
            (self.topic, t("Help for this setting is coming soon.")))
        HelpPopup(self).show(heading, body)


class HelpPopup:
    """A small borderless window under the marker that opened it.

    Its own window rather than a panel: help is a glance at one setting,
    and pushing a panel would take the settings screen away from under
    the very control being explained.
    """

    _open = None          # the popup currently on screen, if any

    def __init__(self, anchor):
        self.anchor = anchor
        self.win = None

    def show(self, heading, body):
        if HelpPopup._open is not None:
            HelpPopup._open.close()
        HelpPopup._open = self

        # fg_color on the window as well as the frame: the rounded frame
        # is drawn on a canvas, and anything it does not cover would
        # otherwise show the toplevel's default background.
        top = customtkinter.CTkToplevel(self.anchor, fg_color=POPUP_BG)
        self.win = top
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        frame = customtkinter.CTkFrame(top, fg_color=POPUP_BG,
                                       border_color=POPUP_BORDER,
                                       border_width=1, corner_radius=10)
        frame.pack(fill="both", expand=True)
        title = customtkinter.CTkLabel(
            frame, text=t(heading), text_color=TEXT, anchor="w",
            justify="left", wraplength=POPUP_W - 40)
        title.cget("font").configure(size=TITLE_SIZE, weight="bold")
        title.pack(anchor="w", padx=16, pady=(14, 2))
        text = customtkinter.CTkLabel(
            frame, text=t(body), text_color=TEXT, anchor="w",
            justify="left", wraplength=POPUP_W - 40)
        text.cget("font").configure(size=BODY_SIZE)
        text.pack(anchor="w", padx=16, pady=(0, 14))

        close_btn = customtkinter.CTkButton(
            frame, text="✕", width=26, height=26, corner_radius=6,
            command=self.close)
        close_btn.place(relx=1.0, x=-8, y=8, anchor="ne")

        top.update_idletasks()
        self._place(top)
        _install_dismiss(self.anchor.winfo_toplevel())

    def _place(self, top):
        """Under the marker, nudged to stay on the screen it is on."""
        try:
            w = POPUP_W
            h = max(80, top.winfo_reqheight())
            x = self.anchor.winfo_rootx() - w // 2 + self.anchor.winfo_width() // 2
            y = self.anchor.winfo_rooty() + self.anchor.winfo_height() + 6
            sw = self.anchor.winfo_screenwidth()
            sh = self.anchor.winfo_screenheight()
            x = max(8, min(sw - w - 8, x))
            if y + h > sh - 8:
                y = max(8, self.anchor.winfo_rooty() - h - 6)
            top.geometry("%dx%d+%d+%d" % (w, h, x, y))
        except Exception as e:
            logger.debug("help popup placement: %s", e)

    def _maybe_close(self, event):
        # A click on the popup itself (or on the marker, which would
        # otherwise close and immediately reopen it) keeps it open.
        w = getattr(event, "widget", None)
        try:
            while w is not None:
                if (w is self.win or w is self.anchor
                        or w is getattr(self.anchor, "_big", None)):
                    return
                w = w.master
        except Exception:
            pass
        self.close()

    def close(self):
        if HelpPopup._open is self:
            HelpPopup._open = None
        if self.win is not None:
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = None
