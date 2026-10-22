from datetime import timedelta

import pytest

from magpie.models import ReminderStatus
from magpie.notify import Notification
from magpie.scheduler import RETRY_DELAYS, Scheduler
from magpie.timeutil import local_to_utc, now_utc

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
