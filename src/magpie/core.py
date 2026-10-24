"""The application container: builds every service and runs the background loops."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import httpx2

from magpie import __version__
from magpie.auth import ActionSigner, Secrets
from magpie.brain import Brain, BrainError, Context, make_brain
from magpie.brain.providers import PROVIDERS, AIConfig, clean_saved, default_url, resolve
from magpie.config import Prefs, Settings
from magpie.content.fetch import Fetcher
from magpie.db import Database
from magpie.digest import Digests
from magpie.models import Note
from magpie.notify import Notifier
from magpie.notify.channels import (
    DesktopChannel,
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
from magpie.stt import (
    SERVICES,
    Transcriber,
    TranscriptionError,
    address,
    clean_saved_speech,
)
from magpie.stt import resolve as resolve_speech
from magpie.telegram import TelegramBot
from magpie.telegram_api import TelegramApi
from magpie.timeutil import iso, now_utc

log = logging.getLogger(__name__)

ASK_ALL_NOTES_LIMIT = 300
MAX_LESSONS = 40
LESSONS_SENT = 20  # the newest corrections go with each note


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
        self.brain: Brain = brain or self._make_brain(self.ai_config())
        self.fetcher = Fetcher(self.http, settings)
        self.stt = Transcriber(self.http, settings, lambda: self.store.get_kv("speech"))
        self.reminders = Reminders(self.store, self.prefs)
        self.telegram_api = (
            TelegramApi(self.http, settings.telegram_bot_token)
            if settings.telegram_bot_token
            else None
        )
        self.webpush = WebPushChannel(self.store, settings)
        self.email = EmailChannel(settings, self.store)
        # Set by the desktop app when it runs this server: its pop-ups and its microphone.
        self.desktop: Any = None
        self.notifier = Notifier(
            [
                DesktopChannel(lambda: self.desktop),
                TelegramChannel(self.telegram_api, self.telegram_chat_id),
                self.webpush,
                NtfyChannel(self.http, settings),
                self.email,
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

    # ------------------------------------------------------------ AI provider

    def _saved_claude_key(self) -> str | None:
        key = self.store.get_kv("anthropic_api_key")
        return key if isinstance(key, str) and key else None

    def ai_config(self) -> AIConfig:
        """The provider, model, address and key in use (the server's settings win)."""
        return resolve(self.settings, self.store.get_kv("ai"), self._saved_claude_key())

    def _make_brain(self, config: AIConfig) -> Brain:
        return make_brain(self.settings, config=config, http=self.http)

    def claude_key_source(self) -> str | None:
        """Where the Claude key comes from: "env", "dashboard" or None."""
        if self.settings.anthropic_api_key or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return "env"
        return "dashboard" if self._saved_claude_key() else None

    async def set_ai(
        self,
        provider: str,
        *,
        model: str | None = None,
        base_url: str | None = None,
        key: str | None = None,
    ) -> str | None:
        """Use ``provider`` from now on, as chosen in Settings → AI.

        The choice is tried with the service first, and nothing is saved when it
        refuses (BrainError) or can't work (ValueError). Returns a warning when the
        service couldn't be reached to check, in which case it is saved anyway.
        """
        info = PROVIDERS.get(provider)
        if info is None:
            raise ValueError("Unknown AI provider.")
        stored = clean_saved(self.store.get_kv("ai"))
        entry = dict(stored["settings"].get(provider, {}))
        if model is not None:
            entry["model"] = model.strip()
        if base_url is not None and not info.fixed_url:
            entry["base_url"] = base_url.strip().rstrip("/")
        claude_key = key if provider == "claude" and key else self._saved_claude_key()
        url = (entry.get("base_url") if not info.fixed_url else "") or default_url(info)
        url = url.rstrip("/")
        if provider != "claude":
            if key:
                entry["key"], entry["key_for"] = key, url
            elif entry.get("key_for") != url:
                # A key only ever goes to the address it was entered for.
                entry.pop("key", None)
                entry.pop("key_for", None)
        choice = {"provider": provider, "settings": {**stored["settings"], provider: entry}}
        config = resolve(self.settings, choice, claude_key)
        if config.provider.id != provider:
            raise ValueError(f"The server chooses the AI (MAGPIE_BRAIN={self.settings.brain}).")
        if not config.ready:
            if provider != "claude" and not config.base_url:
                raise ValueError(f"Enter the address of the {info.name} service.")
            if provider != "claude" and not config.model:
                raise ValueError("Enter the name of the model to use.")
            raise ValueError(f"Paste your {info.name} API key.")
        warning = None
        check = getattr(self._make_brain(config), "check", None)
        if check is not None:
            try:
                await check()
            except BrainError as exc:
                if not exc.retryable:
                    raise
                warning = f"{exc} Saved anyway; notes will be filed once it's reachable."
        if provider == "claude" and key:
            self.store.set_kv("anthropic_api_key", key)
        self.store.set_kv("ai", choice)
        self.brain = self._make_brain(self.ai_config())
        if self.brain.available:
            self.pipeline.resume()  # file the notes that waited for an AI
        return warning

    def forget_ai_key(self, provider: str) -> None:
        if provider == "claude":
            self.store.delete_kv("anthropic_api_key")
        else:
            stored = clean_saved(self.store.get_kv("ai"))
            entry = stored["settings"].get(provider)
            if entry:
                entry.pop("key", None)
                entry.pop("key_for", None)
                self.store.set_kv("ai", stored)
        self.brain = self._make_brain(self.ai_config())

    async def set_claude_key(self, key: str | None) -> str | None:
        """Save (or with None, forget) a Claude key from the dashboard: the older API."""
        if key:
            return await self.set_ai("claude", key=key)
        self.forget_ai_key("claude")
        return None

    async def ai_models(
        self, provider: str, *, base_url: str | None = None, key: str | None = None
    ) -> list[str]:
        """The models a provider offers, to pick from in Settings → AI."""
        info = PROVIDERS.get(provider)
        if info is None:
            raise ValueError("Unknown AI provider.")
        stored = clean_saved(self.store.get_kv("ai"))
        entry = dict(stored["settings"].get(provider, {}))
        if base_url is not None and not info.fixed_url:
            entry["base_url"] = base_url.strip().rstrip("/")
        url = ((entry.get("base_url") if not info.fixed_url else "") or default_url(info)).rstrip(
            "/"
        )
        if key:
            entry["key"], entry["key_for"] = key, url
        entry.setdefault("model", entry.get("model") or info.model or "model")
        choice = {"provider": provider, "settings": {**stored["settings"], provider: entry}}
        config = resolve(
            self.settings, choice, key if provider == "claude" and key else self._saved_claude_key()
        )
        lister = getattr(self._make_brain(config), "list_models", None)
        if lister is None:
            return []
        return await lister()

    def ai_status(self) -> dict[str, Any]:
        config = self.ai_config()
        stored = clean_saved(self.store.get_kv("ai"))
        providers = []
        for info in PROVIDERS.values():
            if config.locked and info.id != config.provider.id:
                continue
            if info.id == config.provider.id:
                shown = config
            else:
                shown = resolve(
                    self.settings,
                    {"provider": info.id, "settings": stored["settings"]},
                    self._saved_claude_key(),
                )
            providers.append(
                {
                    "id": info.id,
                    "label": info.label,
                    "name": info.name,
                    "help": info.help,
                    "needs_key": info.needs_key,
                    "key_url": info.key_url,
                    "key_hint": info.key_hint,
                    "fixed_url": info.fixed_url,
                    "default_model": info.model or "",
                    "model": shown.model,
                    "base_url": shown.base_url,
                    "has_key": bool(shown.api_key),
                    "key_source": shown.key_source,
                }
            )
        return {
            "provider": config.provider.id,
            "label": self.brain.label,
            "model": config.model,
            "available": self.brain.available,
            "locked": config.locked,
            "web_search": config.provider.id == "claude",
            "providers": providers,
        }

    # ---------------------------------------------------------- speech service

    async def set_speech(
        self,
        service: str,
        *,
        model: str | None = None,
        base_url: str | None = None,
        key: str | None = None,
    ) -> str | None:
        """Write recordings down with ``service`` from now on (Settings → Voice), or with
        none ("") at all. Like the AI, a key is tried first and nothing is saved when the
        service refuses it (ValueError); a warning says when it couldn't be tried."""
        stored = clean_saved_speech(self.store.get_kv("speech"))
        if not service:
            self.store.set_kv("speech", {**stored, "service": ""})
            return None
        info = SERVICES.get(service)
        if info is None:
            raise ValueError("Unknown speech service.")
        entry = dict(stored["settings"].get(service, {}))
        if model is not None and info.models:
            entry["model"] = model.strip()
        if base_url is not None and not info.fixed_url:
            url = base_url.strip().rstrip("/")
            if url and not url.startswith(("http://", "https://")):
                raise ValueError("The address must start with http:// or https://.")
            entry["base_url"] = url
        url = address(info, entry)
        if key:
            entry["key"], entry["key_for"] = key.strip(), url
        elif entry.get("key_for") != url:  # a key only ever goes to the address it was for
            entry.pop("key", None)
            entry.pop("key_for", None)
        choice = {"service": service, "settings": {**stored["settings"], service: entry}}
        config = resolve_speech(self.settings, choice)
        if not config.base_url:
            raise ValueError(f"Enter the address of {info.name}.")
        if not config.ready:
            raise ValueError(f"Paste your {info.name} API key.")
        try:
            warning = await self.stt.check(config)
        except TranscriptionError as exc:
            raise ValueError(str(exc)) from exc
        self.store.set_kv("speech", choice)
        return warning

    def forget_speech_key(self, service: str) -> None:
        stored = clean_saved_speech(self.store.get_kv("speech"))
        entry = stored["settings"].get(service)
        if entry:
            entry.pop("key", None)
            entry.pop("key_for", None)
            self.store.set_kv("speech", stored)

    def speech_status(self) -> dict[str, Any]:
        """What Settings → Voice shows: never a key, only whether one is saved."""
        saved = self.store.get_kv("speech")
        stored = clean_saved_speech(saved)
        config = self.stt.config
        services = []
        for info in SERVICES.values():
            shown = resolve_speech(self.settings, saved, info.id)
            services.append(
                {
                    "id": info.id,
                    "label": info.label,
                    "name": info.name,
                    "help": info.help,
                    "needs_key": info.needs_key,
                    "key_url": info.key_url,
                    "key_hint": info.key_hint,
                    "fixed_url": info.fixed_url,
                    "models": info.models,
                    "default_model": info.model,
                    "model": shown.model,
                    "base_url": shown.base_url,
                    "has_key": bool(shown.key),
                }
            )
        return {
            "service": stored["service"],
            "source": config.source,
            "ready": config.ready,
            "name": config.name if config.service is not None else "",
            "services": services,
        }

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

    def context(
        self, source: str = "web", now: datetime | None = None, written_in: str = ""
    ) -> Context:
        prefs = self.prefs()
        return Context(
            timezone=prefs.timezone,
            now=now or now_utc(),
            proactivity=prefs.proactivity,
            about_me=prefs.about_me,
            morning_time=prefs.morning_time,
            source=source,
            instructions=prefs.instructions,
            categories=tuple(category.name for category in self.store.list_categories()),
            lessons=self.lesson_lines() if prefs.learn else (),
            written_in=written_in,
        )

    # -------------------------------------------------------------- learning

    def lessons(self) -> list[dict[str, str]]:
        stored = self.store.get_kv("lessons")
        if not isinstance(stored, list):
            return []
        return [
            item
            for item in stored
            if isinstance(item, dict)
            and all(isinstance(item.get(key), str) for key in ("text", "field", "to"))
        ][:MAX_LESSONS]

    def learn(self, note: Note, field: str, before: str, after: str) -> None:
        """Remember a correction, so similar notes are filed the user's way from now on."""
        if not after or before == after or not self.prefs().learn:
            return
        snippet = " ".join((note.raw_text or note.text).split())[:160]
        lesson = {
            "text": snippet,
            "field": field,
            "from": before,
            "to": after,
            "at": iso(now_utc()),
        }
        kept = [
            item for item in self.lessons() if (item["text"], item["field"]) != (snippet, field)
        ]
        self.store.set_kv("lessons", [lesson, *kept][:MAX_LESSONS])

    def lesson_lines(self) -> tuple[str, ...]:
        lines = []
        for item in self.lessons()[:LESSONS_SENT]:
            instead = f" (not {item['from']})" if item.get("from") else ""
            lines.append(f'"{item["text"]}" → {item["field"]} {item["to"]}{instead}')
        return tuple(lines)

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
                "    (`magpie password` shows it again. Run the setup again to stop using a\n"
                "    password: Magpie then opens straight away, on this computer only.)\n",
                self.secrets.password,
            )
        if not self.settings.login:
            log.info("Magpie opens on this computer: http://localhost:%s", self.settings.port)
        if not self.brain.available:
            log.warning(
                "%s isn't connected yet: notes are saved and filed once you set up an AI "
                "(Settings → AI in the dashboard).",
                self.brain.label,
            )

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self.pipeline.stop()
        await self.http.aclose()
        self.db.close()
