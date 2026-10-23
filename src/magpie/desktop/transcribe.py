"""Writing down what was said, accurately: Whisper (faster-whisper), offline on this computer.

The always-on listener (speech.py) uses a small, fast recogniser: good at spotting
“notes on” and at showing words as they're said, but it mishears a lot. When a note ends,
its sound is written down again by Whisper, which is far more accurate (with accents too)
and adds punctuation and capitals; that is the text that gets saved.

The Whisper model is downloaded once, in the background, into the app's own folder, which
updates keep. Until it's there, or if it can't be had, the listener's own words are used.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from magpie.desktop.paths import AppPaths
from magpie.desktop.phrases import (
    CANCEL_PHRASES,
    DEFAULT_STOP,
    DEFAULT_WAKE,
    STOP_ALIASES,
    WAKE_ALIASES,
    find_in_text,
    phrase_list,
    strip_leading_wake,
    tidy,
)

log = logging.getLogger(__name__)

RATE = 16000  # what Whisper listens at


@dataclass(frozen=True)
class WhisperModel:
    name: str
    repo: str  # where it's downloaded from (Hugging Face)
    size: int  # bytes to download, for the progress shown
    label: str


MODELS = {
    "small.en": WhisperModel(
        "small.en", "Systran/faster-whisper-small.en", 484_000_000, "Accurate (480 MB download)"
    ),
    "large-v3-turbo": WhisperModel(
        "large-v3-turbo",
        "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        1_620_000_000,
        "Most accurate, best with strong accents (1.6 GB download, slower)",
    ),
}
DEFAULT_MODEL = "small.en"
OFF = "off"  # the listener's own words, as they were shown
FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
IDLE_SECONDS = 300.0  # let go of the model (and its memory) after this long unused
RETRY_SECONDS = 900.0  # try a failed download again after this long
# What Whisper sometimes writes for silence or noise, not for anything said.
NOISE = re.compile(
    r"^(?:thank you|thanks for watching|you|bye|okay|so|um|uh|\.+|\W*)[.!?\s]*$", re.IGNORECASE
)


def installed() -> bool:
    """Whisper's library is there (always in the installed app; an extra for Python)."""
    try:
        return importlib.util.find_spec("faster_whisper") is not None
    except (ImportError, ValueError):
        return False


def model_folder(paths: AppPaths, name: str) -> Path:
    return paths.models_dir / f"whisper-{name}"


def complete(folder: Path) -> bool:
    return (folder / "model.bin").is_file() and (folder / "tokenizer.json").is_file()


def find_whisper(paths: AppPaths, name: str = DEFAULT_MODEL) -> Path | None:
    """The model's folder when it's all there (MAGPIE_WHISPER_MODEL can point elsewhere)."""
    for candidate in (os.environ.get("MAGPIE_WHISPER_MODEL"), model_folder(paths, name)):
        if candidate and complete(Path(candidate)):
            return Path(candidate)
    return None


def _download(repo: str, folder: Path, patterns: list[str]) -> None:
    from huggingface_hub import snapshot_download
    from huggingface_hub.utils import disable_progress_bars

    disable_progress_bars()  # the app has no console for them to write to
    snapshot_download(repo, local_dir=str(folder), allow_patterns=patterns)


def fetch_whisper(
    paths: AppPaths,
    name: str = DEFAULT_MODEL,
    progress: Callable[[float], None] | None = None,
    *,
    download: Callable[[str, Path, list[str]], None] = _download,
) -> Path:
    """Download a model into the app's folder; ``progress`` hears 0.0 to 1.0 on the way.

    It downloads into a ``.part`` folder first, so an interrupted download is picked up
    where it stopped and never mistaken for a whole model.
    """
    model = MODELS[name]
    target = model_folder(paths, name)
    if complete(target):
        return target
    partial = target.with_name(target.name + ".part")
    partial.mkdir(parents=True, exist_ok=True)
    done = threading.Event()

    def report() -> None:
        while not done.wait(1.0):
            try:  # files come and go as the download goes on
                got = sum(f.stat().st_size for f in partial.rglob("*") if f.is_file())
            except OSError:
                continue
            if progress is not None:
                progress(min(0.99, got / model.size))

    watcher = threading.Thread(target=report, name="magpie-whisper-progress", daemon=True)
    watcher.start()
    try:
        download(model.repo, partial, FILES)
    finally:
        done.set()
    if not complete(partial):
        raise RuntimeError("the download was incomplete")
    shutil.rmtree(partial / ".cache", ignore_errors=True)
    shutil.rmtree(target, ignore_errors=True)
    partial.replace(target)
    if progress is not None:
        progress(1.0)
    return target


