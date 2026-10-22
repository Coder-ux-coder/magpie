"""The Telegram bot: capture notes by text or voice, get reminders back.

It uses long polling, so it works behind NAT with no public URL. Only the
paired chat is served; pairing uses a one-time code from the dashboard.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from magpie.brain import BrainError
from magpie.digest import markdown_to_plain
from magpie.models import Link, Note, ReminderStatus
from magpie.stt import TranscriptionError
from magpie.telegram_api import TelegramApi, TelegramError, escape
from magpie.timeutil import at_local_time, humanize, iso, now_utc, parse_iso, to_local

if TYPE_CHECKING:
    from magpie.core import Magpie

log = logging.getLogger(__name__)

PAIR_TTL = timedelta(minutes=15)
STRANGER_QUIET = 600  # seconds between replies to the same stranger
MAX_VOICE_BYTES = 20 * 1024 * 1024
VERDICTS = {"must": "must see", "worth_it": "worth it", "skim": "skim it", "skip": "skip it"}
COMMANDS = [
    ("today", "What's scheduled today"),
    ("upcoming", "Next reminders"),
    ("search", "Search your notes: /search budget"),
    ("ask", "Ask about your notes: /ask what did I decide about rent?"),
    ("digest", "Your daily digest, now"),
    ("week", "Your weekly review, now"),
    ("help", "How to use Magpie"),
]
HELP = (
    "<b>Magpie</b> files everything you send here.\n\n"
    "• Type or forward a message: it becomes a note.\n"
    "• Hold the mic and talk: voice notes are transcribed.\n"
    "• Share a YouTube video or article link and I'll review it.\n"
    "• Say when: “remind me to call James at 5 pm”.\n\n"
    "Commands: /today /upcoming /search /ask /digest /week"
)


class TelegramBot:
    def __init__(self, app: Magpie, api: TelegramApi) -> None:
        self.app = app
        self.api = api
        self.username: str | None = None
        self._stranger_replies: dict[int, float] = {}

    # ------------------------------------------------------------- lifecycle

    async def run(self) -> None:
        """Long-poll Telegram until cancelled. Only a rejected token stops it."""
        while True:
            try:
                me = await self.api.call("getMe")
                self.username = me.get("username") if isinstance(me, dict) else None
                await self.api.call("deleteWebhook", drop_pending_updates=False)
                await self.api.call(
                    "setMyCommands",
                    commands=[{"command": name, "description": text} for name, text in COMMANDS],
                )
                break
            except TelegramError as exc:
                if exc.status == 401:
                    log.error("Telegram rejected the bot token; the bot is disabled")
                    return
                log.warning("Telegram start-up failed (%s); retrying in 30s", exc)
                await asyncio.sleep(exc.retry_after or 30)
            except Exception:
                log.exception("Telegram start-up failed; retrying in 60s")
                await asyncio.sleep(60)
        log.info("Telegram bot @%s is listening", self.username)
        try:
            offset = int(self.app.store.get_kv("telegram_offset", 0))
        except (TypeError, ValueError):
            offset = 0
        while True:
            try:
                updates = await self.api.call(
                    "getUpdates",
                    http_timeout=65,
                    timeout=50,
                    offset=offset,
                    allowed_updates=["message", "callback_query"],
                )
            except TelegramError as exc:
                if exc.status == 401:
                    log.error("Telegram rejected the bot token; the bot is disabled")
                    return
                if exc.status == 409:
                    log.warning(
                        "Another program is reading this bot's messages (two Magpie servers "
                        "with the same bot token?); retrying in 30s"
                    )
                else:
                    log.warning("Telegram polling error: %s", exc)
                await asyncio.sleep(exc.retry_after or (30 if exc.status == 409 else 5))
                continue
            except Exception:
                log.exception("Telegram polling failed; retrying in 10s")
                await asyncio.sleep(10)
                continue
            for update in updates if isinstance(updates, list) else []:
                try:
                    offset = max(offset, int(update["update_id"]) + 1)
                    self.app.store.set_kv("telegram_offset", offset)
                    await self.handle_update(update)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Failed to handle Telegram update")

    # --------------------------------------------------------------- pairing

    def chat_id(self) -> int | None:
        return self.app.telegram_chat_id()

    def new_pairing_code(self) -> dict[str, str]:
        code = f"{secrets.randbelow(10**6):06d}"
        expires = now_utc() + PAIR_TTL
        self.app.store.set_kv("telegram_pair", {"code": code, "expires": iso(expires)})
        link = f"https://t.me/{self.username}?start={code}" if self.username else ""
        return {"code": code, "expires_at": iso(expires), "link": link, "bot": self.username or ""}

    def _consume_code(self, code: str) -> bool:
        pending = self.app.store.get_kv("telegram_pair")
        if not isinstance(pending, dict) or not secrets.compare_digest(
            str(pending.get("code")).encode(), code.strip().encode()
        ):
            return False
        try:
            if parse_iso(pending["expires"]) < now_utc():
                return False
        except (KeyError, TypeError, ValueError):
            return False
        self.app.store.delete_kv("telegram_pair")
        return True

    # -------------------------------------------------------------- updates

    async def handle_update(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            await self.handle_callback(update["callback_query"])
            return
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat_id = (message.get("chat") or {}).get("id")
        if not isinstance(chat_id, int):
            return
        text = (message.get("text") or message.get("caption") or "").strip()
        paired = self.chat_id()

        if text.startswith("/start"):
            code = text.split(maxsplit=1)[1] if " " in text else ""
            if code and self._consume_code(code):
                prefs = self.app.prefs()
                prefs.telegram_chat_id = chat_id
                self.app.store.save_prefs(prefs)
                await self.reply(
                    chat_id, "✅ Connected! Send me a note, or hold the mic and talk.\n\n" + HELP
                )
                return
            if paired == chat_id:
                await self.reply(chat_id, HELP)
                return

        if paired is None or chat_id != paired:
            await self._stranger(chat_id, paired is None)
            return

        if text.startswith("/"):
            await self.handle_command(chat_id, text, message["message_id"])
            return

        voice = message.get("voice") or message.get("audio") or message.get("video_note")
        if voice:
            await self.handle_voice(message, voice, caption=text)
            return
        if not text:
            await self.reply(chat_id, "I can file text, voice notes and links. Try one of those.")
            return
        forwarded = message.get("forward_origin")
        if forwarded:
            source = _forward_name(forwarded)
            text = f"Forwarded from {source}:\n{text}" if source else text
        self.app.pipeline.submit(
            text,
            "telegram",
            {"telegram": {"chat_id": chat_id, "message_id": message["message_id"]}},
        )

    async def _stranger(self, chat_id: int, unpaired: bool) -> None:
        now = time.monotonic()
        last = self._stranger_replies.get(chat_id)
        if last is not None and now - last < STRANGER_QUIET:
            return
        if len(self._stranger_replies) > 1000:
            self._stranger_replies = {
                chat: seen
                for chat, seen in self._stranger_replies.items()
                if now - seen < STRANGER_QUIET
            }
        self._stranger_replies[chat_id] = now
        if unpaired:
            text = (
                "👋 This is a private Magpie bot. To connect it, open your Magpie dashboard → "
                "Settings → Telegram, and tap Connect."
            )
        else:
            text = "This is a private Magpie bot."
        with contextlib.suppress(TelegramError):
            await self.reply(chat_id, text)

    async def handle_voice(
        self, message: dict[str, Any], voice: dict[str, Any], caption: str
    ) -> None:
        chat_id = message["chat"]["id"]
        if not self.app.stt.enabled:
            await self.reply(
                chat_id,
                "🎙 Voice notes need a speech-to-text key. Add MAGPIE_STT_API_KEY (OpenAI or "
                "Groq) to your Magpie settings, or type the note instead.",
            )
            return
        if (voice.get("file_size") or 0) > MAX_VOICE_BYTES:
            await self.reply(
                chat_id,
                "🎙 That recording is too long for Telegram bots (over 20 MB). Try a "
                "shorter one, or split it in two.",
            )
            return
        try:
            audio = await self.api.download_file(voice["file_id"])
            transcript = await self.app.stt.transcribe(audio, voice.get("mime_type") or "audio/ogg")
        except (TelegramError, TranscriptionError) as exc:
            await self.reply(chat_id, f"🎙 I couldn't transcribe that: {escape(str(exc))}")
            return
        except Exception:
            log.exception("Voice note failed")
            await self.reply(chat_id, "🎙 Something went wrong with that recording. Try again.")
            return
        if not transcript:
            await self.reply(chat_id, "🎙 I couldn't hear anything in that recording.")
            return
        text = f"{caption}\n{transcript}".strip() if caption else transcript
        self.app.pipeline.submit(
            text,
            "telegram-voice",
            {"telegram": {"chat_id": chat_id, "message_id": message["message_id"]}},
        )

    async def handle_command(self, chat_id: int, text: str, message_id: int) -> None:
        command, _, argument = text.partition(" ")
        command = command.split("@")[0].lstrip("/").lower()
        argument = argument.strip()
        tz = self.app.prefs().timezone
        if command in ("help", "start"):
            await self.reply(chat_id, HELP)
        elif command == "today":
            await self.reply(chat_id, self._today(tz))
        elif command == "upcoming":
            reminders = self.app.store.list_reminders([ReminderStatus.SCHEDULED], limit=10)
            lines = [f"⏰ {humanize(r.due_at, tz)} · {escape(r.message)}" for r in reminders]
            await self.reply(chat_id, "\n".join(lines) or "Nothing scheduled.")
        elif command == "search":
            if not argument:
                await self.reply(chat_id, "Usage: /search budget")
                return
            notes = self.app.store.list_notes(view="all", q=argument, limit=8)
            lines = [f"• {escape(n.display_title)} <i>(#{n.id})</i>" for n in notes]
            await self.reply(chat_id, "\n".join(lines) or "No notes match.")
        elif command == "ask":
            if not argument:
                await self.reply(chat_id, "Usage: /ask what did I decide about rent?")
                return
            try:
                answer = await self.app.ask(argument)
            except BrainError as exc:
                await self.reply(chat_id, f"Couldn't answer: {escape(str(exc))}")
                return
            await self.reply(
                chat_id, escape(markdown_to_plain(answer.answer)) or "…", reply_to=message_id
            )
        elif command in ("digest", "week"):
            kind = "weekly" if command == "week" else "daily"
            digest = await self.app.digests.run(kind, send=False, force=True)
            text = markdown_to_plain(digest.content) if digest else ""
            await self.reply(chat_id, escape(text) or "Nothing to report yet.")
        else:
            await self.reply(chat_id, "I don't know that command. Try /help.")

    def _today(self, tz: str) -> str:
        day = to_local(now_utc(), tz).date()
        # The whole local day, so a reminder due a moment ago but not sent yet still shows.
        start = at_local_time(day, "00:00", tz)
        end = at_local_time(day + timedelta(days=1), "00:00", tz)
        reminders = self.app.store.list_reminders([ReminderStatus.SCHEDULED], start=start, end=end)
        tasks = self.app.store.open_tasks(limit=5)
        lines = ["<b>Today</b>"]
        lines += [f"⏰ {humanize(r.due_at, tz)} · {escape(r.message)}" for r in reminders]
        if not reminders:
            lines.append("Nothing scheduled today.")
        if tasks:
            lines.append("\n<b>Open tasks</b>")
            lines += [f"• {escape(n.display_title)}" for n in tasks]
        return "\n".join(lines)

    # ------------------------------------------------------------- callbacks

    async def handle_callback(self, query: dict[str, Any]) -> None:
        chat_id = ((query.get("message") or {}).get("chat") or {}).get("id")
        data = query.get("data") or ""
        toast = "Done"
        if chat_id is None or chat_id != self.chat_id():
            with contextlib.suppress(TelegramError):
                await self.api.call("answerCallbackQuery", callback_query_id=query.get("id"))
            return
        if data == "noop":
            with contextlib.suppress(TelegramError):
                await self.api.call("answerCallbackQuery", callback_query_id=query.get("id"))
            return
        parts = data.split(":")
        try:
            if parts[0] == "r":
                action, reminder_id = parts[1], int(parts[2])
                tz = self.app.prefs().timezone
                if action == "done":
                    self.app.reminders.done(reminder_id)
                    toast = "Marked as done ✅"
                elif action == "snz":
                    reminder = self.app.reminders.snooze(reminder_id, int(parts[3]))
                    toast = f"Snoozed until {humanize(reminder.due_at, tz)}"
                elif action == "tmr":
                    reminder = self.app.reminders.tomorrow(reminder_id)
                    toast = f"See you {humanize(reminder.due_at, tz)}"
            elif parts[0] == "n":
                action, note_id = parts[1], int(parts[2])
                if action == "undo":
                    deleted = self.app.store.delete_note(note_id)
                    toast = "Deleted" if deleted else "That note is already gone."
        except (KeyError, ValueError, IndexError):
            toast = "That item no longer exists."
        except Exception:
            log.exception("Telegram button %r failed", data)
            toast = "Something went wrong. Try again from the dashboard."
        # Telegram stops accepting answers after a while (e.g. while the server was down).
        with contextlib.suppress(TelegramError):
            await self.api.call(
                "answerCallbackQuery", callback_query_id=query.get("id"), text=toast
            )
        message = query.get("message") or {}
        if message.get("message_id"):
            with contextlib.suppress(TelegramError):
                await self.api.call(
                    "editMessageReplyMarkup",
                    chat_id=chat_id,
                    message_id=message["message_id"],
                    reply_markup={"inline_keyboard": [[{"text": toast, "callback_data": "noop"}]]},
                )

    # ---------------------------------------------------- pipeline listener

    async def on_event(self, event: str, note: Note, link: Link | None = None) -> None:
        origin = note.meta.get("telegram") if note.source.startswith("telegram") else None
        if not isinstance(origin, dict):
            origin = None
        chat_id = origin.get("chat_id") if origin else self.chat_id()
        if chat_id is None or chat_id != self.chat_id():
            return
        reply_to = origin.get("message_id") if origin else None
        tz = self.app.prefs().timezone
        if event == "triaged" and origin:
            lines = [f"✅ <b>{escape(note.display_title)}</b>"]
            if note.source == "telegram-voice" and note.text:
                lines.append(f"<i>{escape(note.text[:400])}</i>")
            if note.reply:
                lines.append(escape(note.reply))
            for reminder in note.reminders:
                if reminder.status == ReminderStatus.SCHEDULED:
                    repeat = " (repeats)" if reminder.rrule else ""
                    lines.append(f"⏰ {humanize(reminder.due_at, tz)}{repeat}")
            if note.links:
                lines.append("🔗 I'll review the link and send you a summary.")
            buttons = [[{"text": "🗑 Undo", "callback_data": f"n:undo:{note.id}"}]]
            url = self.app.url(f"/#/note/{note.id}")
            if url and not any(h in url for h in ("localhost", "127.0.0.1")):
                buttons[0].append({"text": "Open", "url": url})
            await self.reply(chat_id, "\n".join(lines), buttons=buttons, reply_to=reply_to)
        elif event == "reviewed" and origin and link is not None and link.review:
            await self.reply(chat_id, format_review(link), reply_to=reply_to)
        elif event == "failed" and origin:
            await self.reply(
                chat_id,
                f"⚠️ Saved, but {escape(self.app.brain.label)} couldn't process it: "
                f"{escape(note.error or 'unknown error')}",
                reply_to=reply_to,
            )

    async def reply(
        self,
        chat_id: int,
        text: str,
        *,
        buttons: list[list[dict[str, str]]] | None = None,
        reply_to: int | None = None,
    ) -> None:
        await self.api.send_message(chat_id, text, buttons=buttons, reply_to=reply_to)


def format_review(link: Link) -> str:
    review = link.review
    meta = [VERDICTS.get(review.verdict, review.verdict), f"{review.rating}/5"]
    minutes = link.minutes or review.minutes
    if minutes:
        meta.append(f"{minutes} min")
    icon = "📺" if link.kind == "youtube" else "📄"
    lines = [
        f"{icon} <b>{escape(review.title or link.title or link.url)}</b>",
        f"<i>{' · '.join(meta)}</i>",
        "",
        escape(review.tldr),
    ]
    lines += [f"• {escape(point)}" for point in review.key_points[:6]]
    if review.best_for:
        lines.append(f"\n<b>Best for:</b> {escape(review.best_for)}")
    if review.caveats:
        lines.append(f"<b>Caveats:</b> {escape(review.caveats)}")
    lines.append(escape(link.url))
    return "\n".join(lines)


def _forward_name(origin: dict[str, Any]) -> str:
    if origin.get("type") == "user":
        user = origin.get("sender_user", {})
        return " ".join(filter(None, [user.get("first_name"), user.get("last_name")]))
    if origin.get("type") == "hidden_user":
        return origin.get("sender_user_name", "")
    chat = origin.get("chat") or origin.get("sender_chat") or {}
    return chat.get("title", "")
