"""Your data, portable: JSON and Markdown export, JSON import."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from typing import Any

from magpie import __version__
from magpie.content.links import is_http_url
from magpie.models import Note, ProcState, ReminderStatus
from magpie.schemas import LinkReview
from magpie.store import Store
from magpie.timeutil import MAX_YEAR, MIN_YEAR, iso, now_utc, to_local, validate_rrule

log = logging.getLogger(__name__)

FORMAT_VERSION = 1


def export_json(store: Store) -> str:
    notes = store.list_notes(view="any", limit=100_000)
    payload = {
        "app": "magpie",
        "format": FORMAT_VERSION,
        "version": __version__,
        "exported_at": iso(now_utc()),
        "notes": [note.model_dump(mode="json") for note in notes],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def export_markdown(store: Store, tz: str) -> str:
    notes = store.list_notes(view="any", limit=100_000)
    today = to_local(now_utc(), tz).strftime("%Y-%m-%d")
    out = [f"# Magpie notes\n\nExported {today} ({len(notes)} notes).\n"]
    for note in notes:
        created = to_local(note.created_at, tz).strftime("%Y-%m-%d %H:%M")
        facts = [note.kind.value, created, note.status.value] + [f"#{tag}" for tag in note.tags]
        out.append(f"## {note.display_title}\n")
        out.append(f"*{' · '.join(facts)}*\n")
        out.append((note.text or note.raw_text).strip() + "\n")
        if note.summary:
            out.append(f"> {note.summary}\n")
        for reminder in note.reminders:
            when = to_local(reminder.due_at, tz).strftime("%Y-%m-%d %H:%M")
            repeat = f", repeats {reminder.rrule}" if reminder.rrule else ""
            out.append(f"- Reminder {when}: {reminder.message} ({reminder.status.value}{repeat})")
        for link in note.links:
            line = f"- Link: [{link.title or link.url}]({link.url})"
            if link.review:
                line += f": {link.review.tldr} ({link.review.verdict}, {link.review.rating}/5)"
            out.append(line)
        if note.briefing:
            out.append(f"\n{note.briefing}")
        out.append("\n---\n")
    return "\n".join(out)


def _when(value: datetime | None, fallback: datetime) -> datetime:
    """Imported times may lack a zone (taken as UTC) or be out of range."""
    if value is None:
        return fallback
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value if MIN_YEAR <= value.year <= MAX_YEAR else fallback


def import_json(store: Store, raw: str | bytes) -> dict[str, int]:
    try:
        payload = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise ValueError("That file is not valid JSON.") from exc
    if not isinstance(payload, dict) or payload.get("app") != "magpie":
        raise ValueError("That file is not a Magpie export.")
    items = payload.get("notes")
    if not isinstance(items, list):
        raise ValueError("That file has no list of notes in it.")
    existing = {
        (row["created_at"], row["raw_text"])
        for row in store.db.query("SELECT created_at, raw_text FROM notes")
    }
    imported = skipped = 0
    now = now_utc()
    for item in items:
        try:
            note = Note.model_validate(item)
        except ValueError:
            skipped += 1
            continue
        note.created_at = _when(note.created_at, now)
        key = (iso(note.created_at), note.raw_text)
        if not note.raw_text.strip() or key in existing:
            skipped += 1
            continue
        try:
            # All of a note or none of it: a bad reminder must not leave half a note behind.
            with store.db.transaction():
                _insert(store, note, now)
        except (ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            log.warning("Skipping an imported note that could not be saved: %s", exc)
            skipped += 1
            continue
        existing.add(key)
        imported += 1
    return {"imported": imported, "skipped": skipped}


def _insert(store: Store, note: Note, now: datetime) -> None:
    source = (note.source or "import")[:24]
    created = store.create_note(note.raw_text, source, note.meta)
    store.set_created_at(created.id, note.created_at)
    fields: dict[str, Any] = {
        "text": note.text,
        "title": note.title,
        "summary": note.summary,
        "kind": note.kind,
        "tags": note.tags,
        "status": note.status,
        "pinned": note.pinned,
        "ping": note.ping,
        "ping_reason": note.ping_reason,
        "briefing": note.briefing,
        "reply": note.reply,
        "state": ProcState.READY if note.text or note.title else ProcState.PENDING,
    }
    store.update_note(created.id, **fields)
    for reminder in note.reminders:
        due = _when(reminder.due_at, now)
        rule = validate_rrule(reminder.rrule)
        if reminder.status == ReminderStatus.SCHEDULED and (due > now or rule):
            store.add_reminder(
                created.id,
                due,
                reminder.message[:500] or "Reminder",
                origin=reminder.origin[:20],
                reason=reminder.reason[:300],
                rrule=rule,
            )
    for link in note.links:
        if not is_http_url(link.url):
            continue  # only web links are ever opened or fetched
        added = store.add_link(created.id, link.url, link.kind, title=link.title)
        review = link.review if isinstance(link.review, LinkReview) else None
        store.update_link(
            added.id,
            state=ProcState.READY if review else ProcState.PENDING,
            review=review,
            author=link.author,
            site=link.site,
            minutes=link.minutes,
        )