def to_float(pcm: bytes, rate: int) -> Any:
    """16-bit sound as Whisper wants it: float32 samples at 16 kHz."""
    import numpy as np

    audio = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
    if rate != RATE and len(audio):
        duration = len(audio) / rate
        wanted = np.arange(int(duration * RATE)) / RATE
        audio = np.interp(wanted, np.arange(len(audio)) / rate, audio).astype(np.float32)
    return audio


def clean(text: str, wake: str | None = None, stop: str | None = None) -> str:
    """Whisper's text without the command words, which the sound can start or end with."""
    text = " ".join(str(text).split())
    if not text or NOISE.match(text):
        return ""
    said = strip_leading_wake(text, phrase_list(wake, DEFAULT_WAKE, WAKE_ALIASES))
    if said != tidy(text):  # “Notes on, buy milk.” -> “Buy milk.”
        said = said[:1].upper() + said[1:]
    text = said
    stops = [*phrase_list(stop, DEFAULT_STOP, STOP_ALIASES), *CANCEL_PHRASES]
    # Only a stop word at the very end: “notes off the record” in the middle stays.
    for _ in range(3):
        span = _last(text, stops)
        if span is None or tidy(text[span[1] :]).strip(" .,!?"):
            break
        text = text[: span[0]]
    text = tidy(text).rstrip(" ,;:-")
    return "" if NOISE.match(text) else text


def _last(text: str, phrases: list[str]) -> tuple[int, int] | None:
    """Where the last of the phrases is in the text."""
    found, offset = None, 0
    while (span := find_in_text(text[offset:], phrases)) is not None:
        found = (offset + span[0], offset + span[1])
        offset = found[1]
    return found


def _load_model(folder: Path, threads: int) -> Any:
    from faster_whisper import WhisperModel as Model

    return Model(
        str(folder), device="cpu", compute_type="int8", cpu_threads=threads, local_files_only=True
    )


