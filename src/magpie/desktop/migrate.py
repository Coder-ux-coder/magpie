"""Bring notes and settings over from Magpie running in Docker (versions before the app).

On the app's first start, if this computer has the Docker version, its database is copied
(safely, with SQLite's backup) and its settings (AI keys, Telegram, email, …) are kept,
without any password. The old Magpie is then stopped so it doesn't send reminders twice;
its data stays in Docker untouched, in case it's wanted again.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Settings worth keeping; Docker's own (data folder, address, password) are not.
KEEP = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "MAGPIE_API_TOKEN",
    "MAGPIE_SECRET_KEY",
    "MAGPIE_TIMEZONE",
    "MAGPIE_BASE_URL",
    "MAGPIE_BRAIN",
    "MAGPIE_MODEL",
    "MAGPIE_EFFORT",
    "MAGPIE_WEB_SEARCH",
    "MAGPIE_FALLBACKS",
    "MAGPIE_AI_API_KEY",
    "MAGPIE_AI_BASE_URL",
    "MAGPIE_STT_API_KEY",
    "MAGPIE_STT_BASE_URL",
    "MAGPIE_STT_MODEL",
    "MAGPIE_STT_LANGUAGE",
    "MAGPIE_TELEGRAM_BOT_TOKEN",
    "MAGPIE_TELEGRAM_CHAT_ID",
    "MAGPIE_NTFY_TOPIC",
    "MAGPIE_NTFY_SERVER",
    "MAGPIE_NTFY_TOKEN",
    "MAGPIE_SMTP_HOST",
    "MAGPIE_SMTP_PORT",
    "MAGPIE_SMTP_USERNAME",
    "MAGPIE_SMTP_PASSWORD",
    "MAGPIE_SMTP_FROM",
    "MAGPIE_SMTP_SECURITY",
    "MAGPIE_EMAIL_TO",
    "MAGPIE_WEBHOOK_URL",
    "MAGPIE_WEBHOOK_FORMAT",
    "MAGPIE_VAPID_SUBJECT",
    "MAGPIE_ALLOW_PRIVATE_URLS",
}
DOCKER_DESKTOP = {
    "win32": Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe"),
    "darwin": Path("/Applications/Docker.app"),
}


@dataclass
class Moved:
    notes: int = 0
    settings: int = 0
    stopped: int = 0


class Docker:
    def __init__(self, exe: str) -> None:
        self.exe = exe

    def run(self, *args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.run(
            [self.exe, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=flags,
            check=False,
        )

    def ok(self, *args: str, timeout: float = 60) -> bool:
        try:
            return self.run(*args, timeout=timeout).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False


def find_docker() -> Docker | None:
    exe = shutil.which("docker")
    if exe is None and sys.platform == "win32":
        candidate = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
        exe = str(candidate) if candidate.exists() else None
    return Docker(exe) if exe else None


def ensure_running(
    docker: Docker, *, wait: float = 150.0, tell: Callable[[str], None] | None = None
) -> bool:
    """Docker answers, starting Docker Desktop first if it's installed but not running."""
    if docker.ok("info", timeout=20):
        return True
    app = DOCKER_DESKTOP.get(sys.platform)
    if app is None or not app.exists():
        return False
    log.info("Starting Docker Desktop to bring over the notes")
    if tell is not None:
        tell("Starting Docker to look for notes from the old Magpie. This takes a minute.")
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-g", str(app)])
        else:
            subprocess.Popen([str(app)], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    except OSError:
        return False
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(5)
        if docker.ok("info", timeout=20):
            return True
    return False


def containers(docker: Docker) -> list[dict[str, Any]]:
    """Magpie containers, the running one (then the newest) first."""
    found: dict[str, dict[str, Any]] = {}
    for selector in ("label=com.docker.compose.service=magpie", "ancestor=magpie:local"):
        result = docker.run("ps", "-a", "--filter", selector, "--format", "{{json .}}")
        for line in result.stdout.splitlines() if result.returncode == 0 else []:
            with contextlib.suppress(ValueError):
                item = json.loads(line)
                if "magpie" in str(item.get("Image", "")).lower():
                    found[item["ID"]] = item
    return sorted(found.values(), key=lambda c: (c.get("State") != "running", c.get("ID", "")))


def labels(item: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in str(item.get("Labels", "")).split(","):
        key, _, value = pair.partition("=")
        if key:
            out[key.strip()] = value.strip()
    return out


def copy_database(docker: Docker, cid: str, target: Path) -> int:
    """Copy the container's database to ``target``; returns how many notes it holds."""
    with tempfile.TemporaryDirectory() as temp:
        local = Path(temp) / "magpie.db"
        copied = False
        if docker.ok("exec", cid, "magpie", "backup", "/data/.magpie-move.db", timeout=120):
            copied = docker.ok("cp", f"{cid}:/data/.magpie-move.db", str(local), timeout=300)
            docker.ok("exec", cid, "rm", "-f", "/data/.magpie-move.db")
        if not copied:  # an old image without `magpie backup`, or a stopped container
            if not docker.ok("cp", f"{cid}:/data/magpie.db", str(local), timeout=300):
                raise RuntimeError("Couldn't copy the database out of Docker.")
            for extra in ("magpie.db-wal", "magpie.db-shm"):
                docker.ok("cp", f"{cid}:/data/{extra}", str(Path(temp) / extra), timeout=300)
        source = sqlite3.connect(local)
        try:
            if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("The old database is damaged.")
            target.parent.mkdir(parents=True, exist_ok=True)
            dest = sqlite3.connect(target)
            try:
                source.backup(dest)
            finally:
                dest.close()
            try:
                return int(source.execute("SELECT COUNT(*) FROM notes").fetchone()[0])
            except sqlite3.Error:
                return 0
        finally:
            source.close()


def container_env(docker: Docker, cid: str) -> dict[str, str]:
    result = docker.run("inspect", cid, "--format", "{{json .Config.Env}}")
    if result.returncode != 0:
        return {}
    try:
        pairs = json.loads(result.stdout)
    except ValueError:
        return {}
    env: dict[str, str] = {}
    for pair in pairs if isinstance(pairs, list) else []:
        key, sep, value = str(pair).partition("=")
        if sep and key in KEEP and value:
            env[key] = value
    return env


def stop_old(docker: Docker, item: dict[str, Any]) -> int:
    """Stop the Docker Magpie (and its update helper) and keep it from coming back."""
    project = labels(item).get("com.docker.compose.project", "")
    ids = [item["ID"]]
    if project:
        result = docker.run("ps", "-q", "--filter", f"label=com.docker.compose.project={project}")
        ids += [cid for cid in result.stdout.split() if cid and cid not in ids]
    docker.ok("update", "--restart=no", *ids)
    docker.ok("stop", *ids, timeout=120)
    return len(ids)


def startup_shortcut() -> Path | None:
    """Where the Docker setup made Magpie open at sign-in (Windows' Startup folder)."""
    appdata = os.environ.get("APPDATA")
    if sys.platform != "win32" or not appdata:
        return None
    return Path(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup", "Magpie.lnk")


def forget_old_startup() -> bool:
    """Stop the old Magpie coming back at sign-in: its shortcut runs Docker's Magpie again.

    The app starts with Windows by itself (the registry's Run key), never from there.
    """
    shortcut = startup_shortcut()
    if shortcut is None or not shortcut.exists():
        return False
    try:
        shortcut.unlink()
    except OSError:
        log.warning("Couldn't remove %s", shortcut, exc_info=True)
        return False
    return True


def migrate(
    data_dir: Path,
    env_file: Path,
    *,
    start_docker: bool = True,
    tell: Callable[[str], None] | None = None,
) -> Moved | None:
    """Move notes from Docker into ``data_dir``. None when there's nothing to move.

    ``tell`` shows the user what's happening, as it can take a minute or two.
    """
    target = data_dir / "magpie.db"
    if target.exists():
        return None  # never overwrite notes the app already has
    docker = find_docker()
    if docker is None:
        return None
    running = ensure_running(docker, tell=tell) if start_docker else docker.ok("info", timeout=20)
    if not running:
        log.info("Docker isn't running; not bringing over notes from it")
        return None
    found = containers(docker)
    if not found:
        return None
    if tell is not None:
        tell("Bringing over your notes and settings from the old Magpie…")
    item = found[0]
    moved = Moved(notes=copy_database(docker, item["ID"], target))
    env = container_env(docker, item["ID"])
    if env:
        from dotenv import dotenv_values

        from magpie.cli import write_env

        existing = dotenv_values(env_file) if env_file.exists() else {}
        new = {k: v for k, v in env.items() if k not in existing}
        if new:
            write_env(env_file, new)
        moved.settings = len(new)
    moved.stopped = stop_old(docker, item)
    forget_old_startup()
    log.info("Brought over %d notes and %d settings from Docker", moved.notes, moved.settings)
    return moved
