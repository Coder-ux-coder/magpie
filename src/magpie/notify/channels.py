"""Notification channels: Telegram, ntfy, web push, email and webhooks."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import smtplib
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

import httpx2
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid02
from pywebpush import WebPushException, webpush

from magpie.config import Settings
from magpie.notify import Notification, snooze_label
from magpie.store import Store
from magpie.telegram_api import TelegramApi, escape

log = logging.getLogger(__name__)

ICONS = {"reminder": "⏰", "digest": "☀️", "review": "📺", "test": "🔔", "info": "🐦"}


def _one_line(text: str, limit: int) -> str:
    """Header-safe text: email subjects and push titles can't contain line breaks."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _public_url(url: str | None) -> bool:
    if not url or not url.startswith(("http://", "https://")):
        return False
    return not any(host in url for host in ("localhost", "127.0.0.1", "0.0.0.0", "[::1]"))


class TelegramChannel:
    name = "telegram"
    label = "Telegram"

    def __init__(self, api: TelegramApi | None, chat_id: Callable[[], int | None]) -> None:
        self.api = api
        self._chat_id = chat_id

    @property
    def configured(self) -> bool:
        return self.api is not None and self._chat_id() is not None

    async def send(self, notification: Notification) -> None:
        chat_id = self._chat_id()
        if self.api is None or chat_id is None:
            raise RuntimeError("Telegram is not paired yet")
        icon = ICONS.get(notification.kind, "")
        text = f"{icon} <b>{escape(notification.title)}</b>".strip()
        if notification.body:
            text += f"\n\n{escape(notification.body)}"
        buttons: list[list[dict[str, str]]] = []
        if notification.reminder_id is not None:
            rid = notification.reminder_id
            minutes = notification.snooze_minutes
            buttons.append(
                [
                    {"text": "✅ Done", "callback_data": f"r:done:{rid}"},
                    {
                        "text": f"⏰ {snooze_label(minutes)}",
                        "callback_data": f"r:snz:{rid}:{minutes}",
                    },
                    {"text": "🌅 Tomorrow", "callback_data": f"r:tmr:{rid}"},
                ]
            )
        if _public_url(notification.url):
            buttons.append([{"text": "Open in Magpie", "url": notification.url or ""}])
        await self.api.send_message(chat_id, text, buttons=buttons or None)


class NtfyChannel:
    name = "ntfy"
    label = "ntfy"

    def __init__(self, http: httpx2.AsyncClient, settings: Settings) -> None:
        self.http = http
        self.settings = settings

    @property
    def configured(self) -> bool:
        return bool(self.settings.ntfy_topic)

    async def send(self, notification: Notification) -> None:
        payload: dict[str, Any] = {
            "topic": self.settings.ntfy_topic,
            "title": notification.title,
            "message": notification.body or notification.title,
            "tags": [{"reminder": "alarm_clock", "digest": "sunny"}.get(notification.kind, "bird")],
            "priority": 4 if notification.kind == "reminder" else 3,
        }
        if _public_url(notification.url):
            payload["click"] = notification.url
        actions = []
        if "done" in notification.actions:
            actions.append(
                {
                    "action": "http",
                    "label": "Done",
                    "url": notification.actions["done"],
                    "method": "POST",
                    "clear": True,
                }
            )
        if "snooze" in notification.actions:
            actions.append(
                {
                    "action": "http",
                    "label": f"Snooze {snooze_label(notification.snooze_minutes)}",
                    "url": notification.actions["snooze"],
                    "method": "POST",
                    "clear": True,
                }
            )
        if actions:
            payload["actions"] = actions
        headers = {}
        if self.settings.ntfy_token:
            headers["Authorization"] = f"Bearer {self.settings.ntfy_token}"
        response = await self.http.post(
            self.settings.ntfy_server.rstrip("/"), json=payload, headers=headers, timeout=20
        )
        if response.status_code >= 400:
            raise RuntimeError(f"ntfy returned HTTP {response.status_code}")


