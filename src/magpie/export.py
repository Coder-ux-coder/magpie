"""Your data, portable: JSON and Markdown export, JSON import."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from magpie import __version__
from magpie.models import Note, ProcState, ReminderStatus
from magpie.schemas import LinkReview
from magpie.store import Store
from magpie.timeutil import iso, now_utc, to_local

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


def import_json(store: Store, raw: str | bytes) -> dict[str, int]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("That file is not valid JSON.") from exc
    if not isinstance(payload, dict) or payload.get("app") != "magpie":
        raise ValueError("That file is not a Magpie export.")
    existing = {
        (row["created_at"], row["raw_text"])
        for row in store.db.query("SELECT created_at, raw_text FROM notes")
    }
    imported = skipped = 0
    now = now_utc()
    for item in payload.get("notes", []):
        try:
            note = Note.model_validate(item)
        except ValueError:
            skipped += 1
            continue
        if (iso(note.created_at), note.raw_text) in existing:
            skipped += 1
            continue
        _insert(store, note, now)
        imported += 1
    return {"imported": imported, "skipped": skipped}


def _insert(store: Store, note: Note, now: datetime) -> None:
    created = store.create_note(note.raw_text, note.source or "import", note.meta)
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
        if reminder.status == ReminderStatus.SCHEDULED and (
            reminder.due_at > now or reminder.rrule
        ):
            store.add_reminder(
                created.id,
                reminder.due_at,
                reminder.message,
                origin=reminder.origin,
                reason=reminder.reason,
                rrule=reminder.rrule,
            )
    for link in note.links:
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
