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
"""Entry point for the crash-sentinel process (GazeFace_Sub.exe).

Ships as its OWN small exe rather than reusing GazeFace.exe with a flag,
so it shows up in Task Manager under a clear, distinct name instead of a
second "GazeFace.exe" that looks like malware. It imports none of the GUI
/ camera / MediaPipe stack — only enough to watch a pid and relaunch the
main app. See src/sentinel.py for the logic.

Usage: GazeFace_Sub.exe <main_pid>
"""

import logging
import logging.handlers
import sys

from src import paths


def _setup_logging():
    """Append to the same log file the main app uses, so a relaunch is
    visible in one place. Best-effort — never let logging setup stop the
    sentinel from doing its job."""
    try:
        paths.ensure_user_data()
        handler = logging.handlers.RotatingFileHandler(
            str(paths.log_path()), maxBytes=2_000_000, backupCount=4,
            encoding="utf-8", delay=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            handlers=[handler])
    except Exception:
        pass


if __name__ == "__main__":
    _setup_logging()
    try:
        pid = int(sys.argv[1])
    except Exception:
        logging.error("sentinel: missing/invalid main pid arg")
        sys.exit(1)
    try:
        from src import sentinel
        sentinel.run_sentinel(pid)
    except Exception as e:
        logging.error("sentinel run failed: %s", e, exc_info=e)
    sys.exit(0)
