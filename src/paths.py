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
"""Where GazeFace keeps user data.

Historically every path in the app was bare-relative ("configs/...",
"log.txt"), so they resolved against the process CWD and only worked
because the exe happened to be launched from its install directory.
That had three consequences worth fixing:

  * settings lived next to the exe, so an install under Program Files
    (or any read-only location) could not save at all;
  * `build.spec` ships `configs/` as bundle data, so re-installing over
    an existing folder overwrote the user's real profiles with the
    build-time snapshot;
  * a self-restart had to reproduce the CWD exactly or the app would
    die on startup looking for its own config.

User data now lives under %LOCALAPPDATA%\\GazeFace (machine-local, not
roamed — this is per-machine camera/gesture tuning, not something you
want synced across PCs). The copy shipped next to the exe is demoted to
a factory template that seeds the user copy on first run.

Import this before anything that touches config or logging: run_app
calls ensure_user_data() at module scope, ahead of the log handler.
"""

import json
import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger("Paths")

APP_DIR_NAME = "GazeFace"

# Regenerable icon cache — not user data, and big. Never copied when
# seeding or backing up; launcher_icons rebuilds entries on demand.
_ICON_CACHE_DIR = "launcher_icons"


def install_dir() -> Path:
    """The directory the app ships in (frozen: the exe's folder)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # src/paths.py -> project root
    return Path(__file__).resolve().parent.parent


def signin_data_dir() -> Path:
    """Data for the copy Windows starts on the sign-in / lock screen.

    That copy runs as SYSTEM, whose profile has none of the user's
    settings, so it uses this machine-wide folder instead; the user's own
    copy refreshes the settings in it (src/at_startup.py). ProgramData
    lets an ordinary user create a folder they then own, so no elevation
    is needed to keep it current.
    """
    base = os.environ.get("ProgramData") or r"C:\ProgramData"
    return Path(base) / APP_DIR_NAME / "signin"


def _is_system_account() -> bool:
    local = (os.environ.get("LOCALAPPDATA") or "").lower()
    return ("\\systemprofile\\" in local
            or (os.environ.get("USERNAME") or "").endswith("$"))


def user_data_dir() -> Path:
    if _is_system_account():
        return signin_data_dir()
    base = os.environ.get("LOCALAPPDATA") or ""
    if not base:
        # No LOCALAPPDATA (unusual, e.g. some service contexts) — fall
        # back to the home dir rather than writing next to the exe.
        base = str(Path.home())
    return Path(base) / APP_DIR_NAME


def configs_dir() -> Path:
    return user_data_dir() / "configs"


def backups_dir() -> Path:
    return user_data_dir() / "backups"


def log_path() -> Path:
    return user_data_dir() / "log.txt"


def restart_state_path() -> Path:
    return user_data_dir() / "restart_state.json"


def sentinel_marker_path() -> Path:
    return user_data_dir() / "sentinel_state.json"


def bundled_configs_dir() -> Path:
    """The factory template that ships next to the exe.

    Deliberately NOT "configs": build.spec bundles the repo's configs
    tree, so a build carries a snapshot of whatever profiles existed on
    the build machine. Shipping that as `configs` meant copying a new
    build over an install silently replaced the user's real settings
    with build-time junk. Under its own name it can never collide with
    a live install's data.
    """
    return install_dir() / "configs_template"


def legacy_appdata_configs_dir() -> Path:
    """Where settings lived while the app was called WorkFace.

    The rename to GazeFace moved the user-data directory with it, and
    that directory holds every profile, gesture threshold and camera
    calibration the user has built up. Nothing about a rename should
    cost them that, so the old directory is treated exactly like an old
    install: a seeding source that wins over the shipped template.

    The old copy is left in place rather than moved. A rename is one
    filesystem call away from losing the lot if it half-completes, and a
    user who downgrades should still find their settings where the old
    build looks for them.
    """
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "WorkFace" / "configs"


def legacy_configs_dir() -> Path:
    """Where settings lived before the move to %LOCALAPPDATA%.

    Still the real data for anyone upgrading, so it wins over the
    template when seeding.
    """
    return install_dir() / "configs"


def _copy_configs(src: Path, dst: Path):
    """Copy a configs tree, skipping the regenerable icon cache."""
    shutil.copytree(
        src, dst,
        ignore=shutil.ignore_patterns(_ICON_CACHE_DIR),
        dirs_exist_ok=True)


# Choices made in the installer, handed to the app's first run: the
# installer writes {app}\install_defaults.json and a FRESH settings folder
# takes them over (an existing user's settings are never touched). Only
# app-wide keys of default.json are accepted.
INSTALL_DEFAULTS = "install_defaults.json"
_INSTALL_DEFAULT_KEYS = {"language": ("en", "ja")}


def _apply_install_defaults(cfg: Path) -> None:
    src = install_dir() / INSTALL_DEFAULTS
    if not src.is_file():
        return
    try:
        with open(src, encoding="utf-8-sig") as f:
            given = json.load(f)
        target = cfg / "default.json"
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
        changed = False
        for key, allowed in _INSTALL_DEFAULT_KEYS.items():
            if key in given and given[key] in allowed:
                data[key] = given[key]
                changed = True
        if changed:
            with open(target, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            logger.info("Applied installer defaults %s", given)
    except Exception as e:
        logger.error("install defaults not applied: %s", e)


def ensure_user_data() -> Path:
    """Make sure the user-data configs exist, seeding them on first run.

    Returns the configs dir. Safe to call repeatedly; only the first
    call (no user copy yet) does any copying. Never overwrites an
    existing user copy — that is the whole point of the split.
    """
    cfg = configs_dir()
    try:
        if cfg.is_dir() and (cfg / "default.json").is_file():
            return cfg
        cfg.parent.mkdir(parents=True, exist_ok=True)
        # Order matters: an existing install's settings are the user's
        # real data and must win over the shipped template. Only a
        # genuinely fresh install falls through to the template.
        for src, what in ((legacy_appdata_configs_dir(),
                           "pre-rename user data"),
                          (legacy_configs_dir(), "existing install"),
                          (bundled_configs_dir(), "factory template")):
            if src.is_dir() and (src / "default.json").is_file():
                _copy_configs(src, cfg)
                logger.info("Seeded user data from %s (%s) -> %s",
                            src, what, cfg)
                if src == bundled_configs_dir():
                    _apply_install_defaults(cfg)
                return cfg
        # Nothing to copy (unusual). Leave the dir; the caller's own
        # missing-config path reports it properly.
        cfg.mkdir(parents=True, exist_ok=True)
        logger.error("No configs to seed from (tried %s, %s, %s)",
                     legacy_appdata_configs_dir(), legacy_configs_dir(),
                     bundled_configs_dir())
    except Exception as e:
        logger.error("ensure_user_data failed: %s", e, exc_info=e)
    return cfg
