"""The application container: builds every service and runs the background loops."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2

from magpie import __version__
from magpie.auth import ActionSigner, Secrets
from magpie.brain import Brain, BrainError, Context, make_brain
from magpie.config import Prefs, Settings
from magpie.content.fetch import Fetcher
from magpie.db import Database
from magpie.digest import Digests
from magpie.models import Note
from magpie.notify import Notifier
from magpie.notify.channels import (
    EmailChannel,
    NtfyChannel,
    TelegramChannel,
    WebhookChannel,
    WebPushChannel,
)
from magpie.pipeline import Pipeline
from magpie.reminders import Reminders
from magpie.scheduler import Scheduler
from magpie.schemas import Answer
from magpie.store import Store
from magpie.stt import Transcriber
from magpie.telegram import TelegramBot
from magpie.telegram_api import TelegramApi
from magpie.timeutil import now_utc

log = logging.getLogger(__name__)

ASK_ALL_NOTES_LIMIT = 300


class Magpie:
    def __init__(
        self,
        settings: Settings,
        *,
        brain: Brain | None = None,
        http: httpx2.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        if str(settings.data_dir) != ":memory:":
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            self.db = Database(settings.db_path)
        else:
            self.db = Database(":memory:")
        self.store = Store(self.db, settings)
        self.secrets = Secrets(self.store, settings)
        self.signer = ActionSigner(self.secrets.secret_key)
        self.http = http or httpx2.AsyncClient(
            headers={"User-Agent": f"Magpie/{__version__}"}, timeout=30
        )
        self.brain: Brain = brain or make_brain(settings, api_key=self._saved_claude_key())
        self.fetcher = Fetcher(self.http, settings)
        self.stt = Transcriber(self.http, settings)
        self.reminders = Reminders(self.store, self.prefs)
        self.telegram_api = (
            TelegramApi(self.http, settings.telegram_bot_token)
            if settings.telegram_bot_token
            else None
        )
        self.webpush = WebPushChannel(self.store, settings)
        self.notifier = Notifier(
            [
                TelegramChannel(self.telegram_api, self.telegram_chat_id),
                self.webpush,
                NtfyChannel(self.http, settings),
                EmailChannel(settings),
                WebhookChannel(self.http, settings),
            ],
            self.prefs,
        )
        self.pipeline = Pipeline(self)
        self.digests = Digests(self)
        self.scheduler = Scheduler(self)
        self.bot = TelegramBot(self, self.telegram_api) if self.telegram_api else None
        if self.bot is not None:
            self.pipeline.listeners.append(self.bot.on_event)
        self._tasks: list[asyncio.Task[Any]] = []

    # ------------------------------------------------------------ Claude key

    def _saved_claude_key(self) -> str | None:
        key = self.store.get_kv("anthropic_api_key")
        return key if isinstance(key, str) and key else None

    def claude_key_source(self) -> str | None:
        """Where the API key comes from: "env", "dashboard" or None."""
        if self.settings.anthropic_api_key or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return "env"
        return "dashboard" if self._saved_claude_key() else None

    async def set_claude_key(self, key: str | None) -> str | None:
        """Save (or with None, forget) a key entered in the dashboard and start using it.

        Returns a warning when the key could not be checked. Raises BrainError when
        Anthropic refuses it, in which case nothing is saved.
        """
        warning = None
        if key:
            candidate = make_brain(self.settings, api_key=key)
            check = getattr(candidate, "check", None)
            if check is not None:
                try:
                    await check()
                except BrainError as exc:
                    if not exc.retryable:
                        raise
                    warning = f"{exc} Saved anyway; notes will be filed once it's reachable."
            self.store.set_kv("anthropic_api_key", key)
        else:
            self.store.delete_kv("anthropic_api_key")
        if self.claude_key_source() != "env":
            self.brain = make_brain(self.settings, api_key=key)
            if self.brain.available:
                self.pipeline.resume()  # file the notes that waited for a key
        return warning

    # --------------------------------------------------------------- helpers

    def prefs(self) -> Prefs:
        return self.store.get_prefs()

    def telegram_chat_id(self) -> int | None:
        return self.prefs().telegram_chat_id or self.settings.telegram_chat_id

    def url(self, path: str) -> str | None:
        if not self.settings.base_url:
            return None
        return f"{self.settings.base_url}{path}"

    def reminder_actions(self, reminder_id: int) -> dict[str, str]:
        if not self.settings.base_url:
            return {}
        return {
            action: f"{self.settings.base_url}/a/{self.signer.sign(reminder_id, action)}"
            for action in ("done", "snooze", "tomorrow")
        }

    def context(self, source: str = "web") -> Context:
        prefs = self.prefs()
        return Context(
            timezone=prefs.timezone,
            now=now_utc(),
            proactivity=prefs.proactivity,
            about_me=prefs.about_me,
            morning_time=prefs.morning_time,
            source=source,
            instructions=prefs.instructions,
        )

    async def ask(self, question: str) -> Answer:
        notes = self.store.recent_notes(limit=ASK_ALL_NOTES_LIMIT + 1)
        if len(notes) > ASK_ALL_NOTES_LIMIT:
            # Too many to send them all: the best search matches plus the newest notes.
            ids = self.store.search_ids(question, limit=40)
            chosen: dict[int, Note] = {}
            for note_id in ids:
                note = self.store.get_note(note_id)
                if note is not None:
                    chosen[note.id] = note
            for note in notes[:60]:
                chosen.setdefault(note.id, note)
            notes = list(chosen.values())
        return await self.brain.ask(question, notes, self.context())

    # ------------------------------------------------------------- lifecycle

    def _supervise(self, name: str, run: Callable[[], Awaitable[None]]) -> asyncio.Task[None]:
        """Run a background loop forever: if it crashes, log it and start it again.

        Reminders must keep firing even after an unexpected error. A loop that
        returns (the Telegram bot after its token was rejected) stays stopped.
        """

        async def keep_running() -> None:
            delay = 5.0
            while True:
                started = time.monotonic()
                try:
                    await run()
                    return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("The %s stopped unexpectedly; restarting it", name)
                if time.monotonic() - started > 600:
                    delay = 5.0
                await asyncio.sleep(delay)
                delay = min(delay * 2, 300.0)

        return asyncio.get_running_loop().create_task(keep_running(), name=f"magpie-{name}")

    async def start(self) -> None:
        self.pipeline.resume()
        self._tasks.append(self._supervise("scheduler", self.scheduler.run))
        if self.bot is not None:
            self._tasks.append(self._supervise("Telegram bot", self.bot.run))
        if self.secrets.password_generated:
            log.warning(
                "\n\n    Log in to Magpie with this password:  %s\n"
                "    (set MAGPIE_PASSWORD to choose your own; `magpie password` shows it again)\n",
                self.secrets.password,
            )
        if not self.brain.available:
            log.warning(
                "Claude isn't connected yet: notes are saved and filed once you add an API key "
                "(Settings → Claude in the dashboard, or ANTHROPIC_API_KEY)."
            )

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self.pipeline.stop()
        await self.http.aclose()
        self.db.close()
