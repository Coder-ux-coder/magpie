"""What happens after a note is captured.

1. triage: Claude cleans, files and decides on reminders;
2. links found in the note (or found by web search) are fetched and reviewed.

Work runs in background tasks with bounded concurrency. Anything interrupted
(restart, API outage) is picked up again by ``resume()``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from magpie.brain import BrainError, BrainUnavailable, Context
from magpie.content import FetchError
from magpie.content.links import extract_urls, is_http_url, link_kind, normalize_url
from magpie.models import Link, Note, NoteKind, ProcState
from magpie.timeutil import now_utc

if TYPE_CHECKING:
    from magpie.core import Magpie

log = logging.getLogger(__name__)

Listener = Callable[[str, Note, Link | None], Awaitable[None]]

MAX_LINKS_PER_NOTE = 3
CAPTURE_CLOCK_WINDOW = timedelta(hours=6)
MAX_ATTEMPTS = 5
RETRY_DELAYS = [30, 120, 600, 1800]


class Pipeline:
    def __init__(self, app: Magpie, concurrency: int = 3) -> None:
        self.app = app
        self.listeners: list[Listener] = []
        self._tasks: set[asyncio.Task[Any]] = set()
        self._semaphore = asyncio.Semaphore(concurrency)

    # ------------------------------------------------------------ scheduling

    def submit(self, raw_text: str, source: str = "web", meta: dict | None = None) -> Note:
        note = self.app.store.create_note(raw_text.strip(), source, meta)
        self.spawn(self.process(note.id))
        return note

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self) -> None:
        """Wait for all background work (used by tests and the CLI)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def resume(self) -> None:
        """Requeue notes and links left unfinished by a restart."""
        store = self.app.store
        for note_id in store.note_ids_in_state(ProcState.PENDING, ProcState.PROCESSING):
            self.spawn(self.process(note_id))
        for link_id in store.link_ids_in_state(ProcState.PENDING, ProcState.PROCESSING):
            link = store.get_link(link_id)
            note = store.get_note(link.note_id, children=False) if link else None
            if note and note.state == ProcState.READY:
                self.spawn(self.review_link(link_id))

    async def _emit(self, event: str, note: Note, link: Link | None = None) -> None:
        for listener in self.listeners:
            try:
                await listener(event, note, link)
            except Exception:
                log.exception("Listener failed for %s", event)

    # ---------------------------------------------------------------- triage

    async def process(self, note_id: int, *, review: bool = True) -> Note | None:
        async with self._semaphore:
            note = await self._triage(note_id)
        if note is not None and review and note.state == ProcState.READY:
            for link in note.links:
                if link.state == ProcState.PENDING:
                    self.spawn(self.review_link(link.id))
        return note

    async def _triage(self, note_id: int) -> Note | None:
        store = self.app.store
        brain = self.app.brain
        note = store.get_note(note_id)
        if note is None:
            return None
        if not brain.available:
            return store.update_note(
                note_id,
                state=ProcState.PENDING,
                error="Claude is not set up yet (ANTHROPIC_API_KEY). The note is saved and will "
                "be processed once a key is configured.",
            )
        store.update_note(note_id, state=ProcState.PROCESSING, error=None)
        prefs = self.app.prefs()
        now = now_utc()
        # Relative times ("in 20 minutes") count from when the note was captured,
        # which matters for notes queued offline or retried after an outage.
        clock = note.created_at if now - note.created_at < CAPTURE_CLOCK_WINDOW else now
        ctx = Context(
            timezone=prefs.timezone,
            now=clock,
            proactivity=prefs.proactivity,
            about_me=prefs.about_me,
            morning_time=prefs.morning_time,
            source=note.source,
        )
        try:
            triage = await brain.triage(note.raw_text, ctx)
        except BrainError as exc:
            return await self._failed(note, exc)

        reminders_store = self.app.reminders
        store.cancel_open_reminders(note_id, ("explicit", "suggested"))
        created = reminders_store.schedule_planned(note_id, triage.reminders, prefs, now)
        ping = triage.ping
        if ping in ("explicit", "suggested") and not created:
            ping = "digest"
        if ping == "suggested" and prefs.proactivity == "quiet":
            ping = "digest"

        urls: list[str] = []
        for url in extract_urls(note.raw_text) + [normalize_url(u) for u in triage.urls]:
            if is_http_url(url) and url not in urls:
                urls.append(url)
        existing = {link.url for link in note.links}
        for url in urls[:MAX_LINKS_PER_NOTE]:
            if url not in existing:
                store.add_link(note_id, url, link_kind(url))
        kind = triage.kind
        if urls and kind == NoteKind.NOTE:
            kind = NoteKind.LINK

        meta = dict(note.meta)
        meta.pop("attempts", None)
        note = store.update_note(
            note_id,
            title=triage.title.strip()[:200],
            text=triage.text.strip(),
            summary=triage.summary.strip(),
            kind=kind,
            tags=triage.tags,
            ping=ping,
            ping_reason=triage.ping_reason.strip(),
            in_digest=ping == "digest",
            briefing=triage.briefing.strip(),
            reply=triage.reply.strip(),
            state=ProcState.READY,
            error=None,
            meta=meta,
        )
        if (
            not urls
            and not note.links
            and triage.lookup
            and prefs.web_search
            and self.app.settings.web_search
        ):
            self.spawn(self._lookup(note_id, triage.lookup))
        await self._emit("triaged", note)
        return note

    async def _failed(self, note: Note, exc: BrainError) -> Note:
        store = self.app.store
        attempts = int(note.meta.get("attempts", 0)) + 1
        meta = {**note.meta, "attempts": attempts}
        if isinstance(exc, BrainUnavailable):
            updated = store.update_note(note.id, state=ProcState.PENDING, error=str(exc), meta=meta)
        elif exc.retryable and attempts < MAX_ATTEMPTS:
            delay = RETRY_DELAYS[min(attempts - 1, len(RETRY_DELAYS) - 1)]
            updated = store.update_note(
                note.id,
                state=ProcState.PENDING,
                error=f"{exc} Retrying in {delay // 60 or 1} min.",
                meta=meta,
            )
            self.spawn(self._retry(note.id, delay))
        else:
            updated = store.update_note(note.id, state=ProcState.FAILED, error=str(exc), meta=meta)
            await self._emit("failed", updated)
        log.warning("Triage of note %s failed: %s", note.id, exc)
        return updated

    async def _retry(self, note_id: int, delay: float) -> None:
        await asyncio.sleep(delay)
        note = self.app.store.get_note(note_id, children=False)
        if note is not None and note.state == ProcState.PENDING:
            await self.process(note_id)

    async def _lookup(self, note_id: int, query: str) -> None:
        try:
            async with self._semaphore:
                found = await self.app.brain.find_link(query)
        except BrainError as exc:
            log.info("Web search for note %s failed: %s", note_id, exc)
            return
        if not found.url or found.confidence == "low" or not is_http_url(found.url):
            log.info("Web search for note %s found nothing confident", note_id)
            return
        note = self.app.store.get_note(note_id)
        if note is None or note.links:
            return
        url = normalize_url(found.url)
        link = self.app.store.add_link(
            note_id, url, link_kind(url), found_by_search=True, title=found.title
        )
        await self.review_link(link.id)

    # --------------------------------------------------------------- reviews

    async def review_link(self, link_id: int) -> Link | None:
        store = self.app.store
        link = store.get_link(link_id)
        if link is None:
            return None
        note = store.get_note(link.note_id, children=False)
        if note is None:
            return None
        if not self.app.brain.available:
            return link
        async with self._semaphore:
            store.update_link(link_id, state=ProcState.PROCESSING, error=None)
            note_text = note.text or note.raw_text
            try:
                link = await self._review(link, note_text)
            except (BrainError, FetchError) as exc:
                log.warning("Review of link %s failed: %s", link_id, exc)
                return store.update_link(link_id, state=ProcState.FAILED, error=str(exc))
        self._refresh_search_text(note.id)
        updated = store.get_note(note.id)
        if updated is not None:
            await self._emit("reviewed", updated, link)
        return link

    async def _review(self, link: Link, note_text: str) -> Link:
        store = self.app.store
        brain = self.app.brain
        try:
            content = await self.app.fetcher.fetch(link.url)
        except FetchError as exc:
            if "private address" in str(exc) or "Unsupported URL" in str(exc):
                raise
            log.info("Fetching %s failed (%s); letting Claude fetch it", link.url, exc)
            content = None
        if content is not None and (content.has_body or content.kind == "youtube"):
            review = await brain.review(content, note_text)
            return store.update_link(
                link.id,
                url=content.url,
                state=ProcState.READY,
                review=review,
                title=content.title or review.title,
                author=content.author,
                site=content.site,
                minutes=content.minutes or review.minutes,
                error=None,
            )
        review = await brain.review_url(link.url, note_text)
        return store.update_link(
            link.id,
            state=ProcState.READY,
            review=review,
            title=review.title or link.title,
            minutes=review.minutes,
            error=None,
        )

    def _refresh_search_text(self, note_id: int) -> None:
        note = self.app.store.get_note(note_id)
        if note is None:
            return
        parts = []
        for link in note.links:
            if link.review:
                parts.append(f"{link.title} {link.review.tldr} {' '.join(link.review.key_points)}")
            elif link.title:
                parts.append(link.title)
        self.app.store.update_note(note_id, search_extra="\n".join(parts))
