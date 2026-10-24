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
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field, ValidationError, field_validator

from magpie import __version__
from magpie.auth import RateLimiter
from magpie.brain import PROVIDERS, BrainError
from magpie.config import Prefs
from magpie.core import Magpie
from magpie.export import export_json, export_markdown, import_json
from magpie.ics import reminders_calendar
from magpie.models import (
    COLORS,
    Category,
    Note,
    NoteKind,
    NoteStatus,
    ProcState,
    ReminderStatus,
)
from magpie.notify import Notification, snooze_label
from magpie.notify.channels import EmailError, SmtpConfig, smtp_preset
from magpie.stt import MAX_AUDIO_BYTES, SERVICES, TranscriptionError
from magpie.timeutil import humanize, now_utc

router = APIRouter(prefix="/api")
public = APIRouter()

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
WAIT_SECONDS = 60
_SOURCE = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
log = logging.getLogger(__name__)
login_limiter = RateLimiter(attempts=10, window=900)


def get_magpie(request: Request) -> Magpie:
    return request.app.state.magpie


def session_fingerprint(app: Magpie) -> str:
    material = f"{app.secrets.password}\0{app.secrets.secret_key}".encode()
    return hashlib.sha256(material).hexdigest()[:32]


LOCAL_NAMES = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
IN_DOCKER = Path("/.dockerenv").exists()  # the dashboard suggests the Windows app there
LOCAL_ONLY = "Magpie only opens on the computer it runs on (http://localhost)."


def is_local_host(host: str) -> bool:
    """Whether a Host header names this computer.

    Without a password this is what keeps other websites out: a page that points its
    own domain at 127.0.0.1 (DNS rebinding) still sends its own name as the Host.
    """
    host = host.strip().lower()
    if host.startswith("["):
        name = host[1:].split("]", 1)[0]
    else:
        name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return name in LOCAL_NAMES or name.endswith(".localhost")


def signed_in(request: Request, app: Magpie) -> bool:
    if not app.settings.login:
        return is_local_host(request.headers.get("host", ""))
    return request.session.get("fp") == session_fingerprint(app)


async def require_user(request: Request) -> Magpie:
    app = get_magpie(request)
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and token and app.secrets.check_token(token.strip()):
        return app
    if signed_in(request, app):
        if request.method not in SAFE_METHODS and request.headers.get("x-magpie") != "1":
            raise HTTPException(403, "Missing the X-Magpie header.")
        return app
    if not app.settings.login:
        raise HTTPException(403, LOCAL_ONLY)
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
    category_id: int | None = Field(
        default=None, ge=1, description="Put the note in this category (the tab it was written in)."
    )
    captured_in: str | None = Field(
        default=None, max_length=60, description="Where the note was written, e.g. a tab's name."
    )

    _text = field_validator("text")(_not_blank)


class NotePatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    text: str | None = Field(default=None, max_length=50_000)
    kind: NoteKind | None = None
    tags: list[str] | None = None
    status: NoteStatus | None = None
    pinned: bool | None = None
    category_id: int | None = Field(
        default=None, ge=0, description="0 takes it out of its category"
    )


