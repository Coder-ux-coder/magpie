"""SQLite access and schema migrations.

One connection shared by the whole process (FastAPI handlers and background
tasks all run on one event loop), guarded by a lock for the few calls made from
worker threads. Migrations are numbered SQL scripts applied in order and
tracked with ``PRAGMA user_version``.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MIGRATIONS: list[str] = [
    # 1: initial schema
    """
    CREATE TABLE notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'web',
        raw_text TEXT NOT NULL,
        text TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL DEFAULT 'note',
        tags TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'open',
        pinned INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL DEFAULT 'pending',
        error TEXT,
        ping TEXT NOT NULL DEFAULT '',
        ping_reason TEXT NOT NULL DEFAULT '',
        in_digest INTEGER NOT NULL DEFAULT 0,
        briefing TEXT NOT NULL DEFAULT '',
        reply TEXT NOT NULL DEFAULT '',
        search_extra TEXT NOT NULL DEFAULT '',
        meta TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX notes_created ON notes(created_at);
    CREATE INDEX notes_state ON notes(state);

    CREATE TABLE links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
        url TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'article',
        title TEXT NOT NULL DEFAULT '',
        author TEXT NOT NULL DEFAULT '',
        site TEXT NOT NULL DEFAULT '',
        minutes INTEGER,
        state TEXT NOT NULL DEFAULT 'pending',
        error TEXT,
        review TEXT,
        found_by_search INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX links_note ON links(note_id);

    CREATE TABLE reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        note_id INTEGER REFERENCES notes(id) ON DELETE CASCADE,
        due_at TEXT NOT NULL,
        message TEXT NOT NULL,
        origin TEXT NOT NULL DEFAULT 'explicit',
        reason TEXT NOT NULL DEFAULT '',
        rrule TEXT,
        status TEXT NOT NULL DEFAULT 'scheduled',
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        sent_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX reminders_due ON reminders(status, due_at);
    CREATE INDEX reminders_note ON reminders(note_id);

    CREATE TABLE digests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        created_at TEXT NOT NULL,
        content TEXT NOT NULL,
        sent INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE kv (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE push_subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        endpoint TEXT NOT NULL UNIQUE,
        p256dh TEXT NOT NULL,
        auth TEXT NOT NULL,
        user_agent TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    """,
]

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    title, text, raw_text, summary, tags, search_extra,
    content='notes', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS notes_fts_insert AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, title, text, raw_text, summary, tags, search_extra)
    VALUES (new.id, new.title, new.text, new.raw_text, new.summary, new.tags, new.search_extra);
END;
CREATE TRIGGER IF NOT EXISTS notes_fts_delete AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, text, raw_text, summary, tags, search_extra)
    VALUES ('delete', old.id, old.title, old.text, old.raw_text, old.summary, old.tags,
            old.search_extra);
END;
CREATE TRIGGER IF NOT EXISTS notes_fts_update AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, text, raw_text, summary, tags, search_extra)
    VALUES ('delete', old.id, old.title, old.text, old.raw_text, old.summary, old.tags,
            old.search_extra);
    INSERT INTO notes_fts(rowid, title, text, raw_text, summary, tags, search_extra)
    VALUES (new.id, new.title, new.text, new.raw_text, new.summary, new.tags, new.search_extra);
END;
"""


def _fts5_available() -> bool:
    probe = sqlite3.connect(":memory:")
    try:
        probe.execute("CREATE VIRTUAL TABLE probe USING fts5(x)")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        probe.close()


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self.fts = _fts5_available()
        self.migrate()

    def migrate(self) -> None:
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            for number, script in enumerate(MIGRATIONS, start=1):
                if number <= version:
                    continue
                log.info("Applying database migration %d", number)
                self._conn.executescript(
                    f"BEGIN;\n{script}\nPRAGMA user_version={number};\nCOMMIT;"
                )
            if self.fts:
                exists = self._conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name = 'notes_fts'"
                ).fetchone()
                self._conn.executescript(FTS_SCHEMA)
                if not exists:
                    self._conn.execute("INSERT INTO notes_fts(notes_fts) VALUES ('rebuild')")
            else:
                log.warning("SQLite was built without FTS5; search falls back to LIKE queries")

    def execute(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()
