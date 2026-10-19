"""Time zone helpers.

Everything is stored in UTC. Claude reads and writes *local wall-clock* times
("2026-10-19T17:00") and this module converts them using the user's IANA time
zone, which keeps offsets and daylight saving out of the model's hands.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr

_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_LOCAL = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{1,2}):(\d{2})(?::(\d{2}))?$")
_FORBIDDEN_FREQ = re.compile(r"FREQ=(SECONDLY|MINUTELY)", re.IGNORECASE)


def now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso(dt: datetime) -> str:
    """Canonical storage format: UTC, second precision, sortable as text."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return dt.astimezone(UTC).replace(microsecond=0).isoformat()


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def is_valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def get_tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def parse_hhmm(value: str) -> tuple[int, int] | None:
    match = _HHMM.match(value.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def local_now(tz_name: str) -> datetime:
    return now_utc().astimezone(get_tz(tz_name))


def parse_local(value: str) -> datetime:
    """Parse "YYYY-MM-DDTHH:MM" (optionally with seconds) into a naive datetime.

    Any offset or trailing "Z" is ignored on purpose: the value is wall-clock
    time in the user's zone.
    """
    cleaned = value.strip()
    cleaned = re.sub(r"(Z|[+-]\d{2}:?\d{2})$", "", cleaned)
    match = _LOCAL.match(cleaned)
    if not match:
        raise ValueError(f"not a local date-time: {value!r}")
    year, month, day, hour, minute, second = match.groups()
    return datetime(int(year), int(month), int(day), int(hour), int(minute), int(second or 0))


def localize(naive: datetime, tz_name: str) -> datetime:
    """Attach a zone to wall-clock time. Times inside a DST gap move forward."""
    tz = get_tz(tz_name)
    aware = naive.replace(tzinfo=tz)
    # Round-trip through UTC: a time that does not exist (spring forward) comes
    # back shifted, which is the closest real moment.
    roundtrip = aware.astimezone(UTC).astimezone(tz)
    if roundtrip.replace(tzinfo=None) != naive:
        aware = roundtrip
    return aware


def local_to_utc(value: str, tz_name: str) -> datetime:
    return localize(parse_local(value), tz_name).astimezone(UTC)


def to_local(dt: datetime, tz_name: str) -> datetime:
    return dt.astimezone(get_tz(tz_name))


def at_local_time(day: date, hhmm: str, tz_name: str) -> datetime:
    """UTC datetime for HH:MM local time on a given local date."""
    parsed = parse_hhmm(hhmm) or (9, 0)
    naive = datetime(day.year, day.month, day.day, parsed[0], parsed[1])
    return localize(naive, tz_name).astimezone(UTC)


def tomorrow_at(hhmm: str, tz_name: str, now: datetime | None = None) -> datetime:
    current = (now or now_utc()).astimezone(get_tz(tz_name))
    return at_local_time(current.date() + timedelta(days=1), hhmm, tz_name)


def validate_rrule(rule: str | None) -> str | None:
    """Return a cleaned RRULE (without the "RRULE:" prefix) or None if unusable."""
    if not rule:
        return None
    cleaned = rule.strip()
    if cleaned.upper().startswith("RRULE:"):
        cleaned = cleaned[6:]
    if not cleaned or "DTSTART" in cleaned.upper() or _FORBIDDEN_FREQ.search(cleaned):
        return None
    try:
        rrulestr(f"RRULE:{cleaned}", dtstart=datetime(2026, 1, 1, 9, 0))
    except (ValueError, TypeError):
        return None
    return cleaned


def next_occurrence(rule: str, dtstart: datetime, after: datetime, tz_name: str) -> datetime | None:
    """Next occurrence of ``rule`` strictly after ``after``.

    ``dtstart`` is the first (UTC) occurrence. The recurrence is evaluated in
    local wall-clock time so "every day at 9" stays at 9 across DST changes.
    """
    tz = get_tz(tz_name)
    start_local = dtstart.astimezone(tz).replace(tzinfo=None)
    after_local = after.astimezone(tz).replace(tzinfo=None)
    try:
        recurrence = rrulestr(f"RRULE:{rule}", dtstart=start_local)
    except (ValueError, TypeError):
        return None
    following = recurrence.after(after_local)
    if following is None:
        return None
    return localize(following, tz_name).astimezone(UTC)


def humanize(dt: datetime, tz_name: str, now: datetime | None = None) -> str:
    """Short, friendly local time: "today 5:00 PM", "tomorrow 9:00 AM", "Fri 23 Oct, 9:00 AM"."""
    tz = get_tz(tz_name)
    local = dt.astimezone(tz)
    today = (now or now_utc()).astimezone(tz).date()
    clock = local.strftime("%I:%M %p").lstrip("0")
    delta = (local.date() - today).days
    if delta == 0:
        return f"today {clock}"
    if delta == 1:
        return f"tomorrow {clock}"
    if delta == -1:
        return f"yesterday {clock}"
    if 1 < delta < 7:
        return f"{local.strftime('%A')} {clock}"
    fmt = "%a %d %b" if local.year == today.year else "%a %d %b %Y"
    return f"{local.strftime(fmt)}, {clock}"


def describe_now(tz_name: str, now: datetime | None = None) -> str:
    local = (now or now_utc()).astimezone(get_tz(tz_name))
    offset = local.strftime("%z")
    offset = f"UTC{offset[:3]}:{offset[3:]}" if offset else "UTC"
    return f"{local.strftime('%A %d %B %Y, %H:%M')} ({tz_name}, {offset})"