def _category_name(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    if not value or any(ord(char) < 32 for char in value):
        raise ValueError("Give the category a name.")
    return value


def _category_color(value: str | None) -> str | None:
    if value is not None and value not in COLORS:
        raise ValueError(f"Choose one of: {', '.join(COLORS)}.")
    return value


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    color: str = "blue"

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str | None:
        return _category_name(value)

    @field_validator("color")
    @classmethod
    def _color(cls, value: str) -> str | None:
        return _category_color(value)


class CategoryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    color: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return _category_name(value)

    @field_validator("color")
    @classmethod
    def _color(cls, value: str | None) -> str | None:
        return _category_color(value)


class OrderIn(BaseModel):
    ids: list[int] = Field(max_length=200)


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
    authenticated = signed_in(request, app)
    return {
        "authenticated": authenticated,
        "login": app.settings.login,
        "message": None if authenticated or app.settings.login else LOCAL_ONLY,
        "version": __version__,
    }


@router.post("/login")
async def login(request: Request, body: LoginIn) -> dict[str, Any]:
    app = get_magpie(request)
    if not app.settings.login:
        return {"authenticated": signed_in(request, app)}
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
        "build": app.settings.build[:12],
        "brain": {
            "name": app.brain.name,
            "label": app.brain.label,
            "available": app.brain.available,
            "model": getattr(app.brain, "model", app.brain.name),
            "usage": app.brain.usage(),
            "key_source": app.ai_config().key_source,
            "locked": app.ai_config().locked,
            "web_search": app.brain.name == "claude",
        },
        "login": app.settings.login,
        # A fresh install with no time zone chosen anywhere yet: the dashboard
        # adopts the browser's zone once.
        "timezone_unset": "timezone" not in app.settings.model_fields_set
        and "timezone" not in (app.store.get_kv("prefs") or {}),
        "stt": {"enabled": app.stt.enabled, "name": app.stt.config.name},
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
        "categories": [c.model_dump() for c in app.store.list_categories()],
        "desktop": _desktop_status(app),
        "docker": IN_DOCKER,
        "base_url": app.settings.base_url,
        "password_generated": app.secrets.password_generated,
        "prefs": prefs.model_dump(),
    }


def _desktop_status(app: Magpie) -> dict[str, Any]:
    if app.desktop is None:
        return {"active": False}
    try:
        return {"active": True, **app.desktop.status()}
    except Exception:  # a status must never fail the page
        log.exception("The desktop app's status failed")
        return {"active": True, "state": "error"}


class DesktopCommand(BaseModel):
    action: Literal[
        "start",
        "finish",
        "cancel",
        "write",
        "open",
        "listen",
        "pause",
        "check_update",
        "install_update",
    ]


class DesktopUiCall(BaseModel):
    window: Literal["bar", "alerts"]
    action: str = Field(max_length=24, pattern=r"^[a-z_]+$")
    text: str = Field("", max_length=50000)
    key: str = Field("", max_length=24)
    height: float = Field(0, ge=0, le=10000)


async def _in_app(work, *args: Any) -> Any:
    """Run app code off the event loop: it may call back into this server."""
    try:
        return await asyncio.to_thread(work, *args)
    except Exception as exc:
        log.exception("The desktop app failed")
        raise HTTPException(500, "The Magpie app couldn't do that. Its log says why.") from exc


@router.get("/desktop")
async def desktop_state(app: User) -> dict[str, Any]:
    """What the Magpie app is doing (listening, taking a note, …), polled while it runs."""
    return _desktop_status(app)


@router.post("/desktop/command")
async def desktop_command(app: User, body: DesktopCommand) -> dict[str, Any]:
    """From the dashboard: start a voice note in the Magpie app, stop listening, …"""
    if app.desktop is None:
        raise HTTPException(409, "The Magpie app isn't running on this computer.")
    await _in_app(app.desktop.command, body.action)
    return {"ok": True, **_desktop_status(app)}


@router.post("/desktop/ui")
async def desktop_ui(app: User, body: DesktopUiCall) -> dict[str, Any]:
    """The app's own windows (the voice bar, reminder pop-ups) reaching the app."""
    if app.desktop is None:
        raise HTTPException(409, "The Magpie app isn't running on this computer.")
    details = body.model_dump(exclude={"window", "action"})
    problem = await _in_app(app.desktop.ui, body.window, body.action, details)
    return {"ok": problem is None, "problem": problem}


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
    category_id: int | None = None,
    captured_in: str | None = None,
) -> dict[str, Any]:
    if client_id:
        earlier = app.store.note_by_client_id(client_id, now_utc() - timedelta(days=30))
        if earlier is not None:
            return {"note": earlier.model_dump(mode="json"), "reply": earlier.reply or "Saved."}
    category = app.store.get_category(category_id) if category_id else None
    meta: dict[str, Any] = {}
    if client_id:
        meta["client_id"] = client_id
    if captured_in and captured_in.strip():
        meta["captured_in"] = " ".join(captured_in.split())[:60]
    if category is not None:
        meta["category_by"] = "you"
    note = app.store.create_note(text.strip(), source, meta or None)
    if category is not None:
        note = app.store.update_note(note.id, category_id=category.id)
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
        name = app.brain.label
        if fresh.state == ProcState.FAILED:
            reply = f"Saved, but {name} couldn't process it."
        elif not app.brain.available:
            reply = f"Saved. {name} isn't set up yet, so it will be filed later."
        else:
            reply = f"Saved. {name} is still working on it."
    return {"note": fresh.model_dump(mode="json"), "reply": reply}


