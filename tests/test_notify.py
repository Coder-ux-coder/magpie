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
    title="Call James",
    body="Budget call",
    kind="reminder",
    url="https://notes.example.com/#/note/1",
    reminder_id=7,
    note_id=1,
    actions={"done": "https://notes.example.com/a/x", "snooze": "https://notes.example.com/a/y"},
    due="today 5:00 PM",
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
    assert body["title"] == "Call James"
    assert body["priority"] == 4
    assert body["click"] == NOTE.url
    assert [a["label"] for a in body["actions"]] == ["Done", "Snooze 1 hour"]
    assert request.headers["authorization"] == "Bearer tk"


@pytest.mark.parametrize(
    ("fmt", "key", "fragment"),
    [
        ("slack", "text", "*Call James*"),
        ("discord", "content", "**Call James**"),
        ("json", "title", "Call James"),
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
    if fmt == "json":
        assert body["event"] == "reminder" and body["due"] == NOTE.due


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
    assert "<b>Call James</b>" in body["text"]
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
    assert "Call James" in message["Subject"]
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


def test_email_subjects_never_contain_line_breaks():
    settings = Settings(
        _env_file=None, smtp_host="smtp.example.com", smtp_from="m@e.com", email_to="me@e.com"
    )
    note = Notification(title="Call James\nabout the budget\r\n" + "x" * 500, kind="reminder")
    message = EmailChannel(settings).build(note)
    assert "\n" not in message["Subject"] and len(message["Subject"]) <= 150


async def test_a_hanging_channel_times_out_without_blocking_the_rest(monkeypatch):
    import asyncio

    from magpie import notify

    class Slow:
        name, label, configured = "slow", "Slow", True

        async def send(self, notification):
            await asyncio.sleep(5)

    class Quick:
        name, label, configured = "quick", "Quick", True

        async def send(self, notification):
            return None

    monkeypatch.setattr(notify, "CHANNEL_TIMEOUT", 0.05)
    report = await Notifier([Slow(), Quick()], lambda: Prefs()).send(NOTE)
    assert report.results == {"slow": "timed out", "quick": "ok"}
    assert report.ok


def test_one_bad_push_subscription_does_not_stop_the_others(core, monkeypatch):
    from magpie.notify import channels

    core.store.add_push_subscription("https://push.example.com/bad", "bad", "bad", "ua")
    core.store.add_push_subscription("https://push.example.com/good", "good", "good", "ua")
    sent = []

    def fake_webpush(subscription_info, **kwargs):
        if subscription_info["endpoint"].endswith("bad"):
            raise ValueError("Could not deserialize key data")
        sent.append(subscription_info["endpoint"])

    monkeypatch.setattr(channels, "webpush", fake_webpush)
    channel = WebPushChannel(core.store, core.settings)
    delivered, errors = channel._send_sync(channel.payload(NOTE))
    assert delivered == 1 and sent == ["https://push.example.com/good"]
    assert errors == ["push failed (ValueError)"]


def test_push_payload_stays_small_with_long_accented_text(core):
    title = "Café with Mum — 11 o’clock ☕ " * 40
    long = Notification(title=title, body="Bring the crème brûlée 🍮 " * 200, kind="reminder")
    payload = WebPushChannel(core.store, core.settings).payload(long)
    assert len(payload.encode()) < 3000
    assert "Café" in payload and "☕" in payload  # UTF-8, not \\u escapes


def test_a_damaged_push_key_is_replaced(core):
    from magpie.notify.channels import VapidKeys

    core.store.add_push_subscription("https://push.example.com/a", "k", "a", "ua")
    core.store.set_kv("vapid_private_pem", "-----BEGIN garbage-----")
    keys = VapidKeys(core.store)
    assert len(keys.public_key) > 80
    assert core.store.push_subscriptions() == []  # they were made for the old key


async def test_snooze_buttons_follow_the_chosen_length(router):
    from dataclasses import replace

    from magpie.notify import snooze_label

    assert [snooze_label(m) for m in (15, 60, 120, 1440, 90)] == [
        "15 min",
        "1 hour",
        "2 hours",
        "1 day",
        "90 min",
    ]
    router.add("POST", "https://api.telegram.org", {"ok": True, "result": {}})
    channel = TelegramChannel(TelegramApi(client(router), "t"), lambda: 5)
    await channel.send(replace(NOTE, snooze_minutes=30))
    buttons = router.calls("sendMessage")[-1]["reply_markup"]["inline_keyboard"][0]
    assert buttons[1] == {"text": "⏰ 30 min", "callback_data": "r:snz:7:30"}
