"""Automatic updates for the installed app (Windows).

Every few hours the app reads latest.json from the newest GitHub release. When it names
a newer version, the installer is downloaded, its SHA-256 checked, and, once Magpie is
idle (not taking a note), run silently; it replaces the app and starts it again.
"""

from __future__ import annotations

import hashlib
import logging
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2

from magpie.desktop.paths import REPOSITORY, frozen

log = logging.getLogger(__name__)

LATEST = f"https://github.com/{REPOSITORY}/releases/latest/download/latest.json"
FIRST_CHECK = 180.0  # seconds after start
EVERY = 6 * 3600.0
MAX_SIZE = 600 * 1024 * 1024


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    sha256: str
    size: int


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", str(version))[:4])


def parse_latest(data: Any) -> Release | None:
    """The Windows installer from latest.json, or None when it doesn't make sense."""
    if not isinstance(data, dict):
        return None
    windows = data.get("windows")
    if not isinstance(windows, dict):
        return None
    version, url, digest = data.get("version"), windows.get("url"), windows.get("sha256")
    size = windows.get("size")
    if not (
        isinstance(version, str)
        and version_key(version)
        and isinstance(url, str)
        and url.startswith(f"https://github.com/{REPOSITORY}/releases/download/")
        and isinstance(digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", digest)
        and isinstance(size, int)
        and 0 < size <= MAX_SIZE
    ):
        return None
    return Release(version, url, digest, size)


def newer(release: Release, current: str) -> bool:
    return version_key(release.version) > version_key(current)


class Updater:
    def __init__(
        self,
        current: str,
        folder: Path,
        *,
        idle: Callable[[], bool],
        quit_app: Callable[[], None],
        http: httpx2.Client | None = None,
    ) -> None:
        self.current = current
        self.folder = folder
        self.idle = idle
        self.quit_app = quit_app
        self.http = http or httpx2.Client(timeout=60, follow_redirects=True)
        self._stop = threading.Event()

    @staticmethod
    def supported() -> bool:
        return frozen() and sys.platform == "win32"

    def check(self) -> Release | None:
        response = self.http.get(LATEST)
        if response.status_code != 200:
            return None
        try:
            release = parse_latest(response.json())
        except ValueError:
            return None
        return release if release and newer(release, self.current) else None

    def download(self, release: Release) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        target = self.folder / f"MagpieSetup-{release.version}.exe"
        part = target.with_suffix(".part")
        digest = hashlib.sha256()
        size = 0
        try:
            with self.http.stream("GET", release.url) as response, open(part, "wb") as handle:
                if response.status_code != 200:
                    raise RuntimeError(f"Download failed ({response.status_code}).")
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > release.size:
                        raise RuntimeError("The download is larger than expected.")
                    digest.update(chunk)
                    handle.write(chunk)
            if size != release.size or digest.hexdigest() != release.sha256:
                raise RuntimeError("The download doesn't match the release; not installing it.")
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        part.replace(target)
        return target

    def install(self, installer: Path) -> None:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        subprocess.Popen(
            [str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"],
            creationflags=flags,
            close_fds=True,
        )
        self.quit_app()

    def run(self) -> None:
        if not self.supported():
            return
        delay = FIRST_CHECK
        while not self._stop.wait(delay):
            delay = EVERY
            try:
                release = self.check()
                if release is None:
                    continue
                log.info("Magpie %s is available; downloading it", release.version)
                installer = self.download(release)
                while not self.idle():  # never in the middle of a note
                    if self._stop.wait(60):
                        return
                log.info("Installing Magpie %s", release.version)
                self.install(installer)
                return
            except Exception:
                log.warning("Checking for an update failed", exc_info=True)

    def start(self) -> None:
        threading.Thread(target=self.run, name="magpie-updates", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