class Transcriber:
    """One Whisper model, loaded when it's needed (a note has started) and let go when idle."""

    def __init__(
        self,
        folder: Path,
        *,
        threads: int | None = None,
        load: Callable[[Path, int], Any] = _load_model,
    ) -> None:
        self.folder = folder
        # Leave a core for the listener, which keeps going while a note is written down.
        self.threads = threads or max(1, min(8, (os.cpu_count() or 4) - 1))
        self._load_model = load
        self._model: Any = None
        self._lock = threading.Lock()
        self._used = 0.0
        self.problem = ""  # why it last failed to load (too little memory, say)

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                started = time.monotonic()
                try:
                    self._model = self._load_model(self.folder, self.threads)
                except Exception as exc:
                    self.problem = f"{type(exc).__name__}: {exc}"[:200]
                    raise
                self.problem = ""
                log.info("Whisper ready in %.1fs", time.monotonic() - started)
            self._used = time.monotonic()
            return self._model

    def warm(self) -> None:
        """Start loading now, so it's ready by the time the note ends."""
        if self._model is None:
            threading.Thread(target=self._load_quietly, name="magpie-whisper", daemon=True).start()

    def _load_quietly(self) -> None:
        try:
            self._load()
        except Exception:
            log.warning("Whisper couldn't be loaded", exc_info=True)

    def release_if_idle(self, idle: float = IDLE_SECONDS) -> None:
        with self._lock:
            if self._model is not None and time.monotonic() - self._used > idle:
                self._model = None
                log.info("Whisper let go after %.0f idle minutes", idle / 60)

    def transcribe(
        self,
        pcm: bytes,
        rate: int,
        *,
        wake: str | None = None,
        stop: str | None = None,
        budget: float | None = None,
    ) -> str:
        """The note's words. Raises TimeoutError past ``budget`` seconds (by default
        30 plus twice the note's length), so a slow computer falls back to the listener."""
        audio = to_float(pcm, rate)
        seconds = len(audio) / RATE
        if seconds < 0.3:  # too short to hold a word
            return ""
        started = time.monotonic()
        limit = budget if budget is not None else 30.0 + 2 * seconds
        model = self._load()
        segments, _info = model.transcribe(
            audio,
            language="en",
            beam_size=5,
            vad_filter=True,  # skips silence, where Whisper would otherwise make things up
            vad_parameters={"min_silence_duration_ms": 1000},
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        parts = []
        for segment in segments:  # written down one stretch at a time
            parts.append(segment.text.strip())
            if time.monotonic() - started > limit:
                raise TimeoutError(f"Whisper took over {limit:.0f} seconds")
        with self._lock:
            self._used = time.monotonic()
        log.info("Whisper wrote down %.1fs in %.1fs", seconds, time.monotonic() - started)
        return clean(" ".join(parts), wake, stop)


class Whisper:
    """The chosen model: found on this computer, or downloaded in the background.

    ``transcriber`` is what writes notes down; it's None until a model is ready (the
    listener's words are used meanwhile), and while a newly chosen model downloads, the
    one before it keeps working. ``status()`` is what the dashboard shows.
    """

    def __init__(
        self,
        paths: AppPaths,
        *,
        on_change: Callable[[], None] | None = None,
        fetch: Callable[..., Path] = fetch_whisper,
        make: Callable[[Path], Transcriber] = Transcriber,
        available: bool | None = None,
    ) -> None:
        self.paths = paths
        self.on_change = on_change or (lambda: None)
        self._fetch = fetch
        self._make = make
        self.available = installed() if available is None else available
        self.choice = ""
        self.transcriber: Transcriber | None = None
        self.state = "off"  # ready | downloading | error | off | unavailable
        self.progress = 0.0
        self.message = ""
        self._lock = threading.Lock()
        self._downloading: set[str] = set()
        self._failed_at = 0.0

    def status(self) -> dict[str, Any]:
        state, message = self.state, self.message
        problem = getattr(self.transcriber, "problem", "")
        if state == "ready" and problem:
            state = "error"
            message = (
                f"Whisper couldn't start ({problem}), so notes keep the words shown as you "
                "speak. A smaller model may work."
            )
        return {
            "model": self.choice,
            "state": state,
            "progress": round(self.progress, 2),
            "message": message,
            "models": {name: model.label for name, model in MODELS.items()},
        }

    def choose(self, name: str) -> None:
        name = name if name in MODELS or name == OFF else DEFAULT_MODEL
        with self._lock:
            if name == self.choice and self.state in ("ready", "downloading", "unavailable"):
                return
            self.choice = name
            self._settle()
        self.on_change()

    def _settle(self) -> None:
        """Bring the state in line with the choice (holding the lock)."""
        name = self.choice
        self.message = ""
        if name == OFF:
            self.transcriber, self.state = None, "off"
            return
        if not self.available:
            self.transcriber, self.state = None, "unavailable"
            self.message = "Install Magpie's desktop extras for the accurate speech model."
            return
        folder = find_whisper(self.paths, name)
        if folder is not None:
            if self.transcriber is None or self.transcriber.folder != folder:
                self.transcriber = self._make(folder)
            self.state, self.progress = "ready", 1.0
            return
        self.state, self.progress = "downloading", 0.0
        if name not in self._downloading:
            self._downloading.add(name)
            threading.Thread(
                target=self._download, args=(name,), name="magpie-whisper-download", daemon=True
            ).start()

    def _download(self, name: str) -> None:
        log.info("Downloading the %s speech model", name)
        try:
            self._fetch(self.paths, name, self._progress_of(name))
        except Exception as exc:
            log.warning("Downloading the %s speech model failed: %s", name, exc)
            with self._lock:
                self._downloading.discard(name)
                self._failed_at = time.monotonic()
                if self.choice == name:
                    self.state = "error"
                    self.message = (
                        f"The speech model couldn't be downloaded ({exc}). Magpie uses its "
                        "quicker listener meanwhile, and tries again later."
                    )
        else:
            log.info("The %s speech model is ready", name)
            with self._lock:
                self._downloading.discard(name)
                if self.choice == name:
                    self._settle()
        self.on_change()

    def _progress_of(self, name: str) -> Callable[[float], None]:
        def progress(value: float) -> None:
            if self.choice == name and self.state == "downloading":
                self.progress = value

        return progress

    def tick(self) -> None:
        """Every minute or so: let go of an idle model, and retry a failed download."""
        transcriber = self.transcriber
        if transcriber is not None:
            transcriber.release_if_idle()
        if self.state == "error" and time.monotonic() - self._failed_at > RETRY_SECONDS:
            with self._lock:
                if self.state == "error":
                    self._settle()
            self.on_change()
