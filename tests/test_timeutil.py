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


@pytest.mark.parametrize(
    "rule",
    [
        "FREQ=WEEKLY;INTERVAL=0",  # dateutil loops forever on this
        "FREQ=DAILY;BYMONTH=2;BYMONTHDAY=30",  # never happens: years of scanning
        "FREQ=HOURLY;BYMONTH=4;BYMONTHDAY=31",
        "FREQ=HOURLY;BYMINUTE=0,30",  # twice an hour
        "FREQ=DAILY;BYSECOND=0,1",
        "FREQ=MINUTELY",
        "FREQ=WEEKLY;BYDAY=XX",
        "FREQ=WEEKLY;COUNT=0",
        "FREQ=WEEKLY;COUNT=2;UNTIL=20300101",
        "FREQ=MONTHLY;BYDAY=5MO;BYMONTHDAY=1,2,3,4,5,6,7",
        "FREQ=DAILY;INTERVAL=7;BYDAY=TU,WE",
        "FREQ=MONTHLY;BYDAY=MO;BYSETPOS=9",
        "FREQ=WEEKLY;UNTIL=20200101",  # already over
        "FREQ=DAILY;DTSTART=20260101T090000",
        "FREQ=DAILY;FOO=1",
        "garbage",
        "FREQ=DAILY;" * 100,
    ],
)
def test_unsafe_or_impossible_repeat_rules_are_rejected_quickly(rule):
    import time

    started = time.perf_counter()
    assert validate_rrule(rule) is None
    start = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)
    assert next_occurrence(rule, start, start, "Asia/Karachi") is None
    assert time.perf_counter() - started < 0.5


def test_safe_repeat_rules_are_kept_and_normalised():
    assert validate_rrule("rrule:freq=daily") == "FREQ=DAILY"
    assert (
        validate_rrule("FREQ=WEEKLY;UNTIL=20991231T235959Z") == "FREQ=WEEKLY;UNTIL=20991231T235959"
    )
    last_weekday = "FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1"
    assert validate_rrule(last_weekday) == last_weekday
    assert validate_rrule("FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=29")  # leap day: rare but real
    assert validate_rrule("FREQ=HOURLY;INTERVAL=2")


def test_count_becomes_an_end_date():
    from magpie.timeutil import anchor_rrule

    first = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)  # 09:00 in Karachi
    anchored = anchor_rrule("FREQ=DAILY;COUNT=3", first, "Asia/Karachi")
    assert anchored == "FREQ=DAILY;UNTIL=20261021T090000"
    third = datetime(2026, 10, 21, 4, 0, tzinfo=UTC)
    assert next_occurrence(anchored, third, third, "Asia/Karachi") is None
    assert anchor_rrule("FREQ=DAILY", first, "Asia/Karachi") == "FREQ=DAILY"


@pytest.mark.parametrize(
    "value", ["9999-12-31T23:59", "0001-01-01T00:00", "2026-02-30T10:00", "2026-10-19T24:00"]
)
def test_out_of_range_times_are_value_errors(value):
    for zone in ("America/New_York", "Asia/Tokyo"):
        with pytest.raises(ValueError):
            local_to_utc(value, zone)


def test_parse_hhmm_tolerates_missing_values():
    assert parse_hhmm(None) is None
    assert parse_hhmm("") is None
    assert parse_hhmm("24:00") is None
