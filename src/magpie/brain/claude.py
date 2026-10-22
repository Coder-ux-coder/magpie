"""The Claude-backed brain.

Every call uses structured outputs (``output_config.format``) with a JSON
schema generated from the Pydantic models in ``magpie.schemas``. Requests go
through the beta Messages endpoint so server-side refusal fallbacks
(``fallbacks="default"``) can be enabled on the models that support them.
"""

from __future__ import annotations

import base64
import logging
import os
from collections import Counter
from functools import cache
from typing import Any, TypeVar

import anthropic
from anthropic import AsyncAnthropic
from pydantic import BaseModel, ValidationError

from magpie.brain import prompts
from magpie.brain.base import (
    BrainError,
    BrainRefused,
    BrainUnavailable,
    Context,
    DigestData,
)
from magpie.config import Settings
from magpie.content import LinkContent
from magpie.models import Note
from magpie.schemas import Answer, FoundLink, LinkReview, Triage

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that accept fallbacks="default" (Claude API).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
# Models that support the dynamic-filtering web tools.
DYNAMIC_WEB_TOOL_MODELS = {
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-5-5",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-fable-5",
    "claude-fable-5-1",
}
DEFAULT_EFFORT = {
    "triage": "low",
    "review": "medium",
    "find": "low",
    "digest": "low",
    "weekly": "medium",
    "ask": "medium",
}
MAX_TOKENS = {
    "triage": 8_000,
    "review": 16_000,
    "find": 8_000,
    "digest": 8_000,
    "weekly": 16_000,
    "ask": 16_000,
}
MAX_CONTINUATIONS = 4
# Seconds per request. The SDK default (10 minutes) would hold a pipeline slot
# for that long when a connection stalls; reviews that fetch pages need longer.
TIMEOUTS = {
    "triage": 120,
    "review": 300,
    "find": 240,
    "digest": 180,
    "weekly": 240,
    "ask": 180,
}


@cache
def _schema(model: type[BaseModel]) -> dict[str, Any]:
    return anthropic.transform_schema(model.model_json_schema())


def _is_format_conflict(exc: BrainError) -> bool:
    cause = exc.__cause__
    if not isinstance(cause, anthropic.BadRequestError):
        return False
    message = str(cause).lower()
    return "output_config" in message or "json_schema" in message or "format" in message


def _extract_json(text: str) -> str | None:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    return text[start : end + 1]


