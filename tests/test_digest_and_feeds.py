from magpie.brain import BrainError, Context, DigestData
from magpie.digest import markdown_to_plain, render_plain
from magpie.ics import _fold, reminders_calendar
from magpie.models import Note, ProcState, Reminder
from magpie.timeutil import local_to_utc, now_utc

from .conftest import TZ


def _reminder(rid, due, message, rrule=None, status="scheduled"):
    now = now_utc()
    return Reminder(
        id=rid,
        note_id=1,
        due_at=due,
        message=message,
        origin="explicit",
        rrule=rrule,
        status=status,
        created_at=now,
        updated_at=now,
    )


def test_render_plain_daily():
    now = local_to_utc("2026-10-19T08:00", TZ)
    note = Note(
        id=3, created_at=now, updated_at=now, source="web", raw_text="x", title="Garden idea"
    )
    data = DigestData(
        kind="daily",
        context=Context(timezone=TZ, now=now),
        reminders_today=[_reminder(1, local_to_utc("2026-10-19T17:00", TZ), "Call James")],
        digest_notes=[note],
    )
    text = render_plain(data)
    assert text.startswith("Good morning!")
    assert "• 5:00 PM  Call James" in text
    assert "• Garden idea (#3)" in text
    assert "Open tasks" not in text  # empty sections are skipped


def test_markdown_to_plain():
    text = "## Themes\n- **Budget** talks (#12)\n* Garden\n\n\n\nDone"
    assert markdown_to_plain(text) == "Themes\n• Budget talks (#12)\n• Garden\n\nDone"


async def test_digest_falls_back_to_plain_text_when_claude_fails(core, monkeypatch):
    async def broken(data):
        raise BrainError("overloaded", retryable=True)

    monkeypatch.setattr(core.brain, "digest", broken)
    note = core.store.create_note("idea")
    core.store.update_note(note.id, in_digest=True, title="An idea", state=ProcState.READY)
    digest = await core.digests.run("daily", send=False)
    assert "An idea" in digest.content
    assert core.store.get_kv("last_digest_at")


async def test_empty_digest_is_skipped_unless_forced(core):
    assert await core.digests.run("daily", send=False) is None
    forced = await core.digests.run("daily", send=False, force=True)
    assert "Nothing new" in forced.content


def test_ics_feed():
    one_off = _reminder(1, local_to_utc("2026-10-19T17:00", TZ), "Call James, re: budget; today")
    weekly = _reminder(
        2, local_to_utc("2026-10-19T09:00", TZ), "Weekly plan", rrule="FREQ=WEEKLY;BYDAY=MO"
    )
    body = reminders_calendar([one_off, weekly], {}, timezone=TZ, base_url="https://n.example.com")
    lines = body.split("\r\n")
    assert lines[0] == "BEGIN:VCALENDAR" and body.endswith("END:VCALENDAR\r\n")
    assert "DTSTART:20261019T070000Z" in lines
    assert "SUMMARY:Call James\\, re: budget\\; today" in lines
    assert f"DTSTART;TZID={TZ}:20261019T090000" in lines  # recurring events stay in local time
    assert "RRULE:FREQ=WEEKLY;BYDAY=MO" in lines
    assert "URL:https://n.example.com/#/note/1" in lines
    assert body.count("BEGIN:VALARM") == 2


def test_ics_folds_long_lines_on_character_boundaries():
    line = "SUMMARY:" + "ü" * 100
    folded = _fold(line)
    parts = folded.split("\r\n ")
    assert all(len(part.encode()) <= 75 for part in parts)
    assert "".join(parts) == line


def test_gather_picks_todays_reminders(core):
    morning = local_to_utc("2026-10-19T08:00", TZ)
    core.store.add_reminder(None, local_to_utc("2026-10-19T17:00", TZ), "Today")
    core.store.add_reminder(None, local_to_utc("2026-10-20T09:00", TZ), "Tomorrow")
    daily = core.digests.gather("daily", now=morning)
    assert [r.message for r in daily.reminders_today] == ["Today"]
    weekly = core.digests.gather("weekly", now=morning)
    assert weekly.kind == "weekly" and weekly.reminders_today == []


def test_calendar_text_is_clean_and_until_is_utc():
    due = local_to_utc("2026-10-19T09:00", TZ)
    calendar = reminders_calendar(
        [
            _reminder(1, due, "Bell\x07 ring\x00", rrule="FREQ=DAILY;UNTIL=20261031T090000"),
            _reminder(2, due, "Date only", rrule="FREQ=WEEKLY;UNTIL=20261231"),
        ],
        {},
        timezone=TZ,
    )
    assert "\x07" not in calendar and "\x00" not in calendar
    assert "RRULE:FREQ=DAILY;UNTIL=20261030T230000Z" in calendar
    assert "RRULE:FREQ=WEEKLY;UNTIL=20261231T135959Z" in calendar
