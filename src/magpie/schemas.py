"""Structured outputs Claude returns.

These Pydantic models double as the JSON schemas sent to the API
(``output_config.format``), so field descriptions are written for Claude.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

NoteKindLiteral = Literal["task", "idea", "link", "journal", "meeting", "reference", "list", "note"]
PingLiteral = Literal["explicit", "suggested", "digest", "none"]
VerdictLiteral = Literal["must", "worth_it", "skim", "skip"]


class PlannedReminder(BaseModel):
    when: str = Field(
        description="Local wall-clock time in the user's time zone, formatted YYYY-MM-DDTHH:MM "
        "(no offset). For repeating reminders, the first occurrence."
    )
    message: str = Field(
        description="What the user reads when the reminder fires: short, imperative, "
        "self-contained, e.g. 'Call James about the budget'."
    )
    origin: Literal["explicit", "suggested"] = Field(
        description="explicit if the user named this time, suggested if you chose it."
    )
    repeat: str | None = Field(
        description="iCalendar RRULE without DTSTART for repeating reminders, e.g. "
        "'FREQ=WEEKLY;BYDAY=MO'. null for one-off reminders."
    )
    reason: str = Field(description="One short sentence on why this time, written to the user.")


class Triage(BaseModel):
    title: str = Field(description="2-8 word specific title.")
    text: str = Field(
        description='The cleaned-up note in the user\'s own words; a list has a "- item" line '
        "for each thing."
    )
    summary: str = Field(description="One-sentence summary for long notes, otherwise empty string.")
    kind: NoteKindLiteral
    tags: list[str] = Field(description="1-4 short lowercase tags.")
    category: str = Field(
        description="One of the user's own categories (listed with the note) that clearly fits, "
        "written exactly as listed; empty string if none fits or none are listed."
    )
    reminders: list[PlannedReminder]
    ping: PingLiteral
    ping_reason: str = Field(description="One short sentence to the user explaining the decision.")
    urls: list[str] = Field(description="URLs in the note or clearly dictated, as https URLs.")
    lookup: str | None = Field(
        description="Web search query to find a specific video/article/podcast/paper the user "
        "referred to without giving a URL; null otherwise."
    )
    briefing: str = Field(
        description="1-3 sentences of context about a named book, film, product, place, person or "
        "concept the user wants to check out; empty string otherwise."
    )
    reply: str = Field(description="One short confirmation sentence back to the user.")

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, tags: list[str]) -> list[str]:
        cleaned: list[str] = []
        for tag in tags:
            tag = tag.strip().lstrip("#").lower()
            if tag and tag not in cleaned:
                cleaned.append(tag[:32])
        return cleaned[:6]


class LinkReview(BaseModel):
    title: str = Field(description="Title of the video or article.")
    tldr: str = Field(description="1-2 sentences: the core message, not a teaser.")
    key_points: list[str] = Field(description="3-6 specific takeaways, one line each.")
    verdict: VerdictLiteral = Field(
        description="must (rare: exceptional and relevant), worth_it, skim, or skip."
    )
    rating: int = Field(description="Overall rating from 1 to 5.")
    best_for: str = Field(description="Who benefits and when, one sentence.")
    caveats: str = Field(
        description="Bias, sponsorship, outdated info, thin evidence, clickbait, or partial "
        "content you were given. Empty string if none."
    )
    why_saved: str = Field(
        description="How it relates to the user's note: what they likely wanted from it and "
        "whether it delivers."
    )
    minutes: int | None = Field(description="Estimated minutes to watch or read, null if unknown.")

    @field_validator("rating")
    @classmethod
    def _clamp_rating(cls, value: int) -> int:
        return max(1, min(5, value))


class FoundLink(BaseModel):
    url: str | None = Field(description="The best matching URL, or null if nothing fits.")
    title: str = Field(description="Title of the page found, or empty string.")
    confidence: Literal["high", "medium", "low"]


class Answer(BaseModel):
    answer: str = Field(description="The answer in Markdown, citing notes inline like (#12).")
    note_ids: list[int] = Field(description="Ids of the notes the answer relies on.")
