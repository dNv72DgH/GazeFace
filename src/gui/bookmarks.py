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
"""Bookmarked action rows.

The user can star any function (action) on the Mouse / Extensions
binding pages. The starred actions are collected on a dedicated
"Bookmarks" page that reuses the exact same binding UI, so a person can
keep the handful of settings they tune often in one short list.

The bookmark set is app-wide (stored in default.json, not per profile)
so it does not change when the active profile does. Any widget can
subscribe to changes via add_listener()."""

import logging

from src.config_manager import ConfigManager

logger = logging.getLogger("Bookmarks")

_listeners = []


def load() -> list:
    """Bookmarked action names, in the order they were added."""
    try:
        return list(ConfigManager()._read_app_setting("bookmarks", []) or [])
    except Exception:
        return []


def is_bookmarked(action: str) -> bool:
    return action in load()


def toggle(action: str) -> bool:
    """Add or remove `action`; returns the new bookmarked state."""
    lst = load()
    if action in lst:
        lst.remove(action)
        now = False
    else:
        lst.append(action)
        now = True
    try:
        ConfigManager().write_app_setting("bookmarks", lst)
    except Exception as e:
        logger.error("save bookmarks failed: %s", e)
    _notify()
    return now


def add_listener(cb) -> None:
    if cb not in _listeners:
        _listeners.append(cb)


def _notify() -> None:
    for cb in list(_listeners):
        try:
            cb()
        except Exception as e:
            logger.error("bookmark listener failed: %s", e)
