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

import copy
import json
import logging
import shutil
import time
import tkinter as tk
from pathlib import Path

from src import paths
from src.singleton_meta import Singleton
from src.task_killer import TaskKiller

VERSION = "1.00"

# User settings live under %LOCALAPPDATA%\GazeFace (see src/paths.py).
# These were bare-relative paths resolved against the CWD, which meant
# settings sat next to the exe: unwritable in a proper install location,
# and clobbered by the configs/ tree that build.spec bundles.
DEFAULT_JSON = paths.configs_dir() / "default.json"
BACKUP_PROFILE = paths.configs_dir() / "default"

logger = logging.getLogger("ConfigManager")


class ConfigManager(metaclass=Singleton):

    def __init__(self):
        logger.info("Intialize ConfigManager singleton")
        self.version = VERSION
        self.unsave_configs = False
        self.unsave_mouse_bindings = False
        self.unsave_keyboard_bindings = False
        self.config = None

        # Load config
        self.curr_profile_path = None
        self.curr_profile_name = tk.StringVar()
        self.is_started = False

        self.profiles = self.list_profile()

    def start(self):
        if not self.is_started:
            logger.info("Start ConfigManager singleton")
            if not DEFAULT_JSON.is_file():
                logger.critical(f"Missing {DEFAULT_JSON}, exit program...")
                TaskKiller().exit()

            try:
                with open(DEFAULT_JSON) as f:
                    self.load_profile(json.load(f)["default"])
            except Exception as e:
                logging.error(e)
                logging.error(
                    f"Failed to load default profile {DEFAULT_JSON}, using first profile instead."
                )
                self.load_profile(self.list_profile()[0])

            # Apply language as early as possible so any UI built after
            # ConfigManager().start() can call i18n.t() and get the right
            # locale. The selection is stored at the top level of the JSON
            # rather than in a profile so that switching profiles doesn't
            # accidentally re-translate the UI.
            try:
                from src import i18n
                lang = self._read_app_setting("language",
                                              i18n.DEFAULT_LANGUAGE)
                i18n.set_language(lang)
            except Exception as e:
                logger.error(f"Failed to apply language: {e}")

            self.is_started = True

    def _read_app_setting(self, field: str, default):
        """Read a top-level app-wide setting from DEFAULT_JSON, with default."""
        try:
            with open(DEFAULT_JSON) as f:
                data = json.load(f)
            return data.get(field, default)
        except Exception:
            return default

    def write_app_setting(self, field: str, value):
        """Persist a top-level app-wide setting to DEFAULT_JSON."""
        data = {}
        try:
            with open(DEFAULT_JSON) as f:
                data = json.load(f)
        except Exception:
            data = {}
        data[field] = value
        with open(DEFAULT_JSON, "w") as f:
            json.dump(data, f, indent=4)

    # The "default" directory is the factory-defaults template that
    # `add_profile` copies from when the user creates a new profile.
    # It must NOT be exposed in the UI as a selectable profile —
    # users would otherwise edit it directly and accidentally break
    # the template.
    TEMPLATE_PROFILE_NAME = "default"

    # Support folders that live NEXT TO the profiles inside the configs
    # directory but are not profiles themselves. Without this the
    # generated launcher-icon cache showed up as a phantom profile that
    # "came back" every time the user deleted it — the icon renderer
    # simply recreates the folder on the next launcher paint.
    RESERVED_DIRS = {"launcher_icons", "backups"}

    def list_profile(self) -> list:
        profile_dirs = []
        for dir in DEFAULT_JSON.parent.glob("*"):
            if not dir.is_dir():
                continue
            if dir.name == self.TEMPLATE_PROFILE_NAME:
                continue
            if dir.name in self.RESERVED_DIRS:
                continue
            # A real profile always carries its settings JSONs (cursor /
            # mouse_bindings / keyboard_bindings). Anything else is a
            # cache or support folder — belt-and-braces for future ones.
            if not any(dir.glob("*.json")):
                continue
            profile_dirs.append(dir.name)
        logger.info(profile_dirs)
        return profile_dirs

    def remove_profile(self, profile_name):
        logger.info(f"Remove profile {profile_name}")
        shutil.rmtree(Path(DEFAULT_JSON.parent, profile_name))
        self.profiles.remove(profile_name)
        logger.info(f"Current profiles: {self.profiles}")

    def add_profile(self):
        # Random name base on local timestamp
        new_profile_name = "profile_z" + str(hex(int(time.time() * 1000)))[2:]
        logger.info(f"Add profile {new_profile_name}")
        shutil.copytree(BACKUP_PROFILE,
                        Path(DEFAULT_JSON.parent, new_profile_name))
        self.profiles.append(new_profile_name)
        logger.info(f"Current profiles: {self.profiles}")
        return new_profile_name

    def copy_profile(self, source_profile_name: str) -> str:
        """Duplicate `source_profile_name` into a fresh timestamped
        profile and return the new name. The copy contains every
        setting the source had (cursor.json, mouse/keyboard bindings
        all faithfully copied). Returns None on failure."""
        source_dir = Path(DEFAULT_JSON.parent, source_profile_name)
        if not source_dir.is_dir():
            logger.error("Cannot copy: source %s does not exist",
                         source_profile_name)
            return None
        new_profile_name = (
            "profile_z" + str(hex(int(time.time() * 1000)))[2:])
        logger.info("Copy profile %s -> %s",
                    source_profile_name, new_profile_name)
        shutil.copytree(source_dir,
                        Path(DEFAULT_JSON.parent, new_profile_name))
        self.profiles.append(new_profile_name)
        return new_profile_name

    def rename_profile(self, old_profile_name, new_profile_name):
        # Never let the user rename ONTO the template — that would
        # overwrite the factory defaults that `add_profile` copies
        # from. Likewise refuse to rename the template itself.
        if (new_profile_name == self.TEMPLATE_PROFILE_NAME
                or old_profile_name == self.TEMPLATE_PROFILE_NAME):
            logger.warning(
                "Refusing rename involving template profile: %s -> %s",
                old_profile_name, new_profile_name)
            return
        logger.info(f"Rename profile {old_profile_name} to {new_profile_name}")
        shutil.move(Path(DEFAULT_JSON.parent, old_profile_name),
                    Path(DEFAULT_JSON.parent, new_profile_name))
        self.profiles.remove(old_profile_name)
        self.profiles.append(new_profile_name)

        if self.curr_profile_name.get() == old_profile_name:
            self.curr_profile_name.set(new_profile_name)
            # Keep the write path in sync — without this, every config
            # write after renaming the ACTIVE profile would target the
            # old (moved-away) directory and fail, silently losing all
            # subsequent settings changes for the session.
            self.curr_profile_path = Path(DEFAULT_JSON.parent,
                                          new_profile_name)
            # The on-disk default pointer must follow too, or the next
            # launch fails to find the renamed profile.
            if self._read_app_setting("default", None) == old_profile_name:
                self.write_app_setting("default", new_profile_name)



    def load_profile(self, profile_name: str) -> list[bool, Path]:
        profile_path = Path(DEFAULT_JSON.parent, profile_name)
        logger.info(f"Loading profile: {profile_path}")

        cursor_config_file = Path(profile_path, "cursor.json")
        mouse_bindings_file = Path(profile_path, "mouse_bindings.json")
        keyboard_bindings_file = Path(profile_path, "keyboard_bindings.json")

        if (not cursor_config_file.is_file()) or (
                not mouse_bindings_file.is_file()) or (
                    not keyboard_bindings_file.is_file()):
            logger.critical(
                f"{profile_path.as_posix()} Invalid configuration files or missing files, exit program..."
            )
            raise FileNotFoundError

        # Load cursor config
        with open(cursor_config_file) as f:
            self.config = json.load(f)

        # Load mouse bindings
        with open(mouse_bindings_file) as f:
            self.mouse_bindings = json.load(f)

        # Load keyboard bindings
        with open(keyboard_bindings_file) as f:
            self.keyboard_bindings = json.load(f)

        # Migration: an earlier head-pose-aware build saved gaze
        # calibrations with 8 coefficients. That fit turned out to be
        # ill-conditioned (head pose was nearly constant during the
        # 9-point routine -> huge spurious coefficients -> cursor
        # lands far from intended target). Clear any saved 8-coef
        # calibration here so the user is prompted to recalibrate.
        try:
            calib = self.config.get("gaze_calibration", None)
            if (isinstance(calib, dict)
                    and isinstance(calib.get("cx"), list)
                    and len(calib["cx"]) == 8):
                logger.warning(
                    "Detected legacy 8-coefficient gaze calibration; "
                    "clearing it. Please re-run the 9-point calibration.")
                self.config["gaze_calibration"] = None
        except Exception as e:
            logger.error("Calibration migration failed: %s", e)

        self.temp_config = copy.deepcopy(self.config)
        self.temp_mouse_bindings = copy.deepcopy(self.mouse_bindings)
        self.temp_keyboard_bindings = copy.deepcopy(self.keyboard_bindings)

        self.curr_profile_path = profile_path
        self.curr_profile_name.set(profile_name)

    def switch_profile(self, profile_name: str):
        logger.info(f"Switching to profile: {profile_name}")
        self.load_profile(profile_name)
        # Merge-write via write_app_setting — an earlier version dumped
        # {"default": name} over the whole file, destroying every other
        # app-wide setting (language, verbose_logging, ...) on every
        # profile switch.
        self.write_app_setting("default", profile_name)

    # ------------------------------- BASIC CONFIG ------------------------------- #

    def set_temp_config(self, field: str, value):
        logger.info(f"Setting {field} to {value}")
        self.temp_config[field] = value
        self.unsave_configs = True

    def write_config_file(self):
        cursor_config_file = Path(self.curr_profile_path, "cursor.json")
        logger.info(f"Writing config file {cursor_config_file}")
        with open(cursor_config_file, 'w') as f:
            json.dump(self.config, f, indent=4, separators=(', ', ': '))

    def apply_config(self):
        logger.info("Applying config")
        self.config = copy.deepcopy(self.temp_config)
        self.write_config_file()
        self.unsave_configs = False

    # ------------------------------ MOUSE BINDINGS CONFIG ----------------------------- #

    def set_temp_mouse_binding(self, gesture, device: str, action: str,
                               threshold: float, trigger_type: str):

        logger.info(
            "setting keybind for gesture: %s, device: %s, key: %s, threshold: %s, trigger_type: %s",
            gesture, device, action, threshold, trigger_type)

        # Remove duplicate keybinds
        self.remove_temp_mouse_binding(device, action)

        # Assign
        self.temp_mouse_bindings[gesture] = [
            device, action, float(threshold), trigger_type
        ]
        self.unsave_mouse_bindings = True

    def remove_temp_mouse_binding(self, device: str, action: str):
        logger.info(
            f"remove_temp_mouse_binding for device: {device}, key: {action}")
        out_keybinds = {}
        for key, vals in self.temp_mouse_bindings.items():
            if (device == vals[0]) and (action == vals[1]):
                continue
            out_keybinds[key] = vals
        self.temp_mouse_bindings = out_keybinds
        self.unsave_mouse_bindings = True

    def apply_mouse_bindings(self):
        logger.info("Applying keybinds")
        self.mouse_bindings = copy.deepcopy(self.temp_mouse_bindings)
        self.write_mouse_bindings_file()
        self.unsave_mouse_bindings = False

    def write_mouse_bindings_file(self):
        mouse_bindings_file = Path(self.curr_profile_path,
                                   "mouse_bindings.json")
        logger.info(f"Writing keybinds file {mouse_bindings_file}")

        with open(mouse_bindings_file, 'w') as f:
            out_json = dict(sorted(self.mouse_bindings.items()))
            json.dump(out_json, f, indent=4, separators=(', ', ': '))

    # ------------------------------ KEYBOARD BINDINGS CONFIG ----------------------------- #

    def set_temp_keyboard_binding(self, device: str, key_action: str,
                                  gesture: str, threshold: float,
                                  trigger_type: str):
        logger.info(
            "setting keybind for gesture: %s, device: %s, key: %s, threshold: %s, trigger_type: %s",
            gesture, device, key_action, threshold, trigger_type)

        # Remove duplicate keybinds
        self.remove_temp_keyboard_binding(device, key_action, gesture)

        # Assign
        self.temp_keyboard_bindings[gesture] = [
            device, key_action,
            float(threshold), trigger_type
        ]
        self.unsave_keyboard_bindings = True

    def remove_temp_keyboard_binding(self,
                                     device: str,
                                     key_action: str = "None",
                                     gesture: str = "None"):
        """Remove binding from config by providing either key_action or gesture.
        """

        logger.info(
            f"remove_temp_keyboard_binding for device: {device}, key: {key_action} or gesture {gesture}"
        )

        out_keybinds = {}
        for ges, vals in self.temp_keyboard_bindings.items():
            if (gesture == ges):
                continue
            if (key_action == vals[1]):
                continue

            out_keybinds[ges] = vals

        self.temp_keyboard_bindings = out_keybinds

        self.unsave_keyboard_bindings = True
        return

    def apply_keyboard_bindings(self):
        logger.info("Applying keyboard bindings")

        self.keyboard_bindings = copy.deepcopy(self.temp_keyboard_bindings)
        self.write_keyboard_bindings_file()
        self.unsave_keyboard_bindings = False

    def write_keyboard_bindings_file(self):
        keyboard_bindings_file = Path(self.curr_profile_path,
                                      "keyboard_bindings.json")
        logger.info(f"Writing keyboard bindings file {keyboard_bindings_file}")

        with open(keyboard_bindings_file, 'w') as f:
            out_json = dict(sorted(self.keyboard_bindings.items()))
            json.dump(out_json, f, indent=4, separators=(', ', ': '))

    # ---------------------------------------------------------------------------- #
    def apply_all(self):
        self.apply_config()
        self.apply_mouse_bindings()
        self.apply_keyboard_bindings()

    # ----------------------------- EXTENSIONS (app-wide) ----------------------------- #
    # Extensions data (gaze launcher items, clipboard history, etc.) is
    # app-wide (NOT per-profile) — stored in configs/extensions.json.

    def _extensions_path(self) -> Path:
        # Must go through paths like every other config file. While this
        # was CWD-relative it kept reading/writing the INSTALL dir after
        # everything else moved to %LOCALAPPDATA%: the seeded copy was
        # dead, backups captured no launcher data at all, and in a
        # read-only install location every launcher edit was silently
        # dropped by write_extensions' own except.
        return paths.configs_dir() / "extensions.json"

    def read_extensions(self) -> dict:
        try:
            p = self._extensions_path()
            if p.is_file():
                with open(p, encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.error("read_extensions failed: %s", e)
        return {}

    def write_extensions(self, data: dict) -> None:
        try:
            with open(self._extensions_path(), "w",
                      encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error("write_extensions failed: %s", e)

    def destroy(self):
        logger.info("Destory")
