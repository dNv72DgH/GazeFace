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

# Right-Left swapped (camera image is mirrored so MediaPipe's "Left"
# is the user's right side, and vice versa). Names not renamed here
# stay as the raw MediaPipe identifiers — they're still valid keys
# but won't have friendly UI labels.
blendshape_names = [
    "None",                     # 0
    "Lower right eyebrow",      # 1  was browDownLeft
    "Lower left eyebrow",       # 2  was browDownRight
    "browInnerUp",              # 3
    "Raise right eyebrow",      # 4  was browOuterUpLeft
    "Raise left eyebrow",       # 5  was browOuterUpRight
    "Cheek puff",               # 6  was cheekPuff (no L/R)
    "cheekSquintRight",         # 7
    "cheekSquintLeft",          # 8
    "Close right eye",          # 9  was eyeBlinkLeft
    "Close left eye",           # 10 was eyeBlinkRight
    "eyeLookDownRight",         # 11
    "eyeLookDownLeft",          # 12
    "eyeLookInRight",           # 13
    "eyeLookInLeft",            # 14
    "eyeLookOutRight",          # 15
    "eyeLookOutLeft",           # 16
    "eyeLookUpRight",           # 17
    "eyeLookUpLeft",            # 18
    "Squint right eye",         # 19 was eyeSquintLeft
    "Squint left eye",          # 20 was eyeSquintRight
    "Wide right eye",           # 21 was eyeWideLeft
    "Wide left eye",            # 22 was eyeWideRight
    "Jaw forward",              # 23 was jawForward
    "Jaw right",                # 24 was jawLeft
    "Open mouth",               # 25 was jawOpen
    "Jaw left",                 # 26 was jawRight
    "mouthClose",               # 27
    "mouthDimpleRight",         # 28
    "mouthDimpleLeft",          # 29
    "Frown right",              # 30 was mouthFrownLeft
    "Frown left",               # 31 was mouthFrownRight
    "mouthFunnel",              # 32
    "Mouth right",              # 33 was mouthLeft
    "mouthLowerDownRight",      # 34
    "mouthLowerDownLeft",       # 35
    "mouthPressRight",          # 36
    "mouthPressLeft",           # 37
    "Mouth pucker",             # 38 was mouthPucker (no L/R)
    "Mouth left",               # 39 was mouthRight
    "Roll lower mouth",         # 40 was mouthRollLower
    "Roll upper mouth",         # 41 was mouthRollUpper
    "mouthShrugLower",          # 42
    "mouthShrugUpper",          # 43
    "Smile right",              # 44 was mouthSmileLeft
    "Smile left",               # 45 was mouthSmileRight
    "mouthStretchRight",        # 46
    "mouthStretchLeft",         # 47
    "mouthUpperUpRight",        # 48
    "mouthUpperUpLeft",         # 49
    "noseSneerRight",           # 50
    "noseSneerLeft",            # 51
]
blendshape_indices = {name: i for i, name in enumerate(blendshape_names)}

# Order here defines layout order on the Mouse-binding page (col 0 first,
# then col 1, with MAX_ROWS items per column). The current layout is:
#   col 0:  Mouse left click   /  Mouse right click  /  Mouse middle click
#   col 1:  Mouse pause/unpause / Pointer jump (gaze) / Reset to center /
#           Switch focus between monitors
available_actions = {
    "Mouse left click": ["mouse", "left"],
    "Mouse right click": ["mouse", "right"],
    "Mouse middle click": ["mouse", "middle"],
    "Mouse double click": ["mouse", "double"],
    "Mouse pause / unpause": ["mouse", "pause"],
    "Gaze Jump": ["mouse", "pointer_jump"],
    "Gaze move": ["mouse", "gaze_move"],
    "Gaze launcher": ["mouse", "gaze_launcher"],
    "Gaze clipboard history": ["mouse", "gaze_clipboard"],
    "Gaze task switcher": ["mouse", "gaze_taskswitch"],
    "Reset cursor to center": ["mouse", "reset"],
    "Switch focus between monitors": ["mouse", "cycle"],
}
available_actions_keys = list(available_actions.keys())
available_actions_values = list(available_actions.values())

# The Mouse-binding page shows only genuine mouse actions; the gaze
# extension launchers are bound on the Extensions page with the SAME
# binding UI. Both pages share the one mouse_bindings store
# (device="mouse"); they just render disjoint subsets of the actions.
EXTENSION_ACTION_KEYS = [
    "Gaze launcher",
    "Gaze clipboard history",
    "Gaze task switcher",
]
MOUSE_ACTION_KEYS = [k for k in available_actions_keys
                     if k not in EXTENSION_ACTION_KEYS]

