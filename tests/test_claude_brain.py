"""Contract tests for ClaudeBrain: the exact requests it sends and how it handles answers.

They use a stand-in for the Anthropic client, so they run offline and for free.
"""

import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from magpie.brain import BrainError, BrainRefused, BrainUnavailable, Context
from magpie.brain.claude import FALLBACK_BETA, ClaudeBrain
from magpie.config import Settings
from magpie.content import LinkContent
from magpie.timeutil import now_utc

TRIAGE = {
    "title": "Call Ahmed",
    "text": "Call Ahmed at 5 PM.",
    "summary": "",
    "kind": "task",
    "tags": ["Work", "#work", "calls"],
    "reminders": [
        {
            "when": "2026-10-19T17:00",
            "message": "Call Ahmed",
            "origin": "explicit",
            "repeat": None,
            "reason": "You asked.",
        }
    ],
    "ping": "explicit",
    "ping_reason": "You asked for 5 PM.",
    "urls": [],
    "lookup": None,
    "briefing": "",
    "reply": "Saved. I'll remind you at 5:00 PM.",
}
REVIEW = {
    "title": "Essay",
    "tldr": "Do unscalable things early.",
    "key_points": ["Recruit users by hand"],
    "verdict": "worth_it",
    "rating": 4,
    "best_for": "Founders",
    "caveats": "",
    "why_saved": "You wanted startup advice.",
    "minutes": 12,
}


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def response(*blocks, stop="end_turn"):
    usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=20,
        cache_read_input_tokens=50,
        cache_creation_input_tokens=0,
    )
    return SimpleNamespace(stop_reason=stop, content=list(blocks), usage=usage)


def api_error(cls, status, message):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(message, response=httpx2.Response(status, request=request), body=None)


class FakeClient:
    def __init__(self, *responses):
        self.queue = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def brain_with(*responses, model="claude-opus-5-5", **settings):
    client = FakeClient(*responses)
    return ClaudeBrain(Settings(_env_file=None, model=model, **settings), client=client), client


CTX = Context(
    timezone="Asia/Karachi", now=now_utc(), proactivity="balanced", about_me="I'm in Lahore."
)


async def test_triage_request_shape():
    brain, client = brain_with(response(text_block(json.dumps(TRIAGE))))
    triage = await brain.triage("uh call ahmed at 5", CTX)
    assert triage.tags == ["work", "calls"]  # cleaned
    assert triage.reminders[0].when == "2026-10-19T17:00"

    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["max_tokens"] >= 8000
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "notes app" in call["system"][0]["text"]
    content = call["messages"][0]["content"]
    assert "<note>\nuh call ahmed at 5\n</note>" in content
    assert "Asia/Karachi" in content and "I'm in Lahore." in content
    config = call["output_config"]
    assert config["effort"] == "low"
    assert config["format"]["type"] == "json_schema"
    schema = config["format"]["schema"]
    assert schema["additionalProperties"] is False
    assert "reminders" in schema["required"]
    # Opus 5.5 rules: no forced tool choice, no disabled thinking, no budgets.
    assert "tool_choice" not in call and "thinking" not in call
    assert call["betas"] == [FALLBACK_BETA]
    assert call["fallbacks"] == "default"
    assert brain.usage()["requests"] == 1
    assert brain.usage()["cache_read_input_tokens"] == 50


async def test_effort_override_and_models_without_fallbacks():
    brain, client = brain_with(
        response(text_block(json.dumps(TRIAGE))), model="claude-haiku-5-5", effort="high"
    )
    await brain.triage("x", CTX)
    call = client.calls[0]
    assert call["output_config"]["effort"] == "high"
    assert "fallbacks" not in call and "betas" not in call


async def test_fallbacks_are_dropped_if_the_api_rejects_them():
    rejected = api_error(
        anthropic.BadRequestError, 400, "fallbacks: not available for this organization"
    )
    brain, client = brain_with(
        rejected, response(text_block(json.dumps(TRIAGE))), response(text_block(json.dumps(TRIAGE)))
    )
    await brain.triage("x", CTX)
    await brain.triage("y", CTX)
    assert "fallbacks" in client.calls[0]
    assert "fallbacks" not in client.calls[1] and "fallbacks" not in client.calls[2]


async def test_effort_is_dropped_for_models_that_reject_it():
    rejected = api_error(
        anthropic.BadRequestError, 400, "output_config.effort is not supported on this model"
    )
    brain, client = brain_with(
        rejected, response(text_block(json.dumps(TRIAGE))), model="claude-haiku-4-5"
    )
    await brain.triage("x", CTX)
    assert "effort" not in client.calls[1]["output_config"]
    assert client.calls[1]["output_config"]["format"]["type"] == "json_schema"


async def test_refusals_and_truncation():
    brain, _ = brain_with(response(stop="refusal"))
    with pytest.raises(BrainRefused):
        await brain.triage("x", CTX)
    brain, _ = brain_with(response(text_block('{"title": "cut'), stop="max_tokens"))
    with pytest.raises(BrainError, match="cut off"):
        await brain.triage("x", CTX)


