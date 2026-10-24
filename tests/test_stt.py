"""Speech services of the user's own: what each is sent, what comes back, keys that only
go where they were entered for, and plain answers when a service fails."""

from __future__ import annotations

import json

import httpx2
import pytest

from magpie import stt
from magpie.stt import SpeechConfig, TranscriptionError

WAV = b"RIFF....WAVEfmt "  # what the app sends: its note's sound


def config(service: str, key: str | None = "k-123", **changes) -> SpeechConfig:
    info = stt.SERVICES[service]
    values = {"model": info.model, "base_url": info.base_url or "http://localhost:8000/v1"}
    values.update(changes)
    return SpeechConfig(info, values["model"], values["base_url"], key, "dashboard")


def form(request: httpx2.Request) -> bytes:
    return request.content


async def test_openai_and_groq_are_sent_the_note_with_the_words_to_know(core, router):
    router.add("POST", "https://api.openai.com/v1/audio/transcriptions", {"text": " Call James. "})
    text = await core.stt.transcribe(
        WAV, "audio/wav", words=["James", "Magpie", "James", " "], config=config("openai")
    )
    assert text == "Call James."
    request = router.requests[-1]
    assert request.headers["authorization"] == "Bearer k-123"
    body = form(request)
    assert b'name="model"\r\n\r\ngpt-4o-transcribe' in body
    assert b'name="language"\r\n\r\nen' in body  # Magpie listens for English
    assert b'name="prompt"\r\n\r\nJames, Magpie' in body
    assert b'filename="recording.wav"' in body

    router.add("POST", "https://api.groq.com/openai/v1/audio/transcriptions", {"text": "Hi."})
    assert await core.stt.transcribe(WAV, "audio/wav", config=config("groq")) == "Hi."
    assert b"whisper-large-v3" in form(router.requests[-1])


async def test_a_long_list_of_words_is_cut_to_whole_words_that_fit_a_prompt(core, router):
    router.add("POST", "https://api.groq.com/openai/v1/audio/transcriptions", {"text": "Hi."})
    words = [f"Name{i:03d}" for i in range(100)]  # 100 words of 7 letters: 898 characters
    await core.stt.transcribe(WAV, "audio/wav", words=words, config=config("groq"))
    body = form(router.requests[-1]).decode("latin-1")
    prompt = body.split('name="prompt"\r\n\r\n')[1].split("\r\n")[0]
    assert len(prompt) <= stt.MAX_PROMPT and prompt.startswith("Name000, Name001")
    assert prompt.split(", ")[-1] in words  # no word cut in half


async def test_a_service_on_your_own_network_needs_no_key(core, router):
    router.add("POST", "http://localhost:8000/v1/audio/transcriptions", {"text": "Buy milk."})
    chosen = config("compatible", key=None, model="Systran/faster-whisper-large-v3")
    assert chosen.ready
    assert await core.stt.transcribe(WAV, "audio/wav", config=chosen) == "Buy milk."
    assert "authorization" not in router.requests[-1].headers


async def test_deepgram_gets_the_sound_itself_and_the_words_as_key_terms(core, router):
    answer = {"results": {"channels": [{"alternatives": [{"transcript": "Call James at 5."}]}]}}
    router.add("POST", "https://api.deepgram.com/v1/listen", answer)
    text = await core.stt.transcribe(
        WAV, "audio/wav", words=["James", "Kinnaird Road"], config=config("deepgram")
    )
    assert text == "Call James at 5."
    request = router.requests[-1]
    assert request.headers["authorization"] == "Token k-123"
    assert request.headers["content-type"] == "audio/wav" and request.content == WAV
    params = request.url.params
    assert params["model"] == "nova-3" and params["smart_format"] == "true"
    assert params.get_list("keyterm") == ["James", "Kinnaird Road"]
    # Older models don't take key terms.
    await core.stt.transcribe(
        WAV, "audio/wav", words=["James"], config=config("deepgram", model="nova-2")
    )
    assert "keyterm" not in router.requests[-1].url.params


async def test_elevenlabs_scribe(core, router):
    router.add("POST", "https://api.elevenlabs.io/v1/speech-to-text", {"text": "Pay the rent."})
    assert (
        await core.stt.transcribe(WAV, "audio/wav", config=config("elevenlabs")) == "Pay the rent."
    )
    request = router.requests[-1]
    assert request.headers["xi-api-key"] == "k-123"
    body = form(request)
    assert b'name="model_id"\r\n\r\nscribe_v2' in body
    assert b'name="tag_audio_events"\r\n\r\nfalse' in body  # no "(laughs)" in notes


