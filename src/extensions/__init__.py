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
"""Gaze-app registry.

A "gaze app" (launcher / clipboard history / task switcher) is a
fullscreen gaze-grid tool. At most one is active at a time. Each exposes
the same minimal interface so Keybinder can drive whichever is open:

    is_active()  -> classmethod bool
    instance()   -> classmethod -> singleton
    gesture_map()-> {gesture_name: action}   (its local map)
    on_action(a) -> handle confirm/back/forward/exit/settings
"""


def active_gaze_app():
    """Return the currently-open gaze app, or None."""
    try:
        from .launcher import Launcher
        if Launcher.is_active():
            return Launcher.instance()
    except Exception:
        pass
    try:
        from .clipboard_history import ClipboardHistory
        if ClipboardHistory.is_active():
            return ClipboardHistory.instance()
    except Exception:
        pass
    try:
        from .task_switcher import TaskSwitcher
        if TaskSwitcher.is_active():
            return TaskSwitcher.instance()
    except Exception:
        pass
    return None
