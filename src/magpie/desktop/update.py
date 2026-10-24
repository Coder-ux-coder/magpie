"""Keeping the installed app up to date: by itself, and from the update button.

Every few hours, or when the update button asks, the app reads latest.json from the newest
GitHub release. When it names a newer version, the download for this kind of computer is
fetched and its size and SHA-256 checked. It's installed once Magpie is idle (not taking
a note), or straight away from the button:

- Windows: MagpieSetup.exe runs silently; it replaces the app and starts it again.
- Mac: Magpie.app comes in a zip. A small script waits for Magpie to quit, puts the new
  app where the old one was (putting the old one back if that fails), and opens it again.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
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
# What each kind of computer downloads (the key in latest.json, and the file it's kept as).
DOWNLOADS = {"windows": "MagpieSetup-{version}.exe", "mac": "Magpie-{version}-mac.zip"}
SWAP = """\
#!/bin/sh
# Magpie's update on a Mac: wait for Magpie to quit, put the new app where the old one
# was (the old one goes back if that fails), and open it again.
pid="$1"; app="$2"; new="$3"
i=0
while kill -0 "$pid" 2>/dev/null && [ "$i" -lt 120 ]; do sleep 0.5; i=$((i + 1)); done
if mv "$app" "$app.old"; then
  if mv "$new" "$app"; then rm -rf "$app.old"; else mv "$app.old" "$app"; fi
