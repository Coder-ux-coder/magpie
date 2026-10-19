"""Daily digests and weekly reviews."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from magpie.brain import BrainError, Context, DigestData
from magpie.models import Digest, ReminderStatus
from magpie.notify import Notification
from magpie.timeutil import at_local_time, humanize, iso, now_utc, parse_iso, to_local

if TYPE_CHECKING:
    from magpie.core import Magpie

log = logging.getLogger(__name__)

VERDICTS = {"must": "must see", "worth_it": "worth it", "skim": "skim it", "skip": "skip"}


def greeting(data: DigestData) -> str:
    hour = to_local(data.context.now, data.context.timezone).hour
    if data.kind == "weekly":
        return "Here's your week in notes."
    if hour < 12:
        return "Good morning! Here's your day."
    if hour < 18:
        return "Good afternoon! Here's what's on."
    return "Good evening! Here's what's on."


def render_plain(data: DigestData) -> str:
    """A digest without Claude: used offline and when the API is unavailable."""
    tz = data.context.timezone
    lines = [greeting(data)]

    def section(title: str, items: list[str]) -> None:
        if items:
            lines.append("")
            lines.append(title)
            lines.extend(f"• {item}" for item in items)

    section(
        "Scheduled today",
        [
            f"{humanize(r.due_at, tz).replace('today ', '')}  {r.message}"
            for r in data.reminders_today
        ],
    )
    section("Missed", [f"{humanize(r.due_at, tz)}  {r.message}" for r in data.missed])
    if data.kind == "weekly":
        counts: dict[str, int] = {}
        for note in data.week_notes:
            counts[note.kind] = counts.get(note.kind, 0) + 1
        summary = ", ".join(f"{count} {kind}" for kind, count in sorted(counts.items()))
        lines.append("")
        lines.append(
            f"{len(data.week_notes)} notes this week" + (f" ({summary})." if summary else ".")
        )
        section("This week", [f"{n.display_title} (#{n.id})" for n in data.week_notes[:15]])
    section("To revisit", [f"{n.display_title} (#{n.id})" for n in data.digest_notes])
    section(
        "New reviews",
        [
            f"{link.review.title or link.title or link.url}: "
            f"{VERDICTS.get(link.review.verdict, link.review.verdict)}, {link.review.rating}/5"
            for link in data.reviews
            if link.review
        ],
    )
    section("Open tasks", [f"{n.display_title} (#{n.id})" for n in data.open_tasks])
    return "\n".join(lines).strip()


def markdown_to_plain(text: str) -> str:
    """Flatten Claude's Markdown for chat channels that show it raw."""
    out = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            line = stripped.lstrip("#").strip()
        elif stripped.startswith(("- ", "* ")):
            line = "• " + stripped[2:]
        out.append(line.replace("**", "").replace("__", ""))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


class Digests:
    def __init__(self, app: Magpie) -> None:
        self.app = app

    def gather(self, kind: str, now: datetime | None = None) -> DigestData:
        store = self.app.store
        prefs = self.app.prefs()
        now = now or now_utc()
        tz = prefs.timezone
        context = Context(
            timezone=tz,
            now=now,
            proactivity=prefs.proactivity,
            about_me=prefs.about_me,
            morning_time=prefs.morning_time,
        )
        local = to_local(now, tz)
        end_of_day = at_local_time(local.date() + timedelta(days=1), "00:00", tz)
        if kind == "weekly":
            since = now - timedelta(days=7)
            return DigestData(
                kind="weekly",
                context=context,
                reminders_today=[],
                missed=store.list_reminders([ReminderStatus.FAILED], start=since),
                digest_notes=[],
                reviews=store.reviews_since(since)[:15],
                open_tasks=store.open_tasks(limit=10),
                week_notes=store.notes_created_between(since, now)[:150],
            )
        last = store.get_kv("last_digest_at")
        since = parse_iso(last) if last else now - timedelta(days=1)
        return DigestData(
            kind="daily",
            context=context,
            reminders_today=store.list_reminders(
                [ReminderStatus.SCHEDULED], start=now - timedelta(minutes=1), end=end_of_day
            ),
            missed=store.list_reminders([ReminderStatus.FAILED], start=now - timedelta(days=2)),
            digest_notes=store.digest_notes()[:20],
            reviews=store.reviews_since(since)[:10],
            open_tasks=store.open_tasks(limit=5),
        )

    async def run(self, kind: str, *, send: bool = True, force: bool = False) -> Digest | None:
        data = self.gather(kind)
        if data.is_empty and not force:
            log.info("Skipping the %s digest: nothing to report", kind)
            return None
        try:
            if data.is_empty:
                text = render_plain(data) + "\n\nNothing new. Enjoy the quiet."
            else:
                text = await self.app.brain.digest(data)
        except BrainError as exc:
            log.warning("Claude could not write the %s digest (%s); using the plain one", kind, exc)
            text = render_plain(data)
        digest = self.app.store.add_digest(kind, text)
        if kind == "daily":
            self.app.store.clear_digest_flags(note.id for note in data.digest_notes)
            self.app.store.set_kv("last_digest_at", iso(data.context.now))
        if send:
            title = "Your weekly review" if kind == "weekly" else "Your daily digest"
            body = markdown_to_plain(text) if kind == "weekly" else text
            report = await self.app.notifier.send(
                Notification(title=title, body=body, kind="digest", url=self.app.url("/#/digests"))
            )
            if report.ok:
                self.app.store.mark_digest_sent(digest.id)
                digest.sent = True
        return digest
