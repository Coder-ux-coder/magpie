"""HTTP API for the dashboard, Siri Shortcuts, scripts and other integrations.

Two ways to authenticate:

* the dashboard logs in with the password and gets a session cookie; state-changing
  requests must also send ``X-Magpie: 1`` (a header a cross-site form cannot set);
* integrations send ``Authorization: Bearer <api token>`` (shown in Settings).
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field, ValidationError, field_validator

from magpie import __version__
from magpie.auth import RateLimiter
from magpie.brain import BrainError
from magpie.config import Prefs
from magpie.core import Magpie
from magpie.export import export_json, export_markdown, import_json
from magpie.ics import reminders_calendar
from magpie.models import Note, NoteKind, NoteStatus, ProcState, ReminderStatus
from magpie.notify import Notification, snooze_label
from magpie.stt import MAX_AUDIO_BYTES, TranscriptionError
from magpie.timeutil import humanize, now_utc

router = APIRouter(prefix="/api")
public = APIRouter()

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
WAIT_SECONDS = 60
_SOURCE = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
login_limiter = RateLimiter(attempts=10, window=900)


def get_magpie(request: Request) -> Magpie:
    return request.app.state.magpie


def session_fingerprint(app: Magpie) -> str:
    material = f"{app.secrets.password}\0{app.secrets.secret_key}".encode()
    return hashlib.sha256(material).hexdigest()[:32]


async def require_user(request: Request) -> Magpie:
    app = get_magpie(request)
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and token and app.secrets.check_token(token.strip()):
        return app
    if request.session.get("fp") == session_fingerprint(app):
        if request.method not in SAFE_METHODS and request.headers.get("x-magpie") != "1":
            raise HTTPException(403, "Missing the X-Magpie header.")
        return app
    raise HTTPException(401, "Not logged in.")


User = Annotated[Magpie, Depends(require_user)]


# ------------------------------------------------------------------ bodies


class LoginIn(BaseModel):
    password: str = Field(max_length=500)


def _not_blank(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("The note is empty.")
    return value


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=50_000)
    source: str = Field(default="api", max_length=100)
    captured_at: datetime | None = Field(
        default=None, description="When the note was captured, for notes queued offline."
    )
    client_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_-]{8,64}$",
        description="A random id chosen by the client. Sending the same id again (a retry after "
        "a lost answer) returns the note already saved instead of a copy.",
    )

    _text = field_validator("text")(_not_blank)


class NotePatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    text: str | None = Field(default=None, max_length=50_000)
    kind: NoteKind | None = None
    tags: list[str] | None = None
    status: NoteStatus | None = None
    pinned: bool | None = None


class ReminderIn(BaseModel):
    when: str = Field(max_length=40, description="Local time, YYYY-MM-DDTHH:MM")
    message: str = Field(default="", max_length=500)
    repeat: str | None = Field(
        default=None, max_length=300, description="Optional RRULE, e.g. FREQ=DAILY"
    )


class RescheduleIn(BaseModel):
    when: str = Field(max_length=40)


class SnoozeIn(BaseModel):
    minutes: int | None = Field(
        default=None, ge=1, le=60 * 24 * 30, description="Defaults to the snooze length in Settings"
    )


class DigestIn(BaseModel):
    kind: Literal["daily", "weekly"] = "daily"
    send: bool = True


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2_000)


class TestIn(BaseModel):
    channel: str | None = None


class ClaudeKeyIn(BaseModel):
    key: str = Field(min_length=10, max_length=300)

    @field_validator("key")
    @classmethod
    def _one_word(cls, value: str) -> str:
        value = value.strip()
        if not value or any(char.isspace() for char in value):
            raise ValueError("Paste the key on its own, without spaces.")
        return value


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(max_length=2_000)
    keys: dict[str, str] = Field(max_length=10)


class PushEndpointIn(BaseModel):
    endpoint: str = Field(max_length=2_000)


# ------------------------------------------------------------------- session


@router.get("/session")
async def session(request: Request) -> dict[str, Any]:
    app = get_magpie(request)
    return {
        "authenticated": request.session.get("fp") == session_fingerprint(app),
        "version": __version__,
    }


@router.post("/login")
async def login(request: Request, body: LoginIn) -> dict[str, Any]:
    app = get_magpie(request)
    client = request.client.host if request.client else "unknown"
    if not login_limiter.allow(client):
        raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
    if not app.secrets.check_password(body.password):
        raise HTTPException(401, "Wrong password.")
    login_limiter.reset(client)
    request.session.clear()
    request.session["fp"] = session_fingerprint(app)
    return {"authenticated": True}


@router.post("/logout")
async def logout(request: Request) -> dict[str, Any]:
    request.session.clear()
    return {"authenticated": False}


# -------------------------------------------------------------------- status


@router.get("/status")
async def status(app: User) -> dict[str, Any]:
    prefs = app.prefs()
    bot = app.bot
    return {
        "version": __version__,
        "brain": {
            "name": app.brain.name,
            "available": app.brain.available,
            "model": app.settings.model if app.brain.name == "claude" else app.brain.name,
            "usage": app.brain.usage(),
            "key_source": app.claude_key_source() if app.brain.name == "claude" else None,
        },
        # A fresh install with no time zone chosen anywhere yet: the dashboard
        # adopts the browser's zone once.
        "timezone_unset": "timezone" not in app.settings.model_fields_set
        and "timezone" not in (app.store.get_kv("prefs") or {}),
        "stt": {"enabled": app.stt.enabled},
        "web_search": app.settings.web_search,
        "channels": [
            {
                "name": channel.name,
                "label": channel.label,
                "configured": channel.configured,
                "enabled": prefs.channel_enabled(channel.name),
            }
            for channel in app.notifier.channels
        ],
        "telegram": {
            "configured": bot is not None,
            "paired": app.telegram_chat_id() is not None,
            "bot": bot.username if bot else None,
        },
        "push": {
            "public_key": app.webpush.keys.public_key,
            "subscriptions": len(app.store.push_subscriptions()),
        },
        "counts": app.store.count_notes(),
        "base_url": app.settings.base_url,
        "password_generated": app.secrets.password_generated,
        "prefs": prefs.model_dump(),
    }


# --------------------------------------------------------------------- notes


def _source(value: str, default: str) -> str:
    value = (value or "").strip().lower()
    return value if _SOURCE.match(value) else default


async def _create(
    app: Magpie,
    text: str,
    source: str,
    wait: bool,
    captured_at: datetime | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    if client_id:
        earlier = app.store.note_by_client_id(client_id, now_utc() - timedelta(days=30))
        if earlier is not None:
            return {"note": earlier.model_dump(mode="json"), "reply": earlier.reply or "Saved."}
    note = app.store.create_note(
        text.strip(), source, {"client_id": client_id} if client_id else None
    )
    if captured_at is not None:
        captured = captured_at if captured_at.tzinfo else captured_at.replace(tzinfo=UTC)
        if now_utc() - timedelta(days=7) < captured < now_utc():
            app.store.set_created_at(note.id, captured)
    task = app.pipeline.spawn(app.pipeline.process(note.id))
    if wait:
        await asyncio.wait({task}, timeout=WAIT_SECONDS)
    fresh = app.store.get_note(note.id) or note
    reply = fresh.reply
    if not reply:
        if fresh.state == ProcState.FAILED:
            reply = "Saved, but Claude couldn't process it."
        elif not app.brain.available:
            reply = "Saved. Claude isn't set up yet, so it will be processed later."
        else:
            reply = "Saved. Claude is still working on it."
    return {"note": fresh.model_dump(mode="json"), "reply": reply}


@router.get("/notes")
async def list_notes(
    app: User,
    view: Literal["inbox", "all", "done", "archived", "scheduled", "reviews"] | None = None,
    kind: NoteKind | None = None,
    q: str | None = Query(default=None, max_length=300),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=10_000_000),
) -> dict[str, Any]:
    # A search covers every note unless a view is asked for explicitly.
    view = view or ("all" if q else "inbox")
    notes = app.store.list_notes(
        view=view, kind=kind.value if kind else None, q=q, limit=limit, offset=offset
    )
    return {
        "notes": [note.model_dump(mode="json") for note in notes],
        "counts": app.store.count_notes(),
    }


@router.post("/notes", status_code=201)
async def create_note(app: User, body: NoteIn, wait: bool = False) -> dict[str, Any]:
    return await _create(
        app, body.text, _source(body.source, "api"), wait, body.captured_at, body.client_id
    )


@router.post("/notes/audio", status_code=201)
async def create_audio_note(
    app: User,
    file: UploadFile = File(...),
    source: str = Form("web-audio"),
    wait: bool = False,
) -> dict[str, Any]:
    text = await _transcribe(app, file)
    return await _create(app, text, _source(source, "web-audio"), wait)


@router.post("/transcribe")
async def transcribe(app: User, file: UploadFile = File(...)) -> dict[str, str]:
    return {"text": await _transcribe(app, file)}


async def _transcribe(app: Magpie, file: UploadFile) -> str:
    if not app.stt.enabled:
        raise HTTPException(
            503, "Server-side transcription is not configured (MAGPIE_STT_API_KEY)."
        )
    data = await file.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "The recording is larger than 25 MB.")
    try:
        text = await app.stt.transcribe(data, file.content_type or "audio/webm")
    except TranscriptionError as exc:
        raise HTTPException(502, str(exc)) from exc
    if not text:
        raise HTTPException(422, "No speech was detected in the recording.")
    return text


def _note_or_404(app: Magpie, note_id: int) -> Note:
    note = app.store.get_note(note_id)
    if note is None:
        raise HTTPException(404, "Note not found.")
    return note


@router.get("/notes/{note_id}")
async def get_note(app: User, note_id: int) -> Note:
    return _note_or_404(app, note_id)


@router.patch("/notes/{note_id}")
async def update_note(app: User, note_id: int, body: NotePatch) -> Note:
    note = _note_or_404(app, note_id)
    fields = body.model_dump(exclude_none=True)
    if "text" in fields and fields["text"] != note.text:
        fields["meta"] = {**note.meta, "edited": True}
    if "tags" in fields:
        tags = []
        for tag in fields["tags"]:
            tag = str(tag).strip().lstrip("#").lower()[:32]
            if tag and tag not in tags:
                tags.append(tag)
        fields["tags"] = tags[:10]
    if "status" in fields and fields["status"] != NoteStatus.OPEN:
        fields["in_digest"] = False
    return app.store.update_note(note_id, **fields)


@router.delete("/notes/{note_id}", status_code=204)
async def delete_note(app: User, note_id: int) -> Response:
    if not app.store.delete_note(note_id):
        raise HTTPException(404, "Note not found.")
    return Response(status_code=204)


@router.post("/notes/{note_id}/reprocess")
async def reprocess_note(app: User, note_id: int, wait: bool = False) -> Note:
    note = _note_or_404(app, note_id)
    if note.meta.get("edited") and note.text:
        # The user corrected the text: Claude should work from their version.
        meta = {**note.meta, "edited": False}
        meta.setdefault("original_raw", note.raw_text)
        app.store.update_note(note_id, raw_text=note.text, meta=meta)
    app.store.update_note(note_id, state=ProcState.PENDING, error=None)
    task = app.pipeline.spawn(app.pipeline.process(note_id))
    if wait:
        await asyncio.wait({task}, timeout=WAIT_SECONDS)
    return _note_or_404(app, note_id)


@router.post("/notes/{note_id}/reminders", status_code=201)
async def add_reminder(app: User, note_id: int, body: ReminderIn) -> Note:
    note = _note_or_404(app, note_id)
    try:
        app.reminders.add_manual(
            note_id, body.when, body.message or note.display_title, body.repeat
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _note_or_404(app, note_id)


# ----------------------------------------------------------------- reminders


@router.get("/reminders")
async def list_reminders(
    app: User,
    status: Literal["scheduled", "sent", "done", "failed", "all"] = "scheduled",
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    statuses = None if status == "all" else [ReminderStatus(status)]
    reminders = app.store.list_reminders(statuses, limit=limit, newest_first=status != "scheduled")
    note_ids = sorted({r.note_id for r in reminders if r.note_id})
    titles = {}
    for note_id in note_ids:
        note = app.store.get_note(note_id, children=False)
        if note:
            titles[note_id] = note.display_title
    tz = app.prefs().timezone
    return {
        "reminders": [
            {
                **r.model_dump(mode="json"),
                "note_title": titles.get(r.note_id, ""),
                "when": humanize(r.due_at, tz),
            }
            for r in reminders
        ]
    }


def _reminder_action(
    app: Magpie, reminder_id: int, action: str, minutes: int | None = None
) -> dict:
    try:
        if action == "done":
            reminder = app.reminders.done(reminder_id)
        elif action == "snooze":
            reminder = app.reminders.snooze(reminder_id, minutes)
        elif action == "tomorrow":
            reminder = app.reminders.tomorrow(reminder_id)
        elif action == "cancel":
            reminder = app.reminders.cancel(reminder_id)
        else:
            raise HTTPException(400, "Unknown action.")
    except KeyError as exc:
        raise HTTPException(404, "Reminder not found.") from exc
    return {
        **reminder.model_dump(mode="json"),
        "when": humanize(reminder.due_at, app.prefs().timezone),
    }


@router.post("/reminders/{reminder_id}/done")
async def reminder_done(app: User, reminder_id: int) -> dict:
    return _reminder_action(app, reminder_id, "done")


@router.post("/reminders/{reminder_id}/snooze")
async def reminder_snooze(app: User, reminder_id: int, body: SnoozeIn | None = None) -> dict:
    return _reminder_action(app, reminder_id, "snooze", body.minutes if body else None)


@router.post("/reminders/{reminder_id}/tomorrow")
async def reminder_tomorrow(app: User, reminder_id: int) -> dict:
    return _reminder_action(app, reminder_id, "tomorrow")


@router.post("/reminders/{reminder_id}/cancel")
async def reminder_cancel(app: User, reminder_id: int) -> dict:
    return _reminder_action(app, reminder_id, "cancel")


@router.patch("/reminders/{reminder_id}")
async def reschedule_reminder(app: User, reminder_id: int, body: RescheduleIn) -> dict:
    try:
        reminder = app.reminders.reschedule(reminder_id, body.when)
    except KeyError as exc:
        raise HTTPException(404, "Reminder not found.") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        **reminder.model_dump(mode="json"),
        "when": humanize(reminder.due_at, app.prefs().timezone),
    }


@router.delete("/reminders/{reminder_id}", status_code=204)
async def delete_reminder(app: User, reminder_id: int) -> Response:
    if not app.store.delete_reminder(reminder_id):
        raise HTTPException(404, "Reminder not found.")
    return Response(status_code=204)


# --------------------------------------------------------------------- links


@router.post("/links/{link_id}/retry")
async def retry_link(app: User, link_id: int) -> Note:
    link = app.store.get_link(link_id)
    if link is None:
        raise HTTPException(404, "Link not found.")
    app.store.update_link(link_id, state=ProcState.PENDING, error=None)
    app.pipeline.spawn(app.pipeline.review_link(link_id))
    return _note_or_404(app, link.note_id)


# ------------------------------------------------------------ digests and ask


@router.get("/digests")
async def list_digests(app: User) -> dict[str, Any]:
    return {"digests": [d.model_dump(mode="json") for d in app.store.list_digests()]}


@router.post("/digests", status_code=201)
async def create_digest(app: User, body: DigestIn) -> dict[str, Any]:
    digest = await app.digests.run(body.kind, send=body.send, force=True)
    return digest.model_dump(mode="json") if digest else {}


@router.post("/ask")
async def ask(app: User, body: AskIn) -> dict[str, Any]:
    try:
        answer = await app.ask(body.question)
    except BrainError as exc:
        raise HTTPException(503, str(exc)) from exc
    notes = []
    for note_id in answer.note_ids:
        note = app.store.get_note(note_id, children=False)
        if note is not None:
            notes.append({"id": note.id, "title": note.display_title, "kind": note.kind})
    return {"answer": answer.answer, "notes": notes}


# --------------------------------------------------------------------- prefs


@router.get("/prefs")
async def get_prefs(app: User) -> Prefs:
    return app.prefs()


@router.put("/prefs")
async def update_prefs(app: User, body: dict[str, Any] = Body(...)) -> Prefs:
    current = app.prefs().model_dump()
    changes = {
        key: value
        for key, value in body.items()
        if key in Prefs.model_fields and key != "telegram_chat_id"
    }
    try:
        prefs = Prefs.model_validate({**current, **changes})
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first.get("loc", ()))
        raise HTTPException(422, f"{field}: {first.get('msg')}") from exc
    return app.store.save_prefs(prefs)


# -------------------------------------------------------------------- Claude


@router.put("/claude/key")
async def save_claude_key(app: User, body: ClaudeKeyIn) -> dict[str, Any]:
    if app.settings.brain != "claude":
        raise HTTPException(409, "Magpie runs with MAGPIE_BRAIN=fake; remove it to use Claude.")
    if app.claude_key_source() == "env":
        raise HTTPException(
            409, "The key is set by ANTHROPIC_API_KEY on the server; change it there."
        )
    try:
        warning = await app.set_claude_key(body.key)
    except BrainError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"available": app.brain.available, "warning": warning}


@router.delete("/claude/key")
async def forget_claude_key(app: User) -> dict[str, Any]:
    if app.claude_key_source() == "env":
        raise HTTPException(
            409, "The key is set by ANTHROPIC_API_KEY on the server; change it there."
        )
    await app.set_claude_key(None)
    return {"available": app.brain.available}


# ------------------------------------------------------------- notifications


@router.post("/notify/test")
async def test_notification(app: User, body: TestIn | None = None) -> dict[str, Any]:
    channel = body.channel if body else None
    report = await app.notifier.send(
        Notification(
            title="Hello from Magpie",
            body="If you can read this, notifications work.",
            kind="test",
            url=app.url("/"),
        ),
        only=channel,
    )
    return {"ok": report.ok, "results": report.results}


@router.get("/push/key")
async def push_key(app: User) -> dict[str, str]:
    return {"public_key": app.webpush.keys.public_key}


@router.post("/push/subscribe", status_code=201)
async def push_subscribe(request: Request, app: User, body: PushSubscriptionIn) -> dict:
    p256dh, auth = body.keys.get("p256dh"), body.keys.get("auth")
    if (
        not body.endpoint.startswith("https://")
        or not p256dh
        or not auth
        or len(p256dh) > 200
        or len(auth) > 100
    ):
        raise HTTPException(422, "Invalid push subscription.")
    app.store.add_push_subscription(
        body.endpoint, p256dh, auth, request.headers.get("user-agent", "")
    )
    return {"subscribed": True}


@router.post("/push/unsubscribe")
async def push_unsubscribe(app: User, body: PushEndpointIn) -> dict:
    app.store.remove_push_subscription(body.endpoint)
    return {"subscribed": False}


@router.post("/telegram/pair")
async def telegram_pair(app: User) -> dict[str, str]:
    if app.bot is None:
        raise HTTPException(400, "Set MAGPIE_TELEGRAM_BOT_TOKEN and restart Magpie first.")
    return app.bot.new_pairing_code()


@router.delete("/telegram/pair")
async def telegram_unpair(app: User) -> dict[str, bool]:
    prefs = app.prefs()
    prefs.telegram_chat_id = None
    app.store.save_prefs(prefs)
    return {"paired": app.telegram_chat_id() is not None}


# ------------------------------------------------- tokens, calendar, export


@router.get("/token")
async def get_token(app: User) -> dict[str, Any]:
    return {"token": app.secrets.api_token, "from_env": app.secrets.api_token_from_env}


@router.post("/token/rotate")
async def rotate_token(app: User) -> dict[str, Any]:
    try:
        token = app.secrets.rotate_api_token()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"token": token, "from_env": False}


def _calendar_token(app: Magpie, rotate: bool = False) -> str:
    token = app.store.get_kv("calendar_token")
    if rotate or not token:
        token = secrets.token_urlsafe(24)
        app.store.set_kv("calendar_token", token)
    return token


@router.get("/calendar")
async def calendar_info(request: Request, app: User) -> dict[str, str]:
    base = app.settings.base_url or str(request.base_url).rstrip("/")
    return {"url": f"{base}/calendar/{_calendar_token(app)}.ics"}


@router.post("/calendar/rotate")
async def calendar_rotate(request: Request, app: User) -> dict[str, str]:
    base = app.settings.base_url or str(request.base_url).rstrip("/")
    return {"url": f"{base}/calendar/{_calendar_token(app, rotate=True)}.ics"}


@router.get("/export")
async def export(app: User, format: Literal["json", "markdown"] = "json") -> Response:
    stamp = now_utc().strftime("%Y-%m-%d")
    if format == "markdown":
        content = export_markdown(app.store, app.prefs().timezone)
        return Response(
            content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="magpie-{stamp}.md"'},
        )
    return Response(
        export_json(app.store),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="magpie-{stamp}.json"'},
    )


MAX_IMPORT_BYTES = 50 * 1024 * 1024


@router.post("/import")
async def import_notes(app: User, file: UploadFile = File(...)) -> dict[str, int]:
    raw = await file.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "The file is larger than 50 MB.")
    try:
        return import_json(app.store, raw)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


# ------------------------------------------------- public: calendar, actions


@public.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@public.get("/calendar/{token}.ics", include_in_schema=False)
async def calendar_feed(request: Request, token: str) -> Response:
    app = get_magpie(request)
    expected = app.store.get_kv("calendar_token")
    if not isinstance(expected, str) or not secrets.compare_digest(
        expected.encode(), token.encode()
    ):
        raise HTTPException(404)
    since = now_utc() - timedelta(days=14)
    reminders = [
        r
        for r in app.store.list_reminders(
            [ReminderStatus.SCHEDULED, ReminderStatus.SENT, ReminderStatus.DONE], limit=1000
        )
        if r.status == ReminderStatus.SCHEDULED or r.due_at >= since
    ]
    notes = {}
    for reminder in reminders:
        if reminder.note_id and reminder.note_id not in notes:
            note = app.store.get_note(reminder.note_id, children=False)
            if note:
                notes[note.id] = note
    body = reminders_calendar(
        reminders, notes, timezone=app.prefs().timezone, base_url=app.settings.base_url
    )
    return PlainTextResponse(body, media_type="text/calendar; charset=utf-8")


ACTION_LABELS = {
    "done": "Mark as done",
    "snooze": "Snooze for {snooze}",
    "tomorrow": "Remind me tomorrow",
}

ACTION_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Magpie</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:28rem;margin:4rem auto;padding:0 1rem;
color:#1f2430;background:#f7f7fb}}@media(prefers-color-scheme:dark){{body{{color:#e8e9f0;
background:#14161c}}}}button{{font:inherit;padding:.7rem 1.2rem;border:0;border-radius:.6rem;
background:#5b5bd6;color:#fff;cursor:pointer}}h1{{font-size:1.25rem}}</style></head>
<body><main><h1>{heading}</h1><p>{message}</p>{form}</main></body></html>"""


