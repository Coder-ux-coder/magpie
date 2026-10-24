"""Codex as the AI: no key in Magpie, Codex signed in on this computer.

Codex itself is never run: a stand-in runner answers (or a small script in its place, to
check the real process handling), and records what Magpie asked.
"""

from __future__ import annotations

import json
import stat
import sys
import textwrap
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from magpie.app import create_app
from magpie.brain import BrainError, BrainUnavailable, make_brain, resolve
from magpie.brain import codex_cli as codex
from magpie.brain.base import DigestData
from magpie.config import Settings
from magpie.content import LinkContent
from magpie.core import Magpie

from .test_providers import CTX, TRIAGE, TZ


class FakeCodex:
    """Plays ``codex``: each call takes the next answer (code, text written, errors)."""

    def __init__(self, *answers: tuple[int, str, str]) -> None:
        self.answers = list(answers)
        self.calls: list[dict] = []

    def __call__(self, args, folder, stdin, seconds):
        call = {"args": args, "prompt": stdin.decode("utf-8"), "seconds": seconds}
        if folder is not None and (folder / "schema.json").exists():
            call["schema"] = json.loads((folder / "schema.json").read_text("utf-8"))
        self.calls.append(call)
        code, text, err = self.answers.pop(0) if self.answers else (0, "OK", "")
        if folder is not None and text:
            (folder / "answer.txt").write_text(text, encoding="utf-8")
        return code, "", err


def brain_with(fake: FakeCodex, tmp_path: Path, model: str = "") -> codex.CodexBrain:
    program = tmp_path / "codex"
    program.write_text("")
    saved = {"provider": "codex", "settings": {"codex": {"model": model} if model else {}}}
    config = resolve(Settings(_env_file=None), saved)
    return codex.CodexBrain(config, program=str(program), runner=fake)


def ok(answer) -> tuple[int, str, str]:
    return 0, json.dumps(answer) if not isinstance(answer, str) else answer, ""


async def test_codex_files_a_note_without_a_key(tmp_path):
    fake = FakeCodex(ok(TRIAGE))
    brain = brain_with(fake, tmp_path)
    assert brain.available and brain.label == "Codex"
    triage = await brain.triage("call james at 5 about the budget", CTX)
    assert triage.title == "Call James about the budget"
    call = fake.calls[0]
    args = call["args"]
    assert args[:2] == [str(tmp_path / "codex"), "exec"] and args[-1] == "-"
    assert "--model" not in args  # Codex's own default
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "features.shell_tool=false" in args and "--ephemeral" in args
    assert args[args.index("--output-schema") + 1] == "schema.json"
    assert set(call["schema"]["properties"]) >= {"title", "text", "reminders", "reply"}
    # The note goes on stdin, after the brief and Magpie's instructions.
    assert call["prompt"].startswith(codex.BRIEF)
    assert "call james at 5 about the budget" in call["prompt"]
    assert "call james" not in " ".join(args)
    assert brain.usage() == {"requests": 1}


async def test_a_named_model_is_passed_on_and_odd_names_are_refused(tmp_path):
    fake = FakeCodex(ok(TRIAGE))
    brain = brain_with(fake, tmp_path, model="gpt-5-codex")
    await brain.triage("call james", CTX)
    args = fake.calls[0]["args"]
    assert args[args.index("--model") + 1] == "gpt-5-codex"
    odd = brain_with(FakeCodex(), tmp_path, model='x" & calc')
    assert odd.available is False
    with pytest.raises(BrainError, match="isn't a model name"):
        await odd.triage("call james", CTX)


async def test_codex_that_is_not_installed_says_how_to_get_it(tmp_path, monkeypatch):
    monkeypatch.setattr(codex, "find_codex", lambda configured="": None)
    config = resolve(Settings(_env_file=None, brain="codex"), {})
    brain = codex.CodexBrain(config, runner=FakeCodex())
    assert brain.available is False
    with pytest.raises(BrainUnavailable, match="isn't installed.*codex login"):
        await brain.triage("call james", CTX)
    elsewhere = codex.CodexBrain(config, program=str(tmp_path / "nowhere"), runner=FakeCodex())
    with pytest.raises(BrainUnavailable, match="MAGPIE_CODEX_PATH"):
        await elsewhere.check()


