"""Spotting “notes on”, “notes off” and “cancel note” in what the recogniser heard.

The same rules as the web app's phrases.js. Two inputs: plain text (a typed note, or a
transcript) and the recogniser's word lists with timings, which say exactly where a
phrase starts and ends in the audio.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

DEFAULT_WAKE = "notes on"
DEFAULT_STOP = "notes off"
WAKE_ALIASES = ("note on", "nodes on", "node on", "notes own", "knots on", "nots on")
STOP_ALIASES = ("note off", "nodes off", "node off", "end note", "save note")
CANCEL_PHRASES = ("cancel note", "discard note", "delete this note")
UNKNOWN = "[unk]"

_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)?")


def compact(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text)).lower()
    return "".join(char for char in text if char.isalnum())


def words_of(text: str) -> list[str]:
    return [compact(word) for word in _WORD.findall(str(text)) if compact(word)]


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a or not b:
        return len(a) or len(b)
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b))
            )
        previous = current
    return previous[-1]


def phrase_list(custom: str | None, default: str, aliases: tuple[str, ...]) -> list[str]:
    """The user's phrase first; the usual mishearings only when it's the default one."""
    out: list[str] = []
    chosen = custom or default
    usual = aliases if compact(chosen) == compact(default) else ()
    for phrase in [chosen, *usual]:
        clean = " ".join(str(phrase).lower().split())
        if clean and clean not in out:
            out.append(clean)
    return out


def tidy(text: str) -> str:
    text = re.sub(r"^[\s,.;:!?-]+", "", str(text))
    text = re.sub(r"[\s,;:-]+$", "", text)
    return " ".join(text.split())


@dataclass(frozen=True)
class Hit:
    index: int  # first word of the phrase in the list
    length: int  # how many words it covers
    start: float  # seconds into the audio, when the words carry timings
    end: float
    phrase: str


def _close(joined: str, target: str, last: str, words: list[str], fuzzy: bool) -> bool:
    if joined == target:
        return True
    # Fuzzy matches may swap or drop one letter but not add one, and the last word must
    # be right: "nodes on" wakes Magpie, while "notes one" and "notes of" don't.
    return (
        fuzzy
        and len(target) >= 6
        and len(target) - 1 <= len(joined) <= len(target)
        and (len(words) < 2 or words[-1].endswith(last))
        and levenshtein(joined, target) <= 1
    )


def find_in_words(
    words: list[dict[str, Any]], phrases: list[str], *, fuzzy: bool = False, last: bool = False
) -> Hit | None:
    """Where one of the phrases appears in a recogniser word list ([{word, start, end}]).

    Returns the earliest occurrence, or the latest with ``last``.
    """
    plain = [compact(str(item.get("word", ""))) for item in words]
    best: Hit | None = None
    for phrase in phrases:
        target_words = words_of(phrase)
        target = "".join(target_words)
        if not target:
            continue
        size = len(target_words)
        for i in range(len(plain)):
            for length in range(max(1, size - 1), size + 2):
                if i + length > len(plain):
                    break
                chunk = plain[i : i + length]
                if not all(chunk) or not _close(
                    "".join(chunk), target, target_words[-1], chunk, fuzzy
                ):
                    continue
                hit = Hit(
                    i,
                    length,
                    float(words[i].get("start", 0.0)),
                    float(words[i + length - 1].get("end", 0.0)),
                    phrase,
                )
                if best is None or (hit.index > best.index if last else hit.index < best.index):
                    best = hit
    return best


def find_in_text(text: str, phrases: list[str], *, fuzzy: bool = False) -> tuple[int, int] | None:
    """(start, end) character positions of the earliest phrase in ``text``."""
    spans = [(m.start(), m.end()) for m in _WORD.finditer(str(text))]
    hit = find_in_words([{"word": text[a:b]} for a, b in spans], phrases, fuzzy=fuzzy)
    if hit is None:
        return None
    return spans[hit.index][0], spans[hit.index + hit.length - 1][1]


def after_wake(text: str, phrases: list[str]) -> str | None:
    """'ok notes on buy milk' -> 'buy milk'; None when there's no wake phrase."""
    span = find_in_text(text, phrases, fuzzy=True)
    return None if span is None else tidy(text[span[1] :])


def strip_leading_wake(text: str, phrases: list[str]) -> str:
    """Drop a wake phrase said at the very start (after pressing the mic, say)."""
    span = find_in_text(text, phrases, fuzzy=True)
    if span is not None and not tidy(text[: span[0]]):
        return tidy(text[span[1] :])
    return tidy(text)


def before_stop(text: str, phrases: list[str]) -> tuple[bool, str]:
    span = find_in_text(text, phrases)
    return (False, tidy(text)) if span is None else (True, tidy(text[: span[0]]))


def append_text(base: str, addition: str) -> str:
    """Add words to text that's already there, keeping its line breaks."""
    add = str(addition or "").strip()
    if not add:
        return str(base or "")
    start = str(base or "").rstrip()
    if not start:
        return add
    return f"{start}\n{add}" if re.search(r"\n\s*$", str(base)) else f"{start} {add}"
