"""Sending messages to the user: reminders, digests, reviews.

Each channel is optional. A notification goes to every channel that is
configured and enabled in the dashboard; it counts as delivered if at least one
channel accepts it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from magpie.config import Prefs

log = logging.getLogger(__name__)

# A channel that hangs (an SMTP server that never answers) must not hold up the rest.
CHANNEL_TIMEOUT = 90


@dataclass
class Notification:
    title: str
    body: str = ""
    kind: str = "info"  # reminder | digest | review | info | test
    url: str | None = None
    reminder_id: int | None = None
    note_id: int | None = None
    # Signed one-click action URLs, e.g. {"done": ..., "snooze": ..., "tomorrow": ...}
    actions: dict[str, str] = field(default_factory=dict)
    snooze_minutes: int = 60

    @property
    def text(self) -> str:
        return f"{self.title}\n\n{self.body}".strip() if self.body else self.title


def snooze_label(minutes: int) -> str:
    """ "15 min", "1 hour", "2 hours", "1 day": for snooze buttons."""
    if minutes % 1440 == 0:
        days = minutes // 1440
        return f"{days} day" + ("s" if days > 1 else "")
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour" + ("s" if hours > 1 else "")
    return f"{minutes} min"


class Channel(Protocol):
    name: str
    label: str

    @property
    def configured(self) -> bool: ...

    async def send(self, notification: Notification) -> None: ...


@dataclass
class DeliveryReport:
    results: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return any(result == "ok" for result in self.results.values())

    @property
    def no_channels(self) -> bool:
        return not self.results

    @property
    def errors(self) -> str:
        return "; ".join(
            f"{name}: {result}" for name, result in self.results.items() if result != "ok"
        )


class Notifier:
    def __init__(self, channels: list[Channel], prefs: Callable[[], Prefs]) -> None:
        self.channels = channels
        self._prefs = prefs

    def configured(self) -> list[Channel]:
        return [channel for channel in self.channels if channel.configured]

    def active(self) -> list[Channel]:
        prefs = self._prefs()
        return [c for c in self.configured() if prefs.channel_enabled(c.name)]

    def get(self, name: str) -> Channel | None:
        return next((c for c in self.channels if c.name == name), None)

    async def send(self, notification: Notification, *, only: str | None = None) -> DeliveryReport:
        channels = self.active()
        if only is not None:
            channels = [c for c in self.configured() if c.name == only]
        report = DeliveryReport()
        if not channels:
            return report

        async def deliver(channel: Channel) -> None:
            try:
                await asyncio.wait_for(channel.send(notification), CHANNEL_TIMEOUT)
                report.results[channel.name] = "ok"
            except TimeoutError:
                log.warning("Notification via %s timed out", channel.name)
                report.results[channel.name] = "timed out"
            except Exception as exc:  # one broken channel must not block the others
                log.warning("Notification via %s failed: %s", channel.name, exc)
                report.results[channel.name] = str(exc)[:300] or type(exc).__name__

        await asyncio.gather(*(deliver(channel) for channel in channels))
        return report
