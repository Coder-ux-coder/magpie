"""Shared fixtures: an isolated Magpie with a scripted brain and no network."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from magpie.app import create_app
from magpie.brain.fake import FakeBrain
from magpie.config import Settings
from magpie.core import Magpie
from magpie.schemas import PlannedReminder, Triage

# Never UTC and no daylight saving, so results don't depend on the date the tests run.
# DST itself is tested with fixed dates (test_timeutil.py).
TZ = "Australia/Brisbane"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never pick up the developer's real keys or settings."""
    import os

    for key in list(os.environ):
        if key.startswith("MAGPIE_") or key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            monkeypatch.delenv(key, raising=False)


class Router:
    """A tiny programmable stand-in for the internet (httpx2 MockTransport)."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, str, Callable[[httpx2.Request], httpx2.Response]]] = []
        self.requests: list[httpx2.Request] = []

    def add(self, method: str, prefix: str, handler) -> None:
        if not callable(handler):
            payload = handler
            handler = lambda request: httpx2.Response(200, json=payload)  # noqa: E731
        self.routes.append((method.upper(), prefix, handler))

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        url = str(request.url)
        for method, prefix, handler in reversed(self.routes):
            if request.method == method and url.startswith(prefix):
                return handler(request)
        return httpx2.Response(404, json={"ok": False, "description": f"no route for {url}"})

    def calls(self, contains: str) -> list[dict]:
        out = []
        for request in self.requests:
            if contains in str(request.url):
                try:
                    out.append(json.loads(request.content or b"{}"))
                except ValueError:
                    out.append({})
        return out


class ScriptedBrain(FakeBrain):
    """FakeBrain whose triage can be scripted per test."""

    def __init__(self) -> None:
        super().__init__()
        self.next_triage: Triage | None = None
        self.raise_on_triage: Exception | None = None
        self.contexts = []

    async def triage(self, text, ctx):
        self.contexts.append(ctx)
        if self.raise_on_triage is not None:
            self.calls.append("triage")
            raise self.raise_on_triage
        if self.next_triage is not None:
            self.calls.append("triage")
            return self.next_triage
        return await super().triage(text, ctx)


def make_triage(**overrides) -> Triage:
    data = {
        "title": "Call James about the budget",
        "text": "Call James about the budget.",
        "summary": "",
        "kind": "task",
        "tags": ["work"],
        "category": "",
        "reminders": [],
        "ping": "none",
        "ping_reason": "",
        "urls": [],
        "lookup": None,
        "briefing": "",
        "reply": "Saved.",
    }
    data.update(overrides)
    return Triage.model_validate(data)


def reminder(when: str, origin: str = "explicit", repeat: str | None = None, message="Call James"):
    return PlannedReminder(
        when=when, message=message, origin=origin, repeat=repeat, reason="Asked."
    )


@pytest.fixture
def router() -> Router:
    return Router()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        password="correct horse",
        timezone=TZ,
        brain="fake",
        base_url="https://notes.example.com",
    )


@pytest.fixture
def brain() -> ScriptedBrain:
    return ScriptedBrain()


@pytest.fixture
def core(settings: Settings, brain: ScriptedBrain, router: Router) -> Magpie:
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(router))
    return Magpie(settings, brain=brain, http=http)


@pytest.fixture
def client(core: Magpie, settings: Settings):
    app = create_app(settings, magpie=core, background=False)
    with TestClient(app, base_url="https://testserver") as test_client:
        test_client.headers["X-Magpie"] = "1"
        response = test_client.post("/api/login", json={"password": "correct horse"})
        assert response.status_code == 200
        yield test_client


def local(dt: datetime) -> datetime:
    from magpie.timeutil import to_local

    return to_local(dt, TZ)
