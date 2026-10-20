"""A rule-based stand-in for Claude.

Used by the test suite and for trying the app without an API key
(``MAGPIE_BRAIN=fake``). It understands a handful of common phrasings ("at
5 pm", "tomorrow", "in 20 minutes", "every Monday") and is deliberately simple:
the real intelligence lives in ``ClaudeBrain``.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from magpie.brain.base import Context, DigestData
from magpie.content import LinkContent
from magpie.content.links import extract_urls
from magpie.models import Note
from magpie.schemas import Answer, FoundLink, LinkReview, PlannedReminder, Triage
from magpie.timeutil import get_tz, parse_hhmm

FILLERS = re.compile(r"\b(uh+|um+|erm+|uhm+|hmm+|you know|i mean)\b[,.]?\s*", re.IGNORECASE)
COMMANDS = re.compile(r"^\s*notes?\s+on[\s,.:]*|[\s,.]*notes?\s+off[\s.]*$", re.IGNORECASE)
LEADING = re.compile(
    r"^(please\s+)?(remind me (to|about)|i need to|i have to|i should|i must|don't forget to|"
    r"remember to|note to self:?)\s+",
    re.IGNORECASE,
)
TIME_AT = re.compile(r"\b(?:at|by)\s+(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?", re.IGNORECASE)
TIME_IN = re.compile(
    r"\bin\s+(\d+|an?|one|two|five|ten|twenty|thirty)\s+(minutes?|mins?|hours?)\b", re.I
)
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WORD_NUMBERS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "five": 5,
    "ten": 10,
    "twenty": 20,
    "thirty": 30,
}
TASK_WORDS = re.compile(
    r"\b(remind|call|buy|pay|email|send|book|fix|finish|submit|pick up|renew|schedule|reply|"
    r"order|cancel|clean|need to|have to|todo|to-do)\b",
    re.IGNORECASE,
)
WATCH_WORDS = re.compile(r"\b(watch|read|listen to|check out)\b", re.IGNORECASE)


def _clean(text: str) -> str:
    text = COMMANDS.sub("", text)
    text = FILLERS.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" ,")
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    if text[-1] not in ".!?":
        text += "."
    return text


def _title(text: str) -> str:
    core = LEADING.sub("", text.rstrip(".!?"))
    core = TIME_AT.sub("", core)
    core = re.sub(
        r"\b(tomorrow|tonight|today|this (morning|evening|afternoon))\b", "", core, flags=re.I
    )
    words = [w for w in re.sub(r"https?://\S+", "", core).split() if w]
    title = " ".join(words[:6]).strip(" ,.-")
    return (title[:1].upper() + title[1:]) if title else "Untitled note"


def _guess_kind(text: str, urls: list[str]) -> str:
    lowered = text.lower()
    if urls or WATCH_WORDS.search(lowered):
        return "link"
    if re.search(r"\b(idea|what if|maybe we could)\b", lowered):
        return "idea"
    if re.search(r"\b(meeting|call with|discussed|agreed)\b", lowered) and "remind" not in lowered:
        return "meeting"
    if TASK_WORDS.search(lowered):
        return "task"
    if re.search(r"\b(today i|i feel|i felt|grateful|had a (great|good|bad))\b", lowered):
        return "journal"
    if re.search(r"\b(shopping list|groceries|packing list)\b", lowered):
        return "list"
    return "note"


def _number(token: str) -> int:
    return int(token) if token.isdigit() else WORD_NUMBERS.get(token.lower(), 1)


def _plan_reminders(text: str, ctx: Context) -> list[PlannedReminder]:
    lowered = text.lower()
    now = ctx.now.astimezone(get_tz(ctx.timezone)).replace(tzinfo=None, second=0, microsecond=0)
    message = _title(_clean(text))
    when: datetime | None = None
    repeat: str | None = None

    rel = TIME_IN.search(lowered)
    at = TIME_AT.search(lowered)
    if at and int(at.group(2) or 0) > 59:
        at = None  # "at 12:99" is not a time
    if rel:
        amount = min(_number(rel.group(1)), 10_000)
        unit = rel.group(2)
        when = now + (
            timedelta(hours=amount) if unit.startswith("hour") else timedelta(minutes=amount)
        )
    elif at or re.search(r"\b(tomorrow|tonight)\b", lowered):
        hour, minute = parse_hhmm(ctx.morning_time) or (9, 0)
        if "tonight" in lowered:
            hour, minute = 20, 0
        if at:
            hour, minute = int(at.group(1)) % 24, int(at.group(2) or 0)
            meridiem = (at.group(3) or "").replace(".", "").lower()
            if meridiem == "pm" and hour < 12:
                hour += 12
            elif meridiem == "am" and hour == 12:
                hour = 0
            elif not meridiem and 1 <= hour <= 7:
                hour += 12
        day = now.date()
        if "tomorrow" in lowered:
            day = day + timedelta(days=1)
        for index, name in enumerate(WEEKDAYS):
            if re.search(rf"\b{name}\b", lowered):
                ahead = (index - now.weekday()) % 7
                if ahead == 0 and datetime(day.year, day.month, day.day, hour, minute) <= now:
                    ahead = 7
                day = now.date() + timedelta(days=ahead)
        when = datetime(day.year, day.month, day.day, hour, minute)
        if when <= now:
            when += timedelta(days=1)

    if re.search(r"\bevery (day|morning|evening|night)\b", lowered):
        repeat = "FREQ=DAILY"
    for index, name in enumerate(WEEKDAYS):
        if re.search(rf"\bevery {name}\b", lowered):
            repeat = f"FREQ=WEEKLY;BYDAY={name[:2].upper()}"
            if when is None:
                hour, minute = parse_hhmm(ctx.morning_time) or (9, 0)
                ahead = (index - now.weekday()) % 7 or 7
                day = now.date() + timedelta(days=ahead)
                when = datetime(day.year, day.month, day.day, hour, minute)
    if repeat and when is None:
        hour, minute = parse_hhmm(ctx.morning_time) or (9, 0)
        when = datetime(now.year, now.month, now.day, hour, minute)
        if when <= now:
            when += timedelta(days=1)

    if when is None:
        return []
    return [
        PlannedReminder(
            when=when.strftime("%Y-%m-%dT%H:%M"),
            message=message,
            origin="explicit",
            repeat=repeat,
            reason="You asked for this time.",
        )
    ]


class FakeBrain:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[str] = []

    @property
    def available(self) -> bool:
        return True

    def usage(self) -> dict[str, int]:
        return {"requests": len(self.calls)}

    async def triage(self, text: str, ctx: Context) -> Triage:
        self.calls.append("triage")
        clean = _clean(text)
        urls = extract_urls(text)
        kind = _guess_kind(clean, urls)
        reminders = _plan_reminders(text, ctx)
        lookup = None
        if kind == "link" and not urls:
            match = WATCH_WORDS.search(clean)
            lookup = clean[match.end() :].strip(" .") if match else None
        if reminders:
            ping, reason = "explicit", "You asked for a reminder."
        elif kind in ("idea", "link", "task"):
            ping, reason = "digest", "No deadline, so it will be in your next digest."
        else:
            ping, reason = "none", "Nothing to follow up."
        title = _title(clean)
        if reminders:
            local = datetime.strptime(reminders[0].when, "%Y-%m-%dT%H:%M")
            reply = f"Saved. I'll remind you {local.strftime('%a %I:%M %p').replace(' 0', ' ')}."
        else:
            reply = f"Saved to {kind}s." if kind != "note" else "Saved."
        tags = sorted({w.lower() for w in re.findall(r"#(\w+)", text)})[:4]
        return Triage(
            title=title,
            text=clean,
            summary="",
            kind=kind,
            tags=tags,
            reminders=reminders,
            ping=ping,
            ping_reason=reason,
            urls=urls,
            lookup=lookup or None,
            briefing="",
            reply=reply,
        )

    async def review(self, content: LinkContent, note_text: str) -> LinkReview:
        self.calls.append("review")
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", content.text) if s.strip()]
        tldr = " ".join(sentences[:2]) or f"Saved {content.kind} with no readable text."
        points = [s[:160] for s in sentences[2:6]] or ["No further detail available offline."]
        return LinkReview(
            title=content.title or content.url,
            tldr=tldr[:400],
            key_points=points,
            verdict="worth_it",
            rating=4,
            best_for="Anyone curious about the topic.",
            caveats="Offline demo review (MAGPIE_BRAIN=fake), not written by Claude.",
            why_saved=f"You noted: {note_text.strip()[:140]}",
            minutes=content.minutes,
        )

    async def review_url(self, url: str, note_text: str) -> LinkReview:
        self.calls.append("review_url")
        return LinkReview(
            title=url,
            tldr="The page could not be fetched in offline mode.",
            key_points=["Open the link to read it."],
            verdict="skim",
            rating=3,
            best_for="You, when you have a minute.",
            caveats="Offline demo review (MAGPIE_BRAIN=fake).",
            why_saved=f"You noted: {note_text.strip()[:140]}",
            minutes=None,
        )

    async def find_link(self, query: str) -> FoundLink:
        self.calls.append("find_link")
        return FoundLink(url=None, title="", confidence="low")

    async def digest(self, data: DigestData) -> str:
        self.calls.append(f"digest:{data.kind}")
        from magpie.digest import render_plain

        return render_plain(data)

    async def ask(self, question: str, notes: list[Note], ctx: Context) -> Answer:
        self.calls.append("ask")
        words = {w for w in re.findall(r"\w{3,}", question.lower())}
        scored = []
        for note in notes:
            haystack = f"{note.title} {note.text or note.raw_text} {' '.join(note.tags)}".lower()
            score = sum(1 for word in words if word in haystack)
            if score:
                scored.append((score, note))
        scored.sort(key=lambda item: (-item[0], -item[1].id))
        hits = [note for _, note in scored[:5]]
        if not hits:
            return Answer(answer="I couldn't find anything about that in your notes.", note_ids=[])
        lines = [f"- {note.display_title} (#{note.id})" for note in hits]
        return Answer(
            answer="Here's what I found in your notes:\n" + "\n".join(lines),
            note_ids=[note.id for note in hits],
        )
