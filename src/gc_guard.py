"""Run Python's cyclic garbage collector on the tk main thread only.

Automatic collection runs on whichever thread happens to allocate when a
threshold is crossed. When that is a worker thread and the garbage holds
a tkinter object (a CTkFont, an image), its __del__ calls into Tcl, and
Tcl hands the call to the main thread and waits for it. Measured
2026-10-27 08:56: the main thread was inside MediaPipe's detect_async,
waiting for MediaPipe's executor thread; that thread collected, hit
tkinter\\font.py __del__ and waited for the main thread. Neither moved
again: meter frozen, face control dead, until the watchdog restarted the
app 122 s later.

With automatic collection off and every collection done from a tk
`after` callback, a tkinter __del__ always runs on the thread that owns
Tcl, so that wait cannot happen.
"""
import gc
import logging
import time

logger = logging.getLogger("GcGuard")

TICK_MS = 250           # young generation every tick
GEN1_EVERY = 10         # ticks (2.5 s)
GEN2_EVERY = 1200       # ticks (5 min)
SLOW_MS = 50            # log collections that take longer than this


def start(tk_root):
    gc.disable()
    count = 0

    def tick():
        nonlocal count
        count += 1
        if count % GEN2_EVERY == 0:
            gen = 2
        elif count % GEN1_EVERY == 0:
            gen = 1
        else:
            gen = 0
        t0 = time.perf_counter()
        try:
            gc.collect(gen)
        except Exception as e:
            logger.error("collect(%d) failed: %s", gen, e)
        ms = (time.perf_counter() - t0) * 1000.0
        if ms > SLOW_MS:
            logger.info("gen%d collection took %.0f ms", gen, ms)
        tk_root.after(TICK_MS, tick)

    tk_root.after(TICK_MS, tick)
    logger.info("Automatic GC off - collecting on the tk thread every %d ms",
                TICK_MS)
