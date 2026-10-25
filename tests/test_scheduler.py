from datetime import timedelta

import pytest

from magpie.models import ReminderStatus
from magpie.notify import Notification
from magpie.scheduler import RETRY_DELAYS, Scheduler
from magpie.timeutil import local_to_utc, now_utc, to_local, tomorrow_at

from .conftest import TZ


class FakeChannel:
    name = "fake"
    label = "Fake"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[Notification] = []

    @property
    def configured(self) -> bool:
        return True

    async def send(self, notification: Notification) -> None:
        if self.fail:
            raise RuntimeError("boom")
        self.sent.append(notification)


@pytest.fixture
def channel(core):
    fake = FakeChannel()
    core.notifier.channels = [fake]
    # Mark today's digest and this week's review as done, so a reminder test run at
    # 9 am or on a Sunday evening doesn't also send one. Digest tests set their own.
    core.scheduler._prime()
    return fake


async def test_due_reminder_is_sent_with_actions(core, channel):
    note = core.store.create_note("call james")
    core.store.update_note(note.id, title="Budget call")
    reminder = core.store.add_reminder(note.id, now_utc() - timedelta(seconds=5), "Call James")
    await core.scheduler.tick()
    assert len(channel.sent) == 1
    sent = channel.sent[0]
    assert sent.title == "Call James"
    assert sent.body.startswith("Budget call\nDue today ") and sent.due.startswith("today ")
    assert sent.reminder_id == reminder.id
    assert sent.url == f"https://notes.example.com/#/note/{note.id}"
    assert set(sent.actions) == {"done", "snooze", "tomorrow"}
    stored = core.store.get_reminder(reminder.id)
    assert stored.status == ReminderStatus.SENT
    assert stored.sent_at is not None
    await core.scheduler.tick()
    assert len(channel.sent) == 1  # not sent twice


async def test_late_reminders_say_so(core, channel):
    core.store.add_reminder(None, now_utc() - timedelta(hours=3), "Water the plants")
    await core.scheduler.tick()
    assert "was due" in channel.sent[0].body


async def test_repeating_reminder_moves_to_next_occurrence(core, channel):
    due = now_utc() - timedelta(minutes=1)
    reminder = core.store.add_reminder(None, due, "Vitamins", rrule="FREQ=DAILY")
    await core.scheduler.tick()
    stored = core.store.get_reminder(reminder.id)
    assert stored.status == ReminderStatus.SCHEDULED
    assert stored.due_at == due + timedelta(days=1)


async def test_failed_delivery_retries_then_gives_up(core, channel):
    channel.fail = True
    reminder = core.store.add_reminder(None, now_utc() - timedelta(seconds=1), "Ping")
    now = now_utc()
    await core.scheduler.tick(now)
    stored = core.store.get_reminder(reminder.id)
    assert stored.status == ReminderStatus.SCHEDULED
    assert stored.attempts == 1
    assert stored.due_at == now + RETRY_DELAYS[0]
    assert "boom" in stored.last_error
    for _ in range(len(RETRY_DELAYS)):
        now = core.store.get_reminder(reminder.id).due_at
        await core.scheduler.tick(now)
    assert core.store.get_reminder(reminder.id).status == ReminderStatus.FAILED


async def test_without_channels_reminders_still_complete(core):
    core.notifier.channels = []
    reminder = core.store.add_reminder(None, now_utc() - timedelta(seconds=1), "Ping")
    await core.scheduler.tick()
    stored = core.store.get_reminder(reminder.id)
    assert stored.status == ReminderStatus.SENT
    assert "No notification channel" in stored.last_error


async def test_one_failing_channel_does_not_block_others(core):
    good, bad = FakeChannel(), FakeChannel(fail=True)
    bad.name = "bad"
    core.notifier.channels = [bad, good]
    core.store.add_reminder(None, now_utc() - timedelta(seconds=1), "Ping")
    await core.scheduler.tick()
    assert len(good.sent) == 1


async def test_disabled_channels_are_skipped(core, channel):
    prefs = core.prefs()
    prefs.channels = {"fake": False}
    core.store.save_prefs(prefs)
    core.store.add_reminder(None, now_utc() - timedelta(seconds=1), "Ping")
    await core.scheduler.tick()
    assert channel.sent == []


