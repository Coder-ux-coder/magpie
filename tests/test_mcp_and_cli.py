import json

import httpx2
import pytest

from magpie import cli
from magpie.config import Settings

mcp = pytest.importorskip("mcp")


def _text(result) -> str:
    return "\n".join(getattr(block, "text", "") for block in result.content)


async def test_mcp_tools_talk_to_the_api(router):
    from magpie.mcp_server import build

    router.add(
        "POST",
        "https://magpie.example.com/api/notes",
        {
            "reply": "Saved. I'll remind you at 5:00 PM.",
            "note": {"id": 7},
        },
    )
    router.add(
        "GET",
        "https://magpie.example.com/api/notes",
        {
            "notes": [
                {
                    "id": 7,
                    "kind": "task",
                    "title": "Call Ahmed",
                    "created_at": "2026-10-19T10:00:00Z",
                    "tags": ["work"],
                }
            ]
        },
    )
    router.add(
        "GET",
        "https://magpie.example.com/api/reminders",
        {"reminders": [{"id": 3, "when": "today 5:00 PM", "message": "Call Ahmed", "rrule": None}]},
    )
    router.add(
        "POST", "https://magpie.example.com/api/reminders/3/snooze", {"when": "today 6:00 PM"}
    )
    client = httpx2.AsyncClient(
        base_url="https://magpie.example.com",
        transport=httpx2.MockTransport(router),
        headers={"Authorization": "Bearer tok"},
    )
    server = build("https://magpie.example.com", "tok", client=client)
    names = {tool.name for tool in await server.list_tools()}
    assert {
        "add_note",
        "search_notes",
        "get_note",
        "upcoming_reminders",
        "snooze_reminder",
    } <= names

    added = _text(await server.call_tool("add_note", {"text": "call ahmed at 5"}))
    assert "remind you at 5:00 PM" in added and "#7" in added
    sent = json.loads(router.requests[-1].content)
    assert sent == {"text": "call ahmed at 5", "source": "mcp"}
    assert router.requests[-1].headers["authorization"] == "Bearer tok"

    found = _text(await server.call_tool("search_notes", {"query": "ahmed"}))
    assert "#7 [task] Call Ahmed" in found
    upcoming = _text(await server.call_tool("upcoming_reminders", {}))
    assert "#3 today 5:00 PM: Call Ahmed" in upcoming
    snoozed = _text(await server.call_tool("snooze_reminder", {"reminder_id": 3, "minutes": 60}))
    assert "6:00 PM" in snoozed


def test_cli_add_and_password(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MAGPIE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MAGPIE_BRAIN", "fake")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit_code:
        cli.main(["add", "remind", "me", "to", "stretch", "in", "10", "minutes", "--no-review"])
    assert exit_code.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("#1 ")
    assert "remind you" in out

    with pytest.raises(SystemExit):
        cli.main(["password"])
    generated = capsys.readouterr().out.strip()
    assert len(generated) >= 10

    with pytest.raises(SystemExit) as code:
        cli.main(["doctor"])
    report = capsys.readouterr().out
    assert "Claude" in report and "Time zone" in report
    assert code.value.code == 1  # no notification channel is set up


def test_settings_from_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("MAGPIE_TELEGRAM_CHAT_ID", "")
    monkeypatch.setenv("MAGPIE_BASE_URL", "https://notes.example.com/")
    settings = Settings(_env_file=None)
    assert settings.anthropic_api_key == "sk-ant-test"
    assert settings.telegram_chat_id is None  # empty values are ignored
    assert settings.base_url == "https://notes.example.com"
    assert settings.secure_cookies


def test_cli_backup(tmp_path, monkeypatch, capsys):
    import sqlite3

    monkeypatch.setenv("MAGPIE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MAGPIE_BRAIN", "fake")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        cli.main(["add", "keep me safe", "--no-review"])
    target = tmp_path / "copy.db"
    with pytest.raises(SystemExit) as code:
        cli.main(["backup", str(target)])
    assert code.value.code == 0
    rows = sqlite3.connect(target).execute("SELECT raw_text FROM notes").fetchall()
    assert rows == [("keep me safe",)]
