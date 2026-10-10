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
"""Lightweight runtime translation.

`t("English source text")` returns the localized string for the currently
selected language. The translation tables live in `src.i18n_strings`.
Language is chosen once at startup from the persisted config; changes in
the UI require an app restart (the Advanced page surfaces that).
"""

from src.i18n_strings import TRANSLATIONS

SUPPORTED_LANGUAGES = ["en", "ja"]
DEFAULT_LANGUAGE = "en"

_current_language = DEFAULT_LANGUAGE


def set_language(lang: str) -> None:
    global _current_language
    if lang not in SUPPORTED_LANGUAGES:
        lang = DEFAULT_LANGUAGE
    _current_language = lang


def get_language() -> str:
    return _current_language


def t(text: str) -> str:
    """Translate a source string. Falls back to the source on miss."""
    if _current_language == "en":
        return text
    table = TRANSLATIONS.get(_current_language, {})
    return table.get(text, text)