async def test_daily_digest_runs_once_at_its_time(core, channel, brain):
    scheduler = Scheduler(core)
    note = core.store.create_note("idea: a garden")
    core.store.update_note(note.id, in_digest=True, title="Garden idea")
    core.store.set_kv("last_daily_digest", "2000-01-01")
    before = local_to_utc("2026-10-19T07:59", TZ)
    at = local_to_utc("2026-10-19T08:00", TZ)
    await scheduler.maybe_digests(before)
    assert channel.sent == []
    await scheduler.maybe_digests(at)
    await scheduler.maybe_digests(at + timedelta(minutes=5))
    digests = [n for n in channel.sent if n.kind == "digest"]
    assert len(digests) == 1
    assert "Garden idea" in digests[0].body
    assert core.store.digest_notes() == []  # flags cleared
    assert core.store.list_digests()[0].sent


async def test_digest_is_skipped_when_far_too_late(core, channel):
    scheduler = Scheduler(core)
    core.store.set_kv("last_daily_digest", "2000-01-01")
    core.store.add_reminder(None, local_to_utc("2026-10-19T22:00", TZ), "Late thing")
    await scheduler.maybe_digests(local_to_utc("2026-10-19T21:00", TZ))
    assert channel.sent == []
    assert core.store.get_kv("last_daily_digest") == "2026-10-19"


async def test_fresh_install_waits_until_tomorrow(core, channel):
    scheduler = Scheduler(core)
    scheduler._prime()
    assert core.store.get_kv("last_daily_digest") is not None
    await scheduler.maybe_digests(now_utc())
    assert channel.sent == []


async def test_weekly_review(core, channel):
    scheduler = Scheduler(core)
    core.store.set_kv("last_weekly_review", "2000-W01")
    core.store.set_kv("last_daily_digest", "2026-10-18")
    core.store.create_note("something from this week")
    sunday_evening = local_to_utc("2026-10-18T18:05", TZ)  # a Sunday
    await scheduler.maybe_digests(sunday_evening)
    weekly = [n for n in channel.sent if n.title == "Your weekly review"]
    assert len(weekly) == 1


async def test_one_broken_reminder_does_not_hold_up_the_others(core, channel, monkeypatch):
    broken = core.store.add_reminder(None, now_utc() - timedelta(minutes=2), "Broken")
    fine = core.store.add_reminder(None, now_utc() - timedelta(minutes=1), "Fine")
    real_actions = core.reminder_actions

    def actions(reminder_id):
        if reminder_id == broken.id:
            raise RuntimeError("unexpected")
        return real_actions(reminder_id)

    monkeypatch.setattr(core, "reminder_actions", actions)
    await core.scheduler.tick()
    assert [n.title for n in channel.sent] == ["Fine"]
    assert core.store.get_reminder(fine.id).status == ReminderStatus.SENT
    retried = core.store.get_reminder(broken.id)
    assert retried.status == ReminderStatus.SCHEDULED
    assert retried.attempts == 1
    assert retried.due_at > now_utc()
    assert "unexpected" in retried.last_error


async def test_a_change_made_while_sending_is_kept(core, channel):
    reminder = core.store.add_reminder(None, now_utc() - timedelta(minutes=1), "Stretch")
    send = channel.send

    async def send_and_snooze(notification):
        await send(notification)
        core.reminders.snooze(reminder.id, 30)  # the user taps "snooze" meanwhile

    channel.send = send_and_snooze
    await core.scheduler.tick()
    stored = core.store.get_reminder(reminder.id)
    assert stored.status == ReminderStatus.SCHEDULED
    assert stored.due_at > now_utc() + timedelta(minutes=25)


async def test_counted_series_ends(core, channel):
    start = now_utc() - timedelta(minutes=1)
    reminder = core.store.add_reminder(None, start, "Vitamins", rrule="FREQ=DAILY;COUNT=2")
    await core.scheduler.tick(start + timedelta(minutes=1))
    stored = core.store.get_reminder(reminder.id)
    assert stored.status == ReminderStatus.SCHEDULED
    assert "UNTIL=" in stored.rrule and "COUNT" not in stored.rrule
    await core.scheduler.tick(stored.due_at + timedelta(minutes=1))
    assert core.store.get_reminder(reminder.id).status == ReminderStatus.SENT
    assert len(channel.sent) == 2


async def test_digests_still_run_when_a_reminder_fails(core, channel, monkeypatch):
    core.store.add_reminder(None, now_utc() - timedelta(minutes=1), "Broken")
    monkeypatch.setattr(core, "reminder_actions", lambda _: 1 / 0)
    calls = []

    async def digests(now):
        calls.append(now)

    monkeypatch.setattr(core.scheduler, "maybe_digests", digests)
    await core.scheduler.tick()
    assert len(calls) == 1


