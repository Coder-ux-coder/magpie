"""Command line: ``magpie serve`` and a few helpers."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

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
            settings.model if core.brain.available else "set ANTHROPIC_API_KEY",
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
    import sqlite3
    from datetime import datetime
    from pathlib import Path

    if not settings.db_path.exists():
        print(f"No database at {settings.db_path}", file=sys.stderr)
        return 1
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = Path(args.path) if args.path else settings.data_dir / f"backup-{stamp}.db"
    if target.is_dir():
        target = target / f"magpie-backup-{stamp}.db"
    source = sqlite3.connect(settings.db_path)
    destination = sqlite3.connect(target)
    try:
        with destination:
            source.backup(destination)
    finally:
        destination.close()
        source.close()
    print(f"Backed up to {target}")
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

    mcp = sub.add_parser("mcp", help="MCP server so Claude Desktop/Code can use your notes")
    mcp.add_argument("--url", default=None, help="your Magpie URL (or MAGPIE_URL)")
    mcp.add_argument("--token", default=None, help="API token (or MAGPIE_API_TOKEN)")
    mcp.set_defaults(func=cmd_mcp)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args(["serve", *(argv or [])])
    settings = Settings()
    _setup_logging(settings.log_level)
    sys.exit(args.func(settings, args))
