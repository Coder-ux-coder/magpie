"""Command line: ``magpie serve`` and a few helpers."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import re
import sqlite3
import sys
from typing import TYPE_CHECKING

from pydantic import ValidationError

from magpie import __version__
from magpie.config import Settings

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("trafilatura").setLevel(logging.WARNING)


def cmd_serve(settings: Settings, args: argparse.Namespace) -> int:
    import uvicorn

    from magpie.app import create_app

    host = args.host or settings.host
    port = args.port or settings.port
    app = create_app(settings)
    shown = "localhost" if host in ("0.0.0.0", "::") else host
    print(f"Magpie {__version__} on http://{shown}:{port}")
    uvicorn.run(app, host=host, port=port, log_level=settings.log_level.lower(), proxy_headers=True)
    return 0


async def _with_core(settings: Settings, work):
    from magpie.core import Magpie

    core = Magpie(settings)
    try:
        return await work(core)
    finally:
        await core.pipeline.drain()
        await core.stop()


def cmd_add(settings: Settings, args: argparse.Namespace) -> int:
    text = " ".join(args.text).strip() or sys.stdin.read().strip()
    if not text:
        print("Nothing to add.", file=sys.stderr)
        return 1

    async def work(core):
        note = core.store.create_note(text, "cli")
        await core.pipeline.process(note.id, review=not args.no_review)
        await core.pipeline.drain()
        return core.store.get_note(note.id)

    note = asyncio.run(_with_core(settings, work))
    print(f"#{note.id} {note.display_title}")
    print(note.reply or note.error or "Saved.")
    return 0


def cmd_digest(settings: Settings, args: argparse.Namespace) -> int:
    kind = "weekly" if args.weekly else "daily"

    async def work(core):
        return await core.digests.run(kind, send=args.send, force=True)

    digest = asyncio.run(_with_core(settings, work))
    print(digest.content if digest else "Nothing to report.")
    return 0


def cmd_doctor(settings: Settings, args: argparse.Namespace) -> int:
    from magpie.telegram_api import TelegramError

    async def work(core):
        ok = True

        def line(good: bool, label: str, detail: str = "") -> None:
            nonlocal ok
            mark = "✔" if good else "✘"
            ok = ok and good
            print(f" {mark} {label}" + (f": {detail}" if detail else ""))

        print(f"Magpie {__version__}")
        line(True, "Data directory", str(settings.data_dir.resolve()))
        line(True, "Full-text search", "FTS5" if core.db.fts else "basic (no FTS5)")
        config = core.ai_config()
        line(
            core.brain.available,
            "AI",
            f"{config.provider.label}, {config.model}"
            if core.brain.available
            else f"{config.provider.label} isn't set up: choose an AI in Settings → AI",
        )
        if core.brain.available and args.live:
            from magpie.brain import BrainError

            try:
                triage = await core.brain.triage(
                    "remind me to stretch in 5 minutes", core.context()
                )
                line(True, "AI live test", triage.reply)
            except BrainError as exc:
                line(False, "AI live test", str(exc))
        line(
            True,
            "Opens on",
            "other devices too (with the password)" if settings.login else "this computer only",
        )
        line(True, "Speech-to-text", "on" if core.stt.enabled else "off (browser speech only)")
        line(True, "Time zone", core.prefs().timezone)
        line(True, "Public URL", settings.base_url or "not set (links in messages disabled)")
        if core.telegram_api is not None:
            try:
                me = await core.telegram_api.call("getMe")
                paired = core.telegram_chat_id()
                line(
                    True,
                    "Telegram bot",
                    f"@{me.get('username')}"
                    + ("" if paired else " (not paired yet: Settings → Telegram)"),
                )
            except TelegramError as exc:
                line(False, "Telegram bot", str(exc))
        configured = [c.label for c in core.notifier.configured()]
        line(
            bool(configured),
            "Notification channels",
            ", ".join(configured) or "none (set up Telegram, ntfy, push, email or a webhook)",
        )
        return ok

    good = asyncio.run(_with_core(settings, work))
    return 0 if good else 1


def cmd_password(settings: Settings, args: argparse.Namespace) -> int:
    async def work(core):
        return core.secrets

    secrets = asyncio.run(_with_core(settings, work))
    if not settings.login:
        print("Magpie has no password: it opens straight away on this computer.")
    elif not secrets.password_generated:
        print("The password is set by MAGPIE_PASSWORD.")
    else:
        print(secrets.password)
    return 0


def cmd_token(settings: Settings, args: argparse.Namespace) -> int:
    async def work(core):
        return core.secrets.rotate_api_token() if args.rotate else core.secrets.api_token

    print(asyncio.run(_with_core(settings, work)))
    return 0


def cmd_backup(settings: Settings, args: argparse.Namespace) -> int:
    """Copy the database safely while Magpie keeps running (SQLite online backup)."""
    from datetime import datetime
    from pathlib import Path

    if not settings.db_path.exists():
        print(f"No database at {settings.db_path}", file=sys.stderr)
        return 1
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = Path(args.path) if args.path else settings.data_dir / f"backup-{stamp}.db"
    if target.is_dir():
        target = target / f"magpie-backup-{stamp}.db"
    if target.exists():
        print(f"{target} already exists; choose another name.", file=sys.stderr)
        return 1
    if not target.parent.is_dir():
        print(f"The folder {target.parent} does not exist.", file=sys.stderr)
        return 1
    source = sqlite3.connect(settings.db_path)
    try:
        destination = sqlite3.connect(target)
        try:
            with destination:
                source.backup(destination)
        finally:
            destination.close()
    except sqlite3.Error as exc:
        print(f"The backup failed: {exc}", file=sys.stderr)
        target.unlink(missing_ok=True)
        return 1
    finally:
        source.close()
    print(f"Backed up to {target}")
    return 0


# ------------------------------------------------------------------ setup

_PLAIN_VALUE = re.compile(r"^[A-Za-z0-9_\-.:/@+=,]*$")


# Servers and containers default to UTC, so it says nothing about where the user is.
_UNKNOWN_ZONES = {"UTC", "Etc/UTC", "Etc/Universal", "Universal", "Zulu", "GMT", "Etc/GMT"}


def detect_timezone() -> str | None:
    """This computer's IANA time zone, or None if it can't tell (Windows, containers)."""
    from pathlib import Path

    from magpie.timeutil import is_valid_timezone

    candidates = [os.environ.get("TZ", "").lstrip(":")]
    with contextlib.suppress(OSError):
        candidates.append(Path("/etc/timezone").read_text().strip())
    try:
        target = str(Path("/etc/localtime").resolve())
        if "zoneinfo/" in target:
            candidates.append(target.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    return next(
        (zone for zone in candidates if zone not in _UNKNOWN_ZONES and is_valid_timezone(zone)),
        None,
    )


def _env_value(value: str) -> str:
    # Docker Compose and python-dotenv both read single-quoted values literally.
    return value if _PLAIN_VALUE.match(value) else f"'{value}'"


def write_env(path: Path, values: Mapping[str, str | None]) -> None:
    """Set KEY=value lines in an .env file (None removes one), keeping everything else."""
    lines = path.read_text().splitlines() if path.exists() else ["# Magpie settings"]
    pending = dict(values)
    out = []
    for line in lines:
        name = line.lstrip("# ").split("=", 1)[0].strip() if "=" in line else ""
        if name not in pending:
            out.append(line)
            continue
        value = pending[name]
        if value is None:
            if line.lstrip().startswith("#"):
                out.append(line)  # a commented example stays
            continue
        out.append(f"{name}={_env_value(value)}")
        del pending[name]
    out += [f"{name}={_env_value(value)}" for name, value in pending.items() if value is not None]
    path.write_text("\n".join(out) + "\n")
    with contextlib.suppress(OSError):  # e.g. a Windows folder shared with Docker
        path.chmod(0o600)


def _check_key(key: str) -> str | None:
    if not key:
        return None
    if any(char.isspace() for char in key):
        return "The key has spaces in it; paste it on its own."
    from magpie.brain import BrainError
    from magpie.brain.claude import ClaudeBrain

    print("  Checking the key with Anthropic…")
    try:
        asyncio.run(ClaudeBrain(Settings(_env_file=None, anthropic_api_key=key)).check())
    except BrainError as exc:
        if exc.retryable:
            print(f"  {exc} Keeping it anyway.")
            return None
        return str(exc)
    print("  The key works.")
    return None


def _current_env(path) -> dict[str, str]:
    from dotenv import dotenv_values

    try:
        return {k: v for k, v in dotenv_values(path).items() if isinstance(v, str)}
    except (OSError, ValueError):
        return {}


def _check_zone(zone: str) -> str | None:
    from magpie.timeutil import is_valid_timezone

    if not zone or is_valid_timezone(zone):
        return None  # empty: use the browser's time zone
    return "Not a time zone name, e.g. Europe/London (or press Enter to skip)."


def cmd_setup(settings: Settings, args: argparse.Namespace) -> int:
    """Write .env for a Docker or server install, without asking anything.

    Magpie has no password: it opens only on this computer, and running the setup again
    removes a password an older version asked for. --password is for a server that
    other devices reach over the network.
    """
    from pathlib import Path

    path = Path(args.env_file)
    print("Magpie setup\n")
    key = args.key or ""
    if key and (problem := _check_key(key)):
        print(problem, file=sys.stderr)
        return 2
    saved_zone = settings.timezone if "timezone" in settings.model_fields_set else None
    zone = args.timezone or saved_zone or detect_timezone() or ""
    if _check_zone(zone):
        print(f"Unknown time zone {zone!r}.", file=sys.stderr)
        return 2

    if args.password:
        values: dict[str, str | None] = {
            "MAGPIE_PASSWORD": args.password,
            "MAGPIE_LOGIN": None,
            "MAGPIE_BIND": "0.0.0.0",
        }
    else:  # MAGPIE_LOGIN=false also retires a password an older version made up
        values = {"MAGPIE_PASSWORD": None, "MAGPIE_LOGIN": "false", "MAGPIE_BIND": "127.0.0.1"}
    if zone:
        values["MAGPIE_TIMEZONE"] = zone
    if key:
        values["ANTHROPIC_API_KEY"] = key
    elif settings.brain != "claude":
        values["MAGPIE_BRAIN"] = settings.brain
    if args.telegram_token:
        values["MAGPIE_TELEGRAM_BOT_TOKEN"] = args.telegram_token
    write_env(path, values)
    print(f"Saved to {path.resolve()}")
    print(f"Open Magpie at http://localhost:{settings.port} once it's running.")
    if not args.password:
        print("No password: Magpie opens straight away, on this computer only.")
    if not zone:
        print("Magpie will use your computer's time zone when you first open it.")
    if not key:
        print("When Magpie opens, choose your AI: Claude, ChatGPT, Gemini, Ollama, or none.")
    if args.telegram_token:
        print("Then connect Telegram in Settings → Reminders.")
    return 0


def cmd_mcp(settings: Settings, args: argparse.Namespace) -> int:
    try:
        from magpie.mcp_server import run
    except ImportError:
        print("The MCP server needs the extra: pip install 'magpie-notes[mcp]'", file=sys.stderr)
        return 1
    run(args.url, args.token)
    return 0


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["desktop"]:  # the app has options and settings of its own
        from magpie.desktop.main import main as desktop

        desktop(argv[1:])
    parser = argparse.ArgumentParser(prog="magpie", description="Voice-first notes.")
    parser.add_argument("--version", action="version", version=f"magpie {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser(
        "desktop", help="run Magpie as an app: in the tray, always listening for “notes on”"
    )
    serve = sub.add_parser("serve", help="run the web app, scheduler and Telegram bot")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.set_defaults(func=cmd_serve)

    add = sub.add_parser("add", help="add a note from the command line")
    add.add_argument("text", nargs="*", help="note text (or pipe it in)")
    add.add_argument("--no-review", action="store_true", help="skip link reviews")
    add.set_defaults(func=cmd_add)

    digest = sub.add_parser("digest", help="print (and optionally send) a digest now")
    digest.add_argument("--weekly", action="store_true")
    digest.add_argument("--send", action="store_true", help="also send it to your channels")
    digest.set_defaults(func=cmd_digest)

    doctor = sub.add_parser("doctor", help="check the configuration")
    doctor.add_argument("--live", action="store_true", help="also make a test call to Claude")
    doctor.set_defaults(func=cmd_doctor)

    password = sub.add_parser("password", help="show the generated dashboard password")
    password.set_defaults(func=cmd_password)

    token = sub.add_parser("token", help="show (or --rotate) the API token")
    token.add_argument("--rotate", action="store_true")
    token.set_defaults(func=cmd_token)

    backup = sub.add_parser("backup", help="back up the database (safe while running)")
    backup.add_argument("path", nargs="?", help="file or folder (default: the data folder)")
    backup.set_defaults(func=cmd_backup)

    setup = sub.add_parser("setup", help="write the .env file for a Docker or server install")
    setup.add_argument("--env-file", default=".env", help="where to write (default: ./.env)")
    setup.add_argument("--key", help="Claude API key")
    setup.add_argument(
        "--password", help="only for a server other devices reach: require this password"
    )
    setup.add_argument("--no-password", action="store_true", help=argparse.SUPPRESS)
    setup.add_argument("--timezone", help="IANA time zone (default: this computer's)")
    setup.add_argument("--telegram-token", help="Telegram bot token")
    setup.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    setup.set_defaults(func=cmd_setup)

    mcp = sub.add_parser("mcp", help="MCP server so Claude Desktop/Code can use your notes")
    mcp.add_argument("--url", default=None, help="your Magpie URL (or MAGPIE_URL)")
    mcp.add_argument("--token", default=None, help="API token (or MAGPIE_API_TOKEN)")
    mcp.set_defaults(func=cmd_mcp)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args(["serve", *argv])
    try:
        settings = Settings()
    except ValidationError as exc:
        print("Magpie can't start: some settings are not valid.", file=sys.stderr)
        for error in exc.errors():
            name = "MAGPIE_" + "_".join(str(part) for part in error.get("loc", ())).upper()
            print(f"  {name}: {error.get('msg')}", file=sys.stderr)
        sys.exit(2)
    _setup_logging(settings.log_level)
    try:
        code = args.func(settings, args)
    except PermissionError as exc:
        print(
            f"Magpie can't write to {exc.filename or settings.data_dir}. Check that this user "
            "may write to the data folder (in Docker the volume must be writable by uid 1000).",
            file=sys.stderr,
        )
        code = 1
    except sqlite3.DatabaseError as exc:
        print(
            f"The database {settings.db_path} can't be opened ({exc}). If the file is damaged, "
            "move it away and restore a backup.",
            file=sys.stderr,
        )
        code = 1
    sys.exit(code)
