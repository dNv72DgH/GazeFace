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
"""The GazeFace icon on every window the app opens.

Windows that never set an icon (the exit confirmation, option windows)
showed CustomTkinter's own blue logo instead. That is not an omission
that can be fixed window by window: CTkToplevel schedules
iconbitmap(<CustomTkinter_icon_Windows.ico>) 200 ms after it is created,
unconditionally, so even a window that set our icon straight away had it
replaced a moment later.

install() fixes it at the source - any request for CustomTkinter's icon
file is answered with ours - and makes ours the default for plain Tk
toplevels too. Call it once, before any window is created.
"""

import logging

import customtkinter

from src import paths

logger = logging.getLogger("AppIcon")

_CTK_ICON = "customtkinter_icon_windows.ico"
_installed = False


def icon_path() -> str:
    return str(paths.install_dir() / "assets" / "images" / "icon.ico")


def _redirect(original):
    def iconbitmap(self, bitmap=None, default=None):
        import os
        if bitmap and os.path.basename(str(bitmap)).lower() == _CTK_ICON:
            bitmap = icon_path()
        return original(self, bitmap, default)
    return iconbitmap


def install():
    """Patch CustomTkinter's windows to use the GazeFace icon."""
    global _installed
    if _installed:
        return
    _installed = True
    for cls in (customtkinter.CTkToplevel, customtkinter.CTk):
        # CTk's scheduled call goes through `iconbitmap`, which on these
        # classes still resolves to tkinter's Wm alias, not to their own
        # wm_iconbitmap override - so both names are patched.
        patched = _redirect(cls.wm_iconbitmap)
        cls.wm_iconbitmap = patched
        cls.iconbitmap = patched


def apply_default(root):
    """Our icon on `root` and, as the Tk default, on every toplevel
    created after it (including plain tk.Toplevel windows)."""
    try:
        root.iconbitmap(icon_path(), default=icon_path())
    except Exception as e:
        logger.warning("could not set the app icon: %s", e)
