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
# Years outside this range are mistakes, and near the ends of datetime's range a
# time-zone conversion overflows.
MIN_YEAR, MAX_YEAR = 1970, 9000

# Repeat rules come from Claude and from users, so only a safe subset of RRULE is
# accepted: nothing more often than hourly, and nothing that dateutil would have
# to scan for centuries (INTERVAL=0 even loops forever).
_RULE_FREQS = {"YEARLY", "MONTHLY", "WEEKLY", "DAILY", "HOURLY"}
_RULE_WEEKDAY = re.compile(r"^([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)$")
_RULE_UNTIL = re.compile(r"^(\d{8})(T\d{6})?Z?$")
_DAYS_IN_MONTH = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)  # in a leap year
MIN_REPEAT_GAP = timedelta(hours=1)


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


def parse_hhmm(value: str | None) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
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
    if not isinstance(value, str):
        raise ValueError(f"not a local date-time: {value!r}")
    cleaned = value.strip()
    cleaned = re.sub(r"(Z|[+-]\d{2}:?\d{2})$", "", cleaned)
    match = _LOCAL.match(cleaned)
    if not match:
        raise ValueError(f"not a local date-time: {value!r}")
    year, month, day, hour, minute, second = (int(part or 0) for part in match.groups())
    if not MIN_YEAR <= year <= MAX_YEAR:
        raise ValueError(f"the year {year} is out of range")
    return datetime(year, month, day, hour, minute, second)


def localize(naive: datetime, tz_name: str) -> datetime:
    """Attach a zone to wall-clock time. Times inside a DST gap move forward."""
    tz = get_tz(tz_name)
    aware = naive.replace(tzinfo=tz)
    # Round-trip through UTC: a time that does not exist (spring forward) comes
    # back shifted, which is the closest real moment.
    try:
        roundtrip = aware.astimezone(UTC).astimezone(tz)
    except OverflowError as exc:
        raise ValueError(f"{naive} is out of range") from exc
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


def _rule_numbers(value: str, low: int, high: int, *, negative: bool = False) -> list[int] | None:
    numbers = []
    for item in value.split(","):
        try:
            number = int(item)
        except ValueError:
            return None
        if number < 0 and not negative:
            return None
        if not low <= abs(number) <= high:
            return None
        numbers.append(number)
    return numbers


def _parse_rule(rule: str) -> dict[str, str] | None:
    """Split an RRULE into its parts, keeping only the safe subset (see above)."""
    cleaned = rule.strip().upper()
    if cleaned.startswith("RRULE:"):
        cleaned = cleaned[6:]
    parts: dict[str, str] = {}
    for piece in cleaned.split(";"):
        if not piece.strip():
            continue
        name, sep, value = piece.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not value or name in parts:
            return None
        parts[name] = value
    freq = parts.get("FREQ")
    if freq not in _RULE_FREQS:
        return None
    allowed = {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "BYMONTHDAY", "BYMONTH"}
    allowed |= {"BYHOUR", "BYMINUTE", "BYSETPOS", "WKST"}
    if set(parts) - allowed or ("COUNT" in parts and "UNTIL" in parts):
        return None
    checks = {
        "INTERVAL": (1, 1000, False),
        "COUNT": (1, 1000, False),
        "BYMONTH": (1, 12, False),
        "BYMONTHDAY": (1, 31, True),
        "BYHOUR": (0, 23, False),
        "BYMINUTE": (0, 59, False),
        "BYSETPOS": (1, 31, True),
    }
    numbers: dict[str, list[int]] = {}
    for name, (low, high, negative) in checks.items():
        if name in parts:
            found = _rule_numbers(parts[name], low, high, negative=negative)
            if found is None:
                return None
            numbers[name] = found
    for name in ("INTERVAL", "COUNT", "BYMINUTE"):
        # More than one minute per hour would repeat more often than hourly.
        if len(numbers.get(name, [])) > 1:
            return None
    if "UNTIL" in parts:
        match = _RULE_UNTIL.match(parts["UNTIL"])
        if not match:
            return None
        # Evaluated as wall-clock time, like the reminder itself.
        parts["UNTIL"] = match.group(1) + (match.group(2) or "")
    weekdays = 0
    for item in parts.get("BYDAY", "").split(",") if "BYDAY" in parts else []:
        match = _RULE_WEEKDAY.match(item)
        if not match:
            return None
        if match.group(1):
            limit = 53 if freq == "YEARLY" and "BYMONTH" not in parts else 5
            ordinal = int(match.group(1))
            if freq not in ("MONTHLY", "YEARLY") or not 1 <= abs(ordinal) <= limit:
                return None
            if "BYMONTHDAY" in parts:
                return None  # "the 5th Monday, on the 1st to 7th" can never happen
        weekdays += 1
    if freq == "DAILY" and weekdays and numbers.get("INTERVAL", [1])[0] % 7 == 0:
        return None  # every 7th day only ever lands on one weekday
    if "WKST" in parts and parts["WKST"] not in ("MO", "TU", "WE", "TH", "FR", "SA", "SU"):
        return None
    if "BYSETPOS" in parts:
        # Only "the 2nd Tuesday" or "the last weekday" style monthly rules, and
        # only positions that can exist, so the rule always matches some month.
        if freq != "MONTHLY":
            return None
        if "BYMONTHDAY" in parts:
            available = len(numbers["BYMONTHDAY"])
        elif weekdays:
            available = 4 * weekdays
        else:
            return None
        if any(abs(position) > available for position in numbers["BYSETPOS"]):
            return None
    # "February 30th" never happens; dateutil would search until the year 9999.
    if (
        "BYMONTH" in parts
        and "BYMONTHDAY" in parts
        and not any(
            abs(day) <= _DAYS_IN_MONTH[month - 1]
            for month in numbers["BYMONTH"]
            for day in numbers["BYMONTHDAY"]
        )
    ):
        return None
    return parts


