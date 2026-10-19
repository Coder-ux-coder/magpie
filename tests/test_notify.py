import json

import httpx2
import pytest

from magpie.config import Prefs, Settings
from magpie.notify import Notification, Notifier
from magpie.notify.channels import (
    EmailChannel,
    NtfyChannel,
    TelegramChannel,
    WebhookChannel,
    WebPushChannel,
)
from magpie.telegram_api import TelegramApi, split_message

NOTE = Notification(
    title="Call Ahmed",
    body="Budget call",
    kind="reminder",
    url="https://notes.example.com/#/note/1",
    reminder_id=7,
    note_id=1,
    actions={"done": "https://notes.example.com/a/x", "snooze": "https://notes.example.com/a/y"},
)


def client(router):
    return httpx2.AsyncClient(transport=httpx2.MockTransport(router))


async def test_ntfy_payload(router):
    router.add("POST", "https://ntfy.sh", {"id": "1"})
    settings = Settings(_env_file=None, ntfy_topic="secret-topic", ntfy_token="tk")
    channel = NtfyChannel(client(router), settings)
    assert channel.configured
    await channel.send(NOTE)
    request = router.requests[-1]
    body = json.loads(request.content)
    assert body["topic"] == "secret-topic"
    assert body["title"] == "Call Ahmed"
    assert body["priority"] == 4
    assert body["click"] == NOTE.url
    assert [a["label"] for a in body["actions"]] == ["Done", "Snooze 1h"]
    assert request.headers["authorization"] == "Bearer tk"


@pytest.mark.parametrize(
    ("fmt", "key", "fragment"),
    [
        ("slack", "text", "*Call Ahmed*"),
        ("discord", "content", "**Call Ahmed**"),
        ("json", "title", "Call Ahmed"),
    ],
)
async def test_webhook_formats(router, fmt, key, fragment):
    router.add("POST", "https://hooks.example.com", {"ok": True})
    settings = Settings(
        _env_file=None, webhook_url="https://hooks.example.com/x", webhook_format=fmt
    )
    await WebhookChannel(client(router), settings).send(NOTE)
    body = json.loads(router.requests[-1].content)
    assert fragment in body[key]


async def test_webhook_errors_raise(router):
    router.add("POST", "https://hooks.example.com", lambda r: httpx2.Response(500))
    settings = Settings(_env_file=None, webhook_url="https://hooks.example.com/x")
    with pytest.raises(RuntimeError):
        await WebhookChannel(client(router), settings).send(NOTE)


async def test_telegram_channel_buttons(router):
    router.add("POST", "https://api.telegram.org", {"ok": True, "result": {"message_id": 1}})
    api = TelegramApi(client(router), "123:abc")
    channel = TelegramChannel(api, lambda: 42)
    await channel.send(NOTE)
    body = router.calls("sendMessage")[-1]
    assert body["chat_id"] == 42
    assert "<b>Call Ahmed</b>" in body["text"]
    keyboard = body["reply_markup"]["inline_keyboard"]
    assert [b["callback_data"] for b in keyboard[0]] == ["r:done:7", "r:snz:7:60", "r:tmr:7"]
    assert keyboard[1][0]["url"] == NOTE.url


async def test_telegram_escapes_html(router):
    router.add("POST", "https://api.telegram.org", {"ok": True, "result": {}})
    channel = TelegramChannel(TelegramApi(client(router), "t"), lambda: 1)
    await channel.send(Notification(title="<script>", body="a & b"))
    text = router.calls("sendMessage")[-1]["text"]
    assert "&lt;script&gt;" in text and "a &amp; b" in text


def test_telegram_unpaired_is_not_configured():
    assert not TelegramChannel(None, lambda: 1).configured
    assert not TelegramChannel(TelegramApi(None, "t"), lambda: None).configured


def test_split_long_messages():
    parts = split_message("line\n" * 2000, limit=100)
    assert all(len(part) <= 100 for part in parts)
    assert "".join(parts).count("line") == 2000


def test_email_message():
    settings = Settings(
        _env_file=None,
        smtp_host="smtp.example.com",
        smtp_from="m@example.com",
        email_to="me@example.com",
    )
    channel = EmailChannel(settings)
    assert channel.configured
    message = channel.build(NOTE)
    assert message["To"] == "me@example.com"
    assert "Call Ahmed" in message["Subject"]
    body = message.get_content()
    assert "Mark as done: https://notes.example.com/a/x" in body
    assert not EmailChannel(Settings(_env_file=None)).configured


def test_webpush_payload_and_keys(core):
    channel = WebPushChannel(core.store, core.settings)
    assert not channel.configured
    payload = json.loads(channel.payload(NOTE))
    assert payload["tag"] == "reminder-7"
    assert [a["action"] for a in payload["actions"]] == ["done", "snooze"]
    key = channel.keys.public_key
    assert len(key) > 80
    assert WebPushChannel(core.store, core.settings).keys.public_key == key  # persisted


async def test_notifier_report():
    class Good:
        name, label, configured = "good", "Good", True

        async def send(self, n):
            return None

    class Bad:
        name, label, configured = "bad", "Bad", True

        async def send(self, n):
            raise RuntimeError("nope")

    class Off:
        name, label, configured = "off", "Off", False

        async def send(self, n):  # pragma: no cover - never called
            raise AssertionError

    notifier = Notifier([Good(), Bad(), Off()], lambda: Prefs())
    report = await notifier.send(NOTE)
    assert report.ok
    assert report.results == {"good": "ok", "bad": "nope"}
    assert "bad: nope" in report.errors
    only = await notifier.send(NOTE, only="bad")
    assert not only.ok
    empty = await Notifier([Off()], lambda: Prefs()).send(NOTE)
    assert empty.no_channels
