# Python-side handle for the Qt overlay. Presents the SAME interface as
# src.gui.gaze_grid.GazeGrid (open / update_cells / current_cell /
# note_action / close), but instead of drawing with tkinter it:
#   * computes the gaze-selected cell on the tk main loop (reusing the
#     calibration polynomial), and
#   * pushes render commands (open / highlight / cells / close) to a
#     persistent Qt subprocess that does the GPU-accelerated drawing.
#
# This keeps all face/gaze logic in the main process and isolates the Qt
# event loop in its own process.
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time

import numpy as np

from src.config_manager import ConfigManager
from src.gui.gaze_select import BLINK_STABLE_DEFAULT_MS, GazeSelector

logger = logging.getLogger("QtGazeGrid")

TICK_MS = 50
_ACCENT = "#4CC2FF"
# Blink gate, selection stability and dwell confirm: src/gui/gaze_select.py
# (shared with the tk grid).


def _overlay_command():
    """How to launch the overlay subprocess in this environment.

    Always the ORDINARY exe, never sys.executable.

    With the UIAccess build running, sys.executable is GazeFace_UI.exe,
    and CreateProcess refuses to start that at all (WinError 740 — only
    the AppInfo service can mint a UIAccess token). Every overlay spawn
    failed, which takes the gaze grid, the launcher and the ring with it.

    ShellExecuteEx would start it, but cannot hand back a stdin pipe, and
    that pipe IS the command channel to the overlay. So the overlay runs
    from the plain exe. It only draws — it sends no input — so it has no
    use for the flag anyway; it still inherits our integrity level.
    """
    if getattr(sys, "frozen", False):
        exe = os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                           "GazeFace.exe")
        if not os.path.isfile(exe):
            exe = sys.executable        # unpackaged layout — best effort
        return [exe, "--qt-overlay"], None
    # Dev: run the module from the project root.
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    return [sys.executable, "-m", "src.gui.qt_overlay.overlay_main"], root


