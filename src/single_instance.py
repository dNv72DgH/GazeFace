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
"""The single-instance mutex.

Its own module because the handle has to be reachable from teardown code
that is NOT run_app. run_app is the PyInstaller entry script, so in a
frozen build it exists only as `__main__` and `import run_app` raises;
in dev it is worse than useless — `run_app` is not in sys.modules under
that name, so importing it RE-EXECUTES the whole module (a second
RotatingFileHandler on the same log file, a second "App starting" line)
and then hands back a fresh module object whose mutex handle is None.

Two instances fighting over one camera starve the DSHOW capture for
both, which is why the guard exists at all.
"""

import json
import logging
import os

MUTEX_NAME = "GazeFace_SingleInstance_Mutex"
ERROR_ALREADY_EXISTS = 183

_handle = None


def acquire(configs_dir, quiet=False, scope="") -> None:
    """Refuse to start a second copy. Exits the process if one exists.

    A named Windows mutex lives exactly as long as its owning process,
    even if that process crashed hard — no stale lock file to clean up.

    `scope` names a separate slot: the copy Windows starts on the secure
    desktop (sign-in screen, UAC prompt) runs alongside the everyday one,
    so it takes a mutex of its own. `quiet` exits without the "already
    running" box - for a launch Windows made, not the user.
    """
    global _handle
    import ctypes
    # Escape hatches, both checked before the mutex is created:
    #  * env var WORKFACE_ALLOW_MULTI (debug), or
    #  * "Allow multiple instances" in Advanced settings (read straight
    #    from default.json — ConfigManager hasn't started yet).
    # Neither escape hatch applies to a launch Windows made (quiet): at
    # sign-in it starts the assistive technology more than once - once for
    # the user's own list and again when it carries the sign-in screen's
    # copy over into the new session - and "allow multiple instances"
    # then let both run.
    if quiet:
        pass
    elif os.environ.get("WORKFACE_ALLOW_MULTI"):
        logging.warning("WORKFACE_ALLOW_MULTI set — skipping "
                        "single-instance guard")
        return
    if not quiet:
        try:
            with open(os.path.join(str(configs_dir), "default.json")) as f:
                if bool(json.load(f).get("allow_multi_instance", False)):
                    logging.warning("allow_multi_instance set — skipping "
                                    "single-instance guard")
                    return
        except Exception:
            pass

    kernel32 = ctypes.windll.kernel32
    name = MUTEX_NAME + ("_" + scope if scope else "")
    _handle = kernel32.CreateMutexW(None, False, name)
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        logging.error("Another instance is already running — exiting.")
        if quiet:
            os._exit(0)
        MB_ICONWARNING = 0x30
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                "GazeFace はすでに起動しています。\n"
                "GazeFace is already running.",
                "GazeFace", MB_ICONWARNING)
        except Exception:
            pass
        os._exit(0)


def release() -> None:
    """Drop the mutex so a replacement instance can take it.

    Windows frees it at process death anyway, but a restart's child sits
    in restart.wait_for_pid() until then — releasing it here shortens the
    handoff instead of making the new instance wait out the full
    six-second teardown budget.
    """
    global _handle
    if _handle is None:
        return
    try:
        import ctypes
        ctypes.windll.kernel32.CloseHandle(_handle)
    except Exception:
        pass
    _handle = None
