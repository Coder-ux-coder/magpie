"""Other AI providers: choosing one, keys tied to addresses, and the OpenAI-compatible brain."""

from __future__ import annotations

import json

import httpx2
import pytest
from fastapi.testclient import TestClient

from magpie.app import create_app
from magpie.brain import BrainError, BrainRefused, BrainUnavailable
from magpie.brain import openai_compat as compat
from magpie.brain.base import Context, DigestData
from magpie.brain.openai_compat import OpenAICompatibleBrain
from magpie.brain.providers import PROVIDERS, resolve
from magpie.config import Settings
from magpie.content import LinkContent
from magpie.core import Magpie
from magpie.timeutil import now_utc

from .conftest import TZ

OPENAI = "https://api.openai.com/v1/chat/completions"
TRIAGE = {
    "title": "Call James about the budget",
    "text": "Call James about the budget.",
    "summary": "",
    "kind": "task",
    "tags": ["work"],
    "category": "",
    "reminders": [
        {
            "when": "2026-10-21T17:00",
            "message": "Call James",
            "origin": "explicit",
            "repeat": None,
            "reason": "You asked for 5 PM.",
        }
    ],
    "ping": "explicit",
    "ping_reason": "You asked for 5 PM.",
    "urls": [],
    "lookup": None,
    "briefing": "",
    "reply": "Saved. I'll remind you at 5:00 PM.",
}
CTX = Context(timezone=TZ, now=now_utc())


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    monkeypatch.setattr(compat, "QUICK_RETRIES", (0.0, 0.0))


def completion(content, finish="stop", **message):
    return httpx2.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"role": "assistant", "content": content, **message},
                    "finish_reason": finish,
                }
            ],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30},
        },
    )


def make(router, provider="openai", key="sk-test", base_url=None, model=None):
    info = PROVIDERS[provider]
    saved = {"provider": provider, "settings": {provider: {}}}
    entry = saved["settings"][provider]
    if model:
        entry["model"] = model
    if base_url:
        entry["base_url"] = base_url
    config = resolve(Settings(_env_file=None), saved)
    if key:
        entry.update(key=key, key_for=config.base_url)
        config = resolve(Settings(_env_file=None), saved)
    assert config.provider is info
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(router))
    return OpenAICompatibleBrain(http, config)


# --------------------------------------------------------------- choosing


def test_claude_with_haiku_is_the_default():
    config = resolve(Settings(_env_file=None), None, "sk-ant-saved")
    assert config.provider.id == "claude" and config.model == "claude-haiku-5-5"
    assert config.api_key == "sk-ant-saved" and config.key_source == "dashboard"
    assert not config.locked


def test_a_saved_key_only_goes_to_the_address_it_was_entered_for():
    saved = {
        "provider": "compatible",
        "settings": {
            "compatible": {
                "base_url": "https://api.groq.com/openai/v1",
                "model": "llama-3.3-70b",
                "key": "gsk-secret",
                "key_for": "https://evil.example/v1",
            }
        },
    }
    config = resolve(Settings(_env_file=None), saved)
    assert config.base_url == "https://api.groq.com/openai/v1"
    assert config.api_key is None


def test_the_server_can_fix_the_provider():
    settings = Settings(_env_file=None, brain="openai", ai_api_key="sk-env", model="gpt-5")
    config = resolve(settings, {"provider": "gemini", "settings": {}})
    assert config.provider.id == "openai" and config.locked
    assert config.api_key == "sk-env" and config.key_source == "env" and config.model == "gpt-5"


def test_ollama_is_reached_on_the_host_from_docker(monkeypatch):
    from magpie.brain import providers

    saved = {"provider": "ollama", "settings": {}}
    monkeypatch.setattr(providers, "in_docker", lambda: True)
    assert resolve(Settings(_env_file=None), saved).base_url.startswith(
        "http://host.docker.internal"
    )
    monkeypatch.setattr(providers, "in_docker", lambda: False)
    config = resolve(Settings(_env_file=None), saved)
    assert config.base_url == "http://localhost:11434/v1" and config.ready  # no key needed


