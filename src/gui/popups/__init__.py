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
"""Popup windows: gesture list editor, per-action options, per-gesture options."""

# GestureListPopup was removed when the per-row gesture-selection
# popup absorbed its functionality. The module file is left in place
# in case we want to resurrect a global "manage gestures" view later.
from .action_options_popup import ActionOptionsPopup
from .gesture_options_popup import GestureOptionsPopup