class WebhookChannel:
    name = "webhook"
    label = "Webhook"

    def __init__(self, http: httpx2.AsyncClient, settings: Settings) -> None:
        self.http = http
        self.settings = settings

    @property
    def configured(self) -> bool:
        return bool(self.settings.webhook_url)

    def payload(self, notification: Notification) -> dict[str, Any]:
        link = notification.url if _public_url(notification.url) else None
        fmt = self.settings.webhook_format
        if fmt == "slack":
            text = f"*{notification.title}*"
            if notification.body:
                text += f"\n{notification.body}"
            if link:
                text += f"\n<{link}|Open in Magpie>"
            return {"text": text}
        if fmt == "discord":
            text = f"**{notification.title}**"
            if notification.body:
                text += f"\n{notification.body}"
            if link:
                text += f"\n{link}"
            return {"content": text[:2000]}
        return {
            "event": notification.kind,
            "title": notification.title,
            "body": notification.body,
            "text": notification.text,
            "url": link,
            "note_id": notification.note_id,
            "reminder_id": notification.reminder_id,
            "due": notification.due or None,
            "actions": notification.actions,
        }

    async def send(self, notification: Notification) -> None:
        response = await self.http.post(
            self.settings.webhook_url or "", json=self.payload(notification), timeout=20
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Webhook returned HTTP {response.status_code}")


# Outgoing mail servers for common addresses, so people only need their address and an
# app password. Gmail, Outlook, Yahoo and iCloud refuse normal passwords from apps.
EMAIL_PRESETS: dict[str, tuple[str, int, str]] = {
    "gmail.com": ("smtp.gmail.com", 587, "starttls"),
    "googlemail.com": ("smtp.gmail.com", 587, "starttls"),
    "outlook.com": ("smtp-mail.outlook.com", 587, "starttls"),
    "hotmail.com": ("smtp-mail.outlook.com", 587, "starttls"),
    "live.com": ("smtp-mail.outlook.com", 587, "starttls"),
    "msn.com": ("smtp-mail.outlook.com", 587, "starttls"),
    "yahoo.com": ("smtp.mail.yahoo.com", 465, "ssl"),
    "ymail.com": ("smtp.mail.yahoo.com", 465, "ssl"),
    "icloud.com": ("smtp.mail.me.com", 587, "starttls"),
    "me.com": ("smtp.mail.me.com", 587, "starttls"),
    "mac.com": ("smtp.mail.me.com", 587, "starttls"),
    "aol.com": ("smtp.aol.com", 465, "ssl"),
    "zoho.com": ("smtp.zoho.com", 465, "ssl"),
    "fastmail.com": ("smtp.fastmail.com", 465, "ssl"),
    "gmx.com": ("mail.gmx.com", 587, "starttls"),
    "gmx.co.uk": ("mail.gmx.com", 587, "starttls"),
    "yandex.com": ("smtp.yandex.com", 465, "ssl"),
}


def smtp_preset(address: str) -> tuple[str, int, str] | None:
    domain = address.rpartition("@")[2].strip().lower()
    return EMAIL_PRESETS.get(domain)


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    security: str  # starttls | ssl | none
    username: str
    password: str
    sender: str
    to: str
    source: str  # env | dashboard


class EmailError(Exception):
    """The mail server refused or couldn't be reached; the message says what to do."""


class DesktopChannel:
    """Reminders popping up from the Magpie app on this computer (the desktop app).

    Configured while the app runs Magpie; the app shows the pop-up itself, so nothing
    leaves the computer.
    """

    name = "desktop"
    label = "Pop-ups from the Magpie app"

    def __init__(self, bridge: Callable[[], Any]) -> None:
        self._bridge = bridge

    @property
    def configured(self) -> bool:
        return self._bridge() is not None

    async def send(self, notification: Notification) -> None:
        bridge = self._bridge()
        if bridge is None:
            raise RuntimeError("The Magpie app isn't running.")
        await asyncio.to_thread(
            bridge.notify,
            {
                "kind": notification.kind,
                "title": notification.title,
                "body": notification.body,
                "due": notification.due,
                "url": notification.url,
                "note_id": notification.note_id,
                "reminder_id": notification.reminder_id,
                "snooze_minutes": notification.snooze_minutes,
            },
        )


class EmailChannel:
    name = "email"
    label = "Email"

    def __init__(self, settings: Settings, store: Store | None = None) -> None:
        self.settings = settings
        self.store = store

    def config(self) -> SmtpConfig | None:
        """The server's MAGPIE_SMTP_* settings, else what was entered in the dashboard."""
        s = self.settings
        if s.smtp_host:
            sender = s.smtp_from or s.smtp_username or ""
            return SmtpConfig(
                s.smtp_host,
                s.smtp_port,
                s.smtp_security,
                s.smtp_username or "",
                s.smtp_password or "",
                sender,
                s.email_to or "",
                "env",
            )
        saved = self.store.get_kv("email") if self.store is not None else None
        if not isinstance(saved, dict):
            return None
        try:
            address = str(saved["address"])
            return SmtpConfig(
                str(saved["host"]),
                int(saved["port"]),
                str(saved.get("security", "starttls")),
                address,
                str(saved["password"]),
                address,
                str(saved.get("to") or address),
                "dashboard",
            )
        except (KeyError, TypeError, ValueError):
            return None

    @property
    def configured(self) -> bool:
        config = self.config()
        return bool(config and config.host and config.to and config.sender)

    def build(self, notification: Notification, config: SmtpConfig | None = None) -> EmailMessage:
        config = config or self.config()
        message = EmailMessage()
        subject = f"{ICONS.get(notification.kind, '')} {notification.title}".strip()
        if notification.kind == "reminder" and notification.due:
            subject += f" · due {notification.due}"
        message["Subject"] = _one_line(subject, 150)
        message["From"] = config.sender if config else ""
        message["To"] = config.to if config else ""
        lines = [notification.title]
        if notification.body:
            lines.append(notification.body)
        if _public_url(notification.url):
            lines.append(f"Open in Magpie: {notification.url}")
        snooze = f"Snooze {snooze_label(notification.snooze_minutes)}"
        for label, key in (("Mark as done", "done"), (snooze, "snooze")):
            if key in notification.actions:
                lines.append(f"{label}: {notification.actions[key]}")
        lines.append("— Magpie")
        message.set_content("\n\n".join(lines))
        return message

    def _connect(self, config: SmtpConfig) -> smtplib.SMTP:
        context = ssl.create_default_context()
        if config.security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                config.host, config.port, context=context, timeout=30
            )
        else:
            server = smtplib.SMTP(config.host, config.port, timeout=30)
        try:
            if config.security == "starttls":
                server.starttls(context=context)
            if config.username:
                server.login(config.username, config.password)
        except BaseException:
            with contextlib.suppress(Exception):
                server.close()
            raise
        return server

    def check(self, config: SmtpConfig) -> None:
        """Log in to the mail server without sending anything; EmailError says what's wrong."""
        try:
            with self._connect(config):
                pass
        except Exception as exc:
            raise EmailError(explain_smtp_error(exc, config)) from exc

    def _send_sync(self, message: EmailMessage, config: SmtpConfig) -> None:
        with self._connect(config) as server:
            server.send_message(message)

    async def send(self, notification: Notification) -> None:
        config = self.config()
        if config is None:
            raise EmailError("Email isn't set up.")
        try:
            await asyncio.to_thread(self._send_sync, self.build(notification, config), config)
        except Exception as exc:
            raise EmailError(explain_smtp_error(exc, config)) from exc