async def test_errors_are_classified():
    brain, _ = brain_with(api_error(anthropic.AuthenticationError, 401, "invalid x-api-key"))
    with pytest.raises(BrainUnavailable):
        await brain.triage("x", CTX)
    assert brain.available is False

    brain, _ = brain_with(api_error(anthropic.RateLimitError, 429, "slow down"))
    with pytest.raises(BrainError) as caught:
        await brain.triage("x", CTX)
    assert caught.value.retryable

    brain, _ = brain_with(api_error(anthropic.InternalServerError, 529, "overloaded"))
    with pytest.raises(BrainError) as caught:
        await brain.triage("x", CTX)
    assert caught.value.retryable

    brain, _ = brain_with(api_error(anthropic.BadRequestError, 400, "messages: bad"))
    with pytest.raises(BrainError) as caught:
        await brain.triage("x", CTX)
    assert not caught.value.retryable


async def test_missing_credentials(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    brain = ClaudeBrain(Settings(_env_file=None))
    assert brain.available is False
    with pytest.raises(BrainUnavailable):
        await brain.triage("x", CTX)


async def test_find_link_uses_web_search_and_resumes_paused_turns():
    paused = response(
        SimpleNamespace(type="server_tool_use", id="s1", name="web_search", input={}),
        stop="pause_turn",
    )
    found = response(
        text_block("Searching..."),
        text_block(
            json.dumps(
                {
                    "url": "https://paulgraham.com/ds.html",
                    "title": "Do Things That Don't Scale",
                    "confidence": "high",
                }
            )
        ),
    )
    brain, client = brain_with(paused, found)
    result = await brain.find_link("Paul Graham do things that don't scale")
    assert result.url == "https://paulgraham.com/ds.html"
    assert client.calls[0]["tools"] == [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}
    ]
    second = client.calls[1]["messages"]
    assert second[-1]["role"] == "assistant"  # the paused turn is sent back to resume


async def test_older_models_get_basic_web_tools():
    found = response(text_block(json.dumps({"url": None, "title": "", "confidence": "low"})))
    brain, client = brain_with(found, model="claude-haiku-4-5")
    await brain.find_link("q")
    assert client.calls[0]["tools"][0]["type"] == "web_search_20250305"


async def test_review_sends_pdfs_as_documents():
    brain, client = brain_with(response(text_block(json.dumps(REVIEW))))
    content = LinkContent(
        url="https://example.com/a.pdf", kind="pdf", title="a.pdf", pdf=b"%PDF-1.4"
    )
    review = await brain.review(content, "read this paper")
    assert review.verdict == "worth_it"
    blocks = client.calls[0]["messages"][0]["content"]
    assert blocks[0]["type"] == "document"
    assert blocks[0]["source"]["media_type"] == "application/pdf"
    assert blocks[1]["type"] == "text" and "read this paper" in blocks[1]["text"]
    assert client.calls[0]["output_config"]["effort"] == "medium"


async def test_review_url_uses_web_fetch():
    brain, client = brain_with(response(text_block(json.dumps(REVIEW))))
    await brain.review_url("https://example.com/post", "interesting")
    tool = client.calls[0]["tools"][0]
    assert tool["type"] == "web_fetch_20260209" and tool["name"] == "web_fetch"
    assert "https://example.com/post" in client.calls[0]["messages"][0]["content"]


async def test_schema_conflicts_with_tools_fall_back_to_prompted_json():
    conflict = api_error(
        anthropic.BadRequestError, 400, "output_config.format cannot be combined with web_fetch"
    )
    prose = text_block("Here it is:\n```json\n" + json.dumps(REVIEW) + "\n```")
    brain, client = brain_with(conflict, response(prose))
    review = await brain.review_url("https://example.com/post", "interesting")
    assert review.title == "Essay"
    retry = client.calls[1]
    assert "format" not in retry.get("output_config", {})
    assert "JSON schema" in retry["system"][0]["text"]


async def test_digest_returns_plain_text():
    from magpie.brain import DigestData

    brain, client = brain_with(response(text_block("Good morning!\n• Call Ahmed at 5 PM")))
    text = await brain.digest(DigestData(kind="daily", context=CTX))
    assert text.startswith("Good morning!")
    assert "format" not in client.calls[0]["output_config"]


async def test_ask_formats_notes():
    from magpie.models import Note

    note = Note(
        id=12,
        created_at=now_utc(),
        updated_at=now_utc(),
        source="web",
        raw_text="Rent goes up in May",
        title="Rent",
        tags=["money"],
    )
    brain, client = brain_with(
        response(text_block(json.dumps({"answer": "It rises in May (#12).", "note_ids": [12]})))
    )
    answer = await brain.ask("when does rent go up?", [note], CTX)
    assert answer.note_ids == [12]
    content = client.calls[0]["messages"][0]["content"]
    assert "#12 [note] Rent" in content and "Question: when does rent go up?" in content
