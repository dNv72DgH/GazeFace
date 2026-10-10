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
"""Hide-task rules for the gaze task switcher.

A rule carries a process name and a window title, each with its own
checkbox: tick one to match on that field alone, tick both to require
both (which is how a single window of a multi-window app gets hidden).
Window titles must match EXACTLY, or as a whole-title wildcard pattern
(see task_switcher.rule_matches).
Picking an entry from the running-windows list fills in both fields,
leaving the user to choose which to key on.
"""

import logging
import tkinter as tk

import customtkinter

from src.config_manager import ConfigManager
from src.gui.help_icon import HelpIcon
from src.i18n import t

logger = logging.getLogger("HiddenTasks")


class HiddenTasksPopup:

    def __init__(self, master):
        self.master = master
        self._host = None
        self.top = None
        self._rows = []          # one dict of widgets/vars per rule row
        self._list = None

    def open(self):
        from src.gui.panel_host import panel_host
        host = panel_host()
        if host is None:
            return
        self._host = host
        self.top = host.push(t("Hide tasks"), on_close=self._save)
        self._build()

    # --- data ---
    def _data(self):
        e = ConfigManager().read_extensions()
        e.setdefault("task_switcher", {})
        return e

    def _load(self):
        return list((self._data().get("task_switcher", {}) or {}).get(
            "hidden", []) or [])

    def _save(self):
        """Write every row back, dropping rows with nothing filled in."""
        out = []
        for r in self._rows:
            try:
                proc = r["proc_var"].get().strip()
                title = r["title_var"].get().strip()
            except Exception:
                continue        # row was destroyed
            if not proc and not title:
                continue
            out.append({"proc": proc, "title": title,
                        "by_proc": bool(r["by_proc_var"].get()),
                        "by_title": bool(r["by_title_var"].get())})
        e = self._data()
        e["task_switcher"]["hidden"] = out
        ConfigManager().write_extensions(e)

    # --- build ---
    def _build(self):
        for w in self.top.winfo_children():
            w.destroy()
        self._rows = []

        brow = customtkinter.CTkFrame(self.top, fg_color="transparent")
        brow.pack(padx=20, anchor="w", pady=(16, 8))
        customtkinter.CTkButton(
            brow, text=t("Add"), width=110,
            command=self._add_blank).pack(side="left", padx=(0, 8))
        customtkinter.CTkButton(
            brow, text=t("Pick from running"), width=170,
            command=self._open_picker).pack(side="left", padx=(0, 8))
        customtkinter.CTkButton(
            brow, text=t("Apply"), width=90,
            command=self._save).pack(side="left")

        # Headings only — the checkbox beside each field says whether that
        # field takes part in the match. (No explanatory caption here.)
        hrow = customtkinter.CTkFrame(self.top, fg_color="transparent")
        hrow.pack(padx=20, anchor="w", fill="x")
        customtkinter.CTkLabel(hrow, text=t("Process name"), width=206,
                               anchor="w").pack(side="left")
        # How the title is matched lives in the help marker, not in the
        # heading.
        customtkinter.CTkLabel(hrow, text=t("Window title"),
                               anchor="w").pack(side="left")
        HelpIcon(hrow, "Window title").pack(side="left", padx=(8, 0))

        from src.gui.frames.safe_disposable_frame import style_scrollbar
        self._list = customtkinter.CTkScrollableFrame(
            self.top, fg_color="transparent", height=360)
        style_scrollbar(self._list)
        self._list.pack(padx=16, anchor="nw", fill="both", expand=True,
                        pady=(2, 12))
        for rule in self._load():
            self._add_row(rule)

    def _add_row(self, rule=None):
        rule = rule or {}
        row = customtkinter.CTkFrame(self._list, fg_color="transparent")
        row.pack(anchor="w", fill="x", pady=2)

        by_proc_var = tk.BooleanVar(value=bool(rule.get("by_proc", True)))
        proc_var = tk.StringVar(value=str(rule.get("proc", "") or ""))
        customtkinter.CTkCheckBox(
            row, text="", width=24, checkbox_width=17, checkbox_height=17,
            variable=by_proc_var, onvalue=True, offvalue=False,
            command=self._save).pack(side="left")
        proc_entry = customtkinter.CTkEntry(row, textvariable=proc_var,
                                            width=174)
        proc_entry.pack(side="left", padx=(4, 8))

        by_title_var = tk.BooleanVar(value=bool(rule.get("by_title", False)))
        title_var = tk.StringVar(value=str(rule.get("title", "") or ""))
        customtkinter.CTkCheckBox(
            row, text="", width=24, checkbox_width=17, checkbox_height=17,
            variable=by_title_var, onvalue=True, offvalue=False,
            command=self._save).pack(side="left")
        title_entry = customtkinter.CTkEntry(row, textvariable=title_var,
                                             width=264)
        title_entry.pack(side="left", padx=(4, 8))

        entry = {"frame": row, "proc_var": proc_var, "title_var": title_var,
                 "by_proc_var": by_proc_var, "by_title_var": by_title_var}
        customtkinter.CTkButton(
            row, text=t("Delete"), width=80,
            command=lambda e=entry: self._delete_row(e)).pack(side="left")
        # Commit typed text on Enter / focus-out rather than per keystroke.
        for widget in (proc_entry, title_entry):
            widget.bind("<FocusOut>", lambda _e: self._save())
            widget.bind("<Return>", lambda _e: self._save())
        self._rows.append(entry)
        return entry

    def _add_blank(self):
        self._add_row()

    def _delete_row(self, entry):
        try:
            entry["frame"].destroy()
        except Exception:
            pass
        if entry in self._rows:
            self._rows.remove(entry)
        self._save()

    def _open_picker(self):
        RunningWindowPicker(self.master, self._on_picked).open()

    def _on_picked(self, proc, title):
        # Both fields are filled in; matching on the process name only is
        # the default because that is the common case.
        self._add_row({"proc": proc, "title": title,
                       "by_proc": True, "by_title": False})
        self._save()

    def close(self):
        self._save()
        if self._host is not None and self.top is not None:
            self._host.pop(self.top)
        self.top = None


