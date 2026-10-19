"""Notification channels: Telegram, ntfy, web push, email and webhooks."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import smtplib
import ssl
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any

import httpx2
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid02
from pywebpush import WebPushException, webpush

from magpie.config import Settings
from magpie.notify import Notification
from magpie.store import Store
from magpie.telegram_api import TelegramApi, escape

log = logging.getLogger(__name__)

ICONS = {"reminder": "⏰", "digest": "☀️", "review": "📺", "test": "🔔", "info": "🐦"}


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
            buttons.append(
                [
                    {"text": "✅ Done", "callback_data": f"r:done:{rid}"},
                    {"text": "⏰ 1 hour", "callback_data": f"r:snz:{rid}:60"},
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
                    "label": "Snooze 1h",
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
            "actions": notification.actions,
        }

    async def send(self, notification: Notification) -> None:
        response = await self.http.post(
            self.settings.webhook_url or "", json=self.payload(notification), timeout=20
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Webhook returned HTTP {response.status_code}")


class EmailChannel:
    name = "email"
    label = "Email"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def configured(self) -> bool:
        s = self.settings
        return bool(s.smtp_host and s.email_to and (s.smtp_from or s.smtp_username))

    def build(self, notification: Notification) -> EmailMessage:
        s = self.settings
        message = EmailMessage()
        message["Subject"] = f"{ICONS.get(notification.kind, '')} {notification.title}".strip()
        message["From"] = s.smtp_from or s.smtp_username or ""
        message["To"] = s.email_to or ""
        lines = [notification.body or notification.title]
        if _public_url(notification.url):
            lines.append(f"Open in Magpie: {notification.url}")
        for label, key in (("Mark as done", "done"), ("Snooze 1 hour", "snooze")):
            if key in notification.actions:
                lines.append(f"{label}: {notification.actions[key]}")
        message.set_content("\n\n".join(lines))
        return message

    def _send_sync(self, message: EmailMessage) -> None:
        s = self.settings
        context = ssl.create_default_context()
        if s.smtp_security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                s.smtp_host or "", s.smtp_port, context=context, timeout=30
            )
        else:
            server = smtplib.SMTP(s.smtp_host or "", s.smtp_port, timeout=30)
        with server:
            if s.smtp_security == "starttls":
                server.starttls(context=context)
            if s.smtp_username:
                server.login(s.smtp_username, s.smtp_password or "")
            server.send_message(message)

    async def send(self, notification: Notification) -> None:
        await asyncio.to_thread(self._send_sync, self.build(notification))


class VapidKeys:
    """The server's web push identity, generated on first use."""

    def __init__(self, store: Store) -> None:
        pem = store.get_kv("vapid_private_pem")
        if pem:
            self.vapid = Vapid02.from_pem(pem.encode())
        else:
            self.vapid = Vapid02()
            self.vapid.generate_keys()
            store.set_kv("vapid_private_pem", self.vapid.private_pem().decode())
        raw = self.vapid.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        self.public_key = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class WebPushChannel:
    name = "webpush"
    label = "Browser push"

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
            actions.append({"action": "snooze", "title": "Snooze 1h"})
        return json.dumps(
            {
                "title": f"{ICONS.get(notification.kind, '')} {notification.title}".strip(),
                "body": notification.body[:500],
                "url": notification.url or "/",
                "tag": f"reminder-{notification.reminder_id}" if notification.reminder_id else None,
                "actions": actions,
                "links": notification.actions,
            }
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
        return delivered, errors

    async def send(self, notification: Notification) -> None:
        delivered, errors = await asyncio.to_thread(self._send_sync, self.payload(notification))
        if not delivered:
            raise RuntimeError("; ".join(errors) or "no browser is subscribed")
        if errors:
            log.info("Web push partially failed: %s", "; ".join(errors))
