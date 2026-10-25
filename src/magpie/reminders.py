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

    # A repeating reminder is one row whose due time moves on as each occurrence goes off.
    # Done, snooze and tomorrow from a pop-up, an email or Telegram mean the occurrence that
    # has just gone off (the row is already on the next one). From the list of reminders they
    # mean the occurrence it shows, which the app passes as ``due``.

    def _upcoming(self, reminder: Reminder, due: datetime | None) -> bool:
        """Whether ``due`` is the repeating reminder's occurrence still to come."""
        return (
            due is not None
            and bool(reminder.rrule)
            and reminder.status == ReminderStatus.SCHEDULED
            and abs(reminder.due_at - due) < timedelta(seconds=1)
        )

    def _skip(self, reminder: Reminder) -> Reminder:
        """Done (or moved) before it's due: the series goes on from the occurrence after."""
        tz = self._prefs().timezone
        rule = reminder.rrule or ""
        if "COUNT=" in rule.upper():
            rule = anchor_rrule(rule, reminder.due_at, tz)
        following = next_occurrence(rule, reminder.due_at, reminder.due_at, tz)
        if following is None:  # that was the last one
            return self.store.update_reminder(reminder.id, status=ReminderStatus.DONE)
        return self.store.update_reminder(
            reminder.id, due_at=following, rrule=rule, attempts=0, last_error=None
        )

    def done(self, reminder_id: int, due: datetime | None = None) -> Reminder:
        reminder = self.get(reminder_id)
        if self._upcoming(reminder, due):
            return self._skip(reminder)
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

    def snooze_until(
        self, reminder_id: int, when: datetime, *, due: datetime | None = None
    ) -> Reminder:
        reminder = self.get(reminder_id)
        if reminder.rrule:
            # Keep the series; add a one-off copy for this occurrence (and when it's the one
            # still to come, the series skips it).
            copy = self.store.add_reminder(
                reminder.note_id, when, reminder.message, origin="manual", reason="Snoozed"
            )
            if self._upcoming(reminder, due):
                self._skip(reminder)
            return copy
        return self.store.update_reminder(
            reminder_id,
            due_at=when,
            status=ReminderStatus.SCHEDULED,
            attempts=0,
            last_error=None,
            sent_at=None,
        )

    def _base(self, reminder_id: int, due: datetime | None = None) -> datetime:
        """Postpone from the due time if it is still ahead, so a snooze never moves it earlier.
        A repeating reminder's row is already on its next occurrence once one goes off: that
        one is postponed from now."""
        reminder = self.get(reminder_id)
        now = now_utc()
        if reminder.rrule and not self._upcoming(reminder, due):
            return now
        if reminder.status == ReminderStatus.SCHEDULED and reminder.due_at > now:
            return reminder.due_at
        return now

    def snooze(
        self, reminder_id: int, minutes: int | None = None, *, due: datetime | None = None
    ) -> Reminder:
        """Snooze for ``minutes``, or for the length chosen in Settings."""
        minutes = max(1, min(minutes or self._prefs().snooze_minutes, 60 * 24 * 30))
        when = self._base(reminder_id, due) + timedelta(minutes=minutes)
        return self.snooze_until(reminder_id, when, due=due)

    def tomorrow(self, reminder_id: int, *, due: datetime | None = None) -> Reminder:
        """The morning after today, or after the due day if that is later."""
        prefs = self._prefs()
        when = tomorrow_at(prefs.morning_time, prefs.timezone, now=self._base(reminder_id, due))
        return self.snooze_until(reminder_id, when, due=due)

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
