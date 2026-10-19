"""Server-side speech-to-text.

Claude does not take audio, so recordings (Telegram voice messages, browsers
without the Web Speech API) are transcribed by any service that speaks the
OpenAI-compatible ``POST /audio/transcriptions`` API: OpenAI, Groq, or a
self-hosted Whisper server.
"""

from __future__ import annotations

import logging

import httpx2

from magpie.config import Settings

log = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 25 * 1024 * 1024
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


class Transcriber:
    def __init__(self, http: httpx2.AsyncClient, settings: Settings) -> None:
        self.http = http
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.stt_api_key)

    async def transcribe(self, audio: bytes, content_type: str = "audio/webm") -> str:
        if not self.enabled:
            raise TranscriptionError(
                "Server-side transcription is off. Set MAGPIE_STT_API_KEY (OpenAI, Groq or any "
                "OpenAI-compatible Whisper server)."
            )
        if not audio:
            raise TranscriptionError("The recording is empty.")
        if len(audio) > MAX_AUDIO_BYTES:
            raise TranscriptionError("The recording is larger than 25 MB.")
        base_type = content_type.split(";")[0].strip().lower()
        extension = _EXTENSIONS.get(base_type, "webm")
        data = {"model": self.settings.stt_model, "response_format": "json"}
        if self.settings.stt_language:
            data["language"] = self.settings.stt_language
        try:
            response = await self.http.post(
                f"{self.settings.stt_base_url.rstrip('/')}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.settings.stt_api_key}"},
                data=data,
                files={"file": (f"recording.{extension}", audio, base_type or "audio/webm")},
                timeout=120,
            )
        except httpx2.HTTPError as exc:
            raise TranscriptionError(f"Could not reach the transcription service: {exc}") from exc
        if response.status_code >= 400:
            log.warning("Transcription failed (%s): %s", response.status_code, response.text[:300])
            raise TranscriptionError(f"The transcription service returned {response.status_code}.")
        try:
            data = response.json()
        except ValueError:
            data = response.text
        text = data.get("text") if isinstance(data, dict) else data
        return text.strip() if isinstance(text, str) else ""