@pytest.mark.parametrize(
    ("err", "error", "retryable", "words"),
    [
        ("Error: Not logged in. Run `codex login`.", BrainUnavailable, False, "codex login"),
        ("ERROR: You've hit your usage limit. Try again in 3 hours.", BrainError, True, "limit"),
        ("stream disconnected before completion: error sending request", BrainError, True, "reach"),
        ("thread 'main' panicked", BrainError, False, "couldn't answer: thread 'main' panicked"),
    ],
)
async def test_codex_problems_become_plain_messages(tmp_path, err, error, retryable, words):
    brain = brain_with(FakeCodex((1, "", err)), tmp_path)
    with pytest.raises(error, match=words) as raised:
        await brain.triage("call james", CTX)
    assert raised.value.retryable is retryable


async def test_a_slow_or_silent_codex(tmp_path):
    brain = brain_with(FakeCodex((-1, "", "")), tmp_path)
    with pytest.raises(BrainError, match="too long") as slow:
        await brain.triage("call james", CTX)
    assert slow.value.retryable
    brain = brain_with(FakeCodex((0, "", "")), tmp_path)
    with pytest.raises(BrainError, match="empty answer"):
        await brain.triage("call james", CTX)


async def test_a_refused_schema_is_asked_for_in_the_prompt_instead(tmp_path):
    refused = (1, "", "Invalid schema for response_format 'codex_output_schema'")
    fake = FakeCodex(refused, ok(TRIAGE), ok(TRIAGE))
    brain = brain_with(fake, tmp_path)
    assert (await brain.triage("call james", CTX)).title
    await brain.triage("call mum", CTX)  # stays in the prompt from then on
    assert ["--output-schema" in call["args"] for call in fake.calls] == [True, False, False]
    assert "JSON schema" in fake.calls[1]["prompt"] and "schema" not in fake.calls[1]


async def test_a_near_miss_is_repaired_once_then_reported(tmp_path):
    fake = FakeCodex(ok("Here you go: {not json"), ok(TRIAGE))
    brain = brain_with(fake, tmp_path)
    assert (await brain.triage("call james", CTX)).title
    assert "not a valid JSON object" in fake.calls[1]["prompt"]
    hopeless = brain_with(FakeCodex(ok("no"), ok("still no")), tmp_path)
    with pytest.raises(BrainError, match="expected format"):
        await hopeless.triage("call james", CTX)


async def test_digests_are_plain_text_and_what_codex_wont_do_is_said(tmp_path):
    fake = FakeCodex(ok("**Today:** call James at 5."))
    brain = brain_with(fake, tmp_path)
    digest = await brain.digest(DigestData(kind="daily", context=CTX))
    assert digest == "**Today:** call James at 5."
    assert "--output-schema" not in fake.calls[0]["args"]
    with pytest.raises(BrainError, match="PDF"):
        pdf = LinkContent(url="https://example.com/a.pdf", kind="pdf", title="a.pdf", pdf=b"%PDF")
        await brain.review(pdf, "read")
    with pytest.raises(BrainError, match="won't open it"):
        await brain.review_url("https://example.com", "read")
    assert (await brain.find_link("the budget video")).url is None
    assert await brain.list_models() == []


async def test_check_needs_codex_signed_in(tmp_path):
    fake = FakeCodex((1, "", "Not logged in"))
    brain = brain_with(fake, tmp_path)
    with pytest.raises(BrainUnavailable, match="isn't signed in"):
        await brain.check()
    assert fake.calls[0]["args"][1:] == ["login", "status"]
    fake = FakeCodex((0, "", "Logged in using ChatGPT"), ok("OK"))
    await brain_with(fake, tmp_path).check()
    assert [call["args"][1] for call in fake.calls] == ["login", "exec"]


