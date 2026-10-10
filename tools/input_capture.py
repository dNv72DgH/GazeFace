"""Record every mouse event the system sees, with its origin markers.

Purpose: work out why a synthetic click from this app is ignored by some
applications (VMware, Sandboxie, scrcpy, AV consoles) while another
assistive tool's click is accepted. The answer is in the fields Windows
attaches to each event, so the fastest way to it is to watch both tools
through the same low-level hook and diff what they emit.

What the interesting fields mean
--------------------------------
  flags & 0x01  LLMHF_INJECTED            SendInput / mouse_event produced
                                          this. A real mouse (or a virtual
                                          HID *driver*) does NOT set it.
  flags & 0x02  LLMHF_LOWER_IL_INJECTED   injected by a lower-integrity
                                          process than the one reading it.
  dwExtraInfo                             a tag the sender may attach;
                                          some tools mark their own events
                                          so they can recognise them.
  hold ms                                 gap between DOWN and UP. Apps
                                          that poll button state once per
                                          frame cannot see a press that
                                          lasts 0 ms — which is exactly
                                          what a single SendInput carrying
                                          DOWN|UP together produces.

So the three outcomes worth telling apart:
  * other tool NOT injected     -> it drives a real driver; we cannot
                                   match that from user space.
  * injected, but hold > 0 ms   -> we only need to split our DOWN/UP.
  * injected, extra info set    -> it tags events; probably irrelevant to
                                   whether they are accepted, but worth
                                   ruling out.

MOUSE ONLY, deliberately: a keyboard hook would capture whatever is typed
while it runs, and nothing here needs that.

Usage:
    python tools/input_capture.py [seconds]

Run it from an ELEVATED prompt if the app you are testing runs elevated,
otherwise its events will not reach this hook. Ctrl+C stops it early.
"""

import ctypes
import os
import sys
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Everything printed also lands here. The console alone is no good: the
# run has to be read by someone who was not sitting at the machine, and
# the interesting rows scroll past while you are still clicking.
OUT_PATH = os.path.join(os.environ.get("TEMP", "."),
                        "GazeFace_input_capture.log")
_out = None


def say(line=""):
    print(line, flush=True)
    if _out is not None:
        try:
            _out.write(line + "\n")
            _out.flush()
        except Exception:
            pass

WH_MOUSE_LL = 14
LLMHF_INJECTED = 0x00000001
LLMHF_LOWER_IL_INJECTED = 0x00000002

WM = {
    0x0200: "MOVE", 0x0201: "L_DOWN", 0x0202: "L_UP",
    0x0204: "R_DOWN", 0x0205: "R_UP",
    0x0207: "M_DOWN", 0x0208: "M_UP",
    0x020A: "WHEEL", 0x020E: "HWHEEL",
    0x020B: "X_DOWN", 0x020C: "X_UP",
}
DOWN_UP = {"L_DOWN": "L_UP", "R_DOWN": "R_UP", "M_DOWN": "M_UP",
           "X_DOWN": "X_UP"}


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT),
                ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_longlong, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.restype = ctypes.c_longlong
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                  ctypes.c_int]

# Raw captures, resolved to names only after the run. NOTHING slow may
# happen inside the hook: a low-level hook that takes too long is silently
# removed by Windows (LowLevelHooksTimeout), and — worse for this tool —
# the delay lands between DOWN and UP and shows up as a fake hold time.
# The first version looked up the process name via psutil inside the hook
# and measured 7.9 ms of hold on a click that has none.
raw = []             # (evt_time_ms, name, x, y, flags, mouseData, extra)
_start_tick = 0


def _win_at(x, y):
    """(hwnd, process name, title) under a point — what was being aimed at."""
    try:
        hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
        if not hwnd:
            return 0, "", ""
        buf = ctypes.create_unicode_buffer(200)
        user32.GetWindowTextW(hwnd, buf, 200)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = ""
        try:
            import psutil
            name = psutil.Process(pid.value).name()
        except Exception:
            pass
        return hwnd, name, buf.value
    except Exception:
        return 0, "", ""


def _flagstr(f):
    bits = []
    if f & LLMHF_INJECTED:
        bits.append("INJECTED")
    if f & LLMHF_LOWER_IL_INJECTED:
        bits.append("LOWER_IL")
    return "|".join(bits) or "-(real device)"


def _extra(ms):
    try:
        return ctypes.cast(ms.dwExtraInfo, ctypes.c_void_p).value or 0
    except Exception:
        return 0


