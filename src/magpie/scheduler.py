"""The clock: fires due reminders and sends digests on time."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from magpie.models import Reminder, ReminderStatus
from magpie.notify import Notification
from magpie.timeutil import anchor_rrule, humanize, next_occurrence, now_utc, parse_hhmm, to_local

if TYPE_CHECKING:
    from magpie.core import Magpie

log = logging.getLogger(__name__)

TICK_SECONDS = 15
LATE_AFTER = timedelta(minutes=10)
RETRY_DELAYS = [timedelta(minutes=m) for m in (1, 5, 15, 60, 180)]
DAILY_WINDOW = timedelta(hours=6)
WEEKLY_WINDOW = timedelta(hours=12)
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class Scheduler:
    def __init__(self, app: Magpie) -> None:
        self.app = app

    async def run(self) -> None:
        try:
            self._prime()
        except Exception:
            log.exception("Could not prepare the digest schedule")
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Scheduler tick failed")
            await asyncio.sleep(TICK_SECONDS)

    def _prime(self) -> None:
        """On a fresh install, don't send a digest the moment the server starts."""
        store = self.app.store
        prefs = self.app.prefs()
        local = to_local(now_utc(), prefs.timezone)
        if store.get_kv("last_daily_digest") is None:
            store.set_kv("last_daily_digest", local.date().isoformat())
        if store.get_kv("last_weekly_review") is None:
            store.set_kv("last_weekly_review", _week_key(local))

    async def tick(self, now: datetime | None = None) -> None:
        now = now or now_utc()
        for reminder in self.app.store.due_reminders(now):
            # One reminder that fails in an unexpected way must not hold up the others.
            try:
                await self.fire(reminder, now)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Could not send reminder %s", reminder.id)
                self._retry_later(reminder, now, f"Magpie hit an error: {exc}"[:300])
        try:
            await self.maybe_digests(now)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Digest check failed")

    # ------------------------------------------------------------- reminders

    async def fire(self, reminder: Reminder, now: datetime) -> None:
        store = self.app.store
        prefs = self.app.prefs()
        note = store.get_note(reminder.note_id, children=False) if reminder.note_id else None
        due = humanize(reminder.due_at, prefs.timezone, now)
        body_parts = []
        if note and note.display_title and note.display_title != reminder.message:
            body_parts.append(note.display_title)
        if now - reminder.due_at > LATE_AFTER:
            body_parts.append(f"(This was due {due}.)")
        else:
            body_parts.append(f"Due {due}.")
        notification = Notification(
            title=reminder.message,
            body="\n".join(body_parts),
            kind="reminder",
            url=self.app.url(f"/#/note/{reminder.note_id}") if reminder.note_id else None,
            reminder_id=reminder.id,
            note_id=reminder.note_id,
            actions=self.app.reminder_actions(reminder.id),
            snooze_minutes=prefs.snooze_minutes,
            due=due,
        )
        report = await self.app.notifier.send(notification)

        current = store.get_reminder(reminder.id)
        if (
            current is None
            or current.status != ReminderStatus.SCHEDULED
            or current.due_at != reminder.due_at
        ):
            return  # deleted, done or snoozed while it was being sent: keep that change
        if report.ok or report.no_channels:
            error = None if report.ok else "No notification channel is set up yet."
            rule = reminder.rrule
            if rule and "COUNT=" in rule.upper():
                rule = anchor_rrule(rule, reminder.due_at, prefs.timezone)
            following = (
                next_occurrence(rule, reminder.due_at, now, prefs.timezone) if rule else None
            )
            if following is not None:
                store.update_reminder(
                    reminder.id,
                    due_at=following,
                    rrule=rule,
                    status=ReminderStatus.SCHEDULED,
                    sent_at=now,
                    attempts=0,
                    last_error=error,
                )
            else:
                store.update_reminder(
                    reminder.id,
                    status=ReminderStatus.SENT,
                    sent_at=now,
                    attempts=0,
                    last_error=error,
                )
            return

        self._retry_later(reminder, now, report.errors)

    def _retry_later(self, reminder: Reminder, now: datetime, error: str) -> None:
        store = self.app.store
        attempts = reminder.attempts + 1
        try:
            if attempts > len(RETRY_DELAYS):
                log.warning("Giving up on reminder %s: %s", reminder.id, error)
                store.update_reminder(
                    reminder.id, status=ReminderStatus.FAILED, attempts=attempts, last_error=error
                )
            else:
                store.update_reminder(
                    reminder.id,
                    due_at=now + RETRY_DELAYS[attempts - 1],
                    attempts=attempts,
                    last_error=error,
                )
        except KeyError:
            pass  # deleted meanwhile

    # --------------------------------------------------------------- digests

    async def maybe_digests(self, now: datetime) -> None:
        prefs = self.app.prefs()
        store = self.app.store
        local = to_local(now, prefs.timezone)

        daily = parse_hhmm(prefs.digest_time) if prefs.digest_time else None
        today = local.date().isoformat()
        if daily and store.get_kv("last_daily_digest") != today:
            due = local.replace(hour=daily[0], minute=daily[1], second=0, microsecond=0)
            if local >= due:
                store.set_kv("last_daily_digest", today)
                if local - due <= DAILY_WINDOW:
                    await self.app.digests.run("daily")

        weekly = parse_hhmm(prefs.weekly_review_time) if prefs.weekly_review_day else None
        week = _week_key(local)
        if (
            weekly
            and WEEKDAYS[local.weekday()] == prefs.weekly_review_day
            and store.get_kv("last_weekly_review") != week
        ):
            due = local.replace(hour=weekly[0], minute=weekly[1], second=0, microsecond=0)
            if local >= due:
                store.set_kv("last_weekly_review", week)
                if local - due <= WEEKLY_WINDOW:
                    await self.app.digests.run("weekly")


def _week_key(local: datetime) -> str:
    year, week, _ = local.isocalendar()
    return f"{year}-W{week:02d}"