def test_codex_needs_no_key_and_uses_its_own_model_by_default():
    config = resolve(Settings(_env_file=None, brain="codex"), {})
    assert config.provider.id == "codex" and config.model == "" and config.ready
    chosen = resolve(
        Settings(_env_file=None), {"provider": "codex", "settings": {"codex": {"model": "o4"}}}
    )
    assert chosen.model == "o4" and chosen.api_key is None
    assert type(make_brain(Settings(_env_file=None), config=config)).__name__ == "CodexBrain"


# ------------------------------------------------- the real thing, played by a script

FAKE_PROGRAM = """\
import json, os, sys
from pathlib import Path

args = sys.argv[1:]
log = Path(__file__).with_name("calls.jsonl")
if args[:2] == ["login", "status"]:
    sys.stderr.write("Logged in using ChatGPT\\n")
    sys.exit(0)
prompt = sys.stdin.read()
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"args": args, "cwd": os.getcwd(), "prompt": prompt}) + "\\n")
answer = args[args.index("--output-last-message") + 1]
Path(answer).write_text("OK" if "OK" in prompt else json.dumps(TRIAGE), encoding="utf-8")
"""


@pytest.fixture
def program(tmp_path) -> Path:
    if sys.platform == "win32":
        pytest.skip("the stand-in for Codex is a script started by its first line")
    path = tmp_path / "bin" / "codex"
    path.parent.mkdir()
    body = FAKE_PROGRAM.replace("json.dumps(TRIAGE)", f"json.dumps({TRIAGE!r})")
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def first_call(program: Path) -> tuple[dict, bool]:
    """What the stand-in heard first, and whether its working folder is still there."""
    call = json.loads(program.with_name("calls.jsonl").read_text("utf-8").splitlines()[0])
    return call, Path(call["cwd"]).exists()


async def test_codex_runs_in_an_empty_folder_with_the_note_on_stdin(program):
    config = resolve(Settings(_env_file=None, brain="codex"), {})
    brain = codex.CodexBrain(config, program=str(program))
    triage = await brain.triage("call james & mum at 5 > now", CTX)
    assert triage.title == "Call James about the budget"
    call, still_there = first_call(program)
    assert "call james & mum at 5 > now" in call["prompt"]
    assert Path(call["cwd"]).name.startswith("magpie-codex-")
    assert not still_there  # cleaned up afterwards


def test_choosing_codex_in_the_dashboard_checks_it(tmp_path, program, router):
    settings = Settings(
        _env_file=None, data_dir=tmp_path / "data", timezone=TZ, codex_path=str(program)
    )
    magpie = Magpie(settings, http=httpx2.AsyncClient(transport=httpx2.MockTransport(router)))
    app = create_app(settings, magpie=magpie, background=False)
    with TestClient(app, base_url="http://localhost") as http:
        http.headers["X-Magpie"] = "1"
        saved = http.put("/api/ai", json={"provider": "codex"})
        assert saved.status_code == 200, saved.text
        assert saved.json()["provider"] == "codex" and saved.json()["available"] is True
        status = http.get("/api/status").json()["brain"]
        assert status["name"] == "codex" and status["label"] == "Codex"
        note = http.post("/api/notes?wait=true", json={"text": "call james at 5"}).json()["note"]
        assert note["title"] == "Call James about the budget"


def test_choosing_codex_where_it_is_missing_explains_why(tmp_path, monkeypatch, router):
    monkeypatch.setattr(codex, "find_codex", lambda configured="": None)
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", timezone=TZ)
    magpie = Magpie(settings, http=httpx2.AsyncClient(transport=httpx2.MockTransport(router)))
    app = create_app(settings, magpie=magpie, background=False)
    with TestClient(app, base_url="http://localhost") as http:
        http.headers["X-Magpie"] = "1"
        refused = http.put("/api/ai", json={"provider": "codex"})
        assert refused.status_code == 422 and "isn't installed" in refused.json()["detail"]
        assert http.get("/api/ai").json()["provider"] == "claude"