def explain_smtp_error(exc: BaseException, config: SmtpConfig) -> str:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return (
            "The mail server refused the password. Gmail, Outlook, Yahoo and iCloud need an "
            "app password, not the one you sign in with."
        )
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return f"{config.host} doesn't offer a secure connection on port {config.port}."
    if isinstance(exc, ssl.SSLError):
        return f"The secure connection to {config.host} failed ({exc.reason or exc})."
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "The mail server refused the address to send to."
    if isinstance(exc, (OSError, smtplib.SMTPServerDisconnected)):
        return f"Couldn't connect to {config.host} on port {config.port}."
    return f"The mail server said: {exc}"[:300]


class VapidKeys:
    """The server's web push identity, generated on first use."""

    def __init__(self, store: Store) -> None:
        pem = store.get_kv("vapid_private_pem")
        self.vapid = None
        if isinstance(pem, str) and pem:
            try:
                self.vapid = Vapid02.from_pem(pem.encode())
            except Exception:
                # Browsers subscribed with the old key can't receive pushes signed
                # with a new one, so they have to subscribe again.
                log.warning("The stored web push key is damaged; making a new one")
                store.clear_push_subscriptions()
        if self.vapid is None:
            self.vapid = Vapid02()
            self.vapid.generate_keys()
            store.set_kv("vapid_private_pem", self.vapid.private_pem().decode())
        raw = self.vapid.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        self.public_key = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class WebPushChannel:
    name = "webpush"
    label = "Pop-ups (this computer and phone)"

    def __init__(self, store: Store, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self._keys: VapidKeys | None = None

    @property
    def keys(self) -> VapidKeys:
        if self._keys is None:
            self._keys = VapidKeys(self.store)
        return self._keys

    @property
    def configured(self) -> bool:
        return bool(self.store.push_subscriptions())

    def payload(self, notification: Notification) -> str:
        actions = []
        if "done" in notification.actions:
            actions.append({"action": "done", "title": "Done"})
        if "snooze" in notification.actions:
            title = f"Snooze {snooze_label(notification.snooze_minutes)}"
            actions.append({"action": "snooze", "title": title})
        # Push services accept about 4 KB, so keep the text short (and UTF-8, not \u escapes).
        return json.dumps(
            {
                "title": _one_line(f"{ICONS.get(notification.kind, '')} {notification.title}", 150),
                "body": notification.body[:400],
                "url": notification.url or "/",
                "tag": f"reminder-{notification.reminder_id}" if notification.reminder_id else None,
                "actions": actions,
                "links": notification.actions,
            },
            ensure_ascii=False,
        )

    def _send_sync(self, data: str) -> tuple[int, list[str]]:
        delivered = 0
        errors: list[str] = []
        for subscription in self.store.push_subscriptions():
            try:
                webpush(
                    subscription_info=subscription,
                    data=data,
                    vapid_private_key=self.keys.vapid,
                    vapid_claims={"sub": self.settings.vapid_subject},
                    timeout=20,
                    ttl=24 * 3600,
                )
                delivered += 1
            except WebPushException as exc:
                status = getattr(exc.response, "status_code", None)
                if status in (404, 410):
                    log.info("Removing expired push subscription")
                    self.store.remove_push_subscription(subscription["endpoint"])
                    errors.append("a browser subscription had expired and was removed")
                else:
                    errors.append(f"push service returned {status or exc}")
            except Exception as exc:  # network trouble or a malformed subscription
                log.warning("Web push failed: %s", exc)
                errors.append(f"push failed ({type(exc).__name__})")
        return delivered, errors

    async def send(self, notification: Notification) -> None:
        delivered, errors = await asyncio.to_thread(self._send_sync, self.payload(notification))
        if not delivered:
            raise RuntimeError("; ".join(errors) or "no browser is subscribed")
        if errors:
            log.info("Web push partially failed: %s", "; ".join(errors))
