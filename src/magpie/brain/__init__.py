"""The AI behind Magpie (Claude by default, or another provider) behind a small interface."""

from __future__ import annotations

from typing import TYPE_CHECKING

from magpie.brain.base import (
    Brain,
    BrainError,
    BrainRefused,
    BrainUnavailable,
    Context,
    DigestData,
)
from magpie.brain.providers import PROVIDERS, AIConfig, resolve
from magpie.config import Settings

if TYPE_CHECKING:
    import httpx2


def make_brain(
    settings: Settings,
    api_key: str | None = None,
    *,
    config: AIConfig | None = None,
    http: httpx2.AsyncClient | None = None,
) -> Brain:
    """The brain for ``config`` (by default: the server's settings plus a saved Claude key)."""
    if config is None:
        config = resolve(settings, {}, api_key)
    if config.provider.id == "fake":
        from magpie.brain.fake import FakeBrain

        return FakeBrain()
    if config.provider.id == "basic":
        from magpie.brain.fake import BasicBrain

        return BasicBrain()
    if config.provider.id == "claude":
        from magpie.brain.claude import ClaudeBrain

        return ClaudeBrain(settings, api_key=config.api_key, model=config.model)
    if config.provider.id == "codex":
        from magpie.brain.codex_cli import CodexBrain

        return CodexBrain(config, program=settings.codex_path)
    from magpie.brain.openai_compat import OpenAICompatibleBrain

    if http is None:
        import httpx2

        http = httpx2.AsyncClient(timeout=30)
    return OpenAICompatibleBrain(http, config)


__all__ = [
    "PROVIDERS",
    "AIConfig",
    "Brain",
    "BrainError",
    "BrainRefused",
    "BrainUnavailable",
    "Context",
    "DigestData",
    "make_brain",
    "resolve",
]
