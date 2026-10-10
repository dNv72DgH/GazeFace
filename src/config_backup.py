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
"""Daily config backups, and restore-on-repeated-crash.

Every config write in the app is a truncating open() + json.dump, so a
crash or power cut mid-write leaves a truncated file. That is not
theoretical: write_app_setting reads-merges-writes, so a corrupt
default.json silently degrades to {} and the next write wipes every
app-wide setting. A profile whose cursor.json is truncated fails
load_profile outright, which is a startup kill.

So: one backup per calendar day, five kept. When the app restarts
itself repeatedly in a short window (i.e. it comes up, breaks, restarts,
comes up, breaks...), that pattern points at config the app cannot
survive reading — restore_next_candidate() walks the backups newest
first, and finally falls back to the factory template.
"""

import json
import logging
import shutil
import time
from pathlib import Path

from src import paths

logger = logging.getLogger("ConfigBackup")

MAX_BACKUPS = 5
# Marker of which backups a recovery pass has already tried, so a second
# failure moves on to the next one instead of retrying the same bad copy.
_TRIED_FILE = "restore_tried.json"


def _stamp(now=None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now))


def list_backups() -> list:
    """Existing backup dirs, newest first."""
    root = paths.backups_dir()
    if not root.is_dir():
        return []
    out = [d for d in root.iterdir()
           if d.is_dir() and (d / "default.json").is_file()]
    out.sort(key=lambda d: d.name, reverse=True)
    return out


def backup_daily(now=None) -> bool:
    """Snapshot the configs once per calendar day. True if one was made."""
    try:
        cfg = paths.configs_dir()
        if not (cfg / "default.json").is_file():
            return False                    # nothing worth saving yet
        dst = paths.backups_dir() / _stamp(now)
        if dst.exists():
            return False                    # already have today's
        dst.parent.mkdir(parents=True, exist_ok=True)
        paths._copy_configs(cfg, dst)
        logger.info("Config backup written: %s", dst)
        _prune()
        return True
    except Exception as e:
        logger.error("backup_daily failed: %s", e, exc_info=e)
        return False


def _prune():
    for old in list_backups()[MAX_BACKUPS:]:
        try:
            shutil.rmtree(old)
            logger.info("Pruned old backup %s", old.name)
        except Exception as e:
            logger.error("prune %s failed: %s", old.name, e)


def _tried() -> set:
    try:
        p = paths.backups_dir() / _TRIED_FILE
        if p.is_file():
            with open(p, encoding="utf-8") as f:
                return set(json.load(f))
    except Exception:
        pass
    return set()


def _mark_tried(name: str):
    try:
        p = paths.backups_dir() / _TRIED_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        names = _tried()
        names.add(name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(sorted(names), f)
    except Exception as e:
        logger.error("_mark_tried failed: %s", e)


def clear_tried():
    """Called once the app has been up long enough to be considered
    healthy — the next bad spell starts its search from the newest
    backup again."""
    try:
        p = paths.backups_dir() / _TRIED_FILE
        if p.is_file():
            p.unlink()
    except Exception:
        pass


def _swap_in(src: Path) -> bool:
    """Replace the live configs with `src`, keeping the broken copy.

    Build the replacement FIRST, then swap. Renaming the live tree away
    before copying left a window with no configs at all: if the copy
    then failed (disk full, AV lock, a long path), the app was left with
    no settings, its ConfigManager pointing into a renamed tree, and
    every later write failing silently.
    """
    cfg = paths.configs_dir()
    staged = cfg.with_name("configs_new")
    broken = cfg.with_name("configs_broken")
    try:
        if staged.exists():
            shutil.rmtree(staged, ignore_errors=True)
        paths._copy_configs(src, staged)        # nothing live touched yet
        if cfg.is_dir():
            if broken.exists():
                shutil.rmtree(broken, ignore_errors=True)
            # Keep the corrupt tree next to the live one rather than
            # deleting it: if the restore turns out to be the wrong
            # call, the user's most recent settings are still on disk.
            cfg.rename(broken)
        staged.rename(cfg)
        return True
    except Exception as e:
        logger.error("restore swap failed: %s", e, exc_info=e)
        # Put the live tree back if we got as far as moving it aside.
        try:
            if not cfg.exists() and broken.is_dir():
                broken.rename(cfg)
                logger.warning("restore failed — original configs restored")
        except Exception:
            logger.error("could not roll back the configs swap")
        shutil.rmtree(staged, ignore_errors=True)
        return False


def restore_next_candidate() -> str:
    """Restore the newest not-yet-tried backup, else the factory template.

    Returns a short description of what was restored, or "" if there was
    nothing left to try.
    """
    tried = _tried()
    for b in list_backups():
        if b.name in tried:
            continue
        # Mark only on success. A swap can fail for reasons that say
        # nothing about the backup itself (a sharing violation on the
        # rename is likely — this runs with the app fully live), and
        # burning the candidate for that would skip a good copy. On
        # failure, fall through and try the next one.
        if _swap_in(b):
            _mark_tried(b.name)
            logger.warning("Restored config backup %s", b.name)
            return "backup %s" % b.name
        logger.error("Backup %s could not be swapped in — trying next",
                     b.name)

    # Every backup exhausted — fall back to the shipped template.
    if "__factory__" in tried:
        return ""
    src = paths.bundled_configs_dir()
    if src.is_dir() and (src / "default.json").is_file() and _swap_in(src):
        _mark_tried("__factory__")
        logger.warning("Restored factory default configs")
        return "factory defaults"
    return ""