available_gestures = {
    "None": "assets/images/dropdowns/None.png",
    "Open mouth": "assets/images/dropdowns/Open mouth.png",
    "Mouth left": "assets/images/dropdowns/Mouth left.png",
    "Mouth right": "assets/images/dropdowns/Mouth right.png",
    "Roll lower mouth": "assets/images/dropdowns/Roll lower mouth.png",
    "Raise left eyebrow": "assets/images/dropdowns/Raise left eyebrow.png",
    "Lower left eyebrow": "assets/images/dropdowns/Lower left eyebrow.png",
    "Raise right eyebrow": "assets/images/dropdowns/Raise right eyebrow.png",
    "Lower right eyebrow": "assets/images/dropdowns/Lower right eyebrow.png",
    "Close left eye": "assets/images/dropdowns/Close left eye.png",
    "Close right eye": "assets/images/dropdowns/Close right eye.png",
    # Additional MediaPipe blendshapes. Disabled by default — surface
    # them via Advanced > Gesture List.
    "Cheek puff": "assets/images/dropdowns/Cheek puff.png",
    "Smile left": "assets/images/dropdowns/Smile left.png",
    "Smile right": "assets/images/dropdowns/Smile right.png",
    "Frown left": "assets/images/dropdowns/Frown left.png",
    "Frown right": "assets/images/dropdowns/Frown right.png",
    "Mouth pucker": "assets/images/dropdowns/Mouth pucker.png",
    "Jaw forward": "assets/images/dropdowns/Jaw forward.png",
    "Jaw left": "assets/images/dropdowns/Jaw left.png",
    "Jaw right": "assets/images/dropdowns/Jaw right.png",
    "Squint left eye": "assets/images/dropdowns/Squint left eye.png",
    "Squint right eye": "assets/images/dropdowns/Squint right eye.png",
    "Wide left eye": "assets/images/dropdowns/Wide left eye.png",
    "Wide right eye": "assets/images/dropdowns/Wide right eye.png",
}

# The subset of available_gestures shown in the dropdown by default
# (overridable via Advanced > Gesture List). "None" is always shown.
DEFAULT_ENABLED_GESTURES = [
    "Open mouth",
    "Mouth left",
    "Mouth right",
    "Roll lower mouth",
    "Raise left eyebrow",
    "Lower left eyebrow",
    "Raise right eyebrow",
    "Lower right eyebrow",
    "Close left eye",
    "Close right eye",
]
for k, v in available_gestures.items():
    assert k in blendshape_names, f"{k} not in blendshape_names"
available_gestures_keys = list(available_gestures.keys())

# Map tkinter character to valid pyautogui character
keyboard_keys = {
    # Numbers
    "0": "0",
    "1": "1",
    "2": "2",
    "3": "3",
    "4": "4",
    "5": "5",
    "6": "6",
    "7": "7",
    "8": "8",
    "9": "9",

    # Functions
    "f1": "f1",
    "f2": "f2",
    "f3": "f3",
    "f4": "f4",
    "f5": "f5",
    "f6": "f6",
    "f7": "f7",
    "f8": "f8",
    "f9": "f9",
    "f10": "f10",
    "f11": "f11",
    "f12": "f12",

    # Letters
    "a": "a",
    "b": "b",
    "c": "c",
    "d": "d",
    "e": "e",
    "f": "f",
    "g": "g",
    "h": "h",
    "i": "i",
    "j": "j",
    "k": "k",
    "l": "l",
    "m": "m",
    "n": "n",
    "o": "o",
    "p": "p",
    "q": "q",
    "r": "r",
    "s": "s",
    "t": "t",
    "u": "u",
    "v": "v",
    "w": "w",
    "x": "x",
    "y": "y",
    "z": "z",

    # Special characters
    "exclam": "!",
    "at": "@",
    "numbersign": "#",
    "dollar": "$",
    "percent": "%",
    "asciicircum": "^",
    "ampersand": "&",
    "asterisk": "*",
    "parenleft": "(",
    "parenright": ")",
    "minus": "-",
    "plus": "+",
    "underscore": "_",
    "equal": "=",
    "bracketleft": "[",
    "bracketright": "]",
    "braceleft": "{",
    "braceright": "}",
    "backslash": "\\",
    "semicolon": ";",
    "colon": ":",
    "apostrophe": "'",
    "quotedbl": "\"",
    "grave": "`",
    "comma": ",",
    "less": "<",
    "greater": ">",
    "question": "?",
    "slash": "/",
    "asciitilde": "~",
    "bar": "|",
    "period": ".",

    # Miscellaneous
    "return": "enter",
    "kp_enter": "enter",
    "escape": "escape",
    "backspace": "backspace",
    "tab": "tab",
    "space": "space",
    "delete": "delete",
    "insert": "insert",
    "home": "home",
    "end": "end",
    "next": "pagedown",
    "prior": "pageup",
    "print": "printscreen",
    "scroll_lock": "scrolllock",
    "pause": "pause",
    "menu": "apps",
    "win_l": "win",
    "win_r": "win",
    "caps_lock": "capslock",
    "shift_l": "shiftleft",
    "shift_r": "shiftright",
    "control_l": "ctrlleft",
    "control_r": "ctrlright",
    "alt_l": "altleft",
    "alt_r": "altright",
    "num_lock": "numlock",

    # Directions
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
}
available_keyboard_keys = list(keyboard_keys.keys())
