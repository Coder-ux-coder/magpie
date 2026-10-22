"""A brain for any service with an OpenAI-compatible chat API.

OpenAI, Google Gemini, OpenRouter, Ollama and many others accept the same
``POST /chat/completions`` request. They differ in how well they follow a JSON
schema, so structured answers step down until one works: a JSON schema
response format, then plain JSON mode, then JSON asked for in the prompt. Every
answer is parsed leniently (code fences, ``<think>`` blocks) and validated; one
repair round fixes most near misses.

Claude-only features degrade with a clear message: web search finds nothing,
and pages Magpie couldn't download and PDFs can't be reviewed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from functools import cache
from typing import Any, TypeVar

import anthropic
import httpx2
from pydantic import BaseModel, ValidationError

from magpie.brain import prompts
from magpie.brain.base import BrainError, BrainRefused, BrainUnavailable, Context, DigestData
from magpie.brain.claude import TIMEOUTS
from magpie.brain.providers import AIConfig
from magpie.content import LinkContent
from magpie.models import Note
from magpie.schemas import Answer, FoundLink, LinkReview, Triage

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

MODES = ("json_schema", "json_object", "prompt")
FORMAT_WORDS = ("response_format", "json_schema", "json_object", "schema")
QUICK_RETRIES = (1.0, 3.0)
THINK = re.compile(r"<think>.*?</think>|^.*?</think>", re.DOTALL | re.IGNORECASE)
FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


@cache
def _schema(model: type[BaseModel]) -> dict[str, Any]:
    """The schema with every field required and no extra keys (what strict modes expect)."""
    return _drop_titles(anthropic.transform_schema(model.model_json_schema()))


def _drop_titles(node: Any) -> Any:
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "title" and isinstance(value, str):
                continue  # a schema label, not a property called "title"
            if key == "properties" and isinstance(value, dict):
                out[key] = {name: _drop_titles(prop) for name, prop in value.items()}
            else:
                out[key] = _drop_titles(value)
        return out
    if isinstance(node, list):
        return [_drop_titles(item) for item in node]
    return node


def _json_instruction(schema: type[BaseModel]) -> str:
    return (
        "\n\nReply with only one JSON object, with no other text and no code fences. It must "
        f"match this JSON schema:\n{json.dumps(_schema(schema), separators=(',', ':'))}"
    )


def _parse(text: str) -> Any:
    """The JSON object in an answer, tolerating the usual wrappers."""
    text = THINK.sub("", text).strip()
    fenced = FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def _message_text(message: Any) -> str:
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # some services return content parts
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def _error_text(response: httpx2.Response) -> str:
    """The service's own explanation of an error, whatever shape it comes in."""
    try:
        data = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(data, list) and data:
        data = data[0]  # Gemini wraps errors in a list
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        error = error.get("message") or error
    return str(error or data)[:300]


class _FormatUnsupported(Exception):
    """The service refused the response_format we asked for."""


