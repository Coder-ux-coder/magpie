"""Claude, the brain of Magpie, behind a small interface."""

from __future__ import annotations

from magpie.brain.base import (
    Brain,
    BrainError,
    BrainRefused,
    BrainUnavailable,
    Context,
    DigestData,
)
from magpie.config import Settings


def make_brain(settings: Settings) -> Brain:
    if settings.brain == "fake":
        from magpie.brain.fake import FakeBrain

        return FakeBrain()
    from magpie.brain.claude import ClaudeBrain

    return ClaudeBrain(settings)


__all__ = [
    "Brain",
    "BrainError",
    "BrainRefused",
    "BrainUnavailable",
    "Context",
    "DigestData",
    "make_brain",
]
