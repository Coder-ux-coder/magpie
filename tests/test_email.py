"""Email reminders set up from the dashboard: presets, the login check and the message."""

from __future__ import annotations

import smtplib

import pytest

from magpie.notify import Notification
from magpie.notify.channels import EmailChannel, SmtpConfig, smtp_preset


class FakeSMTP:
    """Stands in for smtplib.SMTP: one app password works, one server is down."""

    sent: list = []
    logins: list = []

    def __init__(self, host, port, timeout=None, context=None):
        if host == "down.example.com":
            raise ConnectionRefusedError("refused")
        self.host, self.port, self.tls = host, port, False

    def starttls(self, context=None):
        self.tls = True

    def login(self, user, password):
        FakeSMTP.logins.append((self.host, self.port, self.tls, user))
        if password != "abcd efgh ijkl mnop":
            raise smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")

    def send_message(self, message):
        FakeSMTP.sent.append(message)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch):
    FakeSMTP.sent, FakeSMTP.logins = [], []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)


def test_common_addresses_need_no_server_settings():
    assert smtp_preset("someone@Gmail.com") == ("smtp.gmail.com", 587, "starttls")
    assert smtp_preset("someone@yahoo.com") == ("smtp.mail.yahoo.com", 465, "ssl")
    assert smtp_preset("someone@example.org") is None


def test_setting_up_email_checks_the_password_first(client):
    refused = client.put("/api/email", json={"address": "me@gmail.com", "password": "my-login"})
    assert refused.status_code == 422 and "app password" in refused.json()["detail"]
    assert client.get("/api/email").json()["configured"] is False

    saved = client.put(
        "/api/email", json={"address": "me@gmail.com", "password": "abcd efgh ijkl mnop"}
    ).json()
    assert saved["configured"] is True and saved["host"] == "smtp.gmail.com"
    assert saved["to"] == "me@gmail.com" and "abcd" not in str(saved)
    assert FakeSMTP.logins[-1] == ("smtp.gmail.com", 587, True, "me@gmail.com")
    assert FakeSMTP.sent == []  # checking sends nothing


def test_unknown_providers_and_unreachable_servers_are_explained(client):
    unknown = client.put("/api/email", json={"address": "me@work.example", "password": "x"})
    assert unknown.status_code == 422 and "mail server" in unknown.json()["detail"]
    down = client.put(
        "/api/email",
        json={"address": "me@work.example", "password": "x", "host": "down.example.com"},
    )
    assert down.status_code == 422 and "Couldn't connect" in down.json()["detail"]
    bad = client.put("/api/email", json={"address": "not an address", "password": "x"})
    assert bad.status_code == 422


def test_the_saved_password_is_kept_when_only_the_recipient_changes(client):
    body = {"address": "me@gmail.com", "password": "abcd efgh ijkl mnop"}
    client.put("/api/email", json=body)
    moved = client.put("/api/email", json={"address": "me@gmail.com", "to": "phone@icloud.com"})
    assert moved.status_code == 200 and moved.json()["to"] == "phone@icloud.com"
    other = client.put("/api/email", json={"address": "you@gmail.com"})
    assert other.status_code == 422  # a new address needs its own password


def test_a_test_email_and_a_reminder_email(client, core):
    client.put("/api/email", json={"address": "me@gmail.com", "password": "abcd efgh ijkl mnop"})
    report = client.post("/api/notify/test", json={"channel": "email"}).json()
    assert FakeSMTP.sent, report
    message = EmailChannel(core.settings, core.store).build(
        Notification(
            title="Call James",
            body="Call James about the Q3 budget\nDue today 10:00 AM.",
            kind="reminder",
            due="today 10:00 AM",
        )
    )
    assert message["Subject"] == "⏰ Call James · due today 10:00 AM"
    assert message["To"] == "me@gmail.com"
    content = message.get_content()
    assert "Call James about the Q3 budget" in content and "Due today 10:00 AM." in content


def test_email_can_be_turned_off_again(client):
    client.put("/api/email", json={"address": "me@gmail.com", "password": "abcd efgh ijkl mnop"})
    assert client.delete("/api/email").json()["configured"] is False


def test_server_settings_win_and_cannot_be_changed_here(client, core, monkeypatch):
    monkeypatch.setattr(core.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(core.settings, "email_to", "me@example.com")
    monkeypatch.setattr(core.settings, "smtp_from", "magpie@example.com")
    assert client.get("/api/email").json()["source"] == "env"
    refused = client.put("/api/email", json={"address": "me@gmail.com", "password": "x"})
    assert refused.status_code == 409


def test_a_wrong_password_later_says_what_to_do(core):
    core.store.set_kv(
        "email",
        {
            "address": "me@gmail.com",
            "password": "expired",
            "host": "smtp.gmail.com",
            "port": 587,
            "security": "starttls",
        },
    )
    channel = EmailChannel(core.settings, core.store)
    config = channel.config()
    assert isinstance(config, SmtpConfig) and config.to == "me@gmail.com"
    import asyncio

    with pytest.raises(Exception, match="app password"):
        asyncio.run(channel.send(Notification(title="Call James", kind="reminder")))
