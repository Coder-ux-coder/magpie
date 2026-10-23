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
                    "title": "Call James",
                    "created_at": "2026-10-19T10:00:00Z",
                    "tags": ["work"],
                }
            ]
        },
    )
    router.add(
        "GET",
        "https://magpie.example.com/api/reminders",
        {"reminders": [{"id": 3, "when": "today 5:00 PM", "message": "Call James", "rrule": None}]},
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

    added = _text(await server.call_tool("add_note", {"text": "call james at 5"}))
    assert "remind you at 5:00 PM" in added and "#7" in added
    sent = json.loads(router.requests[-1].content)
    assert sent == {"text": "call james at 5", "source": "mcp"}
    assert router.requests[-1].headers["authorization"] == "Bearer tok"

    found = _text(await server.call_tool("search_notes", {"query": "james"}))
    assert "#7 [task] Call James" in found
    upcoming = _text(await server.call_tool("upcoming_reminders", {}))
    assert "#3 today 5:00 PM: Call James" in upcoming
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
    assert "no password" in capsys.readouterr().out  # none is made up

    with pytest.raises(SystemExit) as code:
        cli.main(["doctor"])
    report = capsys.readouterr().out
    assert "AI" in report and "Time zone" in report and "this computer only" in report
    assert code.value.code == 1  # no notification channel is set up

    monkeypatch.setenv("MAGPIE_LOGIN", "true")  # a server that asks for one
    with pytest.raises(SystemExit):
        cli.main(["password"])
    generated = capsys.readouterr().out.strip()
    assert len(generated) >= 10

    # Like an older version's made-up password: gone on this computer alone ...
    monkeypatch.delenv("MAGPIE_LOGIN")
    with pytest.raises(SystemExit):
        cli.main(["password"])
    assert "no password" in capsys.readouterr().out
    # ... kept where other devices can reach Magpie, until the setup turns it off.
    monkeypatch.setenv("MAGPIE_BIND", "0.0.0.0")
    with pytest.raises(SystemExit):
        cli.main(["password"])
    assert capsys.readouterr().out.strip() == generated
    monkeypatch.setenv("MAGPIE_LOGIN", "false")
    with pytest.raises(SystemExit):
        cli.main(["password"])
    assert "no password" in capsys.readouterr().out


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


async def test_mcp_explains_when_magpie_is_unreachable(router):
    from magpie.mcp_server import build

    def down(request):
        raise httpx2.ConnectError("refused")

    router.add("GET", "https://magpie.example.com", down)
    client = httpx2.AsyncClient(
        base_url="https://magpie.example.com", transport=httpx2.MockTransport(router)
    )
    server = build("https://magpie.example.com", "mgp_x", client=client)
    from mcp.server.mcpserver.exceptions import ToolError

    # A ToolError's message is shown to Claude; any other exception is hidden as a crash.
    with pytest.raises(ToolError, match="Could not reach Magpie"):
        await server.call_tool("recent_notes", {"limit": -5})


def test_cli_explains_invalid_settings(monkeypatch, capsys):
    monkeypatch.setenv("MAGPIE_PORT", "eighty")
    with pytest.raises(SystemExit) as code:
        cli.main(["doctor"])
    assert code.value.code == 2
    assert "MAGPIE_PORT" in capsys.readouterr().err


def test_cli_explains_a_damaged_database(tmp_path, monkeypatch, capsys):
    (tmp_path / "magpie.db").write_bytes(b"this is not a database" * 100)
    monkeypatch.setenv("MAGPIE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MAGPIE_BRAIN", "fake")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as code:
        cli.main(["doctor"])
    assert code.value.code == 1
    assert "can't be opened" in capsys.readouterr().err


def test_backup_never_overwrites(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MAGPIE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MAGPIE_BRAIN", "fake")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        cli.main(["add", "keep me", "--no-review"])
    precious = tmp_path / "precious.txt"
    precious.write_text("do not touch")
    with pytest.raises(SystemExit) as code:
        cli.main(["backup", str(precious)])
    assert code.value.code == 1
    assert precious.read_text() == "do not touch"
    with pytest.raises(SystemExit) as code:
        cli.main(["backup", str(tmp_path / "missing" / "copy.db")])
    assert code.value.code == 1


def test_setup_writes_env_and_keeps_what_is_there(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "America/New_York")
    env = tmp_path / ".env"
    env.write_text("# mine\nMAGPIE_PORT=9000\n# MAGPIE_TIMEZONE=UTC\nOTHER=keep\n")
    with pytest.raises(SystemExit) as code:
        cli.main(["setup", "--yes", "--password", "a b c d e f", "--telegram-token", "1:x"])
    assert code.value.code == 0
    text = env.read_text()
    assert "MAGPIE_TIMEZONE=America/New_York" in text and "# MAGPIE_TIMEZONE" not in text
    assert "MAGPIE_PASSWORD='a b c d e f'" in text and "OTHER=keep" in text
    assert "MAGPIE_TELEGRAM_BOT_TOKEN=1:x" in text and "ANTHROPIC_API_KEY" not in text
    assert oct(env.stat().st_mode & 0o777) == "0o600"
    settings = Settings(_env_file=str(env))
    assert settings.password == "a b c d e f" and settings.port == 9000
    assert "localhost:9000" in capsys.readouterr().out


def test_setup_asks_nothing_and_never_sets_a_password(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    with pytest.raises(SystemExit) as code:
        cli.main(["setup", "--timezone", "Europe/London"])
    assert code.value.code == 0
    assert "No password" in capsys.readouterr().out
    text = env.read_text()
    assert "MAGPIE_BIND=127.0.0.1" in text and "MAGPIE_TIMEZONE=Europe/London" in text
    assert "MAGPIE_PASSWORD" not in text and "MAGPIE_LOGIN=false" in text
    assert Settings(_env_file=str(env)).login is False

    # Only a server other devices reach gets a password, and only when asked for one.
    with pytest.raises(SystemExit):
        cli.main(["setup", "--password", "correct horse"])
    text = env.read_text()
    assert "MAGPIE_PASSWORD='correct horse'" in text and "MAGPIE_BIND=0.0.0.0" in text
    assert "MAGPIE_LOGIN" not in text and Settings(_env_file=str(env)).login is True

    with pytest.raises(SystemExit) as code:
        cli.main(["setup", "--timezone", "Mars/Olympus"])
    assert code.value.code == 2


def test_password_command_without_a_password(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MAGPIE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MAGPIE_LOGIN", "false")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        cli.main(["password"])
    assert "no password" in capsys.readouterr().out.lower()


def test_detect_timezone(monkeypatch):
    monkeypatch.setenv("TZ", ":Europe/Dublin")
    assert cli.detect_timezone() == "Europe/Dublin"
    monkeypatch.setenv("TZ", "Not/AZone")
    assert cli.detect_timezone() != "Not/AZone"
    monkeypatch.setenv("TZ", "Etc/UTC")  # a server's default says nothing about the user
    assert cli.detect_timezone() not in ("Etc/UTC", "UTC")


def test_setup_without_a_known_zone_leaves_it_to_the_browser(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "detect_timezone", lambda: None)
    with pytest.raises(SystemExit) as code:
        cli.main(["setup"])
    assert code.value.code == 0
    assert "MAGPIE_TIMEZONE" not in (tmp_path / ".env").read_text()
    assert "computer's time zone" in capsys.readouterr().out


def test_rerunning_setup_removes_an_old_installs_password(tmp_path, monkeypatch):
    """Older versions asked for a password; Magpie has none now, and the rest is kept."""
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    env.write_text(
        "MAGPIE_PASSWORD=pw\nMAGPIE_LOGIN=true\nMAGPIE_BIND=0.0.0.0\n"
        "MAGPIE_TIMEZONE=Europe/London\nANTHROPIC_API_KEY=sk-ant-x\n"
    )
    with pytest.raises(SystemExit) as code:
        cli.main(["setup", "--yes"])
    assert code.value.code == 0
    text = env.read_text()
    assert "MAGPIE_PASSWORD" not in text and "MAGPIE_LOGIN=false" in text
    assert "MAGPIE_BIND=127.0.0.1" in text and "ANTHROPIC_API_KEY=sk-ant-x" in text
    assert "MAGPIE_TIMEZONE=Europe/London" in text
    assert Settings(_env_file=str(env)).login is False