async def test_assemblyai_and_gladia_are_asked_until_the_text_is_ready(core, router, monkeypatch):
    monkeypatch.setattr(stt, "POLL_SECONDS", 0.0)
    router.add("POST", "https://api.assemblyai.com/v2/upload", {"upload_url": "https://cdn/a1"})
    router.add("POST", "https://api.assemblyai.com/v2/transcript", {"id": "t1", "status": "queued"})
    answers = iter([{"status": "processing"}, {"status": "completed", "text": "Water the plants."}])
    router.add(
        "GET",
        "https://api.assemblyai.com/v2/transcript/t1",
        lambda r: httpx2.Response(200, json=next(answers)),
    )
    chosen = config("assemblyai")
    assert await core.stt.transcribe(WAV, "audio/wav", config=chosen) == "Water the plants."
    job = json.loads(next(r for r in router.requests if r.url.path == "/v2/transcript").content)
    assert job == {"audio_url": "https://cdn/a1", "language_code": "en"}  # the service's model
    assert all(r.headers["authorization"] == "k-123" for r in router.requests)

    router.add("POST", "https://api.gladia.io/v2/upload", {"audio_url": "https://gladia/x"})
    router.add("POST", "https://api.gladia.io/v2/pre-recorded", {"id": "g1"})
    done = {"status": "done", "result": {"transcription": {"full_transcript": "Call Mum."}}}
    router.add("GET", "https://api.gladia.io/v2/pre-recorded/g1", done)
    assert await core.stt.transcribe(WAV, "audio/wav", config=config("gladia")) == "Call Mum."
    assert router.requests[-1].headers["x-gladia-key"] == "k-123"
    upload = next(
        r for r in router.requests if r.url.path == "/v2/upload" and "gladia" in r.url.host
    )
    assert b'name="audio"; filename="recording.wav"' in upload.content


async def test_a_failing_service_says_what_went_wrong(core, router, monkeypatch):
    monkeypatch.setattr(stt, "POLL_SECONDS", 0.0)
    url = "https://api.openai.com/v1/audio/transcriptions"
    cases = [
        (
            httpx2.Response(401, json={"error": {"message": "Incorrect API key sk-...123"}}),
            "refused the key",
        ),
        (
            httpx2.Response(500, json={"error": {"message": "Overloaded"}}),
            "answered 500: Overloaded",
        ),
        (httpx2.Response(429, text="slow down"), "answered 429."),
    ]
    for response, said in cases:
        router.add("POST", url, lambda r, response=response: response)
        with pytest.raises(TranscriptionError, match=said):
            await core.stt.transcribe(WAV, "audio/wav", config=config("openai"))

    def unreachable(request):
        raise httpx2.ConnectError("no route to host")

    router.add("POST", url, unreachable)
    with pytest.raises(TranscriptionError, match="OpenAI couldn't be reached"):
        await core.stt.transcribe(WAV, "audio/wav", config=config("openai"))
    router.add("POST", "http://localhost:8000/v1/audio/transcriptions", unreachable)
    with pytest.raises(TranscriptionError, match="^Your speech service couldn't be reached"):
        await core.stt.transcribe(WAV, "audio/wav", config=config("compatible", key=None))

    router.add("POST", "https://api.deepgram.com/v1/listen", {"results": {}})
    with pytest.raises(TranscriptionError, match="doesn't understand"):
        await core.stt.transcribe(WAV, "audio/wav", config=config("deepgram"))

    router.add("POST", "https://api.assemblyai.com/v2/upload", {"upload_url": "https://cdn/a"})
    router.add("POST", "https://api.assemblyai.com/v2/transcript", {"id": "t2"})
    router.add(
        "GET",
        "https://api.assemblyai.com/v2/transcript/t2",
        {"status": "error", "error": "no speech"},
    )
    with pytest.raises(TranscriptionError, match="couldn't write it down: no speech"):
        await core.stt.transcribe(WAV, "audio/wav", config=config("assemblyai"))

    router.add("POST", "https://api.gladia.io/v2/upload", {"nothing": "useful"})
    with pytest.raises(TranscriptionError, match="doesn't understand"):
        await core.stt.transcribe(WAV, "audio/wav", config=config("gladia"))

    with pytest.raises(TranscriptionError, match="Settings → Voice"):
        await core.stt.transcribe(WAV, "audio/wav")  # nothing set up
    with pytest.raises(TranscriptionError, match="empty"):
        await core.stt.transcribe(b"", "audio/wav", config=config("openai"))


