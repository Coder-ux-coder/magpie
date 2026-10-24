"""Where the desktop app keeps its things, and its own small settings file."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MODEL_NAME = "vosk-model-small-en-us-0.15"
REPOSITORY = "Coder-ux-coder/magpie"


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @property
    def data_dir(self) -> Path:  # the notes database, as for the server
        return self.root / "data"

    @property
    def env_file(self) -> Path:  # settings brought over from an older Magpie, keys and so on
        return self.root / "magpie.env"

    @property
    def state_file(self) -> Path:
        return self.root / "desktop.json"

    @property
    def lock_file(self) -> Path:
        return self.root / "magpie.lock"

    @property
    def running_file(self) -> Path:
        return self.root / "running.json"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def webview_dir(self) -> Path:  # the window's own storage (theme, text size, …)
        return self.root / "webview"

    @property
    def models_dir(self) -> Path:
        return self.root / "models"

    @property
    def updates_dir(self) -> Path:
        return self.root / "updates"


def app_paths(home: Path | None = None) -> AppPaths:
    if home is not None:
        return AppPaths(home)
    if os.environ.get("MAGPIE_DESKTOP_HOME"):
        return AppPaths(Path(os.environ["MAGPIE_DESKTOP_HOME"]))
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return AppPaths(base / "Magpie")
    if sys.platform == "darwin":
        return AppPaths(Path.home() / "Library" / "Application Support" / "Magpie")
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return AppPaths(base / "magpie")


def frozen() -> bool:
    """Running as the installed app (PyInstaller), not from Python."""
    return bool(getattr(sys, "frozen", False))


def resources() -> Path:
    """Files shipped with the app: the speech model, icons."""
    if frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def find_model(paths: AppPaths) -> Path | None:
    candidates = [
        os.environ.get("MAGPIE_VOSK_MODEL"),
        resources() / "models" / MODEL_NAME,
        paths.models_dir / MODEL_NAME,
    ]
    for candidate in candidates:
        if candidate and (Path(candidate) / "am" / "final.mdl").exists():
            return Path(candidate)
    return None


MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
MODEL_MAX_BYTES = 200 * 1024 * 1024


def fetch_model(paths: AppPaths, http: Any = None) -> Path:
    """Download and unpack the speech model (about 40 MB) for a Python install."""
    import shutil
    import zipfile

    import httpx2

    target = paths.models_dir / MODEL_NAME
    if (target / "am" / "final.mdl").exists():
        return target
    paths.models_dir.mkdir(parents=True, exist_ok=True)
    part = paths.models_dir / f"{MODEL_NAME}.zip.part"
    unpacked = paths.models_dir / f".{MODEL_NAME}-unpacking"
    size = 0
    try:
        with (
            contextlib.nullcontext(http) if http else httpx2.Client(timeout=120) as client,
            client.stream("GET", MODEL_URL, follow_redirects=True) as response,
            open(part, "wb") as handle,
        ):
            if response.status_code != 200:
                raise RuntimeError(
                    f"The speech model couldn't be downloaded ({response.status_code})."
                )
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MODEL_MAX_BYTES:
                    raise RuntimeError("The speech model download is larger than expected.")
                handle.write(chunk)
        shutil.rmtree(unpacked, ignore_errors=True)
        with zipfile.ZipFile(part) as archive:
            for member in archive.infolist():
                name = Path(member.filename)
                if name.is_absolute() or ".." in name.parts or name.parts[:1] != (MODEL_NAME,):
                    continue  # only the model's own folder, nothing outside it
                archive.extract(member, unpacked)
    except (OSError, zipfile.BadZipFile, httpx2.HTTPError) as exc:
        raise RuntimeError(f"The speech model couldn't be downloaded: {exc}") from exc
    finally:
        part.unlink(missing_ok=True)
    if not (unpacked / MODEL_NAME / "am" / "final.mdl").exists():
        shutil.rmtree(unpacked, ignore_errors=True)
        raise RuntimeError("The speech model download was incomplete.")
    (unpacked / MODEL_NAME).replace(target)
    shutil.rmtree(unpacked, ignore_errors=True)
    return target


def launch_command(*args: str) -> list[str]:
    """How to start this app again (for start-up with the computer, and restarts)."""
    if frozen():
        return [sys.executable, *args]
    return [sys.executable, "-m", "magpie", "desktop", *args]


class DesktopState:
    """desktop.json: the app's own preferences (start with the computer, listening, …).

    A damaged or missing file never stops the app: it starts again from the defaults.
    """

    DEFAULTS: dict[str, Any] = {
        "autostart": True,  # start with the computer
        "listening": True,  # listen for the command word
        "port": 8000,
        "first_run": True,
        "migrated": False,
        "speech_v2": False,  # moved once to the most accurate speech model (0.7)
    }

    def __init__(self, path: Path) -> None:
        self.path = path
        self.values = dict(self.DEFAULTS)
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                self.values.update(
                    {k: v for k, v in stored.items() if type(v) is type(self.DEFAULTS.get(k, v))}
                )
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            log.warning("Ignoring an unreadable %s", path)

    def get(self, key: str) -> Any:
        return self.values.get(key, self.DEFAULTS.get(key))

    def set(self, **changes: Any) -> None:
        self.values.update(changes)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write a copy and swap it in, so a crash mid-write can't leave half a file.
        fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".desktop-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.values, handle, indent=2)
            os.replace(temp, self.path)
        except OSError:
            log.warning("Couldn't save %s", self.path, exc_info=True)
            with contextlib.suppress(OSError):
                os.unlink(temp)
