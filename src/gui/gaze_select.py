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
"""Which grid cell the gaze has selected, and when it has dwelt long
enough to confirm it.

Shared by both gaze-grid renderers (src/gui/gaze_grid.py, tk, and
src/gui/qt_overlay/qt_gaze_grid.py, Qt). The timing rules are subtle and
each has a reason; kept in one place so the two grids cannot drift apart:

  * blink gate - gaze samples are garbage while the lids are closed and
    for a short grace after, so those ticks yield no cell at all;
  * stability - with "ignore blinks" on, a newly-gazed cell must stay put
    for ignore_blink_ms before it becomes the selection (a blink's jump
    reverts sooner and never commits);
  * dwell confirm - gaze resting CONTINUOUSLY on the selected cell for
    confirm_ms confirms it, once per selection. A tick on another cell
    restarts the dwell (or a long-selected cell would fire the instant
    the gaze passed back over it); a blink does NOT restart it;
  * gaps - blink / face-lost ticks do not count toward either timer:
    both are shifted forward by the gap's length when gaze returns, so a
    long eye-close cannot fire the confirm the moment the eyes open.
"""

import time

from src.detectors import FaceMesh

BLINK_GRACE_S = 0.15
# "Ignore blinks" strength (ms) - the per-tool slider's default. 0 = off.
BLINK_STABLE_DEFAULT_MS = 200
# Gaze positions averaged into one sample.
SMOOTH_N = 5


class GazeSelector:

    def __init__(self):
        self.start(1, 1, 1, 1)

    def start(self, rows, cols, screen_w, screen_h,
              ignore_blink_ms=BLINK_STABLE_DEFAULT_MS, confirm_ms=0):
        """Begin a fresh session for a grid of rows x cols."""
        self.rows = max(1, int(rows))
        self.cols = max(1, int(cols))
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.ignore_blink_ms = max(0, int(ignore_blink_ms or 0))
        self.confirm_ms = max(0, int(confirm_ms or 0))
        self._hist = []
        self._blink_until = 0.0
        self._gap_since = None
        self.reset_selection()

    def reset_selection(self):
        """Forget the selection (the cells changed under it, e.g. paging).

        None = nothing committed yet. Nothing counts as selected until
        the gaze commits a cell, or dwell-confirm would fire the
        pre-highlighted top-left cell by itself.
        """
        self.cur = None
        self._cand = None
        self._cand_since = 0.0
        self._sel_since = None
        self._confirm_done = False

    # ---------------------------------------------------------------- #

    def _blinking(self, now):
        if FaceMesh().is_blinking():
            self._blink_until = now + BLINK_GRACE_S
            return True
        return now < self._blink_until

    def gaze_cell(self, now):
        """The cell under the (smoothed, bias-corrected) gaze, or None
        during a blink or when there is no gaze."""
        if self.ignore_blink_ms > 0 and self._blinking(now):
            return None
        from src import gaze_mapping, gaze_online_cal
        raw = gaze_mapping.predict(self.screen_w, self.screen_h,
                                   apply_bias=False)
        if raw is None:
            return None
        # Feed the implicit re-calibration the RAW prediction, then apply
        # the drift correction it has learned so far.
        gaze_online_cal.note_sample(raw[0], raw[1])
        bx, by = gaze_online_cal.get_bias()
        self._hist.append((raw[0] + bx, raw[1] + by))
        if len(self._hist) > SMOOTH_N:
            self._hist.pop(0)
        n = len(self._hist)
        msx = sum(p[0] for p in self._hist) / n
        msy = sum(p[1] for p in self._hist) / n
        col = int(max(0, min(self.cols - 1,
                             msx // (self.screen_w / self.cols))))
        row = int(max(0, min(self.rows - 1,
                             msy // (self.screen_h / self.rows))))
        return (row, col)

    def step(self, cell=None, now=None, sample=True):
        """Advance one tick.

        Returns (committed, confirm): `committed` is the cell that just
        became the selection (the caller highlights it) or None; `confirm`
        is True when the dwell on the selection has just completed.
        `cell` is the gazed cell; with sample=True it is read here.
        """
        now = time.monotonic() if now is None else now
        if sample:
            cell = self.gaze_cell(now)
        if cell is None:
            if self._gap_since is None:
                self._gap_since = now
            return None, False
        if self._gap_since is not None:
            gap = now - self._gap_since
            self._gap_since = None
            if self._sel_since is not None:
                self._sel_since += gap
            if self._cand is not None:
                self._cand_since += gap

        committed = None
        if self.ignore_blink_ms <= 0:
            if cell != self.cur:
                committed = cell
        elif cell != self._cand:
            self._cand = cell
            self._cand_since = now
        elif (cell != self.cur
              and (now - self._cand_since) * 1000 >= self.ignore_blink_ms):
            committed = cell
        if committed is not None:
            self.cur = committed
            self._sel_since = now
            self._confirm_done = False

        confirm = False
        if self.confirm_ms > 0:
            if cell != self.cur:
                self._sel_since = None
            elif self._sel_since is None:
                self._sel_since = now           # dwell starts on arrival
            elif (not self._confirm_done
                  and (now - self._sel_since) * 1000 >= self.confirm_ms):
                self._confirm_done = True
                confirm = True
        return committed, confirm