def test_a_saved_key_only_goes_to_the_address_it_was_entered_for(settings):
    saved = {
        "service": "compatible",
        "settings": {
            "compatible": {
                "base_url": "http://box:9000/v1",
                "key": "k1",
                "key_for": "http://box:9000/v1",
            },
            "openai": {"key": "sk-1", "key_for": "https://api.openai.com/v1", "model": "whisper-1"},
            "mystery": {"key": "x"},
        },
    }
    chosen = stt.resolve(settings, saved)
    assert chosen.service.id == "compatible" and chosen.key == "k1" and chosen.ready
    moved = json.loads(json.dumps(saved))
    moved["settings"]["compatible"]["base_url"] = "http://elsewhere:9000/v1"
    assert stt.resolve(settings, moved).key is None  # not sent to a new address
    other = stt.resolve(settings, saved, "openai")
    assert other.key == "sk-1" and other.model == "whisper-1"
    for broken in (None, "nonsense", {"service": 42}, {"settings": []}):
        assert stt.resolve(settings, broken).service is None


def test_the_servers_own_settings_are_used_when_nothing_was_chosen(settings):
    assert not stt.resolve(settings, None).ready
    settings.stt_api_key = "sk-server"
    chosen = stt.resolve(settings, None)
    assert (
        chosen.ready and chosen.source == "env" and chosen.base_url == "https://api.openai.com/v1"
    )
    assert stt.resolve(settings, {"service": "groq"}).source == "dashboard"  # the dashboard wins


def test_the_speech_settings_never_show_a_key(client, router):
    router.add("GET", "https://api.groq.com/openai/v1/models", {"data": []})
    saved = client.put("/api/speech", json={"service": "groq", "key": "gsk_secret"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["service"] == "groq" and saved.json()["ready"] is True
    shown = client.get("/api/speech")
    assert "gsk_secret" not in shown.text
    groq = next(s for s in shown.json()["services"] if s["id"] == "groq")
    assert groq["has_key"] is True and groq["model"] == "whisper-large-v3"
    assert router.requests[-1].headers["authorization"] == "Bearer gsk_secret"  # tried first
    status = client.get("/api/status").json()["stt"]
    assert status == {"enabled": True, "name": "Groq"}

    forgot = client.delete("/api/speech/key", params={"service": "groq"})
    assert forgot.status_code == 200 and forgot.json()["ready"] is False
    assert client.put("/api/speech", json={"service": ""}).json()["service"] == ""


def test_a_wrong_key_or_address_is_not_saved(client, router):
    router.add("GET", "https://api.openai.com/v1/models", lambda r: httpx2.Response(401, json={}))
    refused = client.put("/api/speech", json={"service": "openai", "key": "sk-wrong"})
    assert refused.status_code == 422 and "refused the key" in refused.json()["detail"]
    assert client.get("/api/speech").json()["service"] == ""
    missing = client.put("/api/speech", json={"service": "deepgram"})
    assert missing.status_code == 422 and "API key" in missing.json()["detail"]
    odd = client.put("/api/speech", json={"service": "compatible", "base_url": "ftp://box"})
    assert odd.status_code == 422
    assert client.put("/api/speech", json={"service": "nope"}).status_code == 422
    assert (
        client.put("/api/speech", json={"service": "openai", "key": "two words"}).status_code == 422
    )
    assert client.delete("/api/speech/key", params={"service": "nope"}).status_code == 422


def test_an_unreachable_service_is_saved_with_a_warning(client, router):
    def unreachable(request):
        raise httpx2.ConnectError("offline")

    router.add("GET", "https://api.deepgram.com/v1/projects", unreachable)
    saved = client.put("/api/speech", json={"service": "deepgram", "key": "dg-key"})
    assert saved.status_code == 200 and "couldn't be reached" in saved.json()["warning"]
    # ElevenLabs keys can't be tried without sending sound: saved as they are.
    saved = client.put("/api/speech", json={"service": "elevenlabs", "key": "el-key"})
    assert saved.status_code == 200 and saved.json()["warning"] is None


def test_recordings_are_written_down_by_the_chosen_service_with_the_words_to_know(client, router):
    router.add("GET", "https://api.openai.com/v1/models", {"data": []})
    assert client.put("/api/speech", json={"service": "openai", "key": "sk-k"}).status_code == 200
    assert client.put("/api/prefs", json={"vocabulary": "James, Magpie"}).status_code == 200
    router.add("POST", "https://api.openai.com/v1/audio/transcriptions", {"text": "Call James."})
    files = {"file": ("note.wav", WAV, "audio/wav")}
    answer = client.post("/api/transcribe", files=files)
    assert answer.status_code == 200 and answer.json() == {"text": "Call James."}
    assert b'name="prompt"\r\n\r\nJames, Magpie' in router.requests[-1].content
    router.add("POST", "https://api.openai.com/v1/audio/transcriptions", {"text": ""})
    assert client.post("/api/transcribe", files=files).status_code == 422  # nothing heard
