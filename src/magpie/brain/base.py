"""The interface every brain implements, plus shared types and errors."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from magpie.content import LinkContent
from magpie.models import Link, Note, Reminder
from magpie.schemas import Answer, FoundLink, LinkReview, Triage


class BrainError(Exception):
    """Claude could not complete a request.

    ``retryable`` is True for transient problems (rate limits, overload,
    network) so callers can try again later.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class BrainUnavailable(BrainError):
    """No credentials configured (or they were rejected)."""


class BrainRefused(BrainError):
    """Claude declined the request (stop_reason == "refusal")."""


@dataclass(frozen=True)
class Context:
    """What Claude needs to know about the user and the moment."""

    timezone: str
    now: datetime
    proactivity: str = "balanced"
    about_me: str = ""
    morning_time: str = "09:00"
    source: str = "web"


@dataclass
class DigestData:
    kind: Literal["daily", "weekly"]
    context: Context
    reminders_today: list[Reminder] = field(default_factory=list)
    missed: list[Reminder] = field(default_factory=list)
    digest_notes: list[Note] = field(default_factory=list)
    reviews: list[Link] = field(default_factory=list)
    open_tasks: list[Note] = field(default_factory=list)
    week_notes: list[Note] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (
            self.reminders_today
            or self.missed
            or self.digest_notes
            or self.reviews
            or self.open_tasks
            or self.week_notes
        )


class Brain(Protocol):
    name: str

    @property
    def available(self) -> bool: ...

    async def triage(self, text: str, ctx: Context) -> Triage: ...

    async def review(self, content: LinkContent, note_text: str) -> LinkReview: ...

    async def review_url(self, url: str, note_text: str) -> LinkReview: ...

    async def find_link(self, query: str) -> FoundLink: ...

    async def digest(self, data: DigestData) -> str: ...

    async def ask(self, question: str, notes: list[Note], ctx: Context) -> Answer: ...

    def usage(self) -> dict[str, int]: ...