def validate_rrule(rule: str | None) -> str | None:
    """Return a cleaned RRULE (without the "RRULE:" prefix) or None if unusable.

    Unusable means malformed, outside the safe subset, more often than hourly,
    or with no occurrence left.
    """
    if not rule or not isinstance(rule, str) or len(rule) > 300:
        return None
    parts = _parse_rule(rule)
    if parts is None:
        return None
    cleaned = ";".join(f"{name}={value}" for name, value in parts.items())
    start = datetime.now(UTC).replace(tzinfo=None, second=0, microsecond=0)
    try:
        recurrence = rrulestr(f"RRULE:{cleaned}", dtstart=start)
        upcoming = []
        for occurrence in recurrence:
            upcoming.append(occurrence)
            if len(upcoming) == 4:
                break
    except (ValueError, TypeError, OverflowError):
        return None
    if not upcoming:
        return None
    gaps = (later - earlier for earlier, later in zip(upcoming, upcoming[1:], strict=False))
    if any(gap < MIN_REPEAT_GAP for gap in gaps):
        return None
    return cleaned


def anchor_rrule(rule: str, first: datetime, tz_name: str) -> str:
    """Turn COUNT=n into the matching UNTIL.

    The scheduler evaluates a rule from each reminder's current due time, so a
    count would start again after every reminder; an end date does not.
    """
    parts = _parse_rule(rule)
    if parts is None or "COUNT" not in parts:
        return rule
    counted = ";".join(f"{name}={value}" for name, value in parts.items())
    count = int(parts.pop("COUNT"))
    start = first.astimezone(get_tz(tz_name)).replace(tzinfo=None)
    try:
        recurrence = rrulestr(f"RRULE:{counted}", dtstart=start)
        last = None
        for index, occurrence in enumerate(recurrence, start=1):
            last = occurrence
            if index >= count:
                break
    except (ValueError, TypeError, OverflowError):
        return rule
    if last is None:
        return rule
    parts["UNTIL"] = last.strftime("%Y%m%dT%H%M%S")
    return ";".join(f"{name}={value}" for name, value in parts.items())


def next_occurrence(rule: str, dtstart: datetime, after: datetime, tz_name: str) -> datetime | None:
    """Next occurrence of ``rule`` strictly after ``after``.

    ``dtstart`` is the first (UTC) occurrence. The recurrence is evaluated in
    local wall-clock time so "every day at 9" stays at 9 across DST changes.
    """
    cleaned = validate_rrule(rule)
    if cleaned is None:
        return None
    tz = get_tz(tz_name)
    start_local = dtstart.astimezone(tz).replace(tzinfo=None)
    after_local = after.astimezone(tz).replace(tzinfo=None)
    try:
        recurrence = rrulestr(f"RRULE:{cleaned}", dtstart=start_local)
        following = recurrence.after(after_local)
        if following is None:
            return None
        return localize(following, tz_name).astimezone(UTC)
    except (ValueError, TypeError, OverflowError):
        return None


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
