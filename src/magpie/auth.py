"""Secrets, passwords, API tokens and signed action links."""

from __future__ import annotations

import hmac
import logging
import secrets
import time
from collections import defaultdict, deque

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from magpie.config import Settings
from magpie.store import Store

log = logging.getLogger(__name__)

ACTION_MAX_AGE = 14 * 24 * 3600
ACTIONS = {"done", "snooze", "tomorrow"}


class Secrets:
    """Secrets from the environment, or generated once and kept in the database."""

    def __init__(self, store: Store, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self.secret_key = settings.secret_key or self._get_or_create(
            "secret_key", lambda: secrets.token_urlsafe(32)
        )
        self.password_generated = not settings.password
        self.password = settings.password or self._get_or_create(
            "generated_password", lambda: secrets.token_urlsafe(9)
        )
        self.api_token_from_env = bool(settings.api_token)
        self.api_token = settings.api_token or self._get_or_create(
            "api_token", lambda: "mgp_" + secrets.token_urlsafe(24)
        )

    def _get_or_create(self, key: str, factory) -> str:
        value = self.store.get_kv(key)
        if not value:
            value = factory()
            self.store.set_kv(key, value)
        return value

    def check_password(self, candidate: str) -> bool:
        return hmac.compare_digest(candidate.encode(), self.password.encode())

    def check_token(self, candidate: str) -> bool:
        return hmac.compare_digest(candidate.encode(), self.api_token.encode())

    def rotate_api_token(self) -> str:
        if self.api_token_from_env:
            raise RuntimeError("The API token is set by MAGPIE_API_TOKEN; change it there.")
        self.api_token = "mgp_" + secrets.token_urlsafe(24)
        self.store.set_kv("api_token", self.api_token)
        return self.api_token


class ActionSigner:
    """One-click reminder actions for push, ntfy and email.

    The URL itself is the credential and can only perform one action on one
    reminder, so it is safe to put in a notification.
    """

    def __init__(self, secret_key: str) -> None:
        self._serializer = URLSafeTimedSerializer(secret_key, salt="magpie-reminder-action")

    def sign(self, reminder_id: int, action: str) -> str:
        if action not in ACTIONS:
            raise ValueError(action)
        return self._serializer.dumps({"r": reminder_id, "a": action})

    def load(self, token: str) -> tuple[int, str]:
        try:
            data = self._serializer.loads(token, max_age=ACTION_MAX_AGE)
        except SignatureExpired as exc:
            raise ValueError("This link has expired.") from exc
        except BadSignature as exc:
            raise ValueError("This link is not valid.") from exc
        reminder_id, action = data.get("r"), data.get("a")
        if not isinstance(reminder_id, int) or action not in ACTIONS:
            raise ValueError("This link is not valid.")
        return reminder_id, action


class RateLimiter:
    """Sliding-window limiter for login attempts."""

    def __init__(self, attempts: int = 10, window: float = 900) -> None:
        self.attempts = attempts
        self.window = window
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.attempts:
            return False
        hits.append(now)
        return True

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)