class ClaudeBrain:
    name = "claude"
    label = "Claude"

    def __init__(
        self,
        settings: Settings,
        client: AsyncAnthropic | None = None,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self.settings = settings
        self.model = model or settings.model
        # The environment wins over a key saved from the dashboard.
        key = settings.anthropic_api_key or api_key
        has_credentials = bool(
            key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        )
        self._client = client
        if client is None and has_credentials:
            self._client = (
                AsyncAnthropic(api_key=key, max_retries=3) if key else AsyncAnthropic(max_retries=3)
            )
        self._invalid_key = False
        self._fallbacks = settings.fallbacks and self.model in FALLBACK_MODELS
        self._send_effort = True
        self._usage: Counter[str] = Counter()

    @property
    def available(self) -> bool:
        return self._client is not None and not self._invalid_key

    def usage(self) -> dict[str, int]:
        return dict(self._usage)

    async def check(self) -> None:
        """Make sure the key works and can use the model (a free API call)."""
        if self._client is None:
            raise BrainUnavailable("No API key.")
        try:
            await self._client.models.retrieve(self.model, timeout=20)
        except anthropic.AuthenticationError as exc:
            raise BrainUnavailable("Anthropic rejected that API key.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise BrainError(f"That key isn't allowed to use {self.model}.") from exc
        except anthropic.NotFoundError as exc:
            raise BrainError(
                f"That key works, but the model {self.model} isn't available to it. "
                "Choose another in Settings → AI."
            ) from exc
        except anthropic.APIError as exc:
            raise BrainError("Couldn't reach Anthropic to check the key.", retryable=True) from exc

    async def list_models(self) -> list[str]:
        if self._client is None:
            raise BrainUnavailable("No API key.")
        try:
            page = await self._client.models.list(limit=100, timeout=20)
        except anthropic.AuthenticationError as exc:
            raise BrainUnavailable("Anthropic rejected that API key.") from exc
        except anthropic.APIError as exc:
            raise BrainError(
                "Couldn't reach Anthropic to list the models.", retryable=True
            ) from exc
        return sorted({model.id for model in page.data if isinstance(model.id, str)})

    # ------------------------------------------------------------ public API

    async def triage(self, text: str, ctx: Context) -> Triage:
        return await self._structured(
            "triage",
            Triage,
            system=prompts.TRIAGE_SYSTEM,
            content=prompts.triage_message(text, ctx),
        )

    async def review(self, content: LinkContent, note_text: str) -> LinkReview:
        blocks: list[dict[str, Any]] = []
        if content.pdf:
            blocks.append(
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.standard_b64encode(content.pdf).decode("ascii"),
                    },
                    "title": content.title or "Saved document",
                }
            )
        blocks.append({"type": "text", "text": prompts.review_message(content, note_text)})
        return await self._structured(
            "review", LinkReview, system=prompts.REVIEW_SYSTEM, content=blocks
        )

    async def review_url(self, url: str, note_text: str) -> LinkReview:
        return await self._structured(
            "review",
            LinkReview,
            system=prompts.REVIEW_SYSTEM + prompts.REVIEW_FETCH_ADDENDUM,
            content=prompts.review_url_message(url, note_text),
            tools=[self._web_tool("web_fetch", max_uses=2, max_content_tokens=100_000)],
        )

    async def find_link(self, query: str) -> FoundLink:
        return await self._structured(
            "find",
            FoundLink,
            system=prompts.FIND_SYSTEM,
            content=prompts.find_message(query),
            tools=[self._web_tool("web_search", max_uses=3)],
        )

    async def digest(self, data: DigestData) -> str:
        task = "weekly" if data.kind == "weekly" else "digest"
        system = prompts.WEEKLY_SYSTEM if data.kind == "weekly" else prompts.DIGEST_SYSTEM
        return await self._text(task, system=system, content=prompts.digest_message(data))

    async def ask(self, question: str, notes: list[Note], ctx: Context) -> Answer:
        return await self._structured(
            "ask",
            Answer,
            system=prompts.ASK_SYSTEM,
            content=prompts.ask_message(question, notes, ctx),
        )

    # -------------------------------------------------------------- plumbing

    def _web_tool(self, name: str, **options: Any) -> dict[str, Any]:
        if self.model in DYNAMIC_WEB_TOOL_MODELS:
            version = "web_search_20260209" if name == "web_search" else "web_fetch_20260209"
        else:
            version = "web_search_20250305" if name == "web_search" else "web_fetch_20250910"
        return {"type": version, "name": name, **options}

    def _output_config(self, task: str, schema: type[BaseModel] | None) -> dict[str, Any]:
        config: dict[str, Any] = {}
        if self._send_effort:
            config["effort"] = self.settings.effort or DEFAULT_EFFORT[task]
        if schema is not None:
            config["format"] = {"type": "json_schema", "schema": _schema(schema)}
        return config

    async def _create(self, task: str, schema: type[BaseModel] | None, **params: Any) -> Any:
        if not self.available:
            raise BrainUnavailable("Claude is not configured: set ANTHROPIC_API_KEY.")
        assert self._client is not None
        while True:
            kwargs = dict(params)
            output_config = self._output_config(task, schema)
            if output_config:
                kwargs["output_config"] = output_config
            if self._fallbacks:
                kwargs["betas"] = [FALLBACK_BETA]
                kwargs["fallbacks"] = "default"
            kwargs.setdefault("timeout", TIMEOUTS.get(task, 180))
            try:
                response = await self._client.beta.messages.create(**kwargs)
            except anthropic.BadRequestError as exc:
                message = str(exc).lower()
                if self._fallbacks and "fallback" in message:
                    log.warning("Refusal fallbacks unavailable, continuing without: %s", exc)
                    self._fallbacks = False
                    continue
                if self._send_effort and "effort" in message:
                    log.warning("Model %s rejected the effort setting; omitting it", self.model)
                    self._send_effort = False
                    continue
                raise BrainError(f"Claude rejected the request: {exc}") from exc
            except anthropic.AuthenticationError as exc:
                self._invalid_key = True
                raise BrainUnavailable("The Anthropic API key was rejected.") from exc
            except anthropic.PermissionDeniedError as exc:
                raise BrainError(f"The API key cannot use {self.model}: {exc}") from exc
            except anthropic.NotFoundError as exc:
                raise BrainError(f"Unknown model {self.model!r}: {exc}") from exc
            except anthropic.RateLimitError as exc:
                raise BrainError("Claude is rate limited; will retry.", retryable=True) from exc
            except anthropic.APIStatusError as exc:
                retryable = exc.status_code >= 500 or exc.status_code in (408, 409, 429)
                raise BrainError(
                    f"Claude API error {exc.status_code}", retryable=retryable
                ) from exc
            except anthropic.APIConnectionError as exc:
                raise BrainError("Could not reach the Claude API.", retryable=True) from exc
            except anthropic.APIError as exc:  # e.g. a response the SDK couldn't read
                raise BrainError(f"Unexpected answer from the Claude API: {exc}") from exc
            self._record_usage(response)
            return response

    async def _run(self, task: str, schema: type[BaseModel] | None, **params: Any) -> Any:
        """Create a message, resuming server-tool turns that pause, and check the outcome."""
        response = await self._create(task, schema, **params)
        messages = list(params["messages"])
        for _ in range(MAX_CONTINUATIONS):
            if response.stop_reason != "pause_turn":
                break
            messages = [*messages, {"role": "assistant", "content": response.content}]
            response = await self._create(task, schema, **{**params, "messages": messages})
        if response.stop_reason == "refusal":
            raise BrainRefused("Claude declined to process this.")
        if response.stop_reason in ("max_tokens", "model_context_window_exceeded"):
            raise BrainError("Claude's answer was cut off before it finished.")
        if response.stop_reason == "pause_turn":
            raise BrainError("Claude's web lookup did not finish.", retryable=True)
        return response

    async def _structured(
        self,
        task: str,
        schema: type[T],
        *,
        system: str,
        content: str | list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> T:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS[task],
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": content}],
        }
        if tools:
            params["tools"] = tools
        try:
            response = await self._run(task, schema, **params)
        except BrainError as exc:
            if not (tools and _is_format_conflict(exc)):
                raise
            # Some deployments may not combine server tools with a response
            # schema; ask for JSON in the prompt instead and parse it leniently.
            log.warning("Retrying %s without output_config.format: %s", task, exc)
            instruction = (
                "\n\nReply with only a JSON object (no prose, no code fences) that matches this "
                f"JSON schema:\n{_schema(schema)}"
            )
            params["system"] = [{"type": "text", "text": system + instruction}]
            response = await self._run(task, None, **params)
        texts = [
            block.text
            for block in response.content or []
            if getattr(block, "type", None) == "text" and getattr(block, "text", None)
        ]
        # The structured answer is the final text block; earlier text (if any)
        # can be commentary around server-tool calls.
        for text in reversed(texts):
            for candidate in (text, _extract_json(text)):
                if not candidate:
                    continue
                try:
                    return schema.model_validate_json(candidate)
                except ValidationError:
                    continue
        raise BrainError("Claude's answer was not in the expected format.")

    async def _text(self, task: str, *, system: str, content: str) -> str:
        response = await self._run(
            task,
            None,
            model=self.model,
            max_tokens=MAX_TOKENS[task],
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
        )
        text = "\n".join(
            block.text
            for block in response.content or []
            if getattr(block, "type", None) == "text" and getattr(block, "text", None)
        ).strip()
        if not text:
            raise BrainError("Claude returned an empty answer.")
        return text

    def _record_usage(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        self._usage["requests"] += 1
        for field in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            value = getattr(usage, field, None)
            if isinstance(value, int):
                self._usage[field] += value