async def test_background_loops_are_restarted_after_a_crash(core, monkeypatch):
    import asyncio

    from magpie import core as core_module

    runs = []

    async def flaky():
        runs.append(1)
        if len(runs) < 3:
            raise RuntimeError("boom")

    real_sleep = asyncio.sleep

    async def no_wait(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(core_module.asyncio, "sleep", no_wait)
    task = core._supervise("test loop", flaky)
    await asyncio.wait_for(task, 2)
    assert len(runs) == 3  # crashed twice, then finished normally


# A repeating reminder is one row whose due time moves on as each occurrence goes off.


async def test_snoozing_a_repeating_reminder_that_just_went_off_starts_from_now(core, channel):
    """From the pop-up (or email, or Telegram): the series has already moved on a week."""
    due = now_utc() - timedelta(minutes=1)
    series = core.store.add_reminder(None, due, "Bins out", rrule="FREQ=WEEKLY")
    await core.scheduler.tick()
    assert core.store.get_reminder(series.id).due_at == due + timedelta(days=7)
    copy = core.reminders.snooze(series.id, 30)
    assert abs(copy.due_at - (now_utc() + timedelta(minutes=30))) < timedelta(minutes=1)
    later = core.reminders.tomorrow(series.id)
    assert later.due_at - now_utc() < timedelta(days=2)  # tomorrow morning, not next week's
    acknowledged = core.reminders.done(series.id)
    assert acknowledged.due_at == due + timedelta(days=7)  # the series goes on as it was
    assert acknowledged.status == ReminderStatus.SCHEDULED


def test_the_occurrence_shown_in_the_list_can_be_skipped_or_moved(core):
    day = to_local(now_utc() + timedelta(days=2), TZ).date()
    start = local_to_utc(f"{day}T09:00", TZ)
    series = core.store.add_reminder(None, start, "Bins out", rrule="FREQ=WEEKLY")
    # Done before it's due: this week's is skipped, next week's is still coming.
    skipped = core.reminders.done(series.id, start)
    assert skipped.due_at == start + timedelta(days=7)
    assert skipped.status == ReminderStatus.SCHEDULED
    # Snoozed before it's due: an hour later this time only.
    following = skipped.due_at
    copy = core.reminders.snooze(series.id, 60, due=following)
    assert copy.due_at == following + timedelta(hours=1) and copy.rrule is None
    assert core.store.get_reminder(series.id).due_at == following + timedelta(days=7)
    # Moved to the next morning: from that day, not from today.
    third = following + timedelta(days=7)
    moved = core.reminders.tomorrow(series.id, due=third)
    assert moved.due_at == tomorrow_at(core.prefs().morning_time, TZ, now=third)
    assert core.store.get_reminder(series.id).due_at == third + timedelta(days=7)
    # A time that isn't the one to come any more (it went off meanwhile) means the one
    # that went off.
    copy = core.reminders.snooze(series.id, 30, due=start)
    assert abs(copy.due_at - (now_utc() + timedelta(minutes=30))) < timedelta(minutes=1)
    assert core.store.get_reminder(series.id).due_at == third + timedelta(days=7)


def test_skipping_the_last_occurrence_ends_the_series(core):
    start = now_utc() + timedelta(hours=2)
    series = core.store.add_reminder(None, start, "Physio", rrule="FREQ=DAILY;COUNT=2")
    second = core.reminders.done(series.id, start)
    assert second.status == ReminderStatus.SCHEDULED and second.due_at == start + timedelta(days=1)
    assert core.reminders.done(series.id, second.due_at).status == ReminderStatus.DONE


def test_one_off_reminders_ignore_the_occurrence(core):
    due = now_utc() + timedelta(hours=3)
    reminder = core.store.add_reminder(None, due, "Dentist")
    assert core.reminders.snooze(reminder.id, 60, due=due).due_at == due + timedelta(hours=1)
    assert core.reminders.done(reminder.id, due).status == ReminderStatus.DONE


def test_the_api_takes_the_occurrence_meant(client, core):
    start = now_utc() + timedelta(days=1)
    series = core.store.add_reminder(None, start, "Bins out", rrule="FREQ=WEEKLY")
    done = client.post(f"/api/reminders/{series.id}/done", json={"due": start.isoformat()})
    assert done.status_code == 200
    assert core.store.get_reminder(series.id).due_at == start + timedelta(days=7)
    naive = client.post(f"/api/reminders/{series.id}/done", json={"due": "2030-01-01T09:00"})
    assert naive.status_code == 422  # a time without its zone could be any occurrence
    assert client.post(f"/api/reminders/{series.id}/snooze").status_code == 200  # no body
    assert client.post(f"/api/reminders/{series.id}/tomorrow").status_code == 200
