"""The application container: builds every service and runs the background loops."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx2

from magpie import __version__
from magpie.auth import ActionSigner, Secrets
from magpie.brain import Brain, Context, make_brain
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
        self.brain: Brain = brain or make_brain(settings)
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

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self.pipeline.resume()
        self._tasks.append(loop.create_task(self.scheduler.run(), name="magpie-scheduler"))
        if self.bot is not None:
            self._tasks.append(loop.create_task(self.bot.run(), name="magpie-telegram"))
        if self.secrets.password_generated:
            log.warning(
                "No MAGPIE_PASSWORD set. Log in with the generated password: %s",
                self.secrets.password,
            )
        if not self.brain.available:
            log.warning("ANTHROPIC_API_KEY is not set: notes will be saved but not processed.")

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self.pipeline.stop()
        await self.http.aclose()
        self.db.close()
