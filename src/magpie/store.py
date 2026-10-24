"""Data access: every SQL statement in the app lives here."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from typing import Any, TypeVar

from pydantic import ValidationError

from magpie.config import Prefs, Settings
from magpie.db import Database
from magpie.models import (
    Category,
    Digest,
    Link,
    Note,
    NoteStatus,
    ProcState,
    Reminder,
    ReminderStatus,
)
from magpie.schemas import LinkReview
from magpie.timeutil import iso, now_utc, parse_iso

log = logging.getLogger(__name__)

T = TypeVar("T")

# SQLite integers are 64-bit; a larger id in a URL can never match a row.
MAX_ID = 2**63 - 1

NOTE_FIELDS = {
    "raw_text",
    "text",
    "title",
    "summary",
    "kind",
    "tags",
    "status",
    "pinned",
    "state",
    "error",
    "ping",
    "ping_reason",
    "in_digest",
    "briefing",
    "reply",
    "search_extra",
    "meta",
    "source",
    "category_id",
}
LINK_FIELDS = {
    "url",
    "kind",
    "title",
    "author",
    "site",
    "minutes",
    "state",
    "error",
    "review",
    "found_by_search",
}
REMINDER_FIELDS = {
    "due_at",
    "message",
    "origin",
    "reason",
    "rrule",
    "status",
    "attempts",
    "last_error",
    "sent_at",
    "note_id",
}

_WORD = re.compile(r"\w+", re.UNICODE)


def _encode(field: str, value: Any) -> Any:
    if isinstance(value, datetime):
        return iso(value)
    if field in {"tags", "meta"}:
        return json.dumps(value, ensure_ascii=False)
    if field == "review":
        if value is None:
            return None
        if isinstance(value, LinkReview):
            return value.model_dump_json()
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return int(value)
    if hasattr(value, "value"):  # enums
        return value.value
    return value


def _valid_id(value: Any) -> bool:
    return isinstance(value, int) and 0 < value <= MAX_ID


def _new_id(cursor: sqlite3.Cursor) -> int:
    """The id of the row an INSERT just made."""
    if cursor.lastrowid is None:
        raise sqlite3.DatabaseError("The new row has no id.")
    return cursor.lastrowid


def _parse_rows(rows: Iterable[sqlite3.Row], parse: Callable[[sqlite3.Row], T]) -> list[T]:
    """Parse rows, skipping any that are damaged so one bad row can't break a whole list."""
    parsed = []
    for row in rows:
        try:
            parsed.append(parse(row))
        except (ValueError, TypeError, KeyError) as exc:
            log.warning("Skipping a damaged database row (id %s): %s", row["id"], exc)
    return parsed


def _fts_query(text: str) -> str | None:
    words = _WORD.findall(text)
    if not words:
        return None
    return " ".join(f'"{word}"*' for word in words[:12])


