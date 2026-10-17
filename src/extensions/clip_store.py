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
"""Clipboard history and pinned list, in SQLite.

They used to be two JSON lists inside configs/extensions.json. With a few
hundred entries that meant rewriting the whole file on every copy (the
monitor polls each second) and handing the management page the whole
list at once. Here each entry is one row, written alone, and the pages
ask for one page of rows, filtered in SQL.

  clips(text UNIQUE, hist_ts, pin_ts)
    hist_ts  when it last went to the top of the history; NULL = not in
             the history
    pin_ts   when it was pinned; NULL = not pinned
A row in neither list is removed.

The file lives in the user data folder, outside configs/: settings
backups and the copy made for the sign-in screen are not meant to carry
clipboard contents. Lists still found in extensions.json (an older
version, or a settings backup restored over it) are merged in and taken
out of the JSON.
"""

import logging
import sqlite3
import threading
import time

from src import paths

logger = logging.getLogger("ClipStore")

HISTORY = "history"
PINNED = "pinned"
_TS = {HISTORY: "hist_ts", PINNED: "pin_ts"}

_lock = threading.RLock()
_conn = None


def _db():
    """The shared connection (created, migrated on first use). Callers
    hold _lock."""
    global _conn
    if _conn is None:
        path = paths.user_data_dir() / "clipboard.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(path), check_same_thread=False,
                                isolation_level=None)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("CREATE TABLE IF NOT EXISTS clips ("
                      " id INTEGER PRIMARY KEY,"
                      " text TEXT NOT NULL UNIQUE,"
                      " hist_ts REAL, pin_ts REAL)")
        _conn.execute("CREATE INDEX IF NOT EXISTS clips_hist"
                      " ON clips(hist_ts)")
        _conn.execute("CREATE INDEX IF NOT EXISTS clips_pin"
                      " ON clips(pin_ts)")
        _import_from_config(_conn)
    return _conn


def _purge(c):
    c.execute("DELETE FROM clips WHERE hist_ts IS NULL AND pin_ts IS NULL")


def _trim(c, cap):
    if cap and cap > 0:
        c.execute("UPDATE clips SET hist_ts = NULL WHERE hist_ts IS NOT NULL"
                  " AND id NOT IN (SELECT id FROM clips WHERE hist_ts IS NOT"
                  " NULL ORDER BY hist_ts DESC LIMIT ?)", (int(cap),))


def _import_from_config(c):
    """Move the JSON lists of extensions.json into the database."""
    from src.config_manager import ConfigManager
    try:
        e = ConfigManager().read_extensions() or {}
    except Exception:
        return
    cb = e.get("clipboard")
    if not isinstance(cb, dict) or not ("history" in cb or "pinned" in cb):
        return
    hist = [t for t in (cb.get("history") or []) if isinstance(t, str) and t]
    pins = [t for t in (cb.get("pinned") or []) if isinstance(t, str) and t]
    # Older than anything already here, in the lists' own order (first =
    # newest), so a restored backup cannot jump ahead of newer copies.
    row = c.execute("SELECT MIN(COALESCE(hist_ts, pin_ts)) FROM clips"
                    ).fetchone()
    base = min(row[0] or time.time(), time.time()) - 1.0
    c.execute("BEGIN")
    try:
        for i, t in enumerate(hist):
            ts = base - i * 1e-3
            c.execute("INSERT INTO clips(text, hist_ts) VALUES (?, ?)"
                      " ON CONFLICT(text) DO UPDATE SET hist_ts ="
                      " COALESCE(hist_ts, excluded.hist_ts)", (t, ts))
        for i, t in enumerate(pins):
            ts = base - i * 1e-3
            c.execute("INSERT INTO clips(text, pin_ts) VALUES (?, ?)"
                      " ON CONFLICT(text) DO UPDATE SET pin_ts ="
                      " COALESCE(pin_ts, excluded.pin_ts)", (t, ts))
        _trim(c, int(cb.get("max", 100) or 0))
        _purge(c)
        c.execute("COMMIT")
    except Exception as ex:
        c.execute("ROLLBACK")
        logger.error("clipboard import failed, JSON kept: %s", ex)
        return
    cb.pop("history", None)
    cb.pop("pinned", None)
    ConfigManager().write_extensions(e)
    logger.info("clipboard moved to the database: %d history, %d pinned",
                len(hist), len(pins))


def _where(kind, query):
    sql = f" WHERE {_TS[kind]} IS NOT NULL"
    args = []
    if query:
        sql += " AND instr(lower(text), lower(?)) > 0"
        args.append(query)
    return sql, args


def items(kind, query="", limit=None, offset=0):
    """Texts of `kind`, newest first, optionally only those containing
    `query` (case-insensitive for ASCII)."""
    sql, args = _where(kind, query)
    sql = f"SELECT text FROM clips{sql} ORDER BY {_TS[kind]} DESC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        args += [int(limit), int(offset)]
    with _lock:
        return [r[0] for r in _db().execute(sql, args)]


def count(kind, query=""):
    sql, args = _where(kind, query)
    with _lock:
        return _db().execute(f"SELECT COUNT(*) FROM clips{sql}",
                             args).fetchone()[0]


def record(text, cap):
    """Put `text` at the top of the history, keeping at most `cap`
    entries (0 = no limit)."""
    if not text:
        return
    with _lock:
        c = _db()
        c.execute("BEGIN")
        c.execute("INSERT INTO clips(text, hist_ts) VALUES (?, ?)"
                  " ON CONFLICT(text) DO UPDATE SET hist_ts = excluded.hist_ts",
                  (text, time.time()))
        _trim(c, cap)
        _purge(c)
        c.execute("COMMIT")


def pin(texts):
    """Pin `texts`; the last one ends up at the top of the pinned list."""
    now = time.time()
    with _lock:
        c = _db()
        c.execute("BEGIN")
        for i, t in enumerate(texts):
            c.execute("INSERT INTO clips(text, pin_ts) VALUES (?, ?)"
                      " ON CONFLICT(text) DO UPDATE SET pin_ts = excluded.pin_ts",
                      (t, now + i * 1e-6))
        c.execute("COMMIT")


def unpin(texts, cap):
    """Take `texts` off the pinned list and back to the top of the
    history, so an unpinned entry is never lost outright."""
    now = time.time()
    with _lock:
        c = _db()
        c.execute("BEGIN")
        for i, t in enumerate(texts):
            c.execute("UPDATE clips SET pin_ts = NULL, hist_ts = ?"
                      " WHERE text = ?", (now + i * 1e-6, t))
        _trim(c, cap)
        _purge(c)
        c.execute("COMMIT")


def delete(kind, texts):
    """Remove `texts` from the history; from the pinned list they go
    altogether (out of the history too)."""
    sets = ("hist_ts = NULL" if kind == HISTORY
            else "hist_ts = NULL, pin_ts = NULL")
    with _lock:
        c = _db()
        c.execute("BEGIN")
        for t in texts:
            c.execute(f"UPDATE clips SET {sets} WHERE text = ?", (t,))
        _purge(c)
        c.execute("COMMIT")


def clear_history():
    with _lock:
        c = _db()
        c.execute("UPDATE clips SET hist_ts = NULL")
        _purge(c)