class OverlayClient:
    """Owns the single persistent overlay subprocess. Shared by every
    QtGazeGrid so opening a tool is instant (process already warm)."""

    _inst = None

    @classmethod
    def instance(cls):
        if cls._inst is None:
            cls._inst = OverlayClient()
        return cls._inst

    # NOTE on the "all overlays vanish after idle" bug: the cause is the
    # render path dying INSIDE the (possibly brand-new) process — real-
    # machine logs showed commands arriving and isVisible()==True on
    # freshly spawned processes while nothing reached the screen, so
    # process respawning (tried first) cannot fix it. The overlay process
    # now verifies PRESENTED frames after every show and rebuilds its own
    # window / falls back to software rendering when rendering is dead
    # (see overlay_main._check_rendered). No keepalive/respawn here.
    # Writes go through ONE daemon writer thread. A pipe write blocks
    # when the overlay stops draining its stdin (hung render loop, dead
    # reader) and the Windows pipe buffer fills — with an inline write
    # that stalls the CALLER, and the caller is usually the tk main
    # thread, which also drives face detection. Real incident: the main
    # thread sat in stdin.flush() for ~8 minutes, detection stopped, and
    # head tracking froze while the camera/mouse worker threads kept
    # logging healthy heartbeats. Enqueueing keeps every caller free; a
    # jammed pipe now only parks this one throwaway thread.
    def __init__(self):
        self.proc = None
        self._q = queue.Queue(maxsize=256)
        # _lock guards the process (and is held across Popen, which can
        # take seconds). The two below are never held across I/O, so the
        # tk thread calling send() can never wait on a spawn:
        #   _writer_lock  starting the writer / stall-watch threads
        #   _send_lock    the replay snapshot and the queue, kept in step
        self._lock = threading.Lock()
        self._writer_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._writer = None
        self._shutdown = False
        # What is on screen right now, so a replacement overlay can put it
        # back: the last "open" and the "cells" sent after it. Cleared by
        # "close". (The per-tick highlight is re-sent by the grid anyway.)
        self._replay_open = None
        self._replay_cells = None
        # Set while a write to the overlay's stdin is in progress; the
        # stall watchdog kills an overlay that stops accepting commands.
        self._writing_since = None
        self._watchdog = None

    def _start_writer(self):
        # Locked: send() is called from several threads, and is_alive() is
        # False for a created-but-not-yet-started thread, so an unlocked
        # check-then-act can start TWO writers — which would break FIFO
        # ordering permanently (highlight overtaking its open). Not
        # self._lock: ensure() holds that across Popen, and send() runs on
        # the tk thread.
        with self._writer_lock:
            if self._writer is not None and self._writer.is_alive():
                return
            self._writer = threading.Thread(
                target=self._write_loop, name="OverlayWriter", daemon=True)
            self._writer.start()
            if self._watchdog is None or not self._watchdog.is_alive():
                self._watchdog = threading.Thread(
                    target=self._stall_watch, name="OverlayStallWatch",
                    daemon=True)
                self._watchdog.start()

    def _write_loop(self):
        while True:
            try:
                msg = self._q.get(timeout=1.0)
            except queue.Empty:
                # Idle: notice an overlay that has exited (it leaves on its
                # own when it can no longer draw) and replace it NOW, not
                # on the user's next gesture - which would then pay the
                # 2-3 s cold start or, worse, open into a dead window.
                self._revive_if_dead()
                continue
            if self._revive_if_dead():
                # The replay already carries this message's effect (send()
                # records open/cells/close before queueing), and the rest
                # were one-shot updates for the process that died.
                if isinstance(msg, dict) and msg.get("cmd") == "quit":
                    self._write(msg)
                continue
            if not self.ensure():
                continue
            self._write(msg)

    def _revive_if_dead(self):
        """Respawn a dead overlay and replay what should be on screen.
        True if it did."""
        with self._lock:
            p = self.proc
            if self._shutdown or p is None or p.poll() is None:
                return False
            code = p.returncode
        # A process that dies again and again (a display that no new
        # overlay can draw on either) must not turn into a spawn loop:
        # at most 3 revivals a minute, then wait for the next command.
        now = time.monotonic()
        self._revivals = [t for t in getattr(self, "_revivals", [])
                          if now - t < 60.0]
        if len(self._revivals) >= 3:
            return False
        self._revivals.append(now)
        logger.warning("overlay exited (code %s)%s - starting a new one", code,
                       " - it could no longer draw" if code == 75 else "")
        if not self.ensure():
            return False
        # Snapshot and drain together, under the lock send() uses: every
        # queued open/cells/close is already reflected in the snapshot, so
        # replaying it AND then writing the backlog sent the same open
        # twice, or replayed state a queued close was about to undo.
        # Only a pending quit survives the drain.
        with self._send_lock:
            replay = [m for m in (self._replay_open, self._replay_cells)
                      if m]
            quit_pending = False
            while True:
                try:
                    m = self._q.get_nowait()
                except queue.Empty:
                    break
                if isinstance(m, dict) and m.get("cmd") == "quit":
                    quit_pending = True
        for m in replay:
            self._write(m)
        if quit_pending:
            self._write({"cmd": "quit"})
        if replay:
            logger.info("overlay: restored the open %s on the new process",
                        self._replay_open.get("mode"))
        return True

    def _write(self, msg):
        p = self.proc
        if p is None or p.stdin is None:
            return
        self._writing_since = time.monotonic()
        try:
            data = (json.dumps(msg) + "\n").encode("utf-8")
            p.stdin.write(data)
            p.stdin.flush()             # may block — only this thread
        except Exception as e:
            logger.error("overlay send failed: %s", e)
            # Make sure it is really gone and leave it in self.proc: the
            # next pass of the writer sees a dead process and replaces it
            # WITH the replay of what should be on screen (dropping the
            # reference here used to respawn an empty overlay instead).
            self._kill(p, "its input pipe failed")
        finally:
            self._writing_since = None

    @staticmethod
    def _kill(p, why):
        try:
            if p.poll() is None:
                logger.warning("overlay pid %s killed: %s", p.pid, why)
                p.kill()
                p.wait(timeout=2.0)
        except Exception:
            pass

    STALL_S = 3.0

    def _stall_watch(self):
        """Kill an overlay that has stopped taking commands.

        A process can be alive and useless: hung in its own exit, or with
        its reader gone, it still holds the pipe open, so writes succeed
        until the pipe buffer fills and then block for ever - and to
        poll() it is "running", so it would never be replaced. A write that
        has not completed in STALL_S is that case. Killing it unblocks the
        writer (the write fails) and the writer replaces the process.
        """
        while not self._shutdown:
            time.sleep(1.0)
            since = self._writing_since
            p = self.proc
            if since is None or p is None:
                continue
            if time.monotonic() - since > self.STALL_S:
                self._kill(p, "it stopped accepting commands (write blocked "
                              "%.1f s)" % (time.monotonic() - since))

    def ensure(self):
        # Called from the writer thread, so spawning never stalls the UI.
        with self._lock:
            if self._shutdown:
                return False        # app is exiting — never spawn again
            if self.proc is not None and self.proc.poll() is None:
                return True
            dead = self.proc is not None    # had a process that has now died
            try:
                cmd, cwd = _overlay_command()
                # Capture the overlay subprocess's stderr to a file so
                # crashes (which would otherwise be silent and look like
                # "launch failed") are visible.
                errp = os.path.join(os.environ.get("TEMP", "."),
                                    "GazeFace_overlay_stderr.log")
                self.proc = subprocess.Popen(
                    cmd, cwd=cwd, stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=open(errp, "ab", buffering=0))
                logger.info("overlay %s (pid=%s)",
                            "RESPAWNED after death" if dead else "spawned",
                            self.proc.pid)
                return True
            except Exception as e:
                logger.error("overlay spawn failed: %s", e)
                self.proc = None
                return False

    def send(self, msg):
        """Queue a command for the overlay. Never blocks the caller."""
        cmd = msg.get("cmd") if isinstance(msg, dict) else None
        self._start_writer()
        try:
            with self._send_lock:
                self._q.put_nowait(msg)
                # Recorded only once queued, so a dropped command is not
                # replayed as if the overlay had it.
                if cmd == "open":
                    self._replay_open, self._replay_cells = msg, None
                elif cmd == "cells" and self._replay_open is not None:
                    self._replay_cells = msg
                elif cmd in ("close", "quit"):
                    self._replay_open = self._replay_cells = None
            return True
        except queue.Full:
            # Only reachable when the pipe is jammed (the overlay is not
            # rendering anyway). Drop the NEW command, never the head:
            # shedding the head would let a later "highlight" reach the
            # overlay for an "open" it never got — an invisible grid that
            # still answers gestures. Dropping the tail keeps whatever the
            # overlay does receive protocol-consistent.
            logger.warning("overlay write queue full — dropped %s",
                           msg.get("cmd") if isinstance(msg, dict) else msg)
            return False

    def overlay_pid(self):
        p = self.proc
        try:
            return p.pid if (p is not None and p.poll() is None) else None
        except Exception:
            return None

    def recycle(self):
        """Kill the overlay process and start a fresh one.

        The Qt Quick scene graph, its acrylic/shadow effects and the
        driver-side allocations behind them all live in that process, and
        they grow over a long session — restarting the whole app was the
        only thing that gave the memory back. Replacing just this process
        gives the same reset without touching the user's session: the
        camera, calibration and bindings all live in the main process.

        Caller decides WHEN (see the recycler in run_app): this must only
        happen while no gaze tool is on screen.
        """
        with self._lock:
            if self._shutdown:
                return False
            p = self.proc
            self.proc = None            # ensure() will spawn a new one
        if p is not None:
            try:
                p.kill()                # not a graceful quit — no window
                p.wait(timeout=2.0)     # is showing, nothing to flush
            except Exception:
                pass
        # Bring the replacement up NOW rather than on the next open, so
        # the first tool afterwards is still instant.
        ok = self.ensure()
        logger.info("overlay recycled (respawn ok=%s)", ok)
        return ok

    def quit(self):
        p = self.proc
        if p is not None and p.poll() is None:
            try:
                # Enqueue: the writer thread owns the pipe, two writers
                # would interleave mid-message.
                self._q.put_nowait({"cmd": "quit"})
            except Exception:
                pass
            try:
                p.wait(timeout=1.5)
            except Exception:
                try:
                    p.kill()                # writer jammed — force it
                except Exception:
                    pass
        with self._lock:
            # Set AFTER the quit handshake: a jammed writer draining its
            # backlog later must not Popen a replacement overlay that
            # outlives the app.
            self._shutdown = True
            self.proc = None


