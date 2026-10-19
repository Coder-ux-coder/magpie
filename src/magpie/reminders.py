"""Reminder rules shared by the pipeline, the API, Telegram and action links."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from magpie.config import Prefs
from magpie.models import NoteKind, NoteStatus, Reminder, ReminderStatus
from magpie.schemas import PlannedReminder
from magpie.store import Store
from magpie.timeutil import (
    anchor_rrule,
    local_to_utc,
    next_occurrence,
    now_utc,
    tomorrow_at,
    validate_rrule,
)

log = logging.getLogger(__name__)

# A planned time this far in the past is almost certainly a misunderstanding.
STALE_AFTER = timedelta(hours=12)
MAX_PER_NOTE = 5


class Reminders:
    def __init__(self, store: Store, prefs: Callable[[], Prefs]) -> None:
        self.store = store
        self._prefs = prefs

    def get(self, reminder_id: int) -> Reminder:
        reminder = self.store.get_reminder(reminder_id)
        if reminder is None:
            raise KeyError(reminder_id)
        return reminder

    def schedule_planned(
        self, note_id: int, planned: list[PlannedReminder], prefs: Prefs, now: datetime
    ) -> list[Reminder]:
        created: list[Reminder] = []
        seen: set[tuple[datetime, str]] = set()
        for item in planned[:MAX_PER_NOTE]:
            if item.origin == "suggested" and prefs.proactivity == "quiet":
                continue
            try:
                due = local_to_utc(item.when, prefs.timezone)
            except ValueError:
                log.warning("Ignoring reminder with unreadable time %r", item.when)
                continue
            rule = validate_rrule(item.repeat)
            if item.repeat and rule is None:
                log.info("Ignoring an unusable repeat rule %r", item.repeat)
            if rule:
                rule = anchor_rrule(rule, due, prefs.timezone)
            if due < now - timedelta(minutes=1):
                if rule:
                    following = next_occurrence(rule, due, now, prefs.timezone)
                    if following is None:
                        continue
                    due = following
                elif now - due > STALE_AFTER:
                    log.info("Dropping reminder planned in the past: %s", item.when)
                    continue
                else:
                    due = now + timedelta(minutes=1)
            message = item.message.strip()[:500] or "Reminder"
            if (due, message.casefold()) in seen:
                continue  # the same reminder twice would ping twice
            seen.add((due, message.casefold()))
            created.append(
                self.store.add_reminder(
                    note_id,
                    due,
                    message,
                    origin=item.origin,
                    reason=item.reason.strip()[:300],
                    rrule=rule,
                )
            )
        return created

    def add_manual(
        self, note_id: int | None, when_local: str, message: str, repeat: str | None = None
    ) -> Reminder:
        prefs = self._prefs()
        due = local_to_utc(when_local, prefs.timezone)
        rule = validate_rrule(repeat)
        if repeat and rule is None:
            raise ValueError("That repeat rule is not valid, or repeats more than once an hour.")
        if rule:
            rule = anchor_rrule(rule, due, prefs.timezone)
        return self.store.add_reminder(
            note_id, due, message.strip()[:500] or "Reminder", origin="manual", rrule=rule
        )

    def done(self, reminder_id: int) -> Reminder:
        reminder = self.get(reminder_id)
        if reminder.rrule and reminder.status == ReminderStatus.SCHEDULED:
            # Acknowledging one occurrence of a repeating reminder; the series continues.
            return reminder
        reminder = self.store.update_reminder(reminder_id, status=ReminderStatus.DONE)
        if reminder.note_id is not None:
            note = self.store.get_note(reminder.note_id)
            still_pending = note and any(
                r.status == ReminderStatus.SCHEDULED for r in note.reminders if r.id != reminder.id
            )
            if (
                note
                and note.kind == NoteKind.TASK
                and note.status == NoteStatus.OPEN
                and not still_pending
            ):
                self.store.update_note(note.id, status=NoteStatus.DONE)
        return reminder

    def snooze_until(self, reminder_id: int, due: datetime) -> Reminder:
        reminder = self.get(reminder_id)
        if reminder.rrule:
            # Keep the series; add a one-off copy for this occurrence.
            return self.store.add_reminder(
                reminder.note_id, due, reminder.message, origin="manual", reason="Snoozed"
            )
        return self.store.update_reminder(
            reminder_id,
            due_at=due,
            status=ReminderStatus.SCHEDULED,
            attempts=0,
            last_error=None,
            sent_at=None,
        )

    def _base(self, reminder_id: int) -> datetime:
        """Postpone from the due time if it is still ahead, so a snooze never moves it earlier."""
        reminder = self.get(reminder_id)
        now = now_utc()
        if reminder.status == ReminderStatus.SCHEDULED and reminder.due_at > now:
            return reminder.due_at
        return now

    def snooze(self, reminder_id: int, minutes: int | None = None) -> Reminder:
        """Snooze for ``minutes``, or for the length chosen in Settings."""
        minutes = max(1, min(minutes or self._prefs().snooze_minutes, 60 * 24 * 30))
        return self.snooze_until(reminder_id, self._base(reminder_id) + timedelta(minutes=minutes))

    def tomorrow(self, reminder_id: int) -> Reminder:
        """The morning after today, or after the due day if that is later."""
        prefs = self._prefs()
        due = tomorrow_at(prefs.morning_time, prefs.timezone, now=self._base(reminder_id))
        return self.snooze_until(reminder_id, due)

    def cancel(self, reminder_id: int) -> Reminder:
        self.get(reminder_id)
        return self.store.update_reminder(reminder_id, status=ReminderStatus.CANCELLED)

    def reschedule(self, reminder_id: int, when_local: str) -> Reminder:
        prefs = self._prefs()
        reminder = self.get(reminder_id)
        due = local_to_utc(when_local, prefs.timezone)
        return self.store.update_reminder(
            reminder.id,
            due_at=due,
            status=ReminderStatus.SCHEDULED,
            attempts=0,
            last_error=None,
        )