class Store:
    def __init__(self, db: Database, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or Settings()

    # ------------------------------------------------------------------ notes

    def create_note(self, raw_text: str, source: str = "web", meta: dict | None = None) -> Note:
        now = iso(now_utc())
        cursor = self.db.execute(
            "INSERT INTO notes (created_at, updated_at, source, raw_text, meta)"
            " VALUES (?, ?, ?, ?, ?)",
            (now, now, source, raw_text, json.dumps(meta or {}, ensure_ascii=False)),
        )
        note = self.get_note(_new_id(cursor))
        assert note is not None
        return note

    def get_note(self, note_id: int, *, children: bool = True) -> Note | None:
        if not _valid_id(note_id):
            return None
        row = self.db.one("SELECT * FROM notes WHERE id = ?", (note_id,))
        if row is None:
            return None
        note = _parse_rows([row], self._note)
        if not note:
            return None
        note = note[0]
        if children:
            note.links = self.links_for([note_id]).get(note_id, [])
            note.reminders = self.reminders_for([note_id]).get(note_id, [])
        return note

    def list_notes(
        self,
        *,
        view: str = "inbox",
        kind: str | None = None,
        q: str | None = None,
        category_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Note]:
        """List notes for a dashboard view.

        Views: inbox (open), all (not archived), done, archived, scheduled (has
        a pending reminder), reviews (has links), any (no filter at all).
        """
        where: list[str] = []
        params: list[Any] = []
        join = ""
        order = "n.pinned DESC, n.created_at DESC, n.id DESC"

        if q:
            match = _fts_query(q)
            if match is None:
                return []
            if self.db.fts:
                join = "JOIN notes_fts ON notes_fts.rowid = n.id"
                where.append("notes_fts MATCH ?")
                params.append(match)
                order = "bm25(notes_fts), n.created_at DESC"
            else:
                for word in _WORD.findall(q)[:12]:
                    where.append(
                        "(n.title LIKE ? OR n.text LIKE ? OR n.raw_text LIKE ? OR n.tags LIKE ?"
                        " OR n.search_extra LIKE ?)"
                    )
                    params.extend([f"%{word}%"] * 5)

        if view == "inbox":
            where.append("n.status = 'open'")
        elif view == "done":
            where.append("n.status = 'done'")
        elif view == "archived":
            where.append("n.status = 'archived'")
        elif view == "scheduled":
            where.append(
                "EXISTS (SELECT 1 FROM reminders r WHERE r.note_id = n.id"
                " AND r.status = 'scheduled')"
            )
        elif view == "reviews":
            where.append("EXISTS (SELECT 1 FROM links l WHERE l.note_id = n.id)")
            where.append("n.status != 'archived'")
        elif view == "all" and not q:
            where.append("n.status != 'archived'")

        if kind:
            where.append("n.kind = ?")
            params.append(kind)
        if category_id is not None:
            where.append("n.category_id = ?")
            params.append(category_id)

        sql = f"SELECT n.* FROM notes n {join}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {order} LIMIT ? OFFSET ?"
        params.extend([max(1, min(limit, 500)), max(0, min(offset, MAX_ID))])
        notes = _parse_rows(self.db.query(sql, params), self._note)
        self._attach_children(notes)
        return notes

    def count_notes(self) -> dict[str, int]:
        rows = self.db.query(
            "SELECT kind, COUNT(*) AS c FROM notes WHERE status = 'open' GROUP BY kind"
        )
        counts = {row["kind"]: row["c"] for row in rows}
        counts["inbox"] = sum(counts.values())
        scheduled = self.db.one(
            "SELECT COUNT(DISTINCT note_id) AS c FROM reminders WHERE status = 'scheduled'"
        )
        counts["scheduled"] = scheduled["c"] if scheduled else 0
        reviews = self.db.one(
            "SELECT COUNT(DISTINCT l.note_id) AS c FROM links l JOIN notes n ON n.id = l.note_id"
            " WHERE n.status != 'archived'"
        )
        counts["reviews"] = reviews["c"] if reviews else 0
        return counts

    def update_note(self, note_id: int, **fields: Any) -> Note:
        unknown = set(fields) - NOTE_FIELDS
        if unknown:
            raise ValueError(f"unknown note fields: {sorted(unknown)}")
        if fields:
            fields["updated_at"] = now_utc()
            assignments = ", ".join(f"{name} = ?" for name in fields)
            values = [_encode(name, value) for name, value in fields.items()]
            self.db.execute(f"UPDATE notes SET {assignments} WHERE id = ?", (*values, note_id))
        note = self.get_note(note_id)
        if note is None:
            raise KeyError(note_id)
        return note

    def set_created_at(self, note_id: int, created_at: datetime) -> None:
        self.db.execute("UPDATE notes SET created_at = ? WHERE id = ?", (iso(created_at), note_id))

    def delete_note(self, note_id: int) -> bool:
        if not _valid_id(note_id):
            return False
        return self.db.execute("DELETE FROM notes WHERE id = ?", (note_id,)).rowcount > 0

    # ------------------------------------------------------------- categories

    def list_categories(self) -> list[Category]:
        rows = self.db.query(
            "SELECT c.*, (SELECT COUNT(*) FROM notes n WHERE n.category_id = c.id"
            " AND n.status = 'open') AS count FROM categories c ORDER BY c.position, c.id"
        )
        return _parse_rows(rows, lambda row: Category(**dict(row)))

    def get_category(self, category_id: int) -> Category | None:
        if not _valid_id(category_id):
            return None
        return next((c for c in self.list_categories() if c.id == category_id), None)

    def category_named(self, name: str) -> Category | None:
        wanted = name.strip().casefold()
        return next((c for c in self.list_categories() if c.name.casefold() == wanted), None)

    def add_category(self, name: str, color: str = "blue") -> Category:
        """Raises ValueError when a category with that name already exists."""
        row = self.db.one("SELECT COALESCE(MAX(position), -1) + 1 AS next FROM categories")
        try:
            cursor = self.db.execute(
                "INSERT INTO categories (name, color, position, created_at) VALUES (?, ?, ?, ?)",
                (name, color, row["next"] if row else 0, iso(now_utc())),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"There's already a category called {name!r}.") from exc
        category = self.get_category(_new_id(cursor))
        assert category is not None
        return category

    def update_category(
        self, category_id: int, *, name: str | None = None, color: str | None = None
    ) -> Category:
        if self.get_category(category_id) is None:
            raise KeyError(category_id)
        try:
            if name is not None:
                self.db.execute("UPDATE categories SET name = ? WHERE id = ?", (name, category_id))
            if color is not None:
                self.db.execute(
                    "UPDATE categories SET color = ? WHERE id = ?", (color, category_id)
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"There's already a category called {name!r}.") from exc
        category = self.get_category(category_id)
        assert category is not None
        return category

    def delete_category(self, category_id: int) -> bool:
        """Remove a category; its notes stay, without a category."""
        if not _valid_id(category_id):
            return False
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE notes SET category_id = NULL WHERE category_id = ?", (category_id,)
            )
            return conn.execute("DELETE FROM categories WHERE id = ?", (category_id,)).rowcount > 0

    def order_categories(self, ids: list[int]) -> list[Category]:
        """Put categories in this order; any left out keep their place after them."""
        known = [c.id for c in self.list_categories()]
        order = [i for i in dict.fromkeys(ids) if i in known] + [i for i in known if i not in ids]
        with self.db.transaction() as conn:
            for position, category_id in enumerate(order):
                conn.execute(
                    "UPDATE categories SET position = ? WHERE id = ?", (position, category_id)
                )
        return self.list_categories()

    def note_by_client_id(self, client_id: str, since: datetime) -> Note | None:
        """A note a device already sent under this id (it retries when an answer is lost)."""
        try:
            row = self.db.one(
                "SELECT id FROM notes WHERE created_at >= ?"
                " AND json_extract(meta, '$.client_id') = ? ORDER BY id DESC LIMIT 1",
                (iso(since), client_id),
            )
        except sqlite3.OperationalError:  # SQLite built without JSON functions
            row = self.db.one(
                "SELECT id FROM notes WHERE created_at >= ? AND meta LIKE ?"
                " ORDER BY id DESC LIMIT 1",
                (iso(since), f'%"client_id": "{client_id}"%'),
            )
        return self.get_note(row["id"]) if row else None

    def note_ids_in_state(self, *states: ProcState) -> list[int]:
        marks = ",".join("?" for _ in states)
        rows = self.db.query(
            f"SELECT id FROM notes WHERE state IN ({marks}) ORDER BY id", [s.value for s in states]
        )
        return [row["id"] for row in rows]

    def notes_created_between(self, start: datetime, end: datetime) -> list[Note]:
        rows = self.db.query(
            "SELECT * FROM notes WHERE created_at >= ? AND created_at <= ? ORDER BY created_at",
            (iso(start), iso(end)),
        )
        notes = _parse_rows(rows, self._note)
        self._attach_children(notes)
        return notes

    def digest_notes(self) -> list[Note]:
        rows = self.db.query(
            "SELECT * FROM notes WHERE in_digest = 1 AND status = 'open' ORDER BY created_at"
        )
        notes = _parse_rows(rows, self._note)
        self._attach_children(notes)
        return notes

    def clear_digest_flags(self, note_ids: Iterable[int]) -> None:
        ids = list(note_ids)
        if ids:
            marks = ",".join("?" for _ in ids)
            self.db.execute(f"UPDATE notes SET in_digest = 0 WHERE id IN ({marks})", ids)

    def open_tasks(self, limit: int = 10) -> list[Note]:
        rows = self.db.query(
            "SELECT n.* FROM notes n WHERE n.status = 'open' AND n.kind = 'task'"
            " AND NOT EXISTS (SELECT 1 FROM reminders r WHERE r.note_id = n.id"
            " AND r.status = 'scheduled') ORDER BY n.created_at DESC LIMIT ?",
            (limit,),
        )
        return _parse_rows(rows, self._note)

    def recent_notes(self, limit: int = 200) -> list[Note]:
        rows = self.db.query(
            "SELECT * FROM notes WHERE status != 'archived' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        notes = _parse_rows(rows, self._note)
        self._attach_children(notes)
        return notes

    def search_ids(self, q: str, limit: int = 40) -> list[int]:
        return [note.id for note in self.list_notes(view="all", q=q, limit=limit)]

    # ------------------------------------------------------------------ links

    def add_link(
        self, note_id: int, url: str, kind: str, *, found_by_search: bool = False, title: str = ""
    ) -> Link:
        now = iso(now_utc())
        cursor = self.db.execute(
            "INSERT INTO links (note_id, url, kind, title, found_by_search, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (note_id, url, kind, title, int(found_by_search), now, now),
        )
        link = self.get_link(_new_id(cursor))
        assert link is not None
        return link

    def get_link(self, link_id: int) -> Link | None:
        if not _valid_id(link_id):
            return None
        row = self.db.one("SELECT * FROM links WHERE id = ?", (link_id,))
        return next(iter(_parse_rows([row], self._link)), None) if row else None

    def update_link(self, link_id: int, **fields: Any) -> Link:
        unknown = set(fields) - LINK_FIELDS
        if unknown:
            raise ValueError(f"unknown link fields: {sorted(unknown)}")
        fields["updated_at"] = now_utc()
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [_encode(name, value) for name, value in fields.items()]
        self.db.execute(f"UPDATE links SET {assignments} WHERE id = ?", (*values, link_id))
        link = self.get_link(link_id)
        if link is None:
            raise KeyError(link_id)
        return link

    def delete_links(self, note_id: int) -> None:
        self.db.execute("DELETE FROM links WHERE note_id = ?", (note_id,))

    def links_for(self, note_ids: Sequence[int]) -> dict[int, list[Link]]:
        result: dict[int, list[Link]] = {}
        if not note_ids:
            return result
        marks = ",".join("?" for _ in note_ids)
        rows = self.db.query(
            f"SELECT * FROM links WHERE note_id IN ({marks}) ORDER BY id", list(note_ids)
        )
        for link in _parse_rows(rows, self._link):
            result.setdefault(link.note_id, []).append(link)
        return result

    def link_ids_in_state(self, *states: ProcState) -> list[int]:
        marks = ",".join("?" for _ in states)
        rows = self.db.query(
            f"SELECT id FROM links WHERE state IN ({marks}) ORDER BY id", [s.value for s in states]
        )
        return [row["id"] for row in rows]

    def reviews_since(self, since: datetime) -> list[Link]:
        rows = self.db.query(
            "SELECT * FROM links WHERE state = 'ready' AND updated_at >= ? ORDER BY updated_at",
            (iso(since),),
        )
        return _parse_rows(rows, self._link)

    # -------------------------------------------------------------- reminders

    def add_reminder(
        self,
        note_id: int | None,
        due_at: datetime,
        message: str,
        *,
        origin: str = "manual",
        reason: str = "",
        rrule: str | None = None,
    ) -> Reminder:
        now = iso(now_utc())
        cursor = self.db.execute(
            "INSERT INTO reminders (note_id, due_at, message, origin, reason, rrule, created_at,"
            " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (note_id, iso(due_at), message, origin, reason, rrule, now, now),
        )
        reminder = self.get_reminder(_new_id(cursor))
        assert reminder is not None
        return reminder

    def get_reminder(self, reminder_id: int) -> Reminder | None:
        if not _valid_id(reminder_id):
            return None
        row = self.db.one("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
        return next(iter(_parse_rows([row], self._reminder)), None) if row else None

    def update_reminder(self, reminder_id: int, **fields: Any) -> Reminder:
        unknown = set(fields) - REMINDER_FIELDS
        if unknown:
            raise ValueError(f"unknown reminder fields: {sorted(unknown)}")
        fields["updated_at"] = now_utc()
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [_encode(name, value) for name, value in fields.items()]
        self.db.execute(f"UPDATE reminders SET {assignments} WHERE id = ?", (*values, reminder_id))
        reminder = self.get_reminder(reminder_id)
        if reminder is None:
            raise KeyError(reminder_id)
        return reminder

    def delete_reminder(self, reminder_id: int) -> bool:
        if not _valid_id(reminder_id):
            return False
        return self.db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,)).rowcount > 0

    def reminders_for(self, note_ids: Sequence[int]) -> dict[int, list[Reminder]]:
        result: dict[int, list[Reminder]] = {}
        if not note_ids:
            return result
        marks = ",".join("?" for _ in note_ids)
        rows = self.db.query(
            f"SELECT * FROM reminders WHERE note_id IN ({marks}) ORDER BY due_at", list(note_ids)
        )
        for reminder in _parse_rows(rows, self._reminder):
            if reminder.note_id is not None:  # always, given the query
                result.setdefault(reminder.note_id, []).append(reminder)
        return result

    def due_reminders(self, now: datetime) -> list[Reminder]:
        rows = self.db.query(
            "SELECT * FROM reminders WHERE status = 'scheduled' AND due_at <= ? ORDER BY due_at",
            (iso(now),),
        )
        return _parse_rows(rows, self._reminder)

    def list_reminders(
        self,
        statuses: Sequence[ReminderStatus] | None = None,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 200,
        newest_first: bool = False,
    ) -> list[Reminder]:
        where: list[str] = []
        params: list[Any] = []
        if statuses:
            where.append(f"status IN ({','.join('?' for _ in statuses)})")
            params.extend(s.value for s in statuses)
        if start is not None:
            where.append("due_at >= ?")
            params.append(iso(start))
        if end is not None:
            where.append("due_at < ?")
            params.append(iso(end))
        sql = "SELECT * FROM reminders"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY due_at {'DESC' if newest_first else 'ASC'} LIMIT ?"
        params.append(limit)
        return _parse_rows(self.db.query(sql, params), self._reminder)

    def cancel_open_reminders(self, note_id: int, origins: Sequence[str]) -> int:
        """Remove not-yet-sent reminders of the given origins (used when re-running triage)."""
        marks = ",".join("?" for _ in origins)
        cursor = self.db.execute(
            f"DELETE FROM reminders WHERE note_id = ? AND status = 'scheduled'"
            f" AND origin IN ({marks})",
            (note_id, *origins),
        )
        return cursor.rowcount

    # ---------------------------------------------------------------- digests

    def add_digest(self, kind: str, content: str, *, sent: bool = False) -> Digest:
        cursor = self.db.execute(
            "INSERT INTO digests (kind, created_at, content, sent) VALUES (?, ?, ?, ?)",
            (kind, iso(now_utc()), content, int(sent)),
        )
        row = self.db.one("SELECT * FROM digests WHERE id = ?", (cursor.lastrowid,))
        assert row is not None
        return self._digest(row)

    def mark_digest_sent(self, digest_id: int) -> None:
        self.db.execute("UPDATE digests SET sent = 1 WHERE id = ?", (digest_id,))

    def list_digests(self, limit: int = 30) -> list[Digest]:
        rows = self.db.query("SELECT * FROM digests ORDER BY id DESC LIMIT ?", (limit,))
        return _parse_rows(rows, self._digest)

    # --------------------------------------------------------------------- kv

    def get_kv(self, key: str, default: Any = None) -> Any:
        row = self.db.one("SELECT value FROM kv WHERE key = ?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except ValueError:
            log.warning("Ignoring a damaged setting %r", key)
            return default

    def set_kv(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def delete_kv(self, key: str) -> None:
        self.db.execute("DELETE FROM kv WHERE key = ?", (key,))

    def get_prefs(self) -> Prefs:
        stored = self.get_kv("prefs", {})
        if not isinstance(stored, dict):
            stored = {}
        base = {"timezone": self.settings.timezone, "web_search": self.settings.web_search}
        try:
            return Prefs.model_validate({**base, **stored})
        except ValidationError:
            # A bad stored value (e.g. a time zone removed from tzdata) must not
            # take the app down: fall back field by field.
            try:
                prefs = Prefs.model_validate(base)
            except ValidationError:
                prefs = Prefs()
            for key, value in stored.items():
                try:
                    prefs = Prefs.model_validate({**prefs.model_dump(), key: value})
                except ValidationError:
                    continue
            return prefs

    def save_prefs(self, prefs: Prefs) -> Prefs:
        self.set_kv("prefs", prefs.model_dump())
        return prefs

    # --------------------------------------------------------- push endpoints

    def add_push_subscription(self, endpoint: str, p256dh: str, auth: str, user_agent: str) -> None:
        self.db.execute(
            "INSERT INTO push_subscriptions (endpoint, p256dh, auth, user_agent, created_at)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT(endpoint) DO UPDATE SET p256dh = excluded.p256dh,"
            " auth = excluded.auth, user_agent = excluded.user_agent",
            (endpoint, p256dh, auth, user_agent[:300], iso(now_utc())),
        )

    def remove_push_subscription(self, endpoint: str) -> None:
        self.db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))

    def clear_push_subscriptions(self) -> None:
        self.db.execute("DELETE FROM push_subscriptions")

    def push_subscriptions(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT endpoint, p256dh, auth FROM push_subscriptions")
        return [
            {"endpoint": row["endpoint"], "keys": {"p256dh": row["p256dh"], "auth": row["auth"]}}
            for row in rows
        ]

    # ---------------------------------------------------------------- helpers

    def _attach_children(self, notes: list[Note]) -> None:
        ids = [note.id for note in notes]
        links = self.links_for(ids)
        reminders = self.reminders_for(ids)
        for note in notes:
            note.links = links.get(note.id, [])
            note.reminders = reminders.get(note.id, [])

    @staticmethod
    def _note(row: sqlite3.Row) -> Note:
        return Note(
            id=row["id"],
            created_at=parse_iso(row["created_at"]),
            updated_at=parse_iso(row["updated_at"]),
            source=row["source"],
            raw_text=row["raw_text"],
            text=row["text"],
            title=row["title"],
            summary=row["summary"],
            kind=row["kind"],
            tags=json.loads(row["tags"] or "[]"),
            status=row["status"],
            pinned=bool(row["pinned"]),
            state=row["state"],
            error=row["error"],
            ping=row["ping"],
            ping_reason=row["ping_reason"],
            in_digest=bool(row["in_digest"]),
            briefing=row["briefing"],
            reply=row["reply"],
            category_id=row["category_id"],
            meta=json.loads(row["meta"] or "{}"),
        )

    @staticmethod
    def _link(row: sqlite3.Row) -> Link:
        review = None
        if row["review"]:
            try:
                review = LinkReview.model_validate_json(row["review"])
            except ValidationError:
                review = None
        return Link(
            id=row["id"],
            note_id=row["note_id"],
            url=row["url"],
            kind=row["kind"],
            title=row["title"],
            author=row["author"],
            site=row["site"],
            minutes=row["minutes"],
            state=row["state"],
            error=row["error"],
            review=review,
            found_by_search=bool(row["found_by_search"]),
            created_at=parse_iso(row["created_at"]),
            updated_at=parse_iso(row["updated_at"]),
        )

    @staticmethod
    def _reminder(row: sqlite3.Row) -> Reminder:
        return Reminder(
            id=row["id"],
            note_id=row["note_id"],
            due_at=parse_iso(row["due_at"]),
            message=row["message"],
            origin=row["origin"],
            reason=row["reason"],
            rrule=row["rrule"],
            status=row["status"],
            attempts=row["attempts"],
            last_error=row["last_error"],
            sent_at=parse_iso(row["sent_at"]) if row["sent_at"] else None,
            created_at=parse_iso(row["created_at"]),
            updated_at=parse_iso(row["updated_at"]),
        )

    @staticmethod
    def _digest(row: sqlite3.Row) -> Digest:
        return Digest(
            id=row["id"],
            kind=row["kind"],
            created_at=parse_iso(row["created_at"]),
            content=row["content"],
            sent=bool(row["sent"]),
        )


__all__ = ["Store", "NoteStatus", "ReminderStatus"]
