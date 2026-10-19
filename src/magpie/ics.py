"""An iCalendar feed of reminders, for Google Calendar, Apple Calendar, Outlook..."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from magpie.models import Note, Reminder
from magpie.timeutil import localize, now_utc, to_local

EVENT_LENGTH = timedelta(minutes=15)
# Control characters are not allowed in iCalendar text (tabs and newlines are handled).
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _escape(text: str) -> str:
    return (
        _CONTROL.sub("", text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """Fold lines longer than 75 octets, as RFC 5545 requires."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    parts: list[str] = []
    current = b""
    for char in line:
        piece = char.encode("utf-8")
        limit = 75 if not parts else 74
        if len(current) + len(piece) > limit:
            parts.append(current.decode("utf-8"))
            current = b""
        current += piece
    parts.append(current.decode("utf-8"))
    return "\r\n ".join(parts)


def _utc(dt) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _rule(rule: str, timezone: str) -> str:
    """Magpie keeps UNTIL in local time; with a TZID start, RFC 5545 wants it in UTC."""
    parts = []
    for part in rule.split(";"):
        name, _, value = part.partition("=")
        if name.upper() == "UNTIL" and not value.endswith("Z"):
            try:
                if "T" not in value:
                    value += "T235959"
                local = datetime.strptime(value, "%Y%m%dT%H%M%S")
                value = _utc(localize(local, timezone).astimezone(UTC))
            except ValueError:
                continue
        parts.append(f"{name}={value}")
    return ";".join(parts)


def reminders_calendar(
    reminders: Iterable[Reminder],
    notes: dict[int, Note],
    *,
    timezone: str,
    base_url: str = "",
) -> str:
    stamp = _utc(now_utc())
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Magpie//Voice notes//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Magpie reminders",
        f"X-WR-TIMEZONE:{timezone}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT15M",
        "X-PUBLISHED-TTL:PT15M",
    ]
    for reminder in reminders:
        note = notes.get(reminder.note_id) if reminder.note_id else None
        lines += ["BEGIN:VEVENT", f"UID:reminder-{reminder.id}@magpie", f"DTSTAMP:{stamp}"]
        if reminder.rrule:
            # Recurring events are expressed in local time so "every Monday at
            # 9" stays on Monday in every zone.
            start = to_local(reminder.due_at, timezone)
            end = start + EVENT_LENGTH
            lines.append(f"DTSTART;TZID={timezone}:{start.strftime('%Y%m%dT%H%M%S')}")
            lines.append(f"DTEND;TZID={timezone}:{end.strftime('%Y%m%dT%H%M%S')}")
            lines.append(f"RRULE:{_rule(reminder.rrule, timezone)}")
        else:
            lines.append(f"DTSTART:{_utc(reminder.due_at)}")
            lines.append(f"DTEND:{_utc(reminder.due_at + EVENT_LENGTH)}")
        lines.append(f"SUMMARY:{_escape(reminder.message)}")
        description = []
        if note is not None:
            description.append(note.text or note.raw_text)
        if reminder.reason:
            description.append(f"Why: {reminder.reason}")
        if description:
            lines.append(f"DESCRIPTION:{_escape(chr(10).join(description))}")
        if base_url and reminder.note_id:
            lines.append(f"URL:{base_url}/#/note/{reminder.note_id}")
        status = "CANCELLED" if reminder.status.value == "cancelled" else "CONFIRMED"
        lines.append(f"STATUS:{status}")
        lines += [
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_escape(reminder.message)}",
            "TRIGGER:PT0M",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
