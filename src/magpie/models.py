"""Domain models returned by the store and the API."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from magpie.schemas import LinkReview


class NoteKind(StrEnum):
    TASK = "task"
    IDEA = "idea"
    LINK = "link"
    JOURNAL = "journal"
    MEETING = "meeting"
    REFERENCE = "reference"
    LIST = "list"
    NOTE = "note"


class NoteStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    ARCHIVED = "archived"


class ProcState(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class ReminderStatus(StrEnum):
    SCHEDULED = "scheduled"
    SENT = "sent"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"


class LinkKind(StrEnum):
    YOUTUBE = "youtube"
    ARTICLE = "article"
    PDF = "pdf"


class Reminder(BaseModel):
    id: int
    note_id: int | None
    due_at: datetime
    message: str
    origin: str
    reason: str = ""
    rrule: str | None = None
    status: ReminderStatus
    attempts: int = 0
    last_error: str | None = None
    sent_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class Link(BaseModel):
    id: int
    note_id: int
    url: str
    kind: LinkKind
    title: str = ""
    author: str = ""
    site: str = ""
    minutes: int | None = None
    state: ProcState
    error: str | None = None
    review: LinkReview | None = None
    found_by_search: bool = False
    created_at: datetime
    updated_at: datetime


class Note(BaseModel):
    id: int
    created_at: datetime
    updated_at: datetime
    source: str
    raw_text: str
    text: str = ""
    title: str = ""
    summary: str = ""
    kind: NoteKind = NoteKind.NOTE
    tags: list[str] = Field(default_factory=list)
    status: NoteStatus = NoteStatus.OPEN
    pinned: bool = False
    state: ProcState = ProcState.PENDING
    error: str | None = None
    ping: str = ""
    ping_reason: str = ""
    in_digest: bool = False
    briefing: str = ""
    reply: str = ""
    category_id: int | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    links: list[Link] = Field(default_factory=list)
    reminders: list[Reminder] = Field(default_factory=list)

    @property
    def display_title(self) -> str:
        if self.title:
            return self.title
        words = (self.text or self.raw_text).split()
        return " ".join(words[:8]) + ("…" if len(words) > 8 else "")


COLORS = ("red", "orange", "amber", "green", "teal", "blue", "indigo", "purple", "pink", "grey")


class Category(BaseModel):
    id: int
    name: str
    color: str = "blue"
    position: int = 0
    count: int = 0  # open notes in it


class Digest(BaseModel):
    id: int
    kind: str
    created_at: datetime
    content: str
    sent: bool = False