class OpenAICompatibleBrain:
    def __init__(self, http: httpx2.AsyncClient, config: AIConfig) -> None:
        self.http = http
        self.config = config
        self.name = config.provider.id
        self.label = config.provider.name
        self.model = config.model
        self.base_url = config.base_url.rstrip("/")
        self._key = config.api_key
        self._invalid_key = False
        # OpenAI enforces strict schemas; elsewhere "strict" is ignored or refused.
        self._mode = "json_schema"
        self._usage: Counter[str] = Counter()

    @property
    def available(self) -> bool:
        return self.config.ready and not self._invalid_key

    def usage(self) -> dict[str, int]:
        return dict(self._usage)

    async def check(self) -> None:
        """A tiny request that proves the address, the key and the model all work."""
        await self._chat(
            "triage",
            {
                "model": self.model,
                "messages": [{"role": "user", "content": "Reply with the word OK."}],
            },
            seconds=60,
        )

    async def list_models(self) -> list[str]:
        try:
            response = await self.http.get(
                f"{self.base_url}/models", headers=self._headers(), timeout=20
            )
        except httpx2.HTTPError as exc:
            raise BrainError(self._unreachable(), retryable=True) from exc
        if response.status_code >= 400:
            raise self._status_error(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise BrainError(f"{self.label} sent a list of models Magpie couldn't read.") from exc
        items = data.get("data") if isinstance(data, dict) else data
        if not isinstance(items, list):
            items = data.get("models", []) if isinstance(data, dict) else []
        ids = []
        for item in items:
            name = item.get("id") or item.get("name") if isinstance(item, dict) else item
            if isinstance(name, str) and name:
                ids.append(name.removeprefix("models/"))
        return sorted(set(ids))[:500]

    # ------------------------------------------------------------ public API

    async def triage(self, text: str, ctx: Context) -> Triage:
        return await self._structured(
            "triage", Triage, prompts.TRIAGE_SYSTEM, prompts.triage_message(text, ctx)
        )

    async def review(self, content: LinkContent, note_text: str) -> LinkReview:
        if content.pdf and not content.text.strip():
            raise BrainError(f"{self.label} can't read PDF files in Magpie; Claude can.")
        return await self._structured(
            "review", LinkReview, prompts.REVIEW_SYSTEM, prompts.review_message(content, note_text)
        )

    async def review_url(self, url: str, note_text: str) -> LinkReview:
        raise BrainError(
            f"Magpie couldn't download this page, and {self.label} can't open web pages itself."
        )

    async def find_link(self, query: str) -> FoundLink:
        log.info("%s can't search the web; skipping the lookup for %r", self.label, query)
        return FoundLink(url=None, title="", confidence="low")

    async def digest(self, data: DigestData) -> str:
        task = "weekly" if data.kind == "weekly" else "digest"
        system = prompts.WEEKLY_SYSTEM if data.kind == "weekly" else prompts.DIGEST_SYSTEM
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompts.digest_message(data)},
            ],
        }
        text = THINK.sub("", await self._chat(task, body)).strip()
        if not text:
            raise BrainError(f"{self.label} returned an empty answer.")
        return text

    async def ask(self, question: str, notes: list[Note], ctx: Context) -> Answer:
        return await self._structured(
            "ask", Answer, prompts.ASK_SYSTEM, prompts.ask_message(question, notes, ctx)
        )

    # -------------------------------------------------------------- plumbing

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        if self.name == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/Coder-ux-coder/magpie"
            headers["X-Title"] = "Magpie"
        return headers

    def _unreachable(self) -> str:
        message = f"Couldn't reach {self.label} at {self.base_url}."
        if self.name == "ollama":
            message += " Is Ollama running?"
        return message

    def _status_error(self, response: httpx2.Response) -> BrainError:
        status = response.status_code
        detail = _error_text(response)
        if self._key:
            detail = detail.replace(self._key, "…")
        if status == 401:
            self._invalid_key = True
            return BrainUnavailable(f"{self.label} rejected the API key.")
        if status == 403:
            return BrainError(f"{self.label} refused the request: {detail}")
        if status == 404:
            return BrainError(
                f"{self.label} doesn't know the model {self.model!r}, or the address is wrong "
                f"({detail}). Pick another model in Settings → AI."
            )
        if status in (408, 409, 429) or status >= 500:
            return BrainError(f"{self.label} is busy or unavailable ({status}).", retryable=True)
        return BrainError(f"{self.label} rejected the request ({status}): {detail}")

    async def _post(self, body: dict[str, Any], seconds: float) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        delays = iter(QUICK_RETRIES)
        while True:
            try:
                response = await self.http.post(
                    url, json=body, headers=self._headers(), timeout=seconds
                )
            except httpx2.TimeoutException as exc:
                # Not retried here: a slow local model would just be slow again.
                raise BrainError(f"{self.label} took too long to answer.", retryable=True) from exc
            except httpx2.HTTPError as exc:
                delay = next(delays, None)
                if delay is None:
                    raise BrainError(self._unreachable(), retryable=True) from exc
                await asyncio.sleep(delay)
                continue
            if response.status_code < 400:
                try:
                    data = response.json()
                except ValueError:
                    data = None
                if not isinstance(data, dict):
                    raise BrainError(f"{self.label} sent an answer Magpie couldn't read.")
                return data
            if response.status_code in (400, 422) and "response_format" in body:
                detail = _error_text(response).lower()
                if any(word in detail for word in FORMAT_WORDS):
                    raise _FormatUnsupported(detail)
            error = self._status_error(response)
            delay = next(delays, None) if error.retryable else None
            if delay is None:
                raise error
            await asyncio.sleep(delay)

    async def _chat(self, task: str, body: dict[str, Any], seconds: float | None = None) -> str:
        if not self.available:
            if self._invalid_key:
                raise BrainUnavailable(f"{self.label} rejected the API key.")
            raise BrainUnavailable(f"{self.label} isn't set up yet (Settings → AI).")
        data = await self._post(body, seconds or TIMEOUTS.get(task, 180))
        self._record_usage(data)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise BrainError(f"{self.label} sent an answer with no text.")
        choice = choices[0]
        message = choice.get("message") or {}
        if isinstance(message, dict) and message.get("refusal"):
            raise BrainRefused(f"{self.label} declined to process this.")
        reason = choice.get("finish_reason")
        if reason == "content_filter":
            raise BrainRefused(f"{self.label} declined to process this.")
        text = _message_text(message)
        if reason == "length" and not text.strip().endswith("}"):
            raise BrainError(f"{self.label}'s answer was cut off before it finished.")
        return text

    async def _structured(self, task: str, schema: type[T], system: str, content: str) -> T:
        while True:
            mode = self._mode
            strict = mode == "json_schema" and self.name == "openai"
            body: dict[str, Any] = {
                "model": self.model,
                "messages": [
                    # Strict schemas are enforced by the service; elsewhere the schema
                    # in the prompt is what makes most models answer in shape.
                    {
                        "role": "system",
                        "content": system + ("" if strict else _json_instruction(schema)),
                    },
                    {"role": "user", "content": content},
                ],
            }
            if mode == "json_schema":
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema.__name__,
                        "schema": _schema(schema),
                        "strict": strict,
                    },
                }
            elif mode == "json_object":
                body["response_format"] = {"type": "json_object"}
            try:
                text = await self._chat(task, body)
            except _FormatUnsupported as exc:
                following = MODES[MODES.index(mode) + 1]
                log.warning(
                    "%s refused the %s response format (%s); using %s",
                    self.label,
                    mode,
                    exc,
                    following,
                )
                self._mode = following
                continue
            break
        try:
            return schema.model_validate(_parse(text))
        except (ValueError, ValidationError) as exc:
            problem = exc
        # One repair round: show the model its answer and what was wrong with it.
        body["messages"] = [
            *body["messages"],
            {"role": "assistant", "content": text[:20_000]},
            {
                "role": "user",
                "content": "That reply was not a valid JSON object for the schema "
                f"({str(problem)[:500]}). Reply with only the corrected JSON object.",
            },
        ]
        text = await self._chat(task, body)
        try:
            return schema.model_validate(_parse(text))
        except (ValueError, ValidationError) as exc:
            raise BrainError(f"{self.label}'s answer was not in the expected format.") from exc

    def _record_usage(self, data: dict[str, Any]) -> None:
        self._usage["requests"] += 1
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return
        for source, target in (
            ("prompt_tokens", "input_tokens"),
            ("completion_tokens", "output_tokens"),
        ):
            value = usage.get(source)
            if isinstance(value, int):
                self._usage[target] += value