def _action_page(heading: str, message: str, form: str = "", status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        ACTION_PAGE.format(heading=html.escape(heading), message=html.escape(message), form=form),
        status_code=status,
    )


@public.get("/a/{token}", include_in_schema=False)
async def action_confirm(request: Request, token: str) -> HTMLResponse:
    app = get_magpie(request)
    try:
        reminder_id, action = app.signer.load(token)
        reminder = app.reminders.get(reminder_id)
    except ValueError as exc:
        return _action_page("Link not valid", str(exc), status=404)
    except KeyError:
        return _action_page("Not found", "This reminder no longer exists.", status=404)
    # GET only confirms: link scanners and previews must not change anything.
    label = ACTION_LABELS[action].format(snooze=snooze_label(app.prefs().snooze_minutes))
    form = f'<form method="post"><button type="submit">{html.escape(label)}</button></form>'
    return _action_page(reminder.message, "Confirm the action below.", form)


@public.post("/a/{token}", include_in_schema=False)
async def action_perform(request: Request, token: str) -> Response:
    app = get_magpie(request)
    try:
        reminder_id, action = app.signer.load(token)
        result = _reminder_action(app, reminder_id, action)
    except ValueError as exc:
        return _action_page("Link not valid", str(exc), status=404)
    except HTTPException:
        return _action_page("Not found", "This reminder no longer exists.", status=404)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse(result)
    message = {
        "done": "Marked as done.",
        "snooze": f"Snoozed until {result['when']}.",
        "tomorrow": f"See you {result['when']}.",
    }[action]
    return _action_page(result["message"], message)