@router.get("/notes")
async def list_notes(
    app: User,
    view: Literal["inbox", "all", "done", "archived", "scheduled", "reviews"] | None = None,
    kind: NoteKind | None = None,
    category: int | None = Query(default=None, ge=1, le=2**63 - 1),
    q: str | None = Query(default=None, max_length=300),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=10_000_000),
) -> dict[str, Any]:
    # A search covers every note unless a view is asked for explicitly.
    view = view or ("all" if q else "inbox")
    notes = app.store.list_notes(
        view=view,
        kind=kind.value if kind else None,
        q=q,
        category_id=category,
        limit=limit,
        offset=offset,
    )
    return {
        "notes": [note.model_dump(mode="json") for note in notes],
        "counts": app.store.count_notes(),
        "categories": [c.model_dump() for c in app.store.list_categories()],
    }


@router.post("/notes", status_code=201)
async def create_note(app: User, body: NoteIn, wait: bool = False) -> dict[str, Any]:
    return await _create(
        app,
        body.text,
        _source(body.source, "api"),
        wait,
        body.captured_at,
        body.client_id,
        body.category_id,
        body.captured_in,
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
            503,
            "No speech service is set up: choose one in Settings → Voice (or set "
            "MAGPIE_STT_API_KEY on the server).",
        )
    data = await file.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "The recording is larger than 25 MB.")
    try:
        text = await app.stt.transcribe(
            data, file.content_type or "audio/webm", words=app.prefs().vocabulary_words()
        )
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
    meta = dict(note.meta)
    if "text" in fields and fields["text"] != note.text:
        meta["edited"] = True
    if "category_id" in body.model_fields_set:
        wanted = body.category_id or None
        category = app.store.get_category(wanted) if wanted else None
        if wanted and category is None:
            raise HTTPException(422, "That category doesn't exist any more.")
        fields["category_id"] = wanted
        if wanted != note.category_id:
            before = app.store.get_category(note.category_id) if note.category_id else None
            if category is not None and note.state == ProcState.READY:
                app.learn(note, "category", before.name if before else "", category.name)
            meta["category_by"] = "you"
    if "kind" in fields and fields["kind"] != note.kind and note.state == ProcState.READY:
        app.learn(note, "kind", str(note.kind), str(fields["kind"]))
    if meta != note.meta:
        fields["meta"] = meta
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
    saved = app.store.save_prefs(prefs)
    if app.desktop is not None:  # a new command word works at once
        try:
            await asyncio.to_thread(app.desktop.prefs_changed, saved.model_dump())
        except Exception:
            log.exception("The desktop app didn't take the new settings")
    return saved


# -------------------------------------------------------------------- Claude


# ---------------------------------------------------------------- categories


@router.get("/categories")
async def list_categories(app: User) -> dict[str, Any]:
    return {
        "categories": [c.model_dump() for c in app.store.list_categories()],
        "colors": list(COLORS),
    }


@router.post("/categories", status_code=201)
async def add_category(app: User, body: CategoryIn) -> Category:
    if len(app.store.list_categories()) >= 100:
        raise HTTPException(422, "That's a lot of categories already; remove one first.")
    try:
        return app.store.add_category(body.name, body.color)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.put("/categories/order")
async def order_categories(app: User, body: OrderIn) -> dict[str, Any]:
    return {"categories": [c.model_dump() for c in app.store.order_categories(body.ids)]}


@router.patch("/categories/{category_id}")
async def update_category(app: User, category_id: int, body: CategoryPatch) -> Category:
    try:
        return app.store.update_category(category_id, name=body.name, color=body.color)
    except KeyError as exc:
        raise HTTPException(404, "Category not found.") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/categories/{category_id}", status_code=204)
async def delete_category(app: User, category_id: int) -> Response:
    if not app.store.delete_category(category_id):
        raise HTTPException(404, "Category not found.")
    return Response(status_code=204)


