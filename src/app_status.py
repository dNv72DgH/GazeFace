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
"""What the app is currently doing, for the left-pane status readout.

Before this, nothing told the user anything: the camera loop, the
watchdog and the FaceMesh rebuild all reported failures to log.txt and
nowhere else, so a 1 fps camera or a failed model rebuild looked exactly
like "the app stopped working for no reason". There was no error->GUI
channel of any kind to hook into, so this is it.

Producers (camera loop, watchdog, restart) call set_error/clear_error
from their own threads; the GUI polls read() from its tk tick — the same
shape as ui_heartbeat, and for the same reason: a lock-free module
global is safe under the GIL, costs nothing to poll, and cannot deadlock
a worker against the UI.

Errors are sticky until explicitly cleared, so a fault that resolves
itself still leaves the user something to read.
"""

import time

# state values
OK = "ok"
ERROR = "error"

_error = ""          # short user-facing reason, "" = no error
_action = ""         # what the app is doing about it ("", recovering, ...)
_since = 0.0


def set_error(msg: str, action: str = ""):
    """Report a fault. `action` is the remedy in progress, if any."""
    global _error, _action, _since
    if msg != _error:
        _since = time.monotonic()
    _error = msg or ""
    _action = action or ""


def set_action(action: str):
    """Update just the remedy line (keeps the current error text)."""
    global _action
    _action = action or ""


def set_restarting(reason: str = ""):
    set_error(_error or reason, "restarting")


def clear_error():
    global _error, _action, _since
    _error = ""
    _action = ""
    _since = 0.0


def read():
    """(state, error_msg, action_msg) — safe from any thread."""
    return (ERROR if _error else OK, _error, _action)


def error_age_s() -> float:
    return (time.monotonic() - _since) if _since else 0.0
