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

from pydantic import ValidationError

from magpie import __version__
from magpie.config import Settings


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
        line(
            core.brain.available,
            "Claude",
            settings.model
            if core.brain.available
            else "add an API key in Settings → Claude (or set ANTHROPIC_API_KEY)",
        )
        if core.brain.available and args.live:
            from magpie.brain import BrainError

            try:
                triage = await core.brain.triage(
                    "remind me to stretch in 5 minutes", core.context()
                )
                line(True, "Claude live test", triage.reply)
            except BrainError as exc:
                line(False, "Claude live test", str(exc))
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
    if not secrets.password_generated:
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


def detect_timezone() -> str:
    """The computer's IANA time zone, or UTC."""
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
    return next((zone for zone in candidates if zone and is_valid_timezone(zone)), "UTC")


def _env_value(value: str) -> str:
    # Docker Compose and python-dotenv both read single-quoted values literally.
    return value if _PLAIN_VALUE.match(value) else f"'{value}'"


def write_env(path, values: dict[str, str]) -> None:
    """Set KEY=value lines in an .env file, keeping everything else in it."""
    lines = path.read_text().splitlines() if path.exists() else ["# Magpie settings"]
    pending = dict(values)
    out = []
    for line in lines:
        name = line.lstrip("# ").split("=", 1)[0].strip() if "=" in line else ""
        if name in pending:
            out.append(f"{name}={_env_value(pending.pop(name))}")
        else:
            out.append(line)
    out += [f"{name}={_env_value(value)}" for name, value in pending.items()]
    path.write_text("\n".join(out) + "\n")
    path.chmod(0o600)


def _ask(prompt: str, default: str = "", *, secret: bool = False, check=None) -> str:
    import getpass

    shown = f" [{default}]" if default and not secret else ""
    while True:
        answer = (getpass.getpass if secret else input)(f"{prompt}{shown}: ").strip()
        answer = answer or default
        problem = check(answer) if check else None
        if not problem:
            return answer
        print(f"  {problem}")


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


def _check_password(password: str) -> str | None:
    if password and len(password) < 8:
        return "Use at least 8 characters (or press Enter to generate one)."
    if "'" in password:
        return "Please choose a password without a single quote (')."
    return None


def _check_zone(zone: str) -> str | None:
    from magpie.timeutil import is_valid_timezone

    return None if is_valid_timezone(zone) else "Not a time zone name, e.g. Asia/Karachi."


def cmd_setup(settings: Settings, args: argparse.Namespace) -> int:
    """Ask a few questions and write them to .env, so nobody has to edit it by hand."""
    import secrets
    from pathlib import Path

    path = Path(args.env_file)
    interactive = not args.yes and sys.stdin.isatty()
    print("Magpie setup: a few questions, then you're ready. Press Enter to accept [defaults].\n")
    if interactive:
        print("1. Your Claude API key, from https://console.anthropic.com/settings/keys")
        print("   (leave it empty to add it later in Settings, or to try Magpie offline)")
    key = (
        args.key
        if args.key is not None
        else (_ask("   API key", secret=True, check=_check_key) if interactive else "")
    )
    if interactive:
        print("\n2. A password for the dashboard (Enter to generate one)")
    password = (
        args.password
        if args.password is not None
        else (_ask("   Password", secret=True, check=_check_password) if interactive else "")
    )
    generated = not password
    password = password or secrets.token_urlsafe(9)
    zone = args.timezone or detect_timezone()
    if interactive and not args.timezone:
        print("\n3. Your time zone, for reminders")
        zone = _ask("   Time zone", zone, check=_check_zone)
    if _check_zone(zone):
        print(f"Unknown time zone {zone!r}.", file=sys.stderr)
        return 2
    telegram = args.telegram_token or ""
    if interactive and args.telegram_token is None:
        print(
            "\n4. Optional: a Telegram bot token, to get reminders and send notes from your phone"
        )
        print("   (talk to @BotFather in Telegram, send /newbot, paste the token; Enter to skip)")
        telegram = _ask("   Bot token")

    values = {"MAGPIE_PASSWORD": password, "MAGPIE_TIMEZONE": zone}
    if key:
        values["ANTHROPIC_API_KEY"] = key
    elif settings.brain != "claude":
        values["MAGPIE_BRAIN"] = settings.brain
    if telegram:
        values["MAGPIE_TELEGRAM_BOT_TOKEN"] = telegram
    write_env(path, values)
    port = settings.port
    print(f"\nSaved to {path.resolve()}")
    print(f"Open Magpie at http://localhost:{port} once it's running.")
    if generated:
        print(f"Your password: {password}")
    if not key:
        print("No API key yet: add it any time in Settings → Claude.")
    if telegram:
        print("Then connect Telegram in Settings → Notifications.")
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
    parser = argparse.ArgumentParser(prog="magpie", description="Voice-first notes with Claude.")
    parser.add_argument("--version", action="version", version=f"magpie {__version__}")
    sub = parser.add_subparsers(dest="command")

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

    setup = sub.add_parser("setup", help="answer a few questions and write the .env file")
    setup.add_argument("--env-file", default=".env", help="where to write (default: ./.env)")
    setup.add_argument("--key", help="Claude API key")
    setup.add_argument("--password", help="dashboard password (default: generate one)")
    setup.add_argument("--timezone", help="IANA time zone (default: this computer's)")
    setup.add_argument("--telegram-token", help="Telegram bot token")
    setup.add_argument("--yes", action="store_true", help="don't ask; use the defaults")
    setup.set_defaults(func=cmd_setup)

    mcp = sub.add_parser("mcp", help="MCP server so Claude Desktop/Code can use your notes")
    mcp.add_argument("--url", default=None, help="your Magpie URL (or MAGPIE_URL)")
    mcp.add_argument("--token", default=None, help="API token (or MAGPIE_API_TOKEN)")
    mcp.set_defaults(func=cmd_mcp)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args(["serve", *(argv or [])])
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