# ------------------------------------------------------------------ learning


@router.get("/learning")
async def learning(app: User) -> dict[str, Any]:
    return {"enabled": app.prefs().learn, "lessons": app.lessons()}


@router.delete("/learning")
async def forget_learning(
    app: User, index: int | None = Query(default=None, ge=0)
) -> dict[str, Any]:
    lessons = app.lessons()
    if index is None:
        lessons = []
    elif index < len(lessons):
        lessons.pop(index)
    app.store.set_kv("lessons", lessons)
    return {"enabled": app.prefs().learn, "lessons": lessons}


def _claude_key_refusal(app: Magpie) -> str | None:
    if app.settings.brain == "fake":
        return "Magpie runs with MAGPIE_BRAIN=fake; remove it to use Claude."
    config = app.ai_config()
    if config.locked and config.provider.id != "claude":
        return f"The server chooses the AI (MAGPIE_BRAIN={app.settings.brain}); change it there."
    if app.claude_key_source() == "env":
        return "The key is set by ANTHROPIC_API_KEY on the server; change it there."
    return None


@router.put("/claude/key")
async def save_claude_key(app: User, body: ClaudeKeyIn) -> dict[str, Any]:
    refusal = _claude_key_refusal(app)
    if refusal:
        raise HTTPException(409, refusal)
    try:
        warning = await app.set_claude_key(body.key)
    except (BrainError, ValueError) as exc:
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


# ------------------------------------------------------------------------ AI


