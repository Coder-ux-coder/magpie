from datetime import UTC, datetime, timedelta

import pytest

from magpie.timeutil import (
    describe_now,
    humanize,
    local_to_utc,
    next_occurrence,
    parse_hhmm,
    parse_local,
    tomorrow_at,
    validate_rrule,
)


def test_local_to_utc_uses_the_users_zone():
    assert local_to_utc("2026-10-19T17:00", "Asia/Karachi") == datetime(
        2026, 10, 19, 12, 0, tzinfo=UTC
    )
    assert local_to_utc("2026-10-19T17:00", "UTC") == datetime(2026, 10, 19, 17, 0, tzinfo=UTC)


def test_offsets_from_the_model_are_ignored():
    # Claude is asked for wall-clock time; a stray offset must not shift it.
    assert local_to_utc("2026-10-19T17:00+00:00", "Asia/Karachi") == local_to_utc(
        "2026-10-19T17:00", "Asia/Karachi"
    )
    assert parse_local("2026-10-19 07:05:30") == datetime(2026, 10, 19, 7, 5, 30)


def test_time_inside_a_dst_gap_moves_forward():
    # 02:30 does not exist in New York on 8 March 2026.
    assert local_to_utc("2026-03-08T02:30", "America/New_York") == datetime(
        2026, 3, 8, 7, 30, tzinfo=UTC
    )


@pytest.mark.parametrize("bad", ["tomorrow", "2026-13-01T10:00", "17:00", ""])
def test_unreadable_times_raise(bad):
    with pytest.raises(ValueError):
        local_to_utc(bad, "UTC")


def test_weekly_reminder_keeps_local_time_across_dst():
    first = local_to_utc("2026-03-02T09:00", "America/New_York")  # EST
    following = next_occurrence("FREQ=WEEKLY;BYDAY=MO", first, first, "America/New_York")
    assert following == datetime(2026, 3, 9, 13, 0, tzinfo=UTC)  # 09:00 EDT


def test_next_occurrence_skips_to_the_future():
    first = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    now = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    assert next_occurrence("FREQ=DAILY", first, now, "UTC") == datetime(
        2026, 1, 11, 9, 0, tzinfo=UTC
    )
    assert next_occurrence("FREQ=DAILY;COUNT=2", first, now, "UTC") is None


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ("FREQ=DAILY", "FREQ=DAILY"),
        ("RRULE:FREQ=WEEKLY;BYDAY=MO,WE", "FREQ=WEEKLY;BYDAY=MO,WE"),
        ("FREQ=MINUTELY", None),
        ("FREQ=SECONDLY;INTERVAL=5", None),
        ("DTSTART:20260101T090000\nRRULE:FREQ=DAILY", None),
        ("nonsense", None),
        ("", None),
        (None, None),
    ],
)
def test_validate_rrule(rule, expected):
    assert validate_rrule(rule) == expected


def test_humanize_and_describe():
    now = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)  # 11:00 in Karachi
    assert (
        humanize(datetime(2026, 10, 19, 12, 0, tzinfo=UTC), "Asia/Karachi", now) == "today 5:00 PM"
    )
    assert (
        humanize(datetime(2026, 10, 20, 4, 0, tzinfo=UTC), "Asia/Karachi", now)
        == "tomorrow 9:00 AM"
    )
    assert humanize(datetime(2026, 10, 23, 4, 0, tzinfo=UTC), "Asia/Karachi", now).startswith(
        "Friday"
    )
    assert "Asia/Karachi, UTC+05:00" in describe_now("Asia/Karachi", now)


def test_tomorrow_at_and_hhmm():
    now = datetime(2026, 10, 19, 20, 0, tzinfo=UTC)  # already the 20th in Karachi
    assert tomorrow_at("09:00", "Asia/Karachi", now) == datetime(2026, 10, 21, 4, 0, tzinfo=UTC)
    assert parse_hhmm("7:05") == (7, 5)
    assert parse_hhmm("24:00") is None
    assert tomorrow_at("09:00", "UTC", now) - now == timedelta(hours=13)
