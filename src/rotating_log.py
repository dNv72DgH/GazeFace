"""A rotating log handler that cannot stop logging.

RotatingFileHandler.emit is:

    if self.shouldRollover(record):
        self.doRollover()          # <- raises
    FileHandler.emit(self, record) # <- never reached

so when the rename fails the record is DROPPED, and because the file is
still over the cap every later record takes the same path. Logging is
dead for as long as the condition lasts, with nothing written to say so.

On Windows the rename fails whenever another process has the file open,
and this app can legitimately run more than one instance (the
allow_multi_instance option, plus the overlap while a restart hands
over). Python opens log files without FILE_SHARE_DELETE, so renaming to
ANY name fails too — there is no clever target name that works.

That is what happened on 2026-08-21: log.txt froze at 13:53 on exactly
2,097,102 bytes (the 2 MB cap) while the app ran another ten hours, and
every question asked of the log for that window was unanswerable.

The fix is to treat "cannot rotate" as a temporary condition and keep
appending through it rather than dropping records. The file grows past
the cap meanwhile, which is the right trade: a slightly oversized log
beats a silent one. Rotation is retried on a cooldown (not on every
record, which would be pointless work), and only if the file reaches a
hard multiple of the cap is starting over preferred to unbounded growth.
"""

import logging.handlers
import os
import time

# Wait this long before trying a failed rotation again.
RETRY_AFTER_S = 60.0
# Give up preserving history once the file is this many times the cap.
HARD_CAP_FACTOR = 4


class ResilientRotatingFileHandler(logging.handlers.RotatingFileHandler):

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._next_rot_try = 0.0
        self._rot_failures = 0
        self._last_rot_error = None

    def shouldRollover(self, record):
        # While rotation is known to be failing, stop asking: every "yes"
        # costs a failed rename and, in the stock handler, a lost record.
        if self._next_rot_try and time.time() < self._next_rot_try:
            return False
        return super().shouldRollover(record)

    def doRollover(self):
        try:
            super().doRollover()
            self._next_rot_try = 0.0
            self._rot_failures = 0
            return
        except Exception as e:
            self._rot_failures += 1
            self._last_rot_error = e
            self._next_rot_try = time.time() + RETRY_AFTER_S

        # Rotation is impossible right now (another process holds the
        # file). Keep the stream and carry on appending — doRollover has
        # already closed it, so reopen before returning or emit() will
        # write to nothing.
        try:
            if self.stream is None:
                self.stream = self._open()
        except Exception:
            self.stream = None
            return

        try:
            size = os.path.getsize(self.baseFilename)
        except Exception:
            size = 0
        if self.maxBytes and size > self.maxBytes * HARD_CAP_FACTOR:
            # Only now is losing history better than growing forever.
            try:
                self.stream.close()
                self.stream = open(self.baseFilename, "w",
                                   encoding=self.encoding or "utf-8")
                self.stream.write(
                    "(log could not be rotated %d times and reached %d "
                    "bytes; restarted this file)\n"
                    % (self._rot_failures, size))
            except Exception:
                try:
                    self.stream = self._open()
                except Exception:
                    self.stream = None
        elif self._rot_failures == 1:
            try:
                self.stream.write(
                    "(rotation blocked - another process has this file "
                    "open; continuing to append, retrying in %ds)\n"
                    % int(RETRY_AFTER_S))
            except Exception:
                pass

    def handleError(self, record):
        """Never leave the handler permanently without a stream."""
        try:
            if self.stream is None or self.stream.closed:
                self.stream = self._open()
        except Exception:
            pass
        super().handleError(record)