def _clean_key(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip()
    if any(char.isspace() for char in value):
        raise ValueError("Paste the key on its own, without spaces.")
    return value


def _clean_address(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if value and not value.startswith(("http://", "https://")):
        raise ValueError("The address must start with http:// or https://.")
    return value


class AIModelsIn(BaseModel):
    provider: str = Field(max_length=20)
    base_url: str | None = Field(default=None, max_length=500)
    key: str | None = Field(default=None, max_length=500)

    @field_validator("key")
    @classmethod
    def _key(cls, value: str | None) -> str | None:
        return _clean_key(value)

    @field_validator("base_url")
    @classmethod
    def _address(cls, value: str | None) -> str | None:
        return _clean_address(value)


class AIIn(AIModelsIn):
    model: str | None = Field(default=None, max_length=200)

    @field_validator("model")
    @classmethod
    def _model_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if any(char.isspace() for char in value):
            raise ValueError("A model name has no spaces.")
        return value


@router.get("/ai")
async def ai_settings(app: User) -> dict[str, Any]:
    return app.ai_status()


class SpeechIn(BaseModel):
    service: str = Field(max_length=20, description='A service\'s id, or "" for none')
    model: str | None = Field(default=None, max_length=100)
    base_url: str | None = Field(default=None, max_length=300)
    key: str | None = Field(default=None, max_length=500, description="Leave out to keep it")

    @field_validator("model", "key")
    @classmethod
    def _one_word(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if any(char.isspace() for char in value):
            raise ValueError("It has no spaces.")
        return value


@router.get("/speech")
async def speech_settings(app: User) -> dict[str, Any]:
    """The speech service that writes recordings down (Settings → Voice)."""
    return app.speech_status()


@router.put("/speech")
async def save_speech(app: User, body: SpeechIn) -> dict[str, Any]:
    try:
        warning = await app.set_speech(
            body.service, model=body.model, base_url=body.base_url, key=body.key
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {**app.speech_status(), "warning": warning}


@router.delete("/speech/key")
async def forget_speech_key(
    app: User, service: str = Query(min_length=1, max_length=20)
) -> dict[str, Any]:
    if service not in SERVICES:
        raise HTTPException(422, "Unknown speech service.")
    app.forget_speech_key(service)
    return app.speech_status()


@router.put("/ai")
async def save_ai(app: User, body: AIIn) -> dict[str, Any]:
    config = app.ai_config()
    if config.provider.id == "fake":
        raise HTTPException(409, "Magpie runs with MAGPIE_BRAIN=fake; remove it to choose an AI.")
    if config.locked and body.provider != config.provider.id:
        raise HTTPException(
            409, f"The server chooses the AI (MAGPIE_BRAIN={app.settings.brain}); change it there."
        )
    if body.key and body.provider == config.provider.id and config.key_source == "env":
        raise HTTPException(409, "The key is set on the server; change it there.")
    try:
        warning = await app.set_ai(
            body.provider, model=body.model, base_url=body.base_url, key=body.key
        )
    except (BrainError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {**app.ai_status(), "warning": warning}


@router.delete("/ai/key")
async def forget_ai_key(
    app: User, provider: str = Query(min_length=1, max_length=20)
) -> dict[str, Any]:
    config = app.ai_config()
    if provider == config.provider.id and config.key_source == "env":
        raise HTTPException(409, "The key is set on the server; change it there.")
    if provider not in PROVIDERS:
        raise HTTPException(422, "Unknown AI provider.")
    app.forget_ai_key(provider)
    return app.ai_status()


@router.post("/ai/models")
async def ai_models(app: User, body: AIModelsIn) -> dict[str, Any]:
    try:
        models = await app.ai_models(body.provider, base_url=body.base_url, key=body.key)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except BrainError as exc:
        raise HTTPException(502 if exc.retryable else 422, str(exc)) from exc
    return {"models": models}


# --------------------------------------------------------------------- email

_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EmailIn(BaseModel):
    address: str = Field(max_length=254)
    password: str | None = Field(
        default=None, max_length=300, description="Leave out to keep the saved one"
    )
    to: str | None = Field(default=None, max_length=254, description="Defaults to the address")
    host: str | None = Field(default=None, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    security: Literal["starttls", "ssl", "none"] | None = None

    @field_validator("address", "to")
    @classmethod
    def _address(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not _ADDRESS.match(value):
            raise ValueError("That doesn't look like an email address.")
        return value

    @field_validator("host")
    @classmethod
    def _host(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip().lower()
        if any(char.isspace() or char in "/:@" for char in value):
            raise ValueError("Enter just the server name, like smtp.example.com.")
        return value


def _email_status(app: Magpie) -> dict[str, Any]:
    config = app.email.config()
    return {
        "configured": app.email.configured,
        "source": config.source if config else None,
        "address": config.sender if config else "",
        "to": config.to if config else "",
        "host": config.host if config else "",
        "port": config.port if config else None,
        "security": config.security if config else None,
        "enabled": app.prefs().channel_enabled("email"),
    }


@router.get("/email")
async def email_settings(app: User) -> dict[str, Any]:
    return _email_status(app)


@router.put("/email")
async def save_email(app: User, body: EmailIn) -> dict[str, Any]:
    current = app.email.config()
    if current is not None and current.source == "env":
        raise HTTPException(
            409, "Email is set up on the server (MAGPIE_SMTP_HOST); change it there."
        )
    if body.address is None:
        raise HTTPException(422, "Enter your email address.")
    password = body.password or (
        current.password if current and current.sender.lower() == body.address.lower() else ""
    )
    if not password:
        raise HTTPException(422, "Enter the app password for this address.")
    preset = smtp_preset(body.address)
    host = body.host or (preset[0] if preset else None)
    if not host:
        raise HTTPException(
            422, "Magpie doesn't know the mail server for this address; enter it (SMTP server)."
        )
    port = body.port or (preset[1] if preset and host == preset[0] else 587)
    security = body.security or (preset[2] if preset and host == preset[0] else "starttls")
    config = SmtpConfig(
        host,
        port,
        security,
        body.address,
        password,
        body.address,
        body.to or body.address,
        "dashboard",
    )
    try:
        await asyncio.to_thread(app.email.check, config)
    except EmailError as exc:
        raise HTTPException(422, str(exc)) from exc
    app.store.set_kv(
        "email",
        {
            "address": config.sender,
            "password": config.password,
            "host": config.host,
            "port": config.port,
            "security": config.security,
            "to": config.to,
        },
    )
    return _email_status(app)


@router.delete("/email")
async def forget_email(app: User) -> dict[str, Any]:
    current = app.email.config()
    if current is not None and current.source == "env":
        raise HTTPException(
            409, "Email is set up on the server (MAGPIE_SMTP_HOST); change it there."
        )
    app.store.delete_kv("email")
    return _email_status(app)


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
