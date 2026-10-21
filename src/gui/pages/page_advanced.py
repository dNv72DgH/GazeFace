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

import logging
import os
import tkinter

import customtkinter

from src import i18n
from src.config_manager import ConfigManager
from src.gui.frames.safe_disposable_frame import SafeDisposableScrollableFrame
from src.i18n import t

logger = logging.getLogger("PageAdvanced")

CHATTERING_MIN_MS = 10
CHATTERING_MAX_MS = 2000
CHATTERING_DEFAULT_MS = 1000

JUMP_DELAY_MIN_MS = 100
JUMP_DELAY_MAX_MS = 5000
JUMP_DELAY_DEFAULT_MS = 1000

# Gesture sensitivity gain: multiplier applied to blendshape values
# before threshold comparison. Slider uses tenths internally (10 = 1.0x).
GAIN_SLIDER_MIN = 10    # 1.0x
GAIN_SLIDER_MAX = 50    # 5.0x
GAIN_SLIDER_DEFAULT = 30  # 3.0x — default sensitivity
GAIN_DIVISOR = 10.0

# Kept in step with src.ui_access.CONFIG_KEY, imported lazily everywhere
# else because ui_access pulls in the restart machinery.
UI_ACCESS_KEY = "ui_access"



class PageAdvanced(SafeDisposableScrollableFrame):

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)

        self.grid_columnconfigure(0, weight=1)
        self.is_active = False

        # Rows are handed out by _next_row(), never written by hand.
        #
        # They used to be: each section took a start_row argument and
        # indexed off it, so the caller had to know how many rows every
        # section would eventually use. Diagnostics grew to occupy rows
        # 26-42 while Sub-monitor was still pinned at 40, and the two
        # landed in the same grid cells - widgets drawn on top of each
        # other and rows stretched to the tallest occupant. A counter
        # cannot collide, so adding a control to a section no longer
        # silently corrupts the section below it.
        self._row = 0

        self._title(t("Advanced"))

        # Kept for config compatibility (tracking_mode readers); the gaze-
        # calibration UI moved to the left menu.
        self.tracking_mode_var = tkinter.StringVar(value="head")

        # Order: the things a user actually tunes first, housekeeping last.
        #
        # Several sections are built but NOT shown (see HIDDEN_SECTIONS).
        # Their features and config keys are untouched — only the controls
        # are gone, because a settings page that offers a knob for
        # something the user never needs to turn is not neutral: it is a
        # question they have to read and dismiss on every visit.
        self._build_chattering_section()
        # Gesture List section was removed — all gestures are now
        # always selectable from the binding-page gesture popup.
        self._build_gain_section()
        self._build_submonitor_section()
        self._build_windows_startup_section()
        self._build_startup_section()
        # Overlay refresh is no longer shown. Its settings stay at what
        # every profile was already running with -- no profile stores
        # either key, so the code defaults ARE the current values:
        #   overlay_recycle_minutes   = 0     (no timed refresh)
        #   overlay_recycle_on_resume = True  (refresh after sleep/idle)
        # run_app reads them with those same defaults. The builder is kept
        # so the section can be brought back by restoring this call.
        # self._build_overlay_section()
        self._build_recovery_section()
        self._build_language_section()
        self._build_update_section()
        self._build_about_section()

        self.load_initial_config()

    # ---------------------------------------------------------------- #
    # Layout helpers
    # ---------------------------------------------------------------- #

    def _next_row(self) -> int:
        r = self._row
        self._row += 1
        return r

    def _title(self, text: str):
        lbl = customtkinter.CTkLabel(master=self, text=text)
        lbl.cget("font").configure(size=24, weight="bold")
        lbl.grid(row=self._next_row(), column=0, padx=20, pady=(10, 2),
                 sticky="nw")

    def _section(self, text: str, help_topic: str = None):
        """A section heading; with `help_topic`, its "?" marker follows."""
        if help_topic is None:
            lbl = customtkinter.CTkLabel(master=self, text=text)
            lbl.cget("font").configure(size=16, weight="bold")
            lbl.grid(row=self._next_row(), column=0, padx=20, pady=(18, 4),
                     sticky="nw")
            return
        from src.gui.help_icon import section_with_help
        row = customtkinter.CTkFrame(master=self, fg_color="transparent")
        row.grid(row=self._next_row(), column=0, padx=20, pady=(18, 4),
                 sticky="nw")
        section_with_help(row, text, help_topic, size=16, padx=0, pady=0)

    def _subheader(self, text: str):
        lbl = customtkinter.CTkLabel(master=self, text=text)
        lbl.cget("font").configure(size=14, weight="bold")
        lbl.grid(row=self._next_row(), column=0, padx=20, pady=(10, 2),
                 sticky="nw")

    def _row_frame(self, pady=(2, 6), padx=20):
        """A transparent frame occupying one grid row, packed left to right.

        Controls that belong together go in one of these instead of being
        placed in the same grid row with a hand-tuned padx offset, which
        is what the sliders used to do (padx=(350, 5) to sit an entry
        beside a 300px slider).
        """
        f = customtkinter.CTkFrame(master=self, fg_color="transparent")
        f.grid(row=self._next_row(), column=0, padx=padx, pady=pady,
               sticky="nw")
        return f

    def _grid(self, widget, pady=(2, 6), padx=20):
        widget.grid(row=self._next_row(), column=0, padx=padx, pady=pady,
                    sticky="nw")
        return widget

    # ---------------------------------------------------------------- #
    # Sub-monitor opacity
    # ---------------------------------------------------------------- #

    def _build_submonitor_section(self):
        self._section(t("Sub-monitor"))

        srow = self._row_frame()
        slbl = customtkinter.CTkLabel(
            master=srow, text=t("Opacity (%)"), anchor="w")
        slbl.cget("font").configure(size=12)
        slbl.pack(side="left", padx=(0, 10))
        self.submon_alpha_slider = customtkinter.CTkSlider(
            master=srow, from_=20, to=100, width=240,
            number_of_steps=80,
            command=self._on_submon_alpha_change)
        self.submon_alpha_slider.pack(side="left")
        self.submon_alpha_slider.bind("<ButtonRelease-1>",
                                      self._on_submon_alpha_release)
        cur = int(round(float(ConfigManager()._read_app_setting(
            "submonitor_alpha", 1.0)) * 100))
        self.submon_alpha_slider.set(max(20, min(100, cur)))

    # ---------------------------------------------------------------- #
    # Chattering prevention
    # ---------------------------------------------------------------- #

    def _build_chattering_section(self):
        self._section(t("Chattering prevention"), "Chattering prevention")

        self.enabled_var = tkinter.BooleanVar()
        self.enabled_checkbox = self._grid(customtkinter.CTkCheckBox(
            master=self,
            text=t("Enable chattering prevention"),
            variable=self.enabled_var,
            onvalue=True,
            offvalue=False,
            command=self.on_enabled_toggle))

        self._subheader(t("Interval (ms)"))

        # Slider and entry share one packed row. They used to be two
        # widgets in the same grid cell, the entry pushed clear of the
        # 300px slider with padx=(350, 5) — a number that silently goes
        # wrong the moment the slider width or the font changes.
        crow = self._row_frame(padx=30)
        self.slider_dragging = False
        self.threshold_slider = customtkinter.CTkSlider(
            master=crow,
            from_=CHATTERING_MIN_MS,
            to=CHATTERING_MAX_MS,
            width=300,
            number_of_steps=CHATTERING_MAX_MS - CHATTERING_MIN_MS,
            command=self.on_slider_drag)
        self.threshold_slider.bind("<Button-1>", self.on_slider_press)
        self.threshold_slider.bind("<ButtonRelease-1>",
                                   self.on_slider_release)
        self.threshold_slider.pack(side="left", padx=(0, 16))

        self.entry_var = tkinter.StringVar()
        self.entry_trace_id = self.entry_var.trace("w",
                                                   self.on_entry_changed)
        self.threshold_entry = customtkinter.CTkEntry(
            master=crow,
            textvariable=self.entry_var,
            width=80)
        self.threshold_entry.pack(side="left")
        self._entry_ok_color = self.threshold_entry.cget("fg_color")


    # Gesture List section was removed — every gesture is always
    # available from the per-row gesture-selection popup on the
    # binding pages, with its own live meter, so this Advanced
    # subsection is no longer needed.

    # ---------------------------------------------------------------- #
    # Sensitivity gain (multiplier applied to blendshape values)
    # ---------------------------------------------------------------- #

    def _build_gain_section(self):
        self._section(t("Gesture sensitivity"))
        self._subheader(t("Overall sensitivity"))

        grow = self._row_frame(padx=30)
        self.gain_slider_dragging = False
        self.gain_slider = customtkinter.CTkSlider(
            master=grow,
            from_=GAIN_SLIDER_MIN,
            to=GAIN_SLIDER_MAX,
            width=300,
            number_of_steps=GAIN_SLIDER_MAX - GAIN_SLIDER_MIN,
            command=self.on_gain_slider_drag)
        self.gain_slider.bind("<Button-1>", self.on_gain_slider_press)
        self.gain_slider.bind("<ButtonRelease-1>",
                              self.on_gain_slider_release)
        self.gain_slider.pack(side="left", padx=(0, 16))

        self.gain_entry_var = tkinter.StringVar()
        self.gain_entry_trace_id = self.gain_entry_var.trace(
            "w", self.on_gain_entry_changed)
        self.gain_entry = customtkinter.CTkEntry(
            master=grow,
            textvariable=self.gain_entry_var,
            width=80)
        self.gain_entry.pack(side="left")

    # Live blendshape debug panel was removed from this page — the
    # per-gesture meter + sensitivity controls live in the Gesture
    # List popup (Advanced → "Edit gesture list…") now.

    # ---------------------------------------------------------------- #
    # Language
    # ---------------------------------------------------------------- #

    def _build_language_section(self):
        self._section(t("Language"))

        self.language_var = tkinter.StringVar(value=i18n.get_language())
        lang_dropdown = customtkinter.CTkOptionMenu(
            master=self,
            values=["English", "日本語"],
            command=self.on_language_change,
            width=200)
        # Initial display from current language code.
        lang_dropdown.set("日本語" if i18n.get_language() == "ja" else "English")
        self._grid(lang_dropdown)
        self.lang_dropdown = lang_dropdown

    # ---------------------------------------------------------------- #
    # Updates (app-wide)
    # ---------------------------------------------------------------- #

    def _build_update_section(self):
        from src import updater
        self._section(t("Updates"))
        self.update_notify_var = tkinter.BooleanVar(
            value=updater.notify_enabled())
        self._grid(customtkinter.CTkCheckBox(
            master=self, text=t("Notify me of updates"),
            variable=self.update_notify_var, onvalue=True, offvalue=False,
            command=lambda: ConfigManager().write_app_setting(
                updater.NOTIFY_KEY, bool(self.update_notify_var.get()))))

    # ---------------------------------------------------------------- #
    # Diagnostics (verbose logging, open log folder)
    # ---------------------------------------------------------------- #

    # The Diagnostics section (verbose logging + "Open log folder") is
    # gone entirely, controls and handlers alike. It went unused: the log
    # is always written to %LOCALAPPDATA%\GazeFace either way, and the
    # verbose flag is read straight from config at start-up
    # (verbose_logging, app-wide), so nothing here was load-bearing.

    # ---------------------------------------------------------------- #
    # Auto recovery
    # ---------------------------------------------------------------- #

    def _build_recovery_section(self):
        self._section(t("Auto recovery"), "Auto recovery")

        self.wd_enabled_var = tkinter.BooleanVar(
            value=bool(ConfigManager().config.get(
                "auto_restart_enabled", True)))
        self._grid(customtkinter.CTkCheckBox(
            master=self,
            text=t("Enable auto recovery"),
            variable=self.wd_enabled_var,
            onvalue=True, offvalue=False,
            command=self._on_wd_toggle))

        wd_sec_row = self._row_frame()
        wd_sec_lbl = customtkinter.CTkLabel(
            master=wd_sec_row, text=t("Threshold (seconds):"), anchor="w")
        wd_sec_lbl.cget("font").configure(size=12)
        wd_sec_lbl.pack(side="left", padx=(0, 6))
        self.wd_sec_var = tkinter.StringVar(
            value=str(int(ConfigManager().config.get(
                "auto_restart_seconds", 60))))
        self._wd_trace_id = self.wd_sec_var.trace(
            "w", self._on_wd_sec_changed)
        self.wd_sec_entry = customtkinter.CTkEntry(
            master=wd_sec_row, textvariable=self.wd_sec_var, width=80)
        self.wd_sec_entry.pack(side="left")

        # "Stuck input release" (input_force_release_s) had a control here
        # and no longer does. The safeguard still runs — see the janitor in
        # controllers/keybinder.py — but in the whole log history it has
        # never once fired: every force-release on record came from the
        # 2 s deadlock breaker, which needs no setting. A number the user
        # is invited to tune for an event that has never happened is a
        # question with no useful answer. The key is still read from
        # config, so it remains adjustable by anyone who edits the file.

    # ---------------------------------------------------------------- #
    # Startup and window behaviour (app-wide settings)
    # ---------------------------------------------------------------- #

    # ---------------------------------------------------------------- #
    # Windows startup (registered as an assistive technology)
    # ---------------------------------------------------------------- #

    def _build_windows_startup_section(self):
        """Start with Windows - on the sign-in screen, and/or after it.

        The boxes show what the registry actually says, re-read after
        every change: a declined UAC prompt leaves the setting as it was,
        and a box that stays ticked regardless would be lying.
        """
        self._section(t("Startup"))
        self.at_before_var = tkinter.BooleanVar(value=False)
        self.at_after_var = tkinter.BooleanVar(value=False)
        # The box is its own label, so its help marker sits beside it.
        before_row = self._row_frame()
        self.at_before_cb = customtkinter.CTkCheckBox(
            master=before_row,
            text=t("At sign-in (starts on the sign-in screen etc.)"),
            variable=self.at_before_var, onvalue=True, offvalue=False,
            command=lambda: self._on_at_toggle("before"))
        self.at_before_cb.pack(side="left")
        from src.gui.help_icon import HelpIcon
        HelpIcon(before_row, "At sign-in").pack(side="left", padx=(8, 0))
        self.at_after_cb = self._grid(customtkinter.CTkCheckBox(
            master=self,
            text=t("After sign-in (starts on the desktop, UAC prompts "
                   "etc.)"),
            variable=self.at_after_var, onvalue=True, offvalue=False,
            command=lambda: self._on_at_toggle("after")))
        self.at_lbl = self._grid(customtkinter.CTkLabel(
            master=self, text="", wraplength=560, justify=tkinter.LEFT),
            pady=(0, 6))
        self._refresh_at_startup()

    def _refresh_at_startup(self, message=""):
        try:
            from src import at_startup
            self.at_before_var.set(at_startup.before_signin())
            self.at_after_var.set(at_startup.after_signin())
            why = at_startup.blocker()
        except Exception as e:
            logger.error("startup options read failed: %s", e)
            why = str(e)
        # The sign-in screen copy is the UIAccess build (Windows starts
        # nothing else there), so the option is greyed out whenever this
        # app is not running as it - the portable / standard build.
        try:
            from src import ui_access
            no_uia = not ui_access.active()
        except Exception:
            no_uia = True
        # Turning an option OFF must stay possible even where it could
        # not be turned on now (e.g. the install moved).
        for cb, var, blocked in (
                (self.at_before_cb, self.at_before_var, bool(why) or no_uia),
                (self.at_after_cb, self.at_after_var, bool(why))):
            cb.configure(state=("disabled" if blocked and not var.get()
                                else "normal"))
        if why and not message:
            message = t("Not available on this installation: %s") % why
        self.at_lbl.configure(text=message)

    def _on_at_toggle(self, which):
        var = self.at_before_var if which == "before" else self.at_after_var
        want = bool(var.get())
        for cb in (self.at_before_cb, self.at_after_cb):
            cb.configure(state="disabled")
        self.at_lbl.configure(text=t(
            "Applying... allow it if Windows asks for permission."))

        def done(why):
            if why == "cancelled":
                msg = t("Not changed: permission was not given.")
            elif why:
                msg = t("Could not change the setting: %s") % why
            else:
                msg = ""
            try:
                self.after(0, lambda: self._refresh_at_startup(msg))
            except Exception:
                pass

        from src import at_startup
        at_startup.apply_async(which, want, done)

    def _build_startup_section(self):
        self._section(t("Startup and window"))

        sp_row = self._row_frame()
        customtkinter.CTkLabel(sp_row, text=t("Page at startup")).pack(
            side="left", padx=(0, 8))
        self.startup_page_var = tkinter.StringVar(
            value=str(ConfigManager()._read_app_setting(
                "startup_page", "home")))
        for val, label in (("home", t("Home")),
                           ("last", t("Last page used"))):
            customtkinter.CTkRadioButton(
                sp_row, text=label, variable=self.startup_page_var,
                value=val, command=self._on_startup_page).pack(
                    side="left", padx=(0, 12))

        self.multi_var = tkinter.BooleanVar(
            value=bool(ConfigManager()._read_app_setting(
                "allow_multi_instance", False)))
        self._grid(customtkinter.CTkCheckBox(
            master=self,
            text=t("Allow multiple instances (effect after restart)"),
            variable=self.multi_var,
            onvalue=True, offvalue=False,
            command=self._on_multi_toggle))

    # ---------------------------------------------------------------- #
    # Overlay refresh
    # ---------------------------------------------------------------- #

    def _build_overlay_section(self):
        self._section(t("Overlay"))

        or_row = self._row_frame()
        customtkinter.CTkLabel(
            or_row, text=t("Refresh overlay every (min, 0 = off)")).pack(
                side="left", padx=(0, 6))
        self.overlay_recycle_var = tkinter.StringVar(
            value=str(int(ConfigManager().config.get(
                "overlay_recycle_minutes", 0))))
        self._overlay_recycle_trace = self.overlay_recycle_var.trace(
            "w", self._on_overlay_recycle_changed)
        customtkinter.CTkEntry(or_row,
                               textvariable=self.overlay_recycle_var,
                               width=80).pack(side="left")

        self.overlay_resume_var = tkinter.BooleanVar(
            value=bool(ConfigManager().config.get(
                "overlay_recycle_on_resume", True)))
        self._grid(customtkinter.CTkCheckBox(
            master=self,
            text=t("Refresh overlay after sleep or long idle"),
            variable=self.overlay_resume_var,
            onvalue=True, offvalue=False,
            command=self._on_overlay_resume_toggle))

    # ---------------------------------------------------------------- #
    # About GazeFace: a button here, the details on a page of their own
    # (English only, like most About boxes - just the title is translated)
    # ---------------------------------------------------------------- #

    def _build_about_section(self):
        brow = self._row_frame(pady=(18, 20))
        customtkinter.CTkButton(
            brow, text=t("About GazeFace"), width=180,
            command=self._open_about_page).pack(side="left")

    def _open_about_page(self):
        from src.gui.panel_host import panel_host
        host = panel_host()
        if host is None:
            return
        body = host.push(t("About GazeFace"))
        self._fill_about_page(body)

    def _fill_about_page(self, body):
        import platform
        import sys
        from src import paths
        try:
            from src import ui_access
            edition = ("Full Edition" if ui_access.active()
                       else "Standard Edition")
        except Exception:
            edition = "Standard Edition"

        rows = [
            ("Version", ConfigManager().version),
            ("Edition", edition),
            ("Copyright", "© 2026 Nito Ninomae @12monkeys_nito(X/twitter)"),
            ("License", "Apache License 2.0"),
            ("Based on", "Project Gameface by Google LLC "
                         "(Apache License 2.0)"),
            ("Third-party software",
             "MediaPipe (Apache 2.0), OpenCV (Apache 2.0), "
             "Qt for Python / PySide6 (LGPL v3), CustomTkinter (MIT), "
             "IBM Plex fonts (SIL OFL 1.1)"),
            ("Privacy", "Camera images are processed on this PC only. "
                        "They are never saved or sent anywhere."),
            ("Settings folder", str(paths.user_data_dir())),
            ("System", "Windows %s, Python %s" % (
                platform.version(), platform.python_version())),
            # Filled in below: the lookup takes a moment.
            ("Virtual mouse driver", "Checking..."),
        ]
        table = customtkinter.CTkFrame(body, fg_color="transparent")
        table.pack(padx=20, pady=(10, 4), anchor="w")
        values = {}
        for i, (name, value) in enumerate(rows):
            customtkinter.CTkLabel(table, text=name, anchor="nw").grid(
                row=i, column=0, sticky="nw", padx=(0, 16), pady=1)
            values[name] = customtkinter.CTkLabel(
                table, text=value, anchor="nw", justify=tkinter.LEFT,
                wraplength=460)
            values[name].grid(row=i, column=1, sticky="nw", pady=1)
        self._fill_driver_status(values["Virtual mouse driver"])

        brow = customtkinter.CTkFrame(body, fg_color="transparent")
        brow.pack(padx=20, pady=(8, 20), anchor="w")
        customtkinter.CTkButton(
            brow, text="Open settings folder", width=180,
            command=lambda: self._open_path(paths.user_data_dir())).pack(
                side="left", padx=(0, 8))
        base = getattr(sys, "_MEIPASS", None) or paths.install_dir()
        customtkinter.CTkButton(
            brow, text="View license notices", width=180,
            command=lambda: self._open_path(
                os.path.join(base, "NOTICE"))).pack(side="left")

    def _fill_driver_status(self, label):
        """GazeFace's virtual mouse (driver/gfmouse): installed, working?
        Looked up off the UI thread; the label may be gone by then."""
        import threading

        def work():
            try:
                from src import pointer_wake
                text = pointer_wake.driver_status()
            except Exception as e:
                logger.error("driver status: %s", e)
                text = "Unknown"

            def show():
                try:
                    if label.winfo_exists():
                        label.configure(text=text)
                except Exception:
                    pass
            try:
                self.after(0, show)
            except Exception:
                pass
        threading.Thread(target=work, name="DriverStatus",
                         daemon=True).start()

    @staticmethod
    def _open_path(path):
        try:
            os.startfile(str(path))
        except Exception as e:
            logger.error("open %s failed: %s", path, e)

    # ---------------------------------------------------------------- #
    # UIAccess (not shown; the ui_access config key still applies)
    # ---------------------------------------------------------------- #

    def _build_uiaccess_section(self):
        """Whether Windows lets our input reach higher-privilege windows."""
        self._section(t("Elevated input permission"))
        self.uiaccess_var = tkinter.BooleanVar(
            value=bool(ConfigManager().config.get(
                UI_ACCESS_KEY, False)))
        self._grid(customtkinter.CTkCheckBox(
            master=self,
            text=t("Allow control of higher-privilege windows "
                   "(restarts the app)"),
            variable=self.uiaccess_var,
            onvalue=True, offvalue=False,
            command=self._on_uiaccess_toggle))
        self.uiaccess_lbl = self._grid(customtkinter.CTkLabel(
            master=self, text="", wraplength=560, justify=tkinter.LEFT),
            pady=(0, 6))
        self._refresh_uiaccess_label()

    def _refresh_uiaccess_label(self):
        """Say what is actually true, not what was ticked.

        The box can be ticked on a machine where Windows will never grant
        the flag (unsigned build, or installed outside Program Files), and
        a setting that looks on while doing nothing is worse than no
        setting at all.
        """
        try:
            from src import ui_access
            if not self.uiaccess_var.get():
                self.uiaccess_lbl.configure(text="")
                return
            why = ui_access.blocker()
            if why:
                self.uiaccess_lbl.configure(
                    text="%s\n%s" % (
                        t("Not available on this installation: %s") % why,
                        t("Input keeps working normally; only the "
                          "elevated permission is unavailable.")))
            elif ui_access.active():
                self.uiaccess_lbl.configure(text=t("Active."))
            else:
                self.uiaccess_lbl.configure(
                    text=t("Takes effect after the app restarts."))
        except Exception as e:
            logger.error("uiaccess label failed: %s", e)

    def _on_uiaccess_toggle(self):
        want = bool(self.uiaccess_var.get())
        try:
            ConfigManager().set_temp_config(UI_ACCESS_KEY, want)
            ConfigManager().apply_config()
        except Exception as e:
            logger.error("ui_access write failed: %s", e)
            return
        self._refresh_uiaccess_label()
        try:
            from src import ui_access
            if want and ui_access.blocker():
                return          # nothing to restart into; label explains
            ui_access.apply_switch("settings toggle")
        except Exception as e:
            logger.error("ui_access switch failed: %s", e)

    def _on_overlay_resume_toggle(self):
        try:
            ConfigManager().set_temp_config(
                "overlay_recycle_on_resume", bool(self.overlay_resume_var.get()))
            ConfigManager().apply_config()
        except Exception as e:
            logger.error("overlay_recycle_on_resume write failed: %s", e)

    def _on_overlay_recycle_changed(self, *_a):
        try:
            v = int(self.overlay_recycle_var.get())
        except ValueError:
            return
        if v < 0 or v > 600:
            return
        try:
            ConfigManager().set_temp_config("overlay_recycle_minutes", v)
            ConfigManager().apply_config()
        except Exception as e:
            logger.error("overlay_recycle_minutes write failed: %s", e)

    def _on_startup_page(self):
        try:
            ConfigManager().write_app_setting(
                "startup_page", str(self.startup_page_var.get()))
        except Exception as e:
            logger.error("startup_page write failed: %s", e)

    def _on_multi_toggle(self):
        try:
            ConfigManager().write_app_setting(
                "allow_multi_instance", bool(self.multi_var.get()))
        except Exception as e:
            logger.error("allow_multi_instance write failed: %s", e)

    def _on_submon_alpha_change(self, value):
        # Live: straight to the sub-monitor, no file write per drag step.
        from src.gui import sub_monitor
        sub_monitor.set_live_alpha(round(float(value) / 100.0, 2))

    def _on_submon_alpha_release(self, _event=None):
        try:
            ConfigManager().write_app_setting(
                "submonitor_alpha",
                round(float(self.submon_alpha_slider.get()) / 100.0, 2))
        except Exception as e:
            logger.error("submonitor_alpha write failed: %s", e)

    # ---------------------------------------------------------------- #
    # Gaze keyboard (test)
    # ---------------------------------------------------------------- #

    def _build_kbd_section(self):
        self._section(t("Gaze keyboard (test)"))
        krow = self._row_frame()
        customtkinter.CTkLabel(krow, text=t("Gaze keyboard dwell (ms)")).pack(
            side="left", padx=(0, 6))
        self.kbd_dwell_var = tkinter.StringVar(
            value=str(int(ConfigManager().config.get(
                "gaze_keyboard_dwell_ms", 800))))
        self._kbd_dwell_trace = self.kbd_dwell_var.trace(
            "w", self._on_kbd_dwell_changed)
        customtkinter.CTkEntry(krow, textvariable=self.kbd_dwell_var,
                               width=80).pack(side="left", padx=(0, 12))
        customtkinter.CTkButton(
            krow, text=t("Open gaze keyboard"), width=200,
            command=self._open_gaze_keyboard).pack(side="left")

        # Implicit re-calibration: learns the gaze drift from the cells
        # the user executes in the gaze grids, so a head move does not
        # force a full recalibration.
        self.online_cal_var = tkinter.BooleanVar(
            value=bool(ConfigManager().config.get(
                "gaze_online_cal_enabled", True)))
        oc_row = self._row_frame()
        customtkinter.CTkCheckBox(
            oc_row, text=t("Auto-correct gaze drift"),
            variable=self.online_cal_var, onvalue=True, offvalue=False,
            command=self._on_online_cal_toggle).pack(side="left",
                                                     padx=(0, 12))
        customtkinter.CTkButton(
            oc_row, text=t("Reset drift correction"), width=180,
            command=self._reset_online_cal).pack(side="left")

    def _on_online_cal_toggle(self):
        try:
            ConfigManager().set_temp_config(
                "gaze_online_cal_enabled",
                bool(self.online_cal_var.get()))
            ConfigManager().apply_config()
        except Exception as e:
            logger.error("gaze_online_cal_enabled write failed: %s", e)

    def _reset_online_cal(self):
        try:
            from src import gaze_online_cal
            gaze_online_cal.reset()
        except Exception as e:
            logger.error("gaze bias reset failed: %s", e)

    def _on_kbd_dwell_changed(self, *_a):
        try:
            v = int(self.kbd_dwell_var.get())
        except ValueError:
            return
        if v < 100 or v > 5000:
            return
        try:
            ConfigManager().set_temp_config("gaze_keyboard_dwell_ms", v)
            ConfigManager().apply_config()
        except Exception as e:
            logger.error("gaze_keyboard_dwell_ms write failed: %s", e)

    def _open_gaze_keyboard(self):
        from src.gui.gaze_keyboard import GazeKeyboard
        GazeKeyboard(self.winfo_toplevel()).open()

    def _on_wd_toggle(self):
        try:
            ConfigManager().set_temp_config(
                "auto_restart_enabled",
                bool(self.wd_enabled_var.get()))
            ConfigManager().apply_config()
        except Exception as e:
            logger.error("auto_restart_enabled write failed: %s", e)

    def _on_wd_sec_changed(self, *_args):
        text = self.wd_sec_var.get().strip()
        try:
            v = int(text)
        except ValueError:
            self.wd_sec_entry.configure(fg_color="#ee9e9d")
            return
        if v < 5 or v > 3600:
            self.wd_sec_entry.configure(fg_color="#ee9e9d")
            return
        self.wd_sec_entry.configure(fg_color=self._entry_ok_color)
        try:
            ConfigManager().set_temp_config("auto_restart_seconds", v)
            ConfigManager().apply_config()
        except Exception as e:
            logger.error("auto_restart_seconds write failed: %s", e)

    # ---------------------------------------------------------------- #
    # Initial state & helpers
    # ---------------------------------------------------------------- #

    def _get_chattering_cfg(self):
        cfg = ConfigManager().config
        enabled = bool(cfg.get("chattering_prevention_enabled", False))
        threshold = int(cfg.get("chattering_threshold_ms",
                                CHATTERING_DEFAULT_MS))
        threshold = max(CHATTERING_MIN_MS, min(CHATTERING_MAX_MS, threshold))
        return enabled, threshold

    def load_initial_config(self):
        enabled, threshold = self._get_chattering_cfg()
        self.enabled_var.set(enabled)
        self.threshold_slider.set(threshold)
        self.entry_var.trace_vdelete("w", self.entry_trace_id)
        self.entry_var.set(str(threshold))
        self.entry_trace_id = self.entry_var.trace("w",
                                                   self.on_entry_changed)
        self._sync_enabled_visual(enabled)

        mode = ConfigManager().config.get("tracking_mode", "head")
        if mode not in ("head", "gaze"):
            mode = "head"
        self.tracking_mode_var.set(mode)

        # Pointer-jump dwell was moved to the per-action options popup
        # on the Mouse-binding page.

        # Gesture sensitivity gain.
        gain = float(ConfigManager().config.get("gesture_gain", 3.0))
        gain_slider_pos = int(round(gain * GAIN_DIVISOR))
        gain_slider_pos = max(GAIN_SLIDER_MIN,
                              min(GAIN_SLIDER_MAX, gain_slider_pos))
        self.gain_slider.set(gain_slider_pos)
        self.gain_entry_var.trace_vdelete("w", self.gain_entry_trace_id)
        self.gain_entry_var.set(f"{gain_slider_pos / GAIN_DIVISOR:.1f}")
        self.gain_entry_trace_id = self.gain_entry_var.trace(
            "w", self.on_gain_entry_changed)

        # Auto-recovery watchdog — reload on profile switch too, or the
        # page keeps showing (and re-saving) the previous profile's values.
        self.wd_enabled_var.set(bool(ConfigManager().config.get(
            "auto_restart_enabled", True)))
        self.wd_sec_var.trace_vdelete("w", self._wd_trace_id)
        self.wd_sec_var.set(str(int(ConfigManager().config.get(
            "auto_restart_seconds", 60))))
        self._wd_trace_id = self.wd_sec_var.trace(
            "w", self._on_wd_sec_changed)

        # Nothing here reloads the gaze-keyboard dwell any more: that
        # section is no longer built, so its variable does not exist. The
        # value is still read straight from config wherever it is used.

    def _sync_enabled_visual(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        try:
            self.threshold_slider.configure(state=state)
            self.threshold_entry.configure(state=state)
        except Exception:
            pass

    # ---------------------------------------------------------------- #
    # Callbacks
    # ---------------------------------------------------------------- #

    def on_enabled_toggle(self):
        enabled = bool(self.enabled_var.get())
        ConfigManager().set_temp_config("chattering_prevention_enabled",
                                        enabled)
        ConfigManager().apply_config()
        self._sync_enabled_visual(enabled)

    def on_slider_press(self, _event):
        self.slider_dragging = True

    def on_slider_drag(self, new_value):
        self.slider_dragging = True
        new_value = int(new_value)
        self.entry_var.trace_vdelete("w", self.entry_trace_id)
        self.entry_var.set(str(new_value))
        self.entry_trace_id = self.entry_var.trace("w",
                                                   self.on_entry_changed)

    def on_slider_release(self, _event):
        self.slider_dragging = False
        new_value = int(self.threshold_slider.get())
        ConfigManager().set_temp_config("chattering_threshold_ms", new_value)
        ConfigManager().apply_config()

    def on_entry_changed(self, *_args):
        text = self.entry_var.get()
        if not text.isdigit():
            self.threshold_entry.configure(fg_color="#ee9e9d")
            return
        value = int(text)
        if value < CHATTERING_MIN_MS or value > CHATTERING_MAX_MS:
            self.threshold_entry.configure(fg_color="#ee9e9d")
            return
        self.threshold_entry.configure(fg_color=self._entry_ok_color)
        self.threshold_slider.set(value)
        if not self.slider_dragging:
            ConfigManager().set_temp_config("chattering_threshold_ms", value)
            ConfigManager().apply_config()

    def on_tracking_mode_change(self):
        mode = self.tracking_mode_var.get()
        ConfigManager().set_temp_config("tracking_mode", mode)
        ConfigManager().apply_config()
        logger.info("Tracking mode -> %s", mode)


    # --- Sensitivity gain callbacks ---

    def on_gain_slider_press(self, _event):
        self.gain_slider_dragging = True

    def on_gain_slider_drag(self, new_value):
        self.gain_slider_dragging = True
        pos = int(new_value)
        self.gain_entry_var.trace_vdelete("w", self.gain_entry_trace_id)
        self.gain_entry_var.set(f"{pos / GAIN_DIVISOR:.1f}")
        self.gain_entry_trace_id = self.gain_entry_var.trace(
            "w", self.on_gain_entry_changed)

    def on_gain_slider_release(self, _event):
        self.gain_slider_dragging = False
        pos = int(self.gain_slider.get())
        ConfigManager().set_temp_config("gesture_gain",
                                        pos / GAIN_DIVISOR)
        ConfigManager().apply_config()

    def on_gain_entry_changed(self, *_args):
        text = self.gain_entry_var.get()
        try:
            value = float(text)
        except ValueError:
            self.gain_entry.configure(fg_color="#ee9e9d")
            return
        pos = int(round(value * GAIN_DIVISOR))
        if pos < GAIN_SLIDER_MIN or pos > GAIN_SLIDER_MAX:
            self.gain_entry.configure(fg_color="#ee9e9d")
            return
        self.gain_entry.configure(fg_color=self._entry_ok_color)
        self.gain_slider.set(pos)
        if not self.gain_slider_dragging:
            ConfigManager().set_temp_config("gesture_gain",
                                            pos / GAIN_DIVISOR)
            ConfigManager().apply_config()

    def on_language_change(self, choice: str):
        code = "ja" if choice == "日本語" else "en"
        try:
            ConfigManager().write_app_setting("language", code)
        except Exception as e:
            logger.error("Failed to persist language: %s", e)

    def refresh_profile(self):
        self.load_initial_config()
