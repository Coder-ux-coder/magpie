"""Speech-to-text by a service of the user's own.

Claude does not take audio, so recordings are written down by a speech-to-text service:
the Magpie app's voice notes when Settings → Voice says so, Telegram voice messages, and
browsers that can't recognise speech themselves. The service is chosen in Settings →
Voice, and stored in the database the same way as the AI::

    {"service": "groq",
     "settings": {"groq": {"model": "...", "base_url": "", "key": "...", "key_for": "..."}}}

A saved key remembers the address it was entered for (``key_for``) and is only ever sent
there. With nothing chosen in the dashboard, MAGPIE_STT_API_KEY and its friends on the
server name an OpenAI-compatible service instead.

Services speak one of five APIs: OpenAI's ``/audio/transcriptions`` (OpenAI, Groq, and
servers of your own such as Speaches or LocalAI), Deepgram's, ElevenLabs', AssemblyAI's
and Gladia's (the last two take the sound first, then are asked for the text until it's
ready).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx2

from magpie.config import Settings

log = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_WORDS = 100  # words to know sent with a recording
MAX_PROMPT = 600  # characters of them in a Whisper-style prompt (which holds 224 tokens)
POLL_SECONDS = 1.0  # how often a service that works in the background is asked again
POLL_LIMIT = 180.0  # and for how long
ERROR_FIELDS = ("error", "detail", "message", "err_msg")  # where services say what's wrong
_EXTENSIONS = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/m4a": "m4a",
    "audio/aac": "m4a",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/flac": "flac",
    "video/mp4": "mp4",
    "video/webm": "webm",
}


class TranscriptionError(Exception):
    pass


@dataclass(frozen=True)
class Service:
    id: str
    label: str  # in the list: "Groq (Whisper large v3, very fast)"
    name: str  # in sentences: "Groq couldn't be reached"
    api: str  # openai | deepgram | elevenlabs | assemblyai | gladia
    base_url: str
    model: str  # "" means the service's own default
    key_url: str = ""
    key_hint: str = ""
    fixed_url: bool = True  # the address can't be changed in the dashboard
    needs_key: bool = True
    models: bool = True  # a model can be named
    help: str = ""


SERVICES: dict[str, Service] = {
    service.id: service
    for service in (
        Service(
            "openai",
            "OpenAI (gpt-4o-transcribe)",
            "OpenAI",
            "openai",
            "https://api.openai.com/v1",
            "gpt-4o-transcribe",
            key_url="https://platform.openai.com/api-keys",
            key_hint="sk-…",
            help="Very accurate, with accents too. Paid by the minute.",
        ),
        Service(
            "groq",
            "Groq (Whisper large v3, very fast)",
            "Groq",
            "openai",
            "https://api.groq.com/openai/v1",
            "whisper-large-v3",
            key_url="https://console.groq.com/keys",
            key_hint="gsk_…",
            help="Whisper's largest model, in about a second. Groq has a free tier.",
        ),
        Service(
            "deepgram",
            "Deepgram (Nova-3)",
            "Deepgram",
            "deepgram",
            "https://api.deepgram.com/v1",
            "nova-3",
            key_url="https://console.deepgram.com/",
            help="Fast and accurate, and listens out for the words you list below.",
        ),
        Service(
            "elevenlabs",
            "ElevenLabs (Scribe)",
            "ElevenLabs",
            "elevenlabs",
            "https://api.elevenlabs.io/v1",
            "scribe_v2",
            key_url="https://elevenlabs.io/app/settings/api-keys",
            help="Scribe: among the most accurate, with accents too.",
        ),
        Service(
            "assemblyai",
            "AssemblyAI",
            "AssemblyAI",
            "assemblyai",
            "https://api.assemblyai.com/v2",
            "",
            key_url="https://www.assemblyai.com/",
            help="Accurate. A note takes a few seconds longer.",
        ),
        Service(
            "gladia",
            "Gladia",
            "Gladia",
            "gladia",
            "https://api.gladia.io/v2",
            "",
            key_url="https://app.gladia.io/",
            models=False,
            help="Accurate. A note takes a few seconds longer.",
        ),
        Service(
            "compatible",
            "Another service (OpenAI-compatible)",
            "your speech service",
            "openai",
            "",
            "whisper-1",
            fixed_url=False,
            needs_key=False,
            help=(
                "Any service with an OpenAI-style /audio/transcriptions, such as one on your "
                "own network (Speaches, LocalAI, faster-whisper-server)."
            ),
        ),
    )
}
SERVER = Service(  # MAGPIE_STT_* on the server
    "server", "Set on the server", "the server's speech service", "openai", "", ""
)


@dataclass(frozen=True)
class SpeechConfig:
    service: Service | None
    model: str
    base_url: str
    key: str | None
    source: str  # "dashboard", "env" or "" (nothing set up)
    language: str | None = "en"  # Magpie listens for English

    @property
    def ready(self) -> bool:
        if self.service is None or not self.base_url:
            return False
        return bool(self.key) or not self.service.needs_key

    @property
    def name(self) -> str:
        return self.service.name if self.service is not None else "the speech service"

    @property
    def title(self) -> str:
        """The name at the start of a sentence."""
        return self.name[:1].upper() + self.name[1:]


def clean_saved_speech(saved: Any) -> dict[str, Any]:
    """The stored dashboard choice, with anything malformed dropped."""
    out: dict[str, Any] = {"service": "", "settings": {}}
    if not isinstance(saved, dict):
        return out
    if saved.get("service") in SERVICES:
        out["service"] = saved["service"]
    raw = saved.get("settings")
    if isinstance(raw, dict):
        out["settings"] = {
            sid: {
                k: v
                for k, v in values.items()
                if k in ("model", "base_url", "key", "key_for") and isinstance(v, str)
            }
            for sid, values in raw.items()
            if sid in SERVICES and isinstance(values, dict)
        }
    return out


def address(service: Service, entry: dict[str, str]) -> str:
    url = (entry.get("base_url") if not service.fixed_url else "") or service.base_url
    return url.strip().rstrip("/")


def resolve(settings: Settings, saved: Any, service_id: str | None = None) -> SpeechConfig:
    """The service to use: the dashboard's choice (or ``service_id``, to show what it
    would be), else the server's MAGPIE_STT_* settings, else none."""
    stored = clean_saved_speech(saved)
    sid = service_id or stored["service"]
    service = SERVICES.get(sid)
    if service is not None:
        entry = stored["settings"].get(sid, {})
        url = address(service, entry)
        key = entry.get("key") if entry.get("key") and entry.get("key_for") == url else None
        model = entry.get("model", service.model) if service.models else ""
        return SpeechConfig(service, model, url, key, "dashboard")
    if settings.stt_api_key:
        return SpeechConfig(
            SERVER,
            settings.stt_model,
            settings.stt_base_url.rstrip("/"),
            settings.stt_api_key,
            "env",
            settings.stt_language or None,
        )
    return SpeechConfig(None, "", "", None, "")


