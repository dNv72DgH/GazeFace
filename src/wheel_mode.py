"""Wheel rotation mode: head tracking turns the mouse wheel.

Started by the "Wheel rotation" mouse actions (or a launcher macro step),
it takes over head tracking: the pointer stays put and the head's offset
from where it was when the mode started (the origin) turns the wheel for
as long as the head stays away from it. The further away, the faster.

Two layouts:

  "vh"  (up/down, left/right): nodding scrolls vertically, turning the
        head scrolls horizontally.
  "hv"  (left/right, up/down): turning the head scrolls vertically,
        nodding scrolls horizontally.

Options are per action and per HEAD axis ("y" = nodding, "x" = turning),
so each layout keeps its own feel:

  dz10     dead stroke: head offset that does nothing, in tenths of a
           percent of the camera frame width (20 = 2.0 %), so a head
           held still at the origin never scrolls (0 = no play)
  amount   wheel delta per input (120 = one notch)
  rate     inputs per 10 s just outside the dead zone - the slowest
           scrolling there is
  sens     how much faster it gets the further the head is from the
           origin (0 = the same rate everywhere)
  invert   scroll the other way
  off      this axis does not scroll at all

Ends on the same action again, on any other gesture (which then runs its
own action), and when face control is paused.
"""

import math

KINDS = ("vh", "hv")
AXES = ("y", "x")                 # head: nodding, turning
OPTS_KEY = "wheel_mode_opts"

AMOUNT_MIN, AMOUNT_MAX, AMOUNT_DEFAULT = 10, 1200, 120
RATE_MIN, RATE_MAX, RATE_DEFAULT = 1, 200, 20        # per 10 s
SENS_MIN, SENS_MAX, SENS_DEFAULT = 0, 500, 30
# Dead stroke, in tenths of a percent - a whole number with 0.1 % steps.
# (dist232 stored "dz" in percent; a new key so that is not misread.)
DZ_MIN, DZ_MAX, DZ_DEFAULT = 0, 100, 20
# Never more inputs than this per second, however far the head goes.
MAX_RATE_PER_S = 40.0
# After the mode starts, the head position is averaged this long to set
# the origin; nothing scrolls meanwhile. The gesture that started the
# mode moves the face, and a launcher cell may have been chosen with the
# head turned a little.
SETTLE_S = 0.4

DEFAULTS = {"dz10": DZ_DEFAULT, "amount": AMOUNT_DEFAULT,
            "rate": RATE_DEFAULT, "sens": SENS_DEFAULT,
            "invert": False, "off": False}
_LIMITS = {"dz10": (DZ_MIN, DZ_MAX),
           "amount": (AMOUNT_MIN, AMOUNT_MAX),
           "rate": (RATE_MIN, RATE_MAX),
           "sens": (SENS_MIN, SENS_MAX)}
# Options that take fractions (one decimal); none at the moment.
FLOAT_OPTS = ()


def clamp(name, v):
    lo, hi = _LIMITS[name]
    try:
        v = float(v)
    except Exception:
        v = DEFAULTS[name]
    v = round(v, 1) if name in FLOAT_OPTS else int(round(v))
    return max(lo, min(hi, v))


def options(cfg, kind, axis):
    """The options of one head axis of one layout, defaults filled in."""
    out = dict(DEFAULTS)
    try:
        saved = ((cfg or {}).get(OPTS_KEY) or {}).get(kind, {}).get(axis, {})
    except Exception:
        saved = {}
    for k, v in (saved or {}).items():
        if k in _LIMITS:
            out[k] = clamp(k, v)
        elif k in ("invert", "off"):
            out[k] = bool(v)
    return out


def with_option(cfg, kind, axis, name, value):
    """A copy of the stored options dict with one value changed."""
    import copy
    allopts = copy.deepcopy((cfg or {}).get(OPTS_KEY) or {})
    if not isinstance(allopts, dict):
        allopts = {}
    ax = allopts.setdefault(kind, {}).setdefault(axis, {})
    ax[name] = clamp(name, value) if name in _LIMITS else bool(value)
    return allopts


def target(kind, axis):
    """(horizontal_wheel, sign) for a head axis in a layout: which wheel
    it turns, and the wheel direction for a POSITIVE head offset (head
    right for "x", head down for "y"). Wheel + is up / right."""
    if kind == "hv":
        # Turning scrolls vertically: right reads on, i.e. down.
        # Nodding scrolls horizontally: down goes right.
        return (False, -1) if axis == "x" else (True, +1)
    # "vh": nodding scrolls vertically (down = down), turning
    # horizontally (right = right).
    return (False, -1) if axis == "y" else (True, +1)


def rate_per_s(opts, offset_pct):
    """Inputs per second for a head offset (percent of frame width,
    signed); 0 inside the dead zone or when the axis is off."""
    if opts.get("off"):
        return 0.0
    d = abs(offset_pct) - opts.get("dz10", DZ_DEFAULT) / 10.0
    if d <= 0:
        return 0.0
    base = opts["rate"] / 10.0
    r = base * (1.0 + opts["sens"] / 100.0 * d)
    return min(MAX_RATE_PER_S, r)


class AxisDriver:
    """Turns one head axis into wheel inputs over time."""

    def __init__(self):
        self.phase = 0.0
        self.moving = False

    def step(self, opts, offset_pct, dt):
        """Number of inputs to send this tick (usually 0 or 1)."""
        r = rate_per_s(opts, offset_pct)
        if r <= 0:
            self.moving = False
            self.phase = 0.0
            return 0
        if not self.moving:
            # Leaving the dead zone scrolls at once, not one period later.
            self.moving = True
            self.phase = 1.0
        else:
            self.phase += r * max(0.0, min(dt, 0.5))
        n = int(math.floor(self.phase))
        self.phase -= n
        return n
