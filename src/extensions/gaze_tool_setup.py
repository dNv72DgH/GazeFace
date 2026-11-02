"""Setup message for the gaze grid tools (launcher / clipboard / task
switcher).

A tool opened with no way to pick a cell, or no way to leave, just sits
there until its 60 s idle timeout. Say why in the middle of the screen
instead. First match wins:

  1. no dwell-confirm, no confirm gesture and no exit gesture
  2. no dwell-confirm and no confirm gesture
  3. (launcher only) no items registered
"""

from src.i18n import t

NO_EXIT = ("The exit gesture is not set. Assign a facial expression in "
           "the feature options. This screen closes automatically in 60 "
           "seconds.")
NO_CONFIRM = ("The confirm gesture is not set. Assign a facial expression "
              "in the feature options. This screen closes automatically in "
              "60 seconds.")
NO_ITEMS = ("No items are registered. Register items in the feature "
            "options. This screen closes automatically in 60 seconds.")


def setup_message(gesture_map, confirm_ms, no_items=False):
    """The message to show for a tool's settings, or "" when it is
    usable. `gesture_map` is the tool's {gesture: action}."""
    actions = {a for a in (gesture_map or {}).values() if a}
    try:
        dwell = int(confirm_ms or 0) > 0
    except Exception:
        dwell = False
    if not dwell and "confirm" not in actions:
        return t(NO_CONFIRM if "exit" in actions else NO_EXIT)
    if no_items:
        return t(NO_ITEMS)
    return ""