# ----------------------------------------------------------------- the APIs


def _headers(config: SpeechConfig) -> dict[str, str]:
    key = config.key
    if not key or config.service is None:
        return {}
    return {
        "deepgram": {"Authorization": f"Token {key}"},
        "elevenlabs": {"xi-api-key": key},
        "assemblyai": {"authorization": key},
        "gladia": {"x-gladia-key": key},
    }.get(config.service.api, {"Authorization": f"Bearer {key}"})


def _body(response: httpx2.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def _field(config: SpeechConfig, body: Any, name: str) -> str:
    """A value the service must send, as text."""
    value = body.get(name) if isinstance(body, dict) else None
    if not isinstance(value, str | int) or isinstance(value, bool) or value == "":
        raise _odd(config)
    return str(value)


def _odd(config: SpeechConfig) -> TranscriptionError:
    return TranscriptionError(f"{config.title} sent an answer Magpie doesn't understand.")


def _answer(config: SpeechConfig, response: httpx2.Response) -> Any:
    """The service's answer, or a plain sentence about what went wrong."""
    if response.status_code < 400:
        return _body(response)
    body = _body(response)
    if response.status_code in (401, 403):  # its answer may quote part of the key: not logged
        log.warning("%s refused the key (%s)", config.name, response.status_code)
        raise TranscriptionError(f"{config.title} refused the key. Check it in Settings → Voice.")
    detail = ""
    if isinstance(body, dict):
        found = next((body[k] for k in ERROR_FIELDS if body.get(k)), "")
        if isinstance(found, dict):
            found = found.get("message") or found.get("msg") or found.get("status")
        detail = str(found or "")[:200]
    log.warning("%s answered %s: %s", config.name, response.status_code, str(body)[:300])
    ending = f": {detail}" if detail else "."
    raise TranscriptionError(f"{config.title} answered {response.status_code}{ending}")


async def _openai(
    http: httpx2.AsyncClient, config: SpeechConfig, audio: bytes, kind: str, words: list[str]
) -> str:
    data = {"model": config.model, "response_format": "json"}
    if config.language:
        data["language"] = config.language
    if words:
        prompt = ", ".join(words)
        data["prompt"] = (
            prompt if len(prompt) <= MAX_PROMPT else prompt[:MAX_PROMPT].rsplit(",", 1)[0]
        )
    response = await http.post(
        f"{config.base_url}/audio/transcriptions",
        headers=_headers(config),
        data=data,
        files={"file": (f"recording.{_EXTENSIONS.get(kind, 'webm')}", audio, kind)},
        timeout=120,
    )
    body = _answer(config, response)
    return str(body.get("text") or "") if isinstance(body, dict) else str(body)


async def _deepgram(
    http: httpx2.AsyncClient, config: SpeechConfig, audio: bytes, kind: str, words: list[str]
) -> str:
    model = config.model or "nova-3"
    params = [("model", model), ("smart_format", "true"), ("language", "en")]
    if model.startswith(("nova-3", "flux")):  # the models that take key terms
        params += [("keyterm", word) for word in words]
    response = await http.post(
        f"{config.base_url}/listen",
        params=params,
        headers={**_headers(config), "Content-Type": kind},
        content=audio,
        timeout=120,
    )
    body = _answer(config, response)
    try:
        return str(body["results"]["channels"][0]["alternatives"][0]["transcript"] or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise _odd(config) from exc


async def _elevenlabs(
    http: httpx2.AsyncClient, config: SpeechConfig, audio: bytes, kind: str, words: list[str]
) -> str:
    data = {
        "model_id": config.model or "scribe_v2",
        "language_code": "en",
        "tag_audio_events": "false",  # no "(laughs)" in notes
    }
    response = await http.post(
        f"{config.base_url}/speech-to-text",
        headers=_headers(config),
        data=data,
        files={"file": (f"recording.{_EXTENSIONS.get(kind, 'webm')}", audio, kind)},
        timeout=120,
    )
    body = _answer(config, response)
    return str(body.get("text") or "") if isinstance(body, dict) else ""


async def _wait_for(
    http: httpx2.AsyncClient, config: SpeechConfig, url: str, done: str, failed: str
) -> dict[str, Any]:
    """Ask a service that works in the background until its text is ready."""
    deadline = time.monotonic() + POLL_LIMIT
    while True:
        body = _answer(config, await http.get(url, headers=_headers(config), timeout=30))
        status = body.get("status") if isinstance(body, dict) else None
        if status == done:
            return body
        if status == failed:
            reason = body.get("error") or body.get("error_code") or "no reason given"
            raise TranscriptionError(f"{config.title} couldn't write it down: {reason}")
        if time.monotonic() > deadline:
            raise TranscriptionError(f"{config.title} took too long.")
        await asyncio.sleep(POLL_SECONDS)


async def _assemblyai(
    http: httpx2.AsyncClient, config: SpeechConfig, audio: bytes, kind: str, words: list[str]
) -> str:
    uploaded = _answer(
        config,
        await http.post(
            f"{config.base_url}/upload", headers=_headers(config), content=audio, timeout=120
        ),
    )
    job: dict[str, Any] = {
        "audio_url": _field(config, uploaded, "upload_url"),
        "language_code": "en",
    }
    if config.model:
        job["speech_models"] = [config.model]
    started = _answer(
        config,
        await http.post(f"{config.base_url}/transcript", headers=_headers(config), json=job),
    )
    job_id = _field(config, started, "id")
    result = await _wait_for(
        http, config, f"{config.base_url}/transcript/{job_id}", "completed", "error"
    )
    return str(result.get("text") or "")


async def _gladia(
    http: httpx2.AsyncClient, config: SpeechConfig, audio: bytes, kind: str, words: list[str]
) -> str:
    uploaded = _answer(
        config,
        await http.post(
            f"{config.base_url}/upload",
            headers=_headers(config),
            files={"audio": (f"recording.{_EXTENSIONS.get(kind, 'webm')}", audio, kind)},
            timeout=120,
        ),
    )
    started = _answer(
        config,
        await http.post(
            f"{config.base_url}/pre-recorded",
            headers=_headers(config),
            json={"audio_url": _field(config, uploaded, "audio_url")},
        ),
    )
    job_id = _field(config, started, "id")
    result = await _wait_for(
        http, config, f"{config.base_url}/pre-recorded/{job_id}", "done", "error"
    )
    try:
        return str(result["result"]["transcription"]["full_transcript"] or "")
    except (KeyError, TypeError) as exc:
        raise _odd(config) from exc


Api = Callable[[httpx2.AsyncClient, SpeechConfig, bytes, str, list[str]], Awaitable[str]]
APIS: dict[str, Api] = {
    "openai": _openai,
    "deepgram": _deepgram,
    "elevenlabs": _elevenlabs,
    "assemblyai": _assemblyai,
    "gladia": _gladia,
}
# Where a key can be tried without sending any sound (the others are tried by the first
# note). Only a 401 means the key is wrong: anything else is a problem for later.
CHECKS = {"openai": "/models", "deepgram": "/projects", "assemblyai": "/transcript?limit=1"}


class Transcriber:
    """Writes recordings down with the service in use (see ``resolve``)."""

    def __init__(
        self,
        http: httpx2.AsyncClient,
        settings: Settings,
        saved: Callable[[], Any] = lambda: None,
    ) -> None:
        self.http = http
        self.settings = settings
        self._saved = saved

    @property
    def config(self) -> SpeechConfig:
        return resolve(self.settings, self._saved())

    @property
    def enabled(self) -> bool:
        return self.config.ready

    async def transcribe(
        self,
        audio: bytes,
        content_type: str = "audio/webm",
        *,
        words: Sequence[str] = (),
        config: SpeechConfig | None = None,
    ) -> str:
        config = config or self.config
        if not config.ready or config.service is None:
            raise TranscriptionError(
                "No speech service is set up: choose one in Settings → Voice (or set "
                "MAGPIE_STT_API_KEY on the server)."
            )
        if not audio:
            raise TranscriptionError("The recording is empty.")
        if len(audio) > MAX_AUDIO_BYTES:
            raise TranscriptionError("The recording is larger than 25 MB.")
        kind = content_type.split(";")[0].strip().lower() or "audio/webm"
        known = [w for w in dict.fromkeys(" ".join(str(w).split()) for w in words) if w]
        try:
            text = await APIS[config.service.api](self.http, config, audio, kind, known[:MAX_WORDS])
        except httpx2.HTTPError as exc:
            raise TranscriptionError(f"{config.title} couldn't be reached: {exc}") from exc
        return " ".join(str(text or "").split())

    async def check(self, config: SpeechConfig) -> str | None:
        """Try a key before it's saved: raises TranscriptionError when the service says
        it's wrong, and returns a warning when the service couldn't be asked."""
        path = CHECKS.get(config.service.api) if config.service is not None else None
        if path is None or not config.key:
            return None
        try:
            response = await self.http.get(
                f"{config.base_url}{path}", headers=_headers(config), timeout=15
            )
        except httpx2.HTTPError as exc:
            return f"{config.title} couldn't be reached to check the key ({exc}). Saved anyway."
        if response.status_code == 401:
            raise TranscriptionError(
                f"{config.title} refused the key. Check it and paste it again."
            )
        return None