class RunningWindowPicker:
    """The currently open windows; clicking one hands its (process name,
    window title) back to the caller."""

    def __init__(self, master, on_pick):
        self.master = master
        self.on_pick = on_pick
        self._host = None
        self.top = None

    def open(self):
        from src.gui.panel_host import panel_host
        host = panel_host()
        if host is None:
            return
        self._host = host
        self.top = host.push(t("Pick from running"))
        self._build()

    def _build(self):
        from src.extensions.task_switcher import TaskSwitcher
        try:
            wins = TaskSwitcher.list_open_windows()
        except Exception as e:
            logger.error("list windows failed: %s", e)
            wins = []
        from src.gui.frames.safe_disposable_frame import style_scrollbar
        gf = customtkinter.CTkScrollableFrame(
            self.top, fg_color="transparent", height=430)
        style_scrollbar(gf)
        gf.pack(padx=20, pady=(16, 12), anchor="nw", fill="both",
                expand=True)
        for proc, title in wins:
            label = "%s    %s" % (proc, title)
            if len(label) > 72:
                label = label[:72] + "…"
            customtkinter.CTkButton(
                gf, text=label, anchor="w", width=560, height=30,
                command=lambda p=proc, s=title: self._pick(p, s)).pack(
                    anchor="w", pady=2)

    def _pick(self, proc, title):
        self.close()
        try:
            self.on_pick(proc, title)
        except Exception as e:
            logger.error("pick callback failed: %s", e)

    def close(self):
        if self._host is not None and self.top is not None:
            self._host.pop(self.top)
        self.top = None
