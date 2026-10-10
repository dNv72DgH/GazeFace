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
"""The lite mode flag, and what lite mode allows.

Lite mode is the copy of GazeFace that Windows starts on the secure
desktop - the sign-in / lock screen and UAC prompts (src/lite_main.py).
It is kept apart from lite_main so the shared code (mouse controller,
keybinder) can ask "am I lite?" without importing the lite entry point.
"""

ACTIVE = False

# Mouse actions lite mode runs. Everything else is dropped, above all
# anything that starts a program or opens a file: on the sign-in screen
# this process runs as SYSTEM, and whatever it starts would be a SYSTEM
# process that anyone at the keyboard could use without a password.
ALLOWED_MOUSE_ACTIONS = frozenset((
    "left", "right", "middle", "double",   # clicks / press-and-hold
    "pause",                               # face control on / off
    "pointer_jump",                        # Gaze Jump
    "gaze_move",                           # Gaze move (tk arrows)
    "reset",                               # pointer to screen centre
    "cycle",                               # next monitor
))


def filter_mouse_bindings(bindings: dict) -> dict:
    """Only the bindings lite mode runs. Keyboard bindings are dropped
    by the caller altogether."""
    out = {}
    for shape, v in (bindings or {}).items():
        try:
            if v[0] == "mouse" and v[1] in ALLOWED_MOUSE_ACTIONS:
                out[shape] = v
        except (IndexError, TypeError):
            continue
    return out