@pytest.mark.parametrize(
    "saved",
    [None, "nonsense", {"provider": "skynet"}, {"settings": {"openai": "x"}}, {"settings": 5}],
)
def test_damaged_choices_fall_back_to_claude(saved):
    assert resolve(Settings(_env_file=None), saved).provider.id == "claude"


def test_no_ai_needs_nothing():
    config = resolve(Settings(_env_file=None), {"provider": "basic", "settings": {}})
    assert config.provider.id == "basic" and config.ready and config.api_key is None


# ------------------------------------------------------------ the brain


async def test_triage_request_shape(router):
    router.add("POST", OPENAI, lambda request: completion(json.dumps(TRIAGE)))
    brain = make(router)
    triage = await brain.triage("uh call james at 5", CTX)
    assert triage.title == "Call James about the budget"
    request = router.requests[0]
    assert request.headers["authorization"] == "Bearer sk-test"
    body = json.loads(request.content)
    assert body["model"] == "gpt-5-mini"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert "uh call james at 5" in body["messages"][1]["content"]
    assert "in English" in body["messages"][0]["content"]
    response_format = body["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True  # OpenAI enforces it
    assert "temperature" not in body and "max_tokens" not in body
    assert brain.usage() == {"requests": 1, "input_tokens": 120, "output_tokens": 30}


async def test_wrapped_answers_are_unwrapped(router):
    wrapped = "<think>Let me see.</think>\n```json\n" + json.dumps(TRIAGE) + "\n```"
    router.add("POST", "https://generativelanguage.googleapis.com", lambda r: completion(wrapped))
    brain = make(router, provider="gemini")
    assert (await brain.triage("call james", CTX)).kind == "task"
    body = json.loads(router.requests[0].content)
    assert body["response_format"]["json_schema"]["strict"] is False
    assert "JSON schema" in body["messages"][0]["content"]  # the schema is spelled out


async def test_unsupported_response_formats_step_down_and_stay_down(router):
    seen = []

    def handler(request):
        body = json.loads(request.content)
        kind = body.get("response_format", {}).get("type")
        seen.append(kind)
        if kind:
            return httpx2.Response(
                400, json={"error": {"message": f"{kind} response_format is not supported"}}
            )
        return completion("Here you go: " + json.dumps(TRIAGE))

    router.add("POST", "http://localhost:11434/v1/chat/completions", handler)
    brain = make(router, provider="ollama", key=None, base_url="http://localhost:11434/v1")
    await brain.triage("call james", CTX)
    await brain.triage("call james again", CTX)
    assert seen == ["json_schema", "json_object", None, None]


async def test_a_near_miss_is_repaired_once(router):
    answers = iter([completion('{"title": "Call James"}'), completion(json.dumps(TRIAGE))])
    router.add("POST", OPENAI, lambda r: next(answers))
    brain = make(router)
    assert (await brain.triage("call james", CTX)).title == "Call James about the budget"
    repair = json.loads(router.requests[1].content)["messages"]
    assert repair[-2]["role"] == "assistant" and "corrected JSON" in repair[-1]["content"]


async def test_hopeless_answers_become_a_clear_error(router):
    router.add("POST", OPENAI, lambda r: completion("I can't help with that format."))
    with pytest.raises(BrainError, match="expected format"):
        await make(router).triage("call james", CTX)


async def test_a_rejected_key_makes_the_brain_unavailable(router):
    router.add(
        "POST",
        OPENAI,
        lambda r: httpx2.Response(401, json={"error": {"message": "Incorrect API key sk-test"}}),
    )
    brain = make(router)
    with pytest.raises(BrainUnavailable) as caught:
        await brain.triage("call james", CTX)
    assert "sk-test" not in str(caught.value)
    assert brain.available is False


async def test_busy_services_are_retried_then_reported(router):
    router.add("POST", OPENAI, lambda r: httpx2.Response(429, json={"error": "slow down"}))
    with pytest.raises(BrainError) as caught:
        await make(router).triage("call james", CTX)
    assert caught.value.retryable and len(router.requests) == 3


async def test_an_unreachable_ollama_says_what_to_check(router):
    def refuse(request):
        raise httpx2.ConnectError("connection refused", request=request)

    router.add("POST", "http://localhost:11434", refuse)
    brain = make(router, provider="ollama", key=None, base_url="http://localhost:11434/v1")
    with pytest.raises(BrainError, match="Is Ollama running") as caught:
        await brain.triage("call james", CTX)
    assert caught.value.retryable


async def test_slow_answers_are_not_retried_at_once(router):
    def slow(request):
        raise httpx2.ReadTimeout("timed out", request=request)

    router.add("POST", OPENAI, slow)
    with pytest.raises(BrainError, match="too long") as caught:
        await make(router).triage("call james", CTX)
    assert caught.value.retryable and len(router.requests) == 1


async def test_cut_off_and_refused_answers(router):
    router.add("POST", OPENAI, lambda r: completion('{"title": "Ca', finish="length"))
    with pytest.raises(BrainError, match="cut off"):
        await make(router).triage("call james", CTX)
    router.add("POST", OPENAI, lambda r: completion(None, refusal="I can't help with that."))
    with pytest.raises(BrainRefused):
        await make(router).triage("call james", CTX)


async def test_what_other_providers_cannot_do_is_said_plainly(router):
    brain = make(router)
    pdf = LinkContent(url="https://example.com/a.pdf", kind="pdf", title="a.pdf", pdf=b"%PDF")
    with pytest.raises(BrainError, match="PDF"):
        await brain.review(pdf, "read this")
    with pytest.raises(BrainError, match="can't open web pages"):
        await brain.review_url("https://example.com", "read this")
    assert (await brain.find_link("that essay")).url is None
    assert router.requests == []


async def test_digests_are_plain_text(router):
    router.add(
        "POST", OPENAI, lambda r: completion("<think>hmm</think>Good morning!\n• Call James")
    )
    data = DigestData(kind="daily", context=CTX)
    assert await make(router).digest(data) == "Good morning!\n• Call James"
    assert "response_format" not in json.loads(router.requests[0].content)


async def test_models_are_listed(router):
    router.add(
        "GET",
        "https://generativelanguage.googleapis.com/v1beta/openai/models",
        {"data": [{"id": "models/gemini-flash-latest"}, {"id": "models/gemini-pro-latest"}]},
    )
    models = await make(router, provider="gemini").list_models()
    assert models == ["gemini-flash-latest", "gemini-pro-latest"]


# ------------------------------------------------------------- the API


@pytest.fixture
def owner(tmp_path, router):
    """A logged-in dashboard on a fresh install: Claude by default, no key yet."""
    settings = Settings(
        _env_file=None, data_dir=tmp_path, password="pw", timezone=TZ, base_url="https://n.example"
    )
    magpie = Magpie(settings, http=httpx2.AsyncClient(transport=httpx2.MockTransport(router)))
    app = create_app(settings, magpie=magpie, background=False)
    with TestClient(app, base_url="https://testserver") as http:
        http.headers["X-Magpie"] = "1"
        assert http.post("/api/login", json={"password": "pw"}).status_code == 200
        yield http


def test_providers_are_listed_with_the_current_choice(owner):
    ai = owner.get("/api/ai").json()
    assert ai["provider"] == "claude" and ai["model"] == "claude-haiku-5-5"
    assert ai["available"] is False and ai["locked"] is False
    ids = [p["id"] for p in ai["providers"]]
    assert ids[0] == "claude" and {"openai", "gemini", "ollama", "basic", "compatible"} <= set(ids)


def test_switching_to_openai_checks_the_key_and_files_waiting_notes(owner, router):
    router.add("POST", OPENAI, lambda r: completion(json.dumps(TRIAGE)))
    note = owner.post("/api/notes", json={"text": "call james at 5"}).json()["note"]
    assert note["state"] == "pending"
    saved = owner.put("/api/ai", json={"provider": "openai", "key": "sk-openai-good"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["available"] is True and "sk-openai-good" not in saved.text
    status = owner.get("/api/status").json()["brain"]
    assert status["name"] == "openai" and status["label"] == "OpenAI"
    assert status["model"] == "gpt-5-mini" and status["key_source"] == "dashboard"


def test_a_refused_key_changes_nothing(owner, router):
    router.add("POST", OPENAI, lambda r: httpx2.Response(401, json={"error": "bad key"}))
    refused = owner.put("/api/ai", json={"provider": "openai", "key": "sk-openai-bad"})
    assert refused.status_code == 422 and "rejected" in refused.json()["detail"]
    assert owner.get("/api/ai").json()["provider"] == "claude"


def test_an_unreachable_service_is_saved_with_a_warning(owner, router):
    def refuse(request):
        raise httpx2.ConnectError("connection refused", request=request)

    router.add("POST", "http://", refuse)
    saved = owner.put(
        "/api/ai", json={"provider": "ollama", "base_url": "http://192.168.1.20:11434/v1"}
    ).json()
    assert saved["provider"] == "ollama" and "Is Ollama running" in saved["warning"]


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        ({"provider": "compatible", "model": "x"}, "address"),
        ({"provider": "compatible", "base_url": "https://api.groq.com/openai/v1"}, "model"),
        ({"provider": "openai"}, "API key"),
        ({"provider": "openai", "key": "sk two words"}, "without spaces"),
        ({"provider": "compatible", "base_url": "ftp://x"}, "http"),
        ({"provider": "skynet"}, "Unknown"),
    ],
)
def test_choices_that_cannot_work_are_explained(owner, body, problem):
    response = owner.put("/api/ai", json=body)
    assert response.status_code == 422 and problem in response.json()["detail"]


def test_a_new_address_forgets_the_old_key(owner, router):
    router.add("POST", "https://", lambda r: completion("OK"))
    first = {
        "provider": "compatible",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b",
        "key": "gsk-secret",
    }
    assert owner.put("/api/ai", json=first).status_code == 200
    moved = owner.put(
        "/api/ai", json={"provider": "compatible", "base_url": "https://other.example/v1"}
    ).json()
    entry = next(p for p in moved["providers"] if p["id"] == "compatible")
    assert entry["has_key"] is False
    assert all(
        "other.example" not in str(r.url) or "authorization" not in r.headers
        for r in router.requests
    )


def test_models_can_be_listed_before_saving(owner, router):
    router.add("GET", "https://api.openai.com/v1/models", {"data": [{"id": "gpt-5-mini"}]})
    found = owner.post("/api/ai/models", json={"provider": "openai", "key": "sk-new"})
    assert found.json() == {"models": ["gpt-5-mini"]}
    assert router.requests[-1].headers["authorization"] == "Bearer sk-new"


def test_keys_can_be_removed(owner, router):
    router.add("POST", OPENAI, lambda r: completion("OK"))
    owner.put("/api/ai", json={"provider": "openai", "key": "sk-openai-good"})
    after = owner.delete("/api/ai/key", params={"provider": "openai"}).json()
    assert after["available"] is False
    assert next(p for p in after["providers"] if p["id"] == "openai")["has_key"] is False


def test_no_ai_files_notes_with_simple_rules(owner):
    assert owner.put("/api/ai", json={"provider": "basic"}).json()["available"] is True
    note = owner.post("/api/notes?wait=true", json={"text": "remind me at 5 pm to stretch"})
    assert note.json()["note"]["reminders"], note.text


def test_a_provider_fixed_by_the_server_cannot_be_switched(tmp_path, router):
    settings = Settings(_env_file=None, data_dir=tmp_path, password="pw", brain="openai")
    magpie = Magpie(settings, http=httpx2.AsyncClient(transport=httpx2.MockTransport(router)))
    with TestClient(create_app(settings, magpie=magpie, background=False)) as http:
        http.headers["X-Magpie"] = "1"
        http.post("/api/login", json={"password": "pw"})
        refused = http.put("/api/ai", json={"provider": "claude"})
        assert refused.status_code == 409 and "MAGPIE_BRAIN" in refused.json()["detail"]
        assert [p["id"] for p in http.get("/api/ai").json()["providers"]] == ["openai"]