fi
xattr -dr com.apple.quarantine "$app" 2>/dev/null
open "$app" --args --background
"""


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    sha256: str
    size: int


def this_system() -> str | None:
    return {"win32": "windows", "darwin": "mac"}.get(sys.platform)


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", str(version))[:4])


def parse_latest(data: Any, system: str = "windows") -> Release | None:
    """This computer's download from latest.json, or None when it doesn't make sense."""
    if not isinstance(data, dict):
        return None
    download = data.get(system)
    if not isinstance(download, dict):
        return None
    version, url, digest = data.get("version"), download.get("url"), download.get("sha256")
    size = download.get("size")
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


def mac_bundle() -> Path | None:
    """The Magpie.app this copy runs from (the installed app on a Mac)."""
    for folder in Path(sys.executable).resolve().parents:
        if folder.suffix == ".app":
            return folder
    return None


class Updater:
    """Finds, downloads and installs new versions; ``status()`` is for the update button.

    ``state`` is one of: idle, checking, current, downloading, ready, installing, error,
    unsupported.
    """

    def __init__(
        self,
        current: str,
        folder: Path,
        *,
        idle: Callable[[], bool],
        quit_app: Callable[[], None],
        system: str | None = None,
        http: httpx2.Client | None = None,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.current = current
        self.folder = folder
        self.idle = idle
        self.quit_app = quit_app
        self.system = system if system is not None else this_system()
        self.http = http or httpx2.Client(timeout=60, follow_redirects=True)
        self.on_change = on_change or (lambda: None)
        self.state = "idle"
        self.message = ""
        self.progress = 0.0
        self.available: Release | None = None
        self.checked = 0.0  # when it last looked (time.time())
        self.asked = False  # the last look came from the update button
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._now = threading.Event()  # install as soon as it's here

    # ---------------------------------------------------------------- status

    def supported(self) -> bool:
        return self.why_not() == ""

    def why_not(self) -> str:
        """Why this copy can't update itself ("" when it can)."""
        if not frozen() or self.system not in DOWNLOADS:
            return "This copy of Magpie runs from Python: update it the way it was installed."
        if self.system == "mac":
            bundle = mac_bundle()
            if bundle is None or "AppTranslocation" in str(bundle):
                return "Move Magpie to your Applications folder, then open it from there."
            if not os.access(bundle.parent, os.W_OK):
                return f"Magpie can't replace itself in {bundle.parent}."
        return ""

    def status(self) -> dict[str, Any]:
        return {
            "version": self.current,
            "supported": self.supported(),
            "state": self.state,
            "available": self.available.version if self.available else None,
            "progress": round(self.progress, 2),
            "message": self.message,
            "checked": int(self.checked) or None,
        }

    def _set(self, state: str, message: str = "", progress: float | None = None) -> None:
        self.state, self.message = state, message
        if progress is not None:
            self.progress = progress
        self.on_change()

    # ------------------------------------------------------------ the button

    def check_now(self) -> None:
        """Look for an update now (and say what was found)."""
        self.asked = True
        self._wake.set()

    def install_now(self) -> None:
        """Install the update as soon as it's downloaded, without waiting for a quiet moment."""
        self._now.set()
        self.check_now()

    @property
    def installing_now(self) -> bool:
        return self._now.is_set()

    # --------------------------------------------------------------- the work

    def check(self) -> Release | None:
        response = self.http.get(LATEST)
        if response.status_code != 200:
            raise RuntimeError(f"GitHub answered {response.status_code}.")
        try:
            release = parse_latest(response.json(), self.system or "")
        except ValueError:
            release = None
        if release is None:
            raise RuntimeError("The latest release doesn't describe an update for this computer.")
        return release if newer(release, self.current) else None

    def download(self, release: Release) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        target = self.folder / DOWNLOADS[self.system or "windows"].format(version=release.version)
        if target.is_file() and _matches(target, release):
            return target  # downloaded before
        part = target.with_name(target.name + ".part")
        digest = hashlib.sha256()
        size = 0
        try:
            with self.http.stream("GET", release.url) as response, open(part, "wb") as handle:
                if response.status_code != 200:
                    raise RuntimeError(f"The download failed ({response.status_code}).")
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > release.size:
                        raise RuntimeError("The download is larger than expected.")
                    digest.update(chunk)
                    handle.write(chunk)
                    self.progress = size / release.size
            if size != release.size or digest.hexdigest() != release.sha256:
                raise RuntimeError("The download doesn't match the release; not installing it.")
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        part.replace(target)
        return target

    def install(self, package: Path) -> None:
        if self.system == "mac":
            self._install_mac(package)
        else:
            self._install_windows(package)
        self.quit_app()

    def _install_windows(self, installer: Path) -> None:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        subprocess.Popen(
            [str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"],
            creationflags=flags,
            close_fds=True,
        )

    def _install_mac(self, archive: Path) -> None:
        bundle = mac_bundle()
        if bundle is None:
            raise RuntimeError("Magpie isn't running from Magpie.app.")
        staging = self.folder / "next"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        subprocess.run(
            ["/usr/bin/ditto", "-x", "-k", str(archive), str(staging)],
            check=True,
            capture_output=True,
            timeout=600,
        )
        new = staging / "Magpie.app"
        if not (new / "Contents" / "MacOS" / "Magpie").is_file():
            raise RuntimeError("The update doesn't contain Magpie.app.")
        script = staging / "swap.sh"
        script.write_text(SWAP, encoding="utf-8")
        subprocess.Popen(
            ["/bin/sh", str(script), str(os.getpid()), str(bundle), str(new)],
            start_new_session=True,  # carries on after Magpie quits
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def run(self) -> None:
        reason = self.why_not()
        if reason:
            self._set("unsupported", reason)
            return
        delay = FIRST_CHECK
        while True:
            self._wake.wait(delay)
            if self._stop.is_set():
                return
            self._wake.clear()
            self.asked, asked = False, self.asked
            delay = EVERY
            try:
                self._set("checking", "Checking for updates…")
                release = self.check()
                self.checked = time.time()
                self.asked = asked
                if release is None:
                    self._set("current", f"Magpie {self.current} is the latest version.")
                    continue
                self.available = release
                self._set("downloading", f"Downloading Magpie {release.version}…", 0.0)
                package = self.download(release)
                self._set(
                    "ready",
                    f"Magpie {release.version} is ready. It installs itself when you're not "
                    "taking a note, or now with Update now.",
                    1.0,
                )
                while not (self._now.is_set() or self.idle()):  # never in the middle of a note
                    self._wake.wait(30)
                    self._wake.clear()
                    if self._stop.is_set():
                        return
                log.info("Installing Magpie %s", release.version)
                self._set("installing", f"Installing Magpie {release.version}…")
                self.install(package)
                return
            except Exception as exc:
                log.warning("Updating failed: %s", exc)
                self.asked = asked
                self._set("error", f"Couldn't update: {exc}")
                self._now.clear()

    def start(self) -> None:
        threading.Thread(target=self.run, name="magpie-updates", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()


def _matches(path: Path, release: Release) -> bool:
    if path.stat().st_size != release.size:
        return False
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest() == release.sha256
