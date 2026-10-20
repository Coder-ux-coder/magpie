"""Configuration.

Two layers:

* ``Settings`` come from environment variables (or a ``.env`` file). They hold
  infrastructure and secrets and only change on restart.
* ``Prefs`` live in the database and are edited from the dashboard (time zone,
  how proactive Claude should be, digest time, ...). ``Settings`` provide the
  defaults for a fresh install.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

LOG_LEVELS = ("critical", "error", "warning", "info", "debug")
ENGLISH_TAG = re.compile(r"en(?:-[a-z0-9]{1,8})*", re.IGNORECASE)

Effort = Literal["low", "medium", "high", "xhigh", "max"]
Proactivity = Literal["quiet", "balanced", "proactive"]
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun", ""]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MAGPIE_",
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    # Server
    data_dir: Path = Path("data")
    host: str = "127.0.0.1"
    port: int = 8000
    base_url: str = ""
    password: str | None = None
    api_token: str | None = None
    secret_key: str | None = None
    timezone: str = "UTC"
    log_level: str = "info"

    # Claude
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "MAGPIE_ANTHROPIC_API_KEY"),
    )
    model: str = "claude-opus-5-5"
    effort: Effort | None = None
    fallbacks: bool = True
    web_search: bool = True
    brain: Literal["claude", "fake"] = "claude"

    # Server-side speech-to-text (any OpenAI-compatible /audio/transcriptions endpoint)
    stt_api_key: str | None = None
    stt_base_url: str = "https://api.openai.com/v1"
    stt_model: str = "whisper-1"
    stt_language: str | None = "en"

    # Telegram
    telegram_bot_token: str | None = None
    telegram_chat_id: int | None = None

    # ntfy
    ntfy_topic: str | None = None
    ntfy_server: str = "https://ntfy.sh"
    ntfy_token: str | None = None

    # Email
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    email_to: str | None = None

    # Webhook (Slack, Discord or anything that takes JSON)
    webhook_url: str | None = None
    webhook_format: Literal["json", "slack", "discord"] = "json"

    # Web push contact (sent to push services with each message)
    vapid_subject: str = "mailto:magpie@example.com"

    # Link fetching
    allow_private_urls: bool = False

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.strip().rstrip("/")

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        from magpie.timeutil import is_valid_timezone

        value = value.strip()
        if not is_valid_timezone(value):
            # A typo here must not stop the app; the dashboard can set it later.
            log.warning("Unknown time zone %r in MAGPIE_TIMEZONE; using UTC", value)
            return "UTC"
        return value

    @field_validator("log_level")
    @classmethod
    def _known_log_level(cls, value: str) -> str:
        value = value.strip().lower()
        return value if value in LOG_LEVELS else "info"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "magpie.db"

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")


class Prefs(BaseModel):
    """User preferences, stored in the database and edited from the dashboard."""

    timezone: str = Field(default="UTC", max_length=64)
    proactivity: Proactivity = "balanced"
    about_me: str = Field(default="", max_length=2000)
    instructions: str = Field(default="", max_length=2000)
    morning_time: str = "09:00"
    digest_time: str = "08:00"
    weekly_review_day: Weekday = "sun"
    weekly_review_time: str = "18:00"
    speech_lang: str = Field(default="", max_length=35)
    wake_phrase: str = Field(default="notes on", max_length=60)
    stop_phrase: str = Field(default="notes off", max_length=60)
    silence_seconds: int = Field(default=8, ge=0, le=120)
    snooze_minutes: int = Field(default=60, ge=5, le=1440)
    channels: dict[str, bool] = Field(default_factory=dict, max_length=20)
    web_search: bool = True
    telegram_chat_id: int | None = None

    @field_validator("morning_time", "digest_time", "weekly_review_time")
    @classmethod
    def _valid_time(cls, value: str) -> str:
        value = value.strip()
        if value == "":
            return value
        from magpie.timeutil import parse_hhmm

        if parse_hhmm(value) is None:
            raise ValueError("expected HH:MM (24-hour)")
        return value

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, value: str) -> str:
        from magpie.timeutil import is_valid_timezone

        if not is_valid_timezone(value):
            raise ValueError(f"unknown time zone {value!r}")
        return value

    @field_validator("speech_lang")
    @classmethod
    def _english(cls, value: str) -> str:
        """Magpie listens for English: empty (automatic) or an accent such as en-GB."""
        value = value.strip()
        if value and not ENGLISH_TAG.fullmatch(value):
            raise ValueError("expected English, such as en-GB or en-US")
        return value

    def channel_enabled(self, name: str) -> bool:
        return self.channels.get(name, True)
