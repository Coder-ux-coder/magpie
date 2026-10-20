"""Fetching the things notes point at: YouTube videos, articles and PDFs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LinkContent:
    url: str
    kind: str
    title: str = ""
    author: str = ""
    site: str = ""
    text: str = ""
    minutes: int | None = None
    pdf: bytes | None = None
    note: str = ""

    @property
    def has_body(self) -> bool:
        return bool(self.pdf) or len(self.text.strip()) >= 200


class FetchError(Exception):
    """The link could not be fetched or had no readable content."""
