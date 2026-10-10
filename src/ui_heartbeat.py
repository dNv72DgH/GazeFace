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
"""Liveness beacon for the tk main thread.

The tk main thread runs the GUI *and* drives face detection, so when it
blocks, head tracking silently freezes while every worker thread keeps
reporting healthy heartbeats — the camera reader still pulls frames and
MouseController still loops, they just consume face coordinates that
nobody is updating any more. That failure mode is invisible to the
watchdog's camera/no-face probes (a stale landmark is not `None`), which
is how an ~8 minute stall once went unnoticed and unrecovered.

MainGui calls `beat()` from a short tk `after` tick; the watchdog thread
reads `age_s()`. Deliberately module-level and lock-free: a float
assignment/read is atomic under the GIL, and this must stay cheap enough
to run every second forever.
"""

import time

_last_ts = 0.0


def beat():
    """Record that the tk main thread is still servicing its event loop."""
    global _last_ts
    _last_ts = time.monotonic()


def started() -> bool:
    """False until the first beat — the UI may still be building."""
    return _last_ts > 0.0


def age_s() -> float:
    """Seconds since the last beat (0.0 before the first one)."""
    if _last_ts <= 0.0:
        return 0.0
    return time.monotonic() - _last_ts
