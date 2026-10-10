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

import ctypes
import logging
from pathlib import Path


def install_fonts(font_dir: str) -> None:
    font_dir = Path(font_dir)
    gdi32 = ctypes.WinDLL('gdi32')

    for font_file in font_dir.glob("*.ttf"):
        logging.info(f"Installing font {font_file.as_posix()}")
        # FR_PRIVATE (0x10): load the font into THIS process only so it
        # is auto-unloaded — and the .ttf file unlocked — when the
        # process exits. With the old global AddFontResourceW the font
        # could stay registered and keep the file locked, so the install
        # folder couldn't be deleted even after the app had exited.
        gdi32.AddFontResourceExW(font_file.as_posix(), 0x10, 0)


def remove_fonts(font_dir: str) -> None:
    font_dir = Path(font_dir)
    gdi32 = ctypes.WinDLL('gdi32')
    for font_file in font_dir.glob("*.ttf"):
        logging.info(f"Removing font {font_file.as_posix()}")
        gdi32.RemoveFontResourceExW(font_file.as_posix(), 0x10, 0)