def on_event(ncode, wparam, lparam):
    if ncode == 0:
        ms = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        w = int(wparam)
        if w != 0x0200:                         # moves would drown the log
            # Append only. ms.time is stamped by Windows when the event was
            # generated, so hold times measured from it are unaffected by
            # anything this process does afterwards.
            raw.append((int(ms.time), w, ms.pt.x, ms.pt.y,
                        int(ms.flags), int(ms.mouseData), _extra(ms)))
    return user32.CallNextHookEx(None, ncode, wparam, lparam)


def report():
    """Resolve and print everything after the hook is gone."""
    events = []
    pending = {}
    for tms, w, x, y, fl, md, ex in raw:
        name = WM.get(w, hex(w))
        _hwnd, proc, title = _win_at(x, y)
        hold = ""
        if name in DOWN_UP:
            pending[DOWN_UP[name]] = tms
        elif name in pending:
            hold = "  hold=%dms" % (tms - pending.pop(name))
        wheel = ""
        if name in ("WHEEL", "HWHEEL"):
            # mouseData carries a SIGNED delta in the high word
            d = ctypes.c_short((md >> 16) & 0xFFFF).value
            wheel = "  delta=%+d (%.2f notch)" % (d, d / 120.0)
        say("%7.3f  %-7s (%5d,%5d)  %-16s extra=0x%X%s%s  -> %s %r"
              % ((tms - _start_tick) / 1000.0, name, x, y, _flagstr(fl),
                 ex, wheel, hold, proc or "?", title[:40]))
        events.append((tms, name, x, y, fl, md, ex, proc, hold))
    summarise(events)


def summarise(events):
    if not events:
        say("\nNo mouse events captured.")
        return
    say("\n" + "=" * 78)
    say("SUMMARY  (%d events)" % len(events))
    say("=" * 78)
    real = [e for e in events if not (e[4] & LLMHF_INJECTED)]
    inj = [e for e in events if e[4] & LLMHF_INJECTED]
    say("  real-device events : %d" % len(real))
    say("  injected events    : %d" % len(inj))
    tags = sorted({e[6] for e in inj})
    say("  extraInfo seen on injected events: %s"
          % (", ".join(hex(t) for t in tags) or "(none)"))
    holds = [e for e in events if e[8]]
    if holds:
        say("\n  DOWN->UP hold times (this is the field that decides"
              " whether a\n  polling app can see the press at all):")
        for e in holds:
            say("    %-7s %-16s %s" % (e[1], _flagstr(e[4]), e[8].strip()))
    say("\n  Read it like this:")
    say("    hold=0.0ms + INJECTED  -> DOWN|UP in one SendInput; an app"
          " that samples\n                              button state per"
          " frame never sees it down.")
    say("    hold>0ms   + INJECTED  -> splitting our click is enough to"
          " match.")
    say("    no INJECTED flag       -> the other tool goes through a"
          " driver; user-space\n                              SendInput"
          " cannot reproduce that.")


def main():
    global _start_tick, _out
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    try:
        _out = open(OUT_PATH, "w", encoding="utf-8")
    except Exception as e:
        print("cannot write %s: %s" % (OUT_PATH, e))
    proc = HOOKPROC(on_event)
    _start_tick = kernel32.GetTickCount()
    hook = user32.SetWindowsHookExW(WH_MOUSE_LL, proc, None, 0)
    if not hook:
        say("SetWindowsHookEx failed: %d" % kernel32.GetLastError())
        return 1
    say("Writing to %s" % OUT_PATH)
    say("Capturing mouse events for %.0fs (Ctrl+C to stop early)." % secs)
    say("Click the SAME target with each tool in turn so the rows can be"
          " compared.")
    say("Nothing is printed until the end: the hook stays empty on"
          " purpose so it\ncannot distort the timings it is measuring.\n")
    msg = wintypes.MSG()
    end = time.time() + secs
    try:
        while time.time() < end:
            # PeekMessage + a short sleep: GetMessage would block past the
            # deadline, and the hook only needs the thread to pump.
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.005)
    except KeyboardInterrupt:
        pass
    finally:
        user32.UnhookWindowsHookEx(hook)
    say("%7s  %-7s %-15s %-16s %s"
          % ("t", "event", "point", "origin", "extra / hold / target"))
    say("-" * 78)
    report()
    say("\n(this whole report is also in %s)" % OUT_PATH)
    if _out is not None:
        try:
            _out.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