def _abs_icon(p):
    if not p:
        return ""
    try:
        return os.path.abspath(p).replace("\\", "/")
    except Exception:
        return ""


class QtGazeGrid:

    def __init__(self, tk_root):
        self.tk_root = tk_root
        self.client = OverlayClient.instance()
        self.rows = 3
        self.cols = 3
        self.cells = {}
        self.on_exit = None
        self.idle_timeout_s = 60.0
        # Which cell the gaze has selected, and the dwell on it. The
        # overlay shows (0,0) highlighted by default, but nothing counts as
        # selected until the gaze commits a cell (see GazeSelector).
        self._sel = GazeSelector()
        self._on_confirm = None
        self._after = None
        self._last_action = 0.0
        self._screen_w = 0
        self._screen_h = 0
        self._active = False
        self._alpha = 0.2

    # ---------------------------------------------------------------- #

    def open(self, rows, cols, cells, on_exit, idle_timeout_s=60.0,
             alpha=0.2, confirm_ms=0, on_confirm=None,
             ignore_blink_ms=BLINK_STABLE_DEFAULT_MS):
        import pyautogui
        self.rows = max(1, int(rows))
        self.cols = max(1, int(cols))
        self.cells = dict(cells or {})
        self.on_exit = on_exit
        self.idle_timeout_s = float(idle_timeout_s)
        self._alpha = max(0.1, min(1.0, float(alpha)))
        self._on_confirm = on_confirm
        self._screen_w, self._screen_h = pyautogui.size()
        self._sel.start(self.rows, self.cols, self._screen_w,
                        self._screen_h, ignore_blink_ms, confirm_ms)
        self._last_action = time.monotonic()
        self._active = True
        from src.gui.gaze_colors import gaze_colors
        c = gaze_colors()
        ok = self.client.send({
            "cmd": "open", "mode": "grid", "rows": self.rows,
            "cols": self.cols, "cells": self._cells_payload(), "sel": 0,
            "accent": _ACCENT, "gradient": self._gradient(),
            "cellColor": c["cell"], "hlColor": c["highlight"]})
        logger.info("QtGazeGrid.open: sent open to overlay, send_ok=%s", ok)
        self._tick()

    def _gradient(self):
        # Acrylic tint as 0xAABBGGRR from the SHARED gaze colours: the
        # background colour + the transparency slider (both live in the
        # colour-settings popup now, not per tool).
        from src.gui.gaze_colors import gaze_colors, gaze_opacity
        a = int(gaze_opacity() * 255)
        bg = str(gaze_colors().get("background", "#101214")).lstrip("#")
        try:
            r, g, b = (int(bg[0:2], 16), int(bg[2:4], 16), int(bg[4:6], 16))
        except Exception:
            r, g, b = 0x14, 0x12, 0x10
        return (a << 24) | (b << 16) | (g << 8) | r

    def _cells_payload(self):
        out = []
        missing = []
        for r in range(self.rows):
            for c in range(self.cols):
                cell = self.cells.get(f"{r},{c}", {}) or {}
                icon = _abs_icon(cell.get("icon"))
                # An icon that does not resolve reaches the overlay as a
                # perfectly valid-looking path and simply renders nothing,
                # which is indistinguishable from "the overlay ignored it".
                # Say which of the two it is, here, where the answer is
                # cheap to get.
                if icon and not os.path.exists(icon):
                    missing.append("%d,%d %s" % (r, c, icon))
                elif not icon and cell.get("title"):
                    missing.append("%d,%d <no icon> %s"
                                   % (r, c, cell.get("title")))
                out.append({
                    "title": cell.get("title", "") or "",
                    "icon": icon,
                    "app": bool(cell.get("app_specific"))})
        have = sum(1 for x in out if x["icon"])
        logger.info("ICONS: %d/%d cells have an icon%s", have, len(out),
                    ("; unresolved: " + " | ".join(missing[:8]))
                    if missing else "")
        return out

    def update_cells(self, cells):
        self.cells = dict(cells or {})
        self._sel.reset_selection()
        self.client.send({
            "cmd": "cells", "rows": self.rows, "cols": self.cols,
            "cells": self._cells_payload(), "sel": 0})
        self.note_action()

    def current_cell(self):
        # Default to (0,0) for a gesture-confirm before any dwell commit.
        cur = self._sel.cur
        return cur if cur is not None else (0, 0)

    def note_action(self):
        self._last_action = time.monotonic()

    def note_confirmed_cell(self):
        """Tell the implicit re-calibration that the CURRENT cell was just
        executed — i.e. the user was demonstrably looking at it. Call this
        only for real content cells, never for the ◀ ▶ paging cells (the
        user may glance at those from anywhere)."""
        try:
            from src import gaze_online_cal
            r, c = self.current_cell()
            cw = self._screen_w / max(1, self.cols)
            ch = self._screen_h / max(1, self.rows)
            gaze_online_cal.note_confirm(
                (c + 0.5) * cw, (r + 0.5) * ch, cw, ch,
                self._screen_w, self._screen_h)
        except Exception as e:
            logger.debug("note_confirmed_cell failed: %s", e)

    # ---------------------------------------------------------------- #

    def _tick(self):
        if not self._active:
            return
        committed, confirm = self._sel.step()
        if committed is not None:
            self.client.send({"cmd": "highlight",
                              "sel": committed[0] * self.cols
                              + committed[1]})
        if confirm and self._on_confirm is not None:
            try:
                self._on_confirm()
            except Exception:
                logger.error("dwell confirm failed", exc_info=True)
            if not self._active:
                return                  # confirm closed the grid
        if time.monotonic() - self._last_action > self.idle_timeout_s:
            logger.info("Qt gaze grid idle timeout — closing")
            self._fire_exit()
            return
        try:
            self._after = self.tk_root.after(TICK_MS, self._tick)
        except Exception:
            self._after = None

    def _fire_exit(self):
        cb = self.on_exit
        self.close()
        if cb:
            try:
                cb()
            except Exception:
                pass

    def close(self):
        self._active = False
        if self._after is not None:
            try:
                self.tk_root.after_cancel(self._after)
            except Exception:
                pass
        self._after = None
        self.client.send({"cmd": "close"})
