"""Writing down what was said, accurately: Whisper (faster-whisper), offline on this computer.

The always-on listener (speech.py) uses a small, fast recogniser: good at spotting
“notes on”, but it mishears a lot, above all with an accent. So Whisper, which is far more
accurate and adds punctuation and capitals, does the writing down twice over: while you
speak, a quick Whisper model writes down the note so far, again and again, for the bar
(live.py); when the note ends, the chosen model writes the whole of it down once more,
and that is the text that gets saved. By default that's the most accurate model when the
computer is quick enough for it, and the quick one otherwise.

The models are downloaded once, in the background, into the app's own folder, which
updates keep: the quick one first (it's smaller, and does both jobs until the other is
there), then the chosen one. Until then, or if they can't be had, the listener's own words
are used.
"""

from __future__ import annotations

import importlib.util
import io
import logging
import os
import re
import shutil
import threading
import time
import wave
from collections.abc import Callable, Sequence
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
    "large-v3-turbo": WhisperModel(
        "large-v3-turbo",
        "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        1_620_000_000,
        "Most accurate, also with strong accents (1.6 GB download)",
    ),
    "small.en": WhisperModel(
        "small.en",
        "Systran/faster-whisper-small.en",
        484_000_000,
        "Faster, for older computers (480 MB download)",
    ),
}
BEST_MODEL = "large-v3-turbo"
QUICK_MODEL = "small.en"  # writes the words in the bar while you speak
AUTO = "auto"  # the most accurate model this computer is quick enough for (the default)
OFF = "off"  # the listener's own words, as they were shown
# With four processor threads, the most accurate model takes over twice as long as the
# note itself; from eight (most computers of the last few years, every Apple silicon
# Mac), it's quick enough to be the one "auto" means.
FAST_COMPUTER = 8
FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
IDLE_SECONDS = 300.0  # let go of the model (and its memory) after this long unused
RETRY_SECONDS = 900.0  # try a failed download again after this long
# Quiet microphones are brought up to this level (the loudest sounds, as a fraction of
# the most there can be), but never by more than MAX_GAIN: Whisper, and the part of it
# that finds speech, can miss words said quietly.
LEVEL = 0.5
MAX_GAIN = 20.0
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


def suited(cpus: int | None = None) -> str:
    """The model "auto" means on this computer."""
    cpus = os.cpu_count() if cpus is None else cpus
    return BEST_MODEL if (cpus or 0) >= FAST_COMPUTER else QUICK_MODEL


def model_folder(paths: AppPaths, name: str) -> Path:
    return paths.models_dir / f"whisper-{name}"


def complete(folder: Path) -> bool:
    return (folder / "model.bin").is_file() and (folder / "tokenizer.json").is_file()


def find_whisper(paths: AppPaths, name: str) -> Path | None:
    """The model's folder when it's all there.

    MAGPIE_WHISPER_MODEL (tests and the self-test) names a model folder to use instead; a
    model of the name asked for next to it is used first, so both models can be given.
    """
    candidates: list[Path] = []
    override = Path(os.environ.get("MAGPIE_WHISPER_MODEL") or ".")
    if override.name:
        candidates += [override.with_name(model_folder(paths, name).name), override]
    for candidate in [*candidates, model_folder(paths, name)]:
        if complete(candidate):
            return candidate
    return None


def _download(repo: str, folder: Path, patterns: list[str]) -> None:
    from huggingface_hub import snapshot_download
    from huggingface_hub.utils import disable_progress_bars

    disable_progress_bars()  # the app has no console for them to write to
    snapshot_download(repo, local_dir=str(folder), allow_patterns=patterns)


def fetch_whisper(
    paths: AppPaths,
    name: str,
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
    """16-bit sound as Whisper wants it: float32 samples at 16 kHz, at a level it hears."""
    import numpy as np

    audio = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
    if rate != RATE and len(audio):
        audio = _resample(audio, rate)
    return _level(audio)


def _resample(audio: Any, rate: int) -> Any:
    import numpy as np

    if rate > RATE:
        # Sounds too high to keep at 16 kHz must go first, or they fold back down as hiss
        # over the words: a low-pass filter (a windowed sinc) just under the new limit.
        cutoff = 0.45 * RATE / rate
        taps = np.arange(-31, 32)
        kernel = np.sinc(2 * cutoff * taps) * np.hamming(len(taps))
        audio = np.convolve(audio, kernel / kernel.sum(), mode="same")
    wanted = np.arange(int(len(audio) * RATE / rate)) / RATE
    return np.interp(wanted, np.arange(len(audio)) / rate, audio).astype(np.float32)


def _level(audio: Any) -> Any:
    """Quiet sound brought up (never down), going by its loudest moments."""
    import numpy as np

    if not len(audio):
        return audio
    loudest = float(np.percentile(np.abs(audio), 99.9))  # a click alone doesn't count
    if loudest <= 0 or loudest >= LEVEL:
        return audio
    gain = min(MAX_GAIN, LEVEL / loudest)
    return np.clip(audio * gain, -1.0, 1.0).astype(np.float32)


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

    def load(self) -> None:
        """Load it now, and wait."""
        self._load()

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
            if self._model is not None and time.monotonic() - self._used >= idle:
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
        quick: bool = False,
        words: Sequence[str] = (),
    ) -> str:
        """The note's words. Raises TimeoutError past ``budget`` seconds (by default
        30 plus twice the note's length), so a slow computer falls back to the listener.

        ``quick`` is for the words shown while you speak: one guess at each word instead of
        weighing five, and no second tries, so it keeps up. ``words`` are names and words
        to listen out for (Settings → Voice).
        """
        audio = to_float(pcm, rate)
        seconds = len(audio) / RATE
        if seconds < 0.3:  # too short to hold a word
            return ""
        started = time.monotonic()
        limit = budget if budget is not None else 30.0 + 2 * seconds
        model = self._load()
        options: dict[str, Any] = {"beam_size": 1, "temperature": 0.0} if quick else {}
        if words:
            options["hotwords"] = ", ".join(words)
        segments, _info = model.transcribe(
            audio,
            language="en",
            vad_filter=True,  # skips silence, where Whisper would otherwise make things up
            vad_parameters={"min_silence_duration_ms": 1000},
            condition_on_previous_text=False,
            without_timestamps=True,
            **({"beam_size": 5} | options),
        )
        parts = []
        for segment in segments:  # written down one stretch at a time
            parts.append(segment.text.strip())
            if time.monotonic() - started > limit:
                raise TimeoutError(f"Whisper took over {limit:.0f} seconds")
        with self._lock:
            self._used = time.monotonic()
        took = time.monotonic() - started
        if quick:
            log.debug("Whisper (live) wrote down %.1fs in %.1fs", seconds, took)
        else:
            log.info("Whisper (%s) wrote down %.1fs in %.1fs", self.folder.name, seconds, took)
        return clean(" ".join(parts), wake, stop)


def wav(pcm: bytes, rate: int) -> bytes:
    """16-bit mono sound as a WAV file, as speech services take it."""
    out = io.BytesIO()
    with wave.open(out, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(pcm[: len(pcm) // 2 * 2])
    return out.getvalue()


class SpeechService:
    """The speech service chosen in Settings → Voice (stt.py), written to through Magpie's
    own server, which keeps its key: ``send`` takes a WAV file and returns its words."""

    name = "your speech service"

    def __init__(self, send: Callable[[bytes], str]) -> None:
        self._send = send

    def transcribe(
        self,
        pcm: bytes,
        rate: int,
        *,
        wake: str | None = None,
        stop: str | None = None,
        **_: Any,
    ) -> str:
        if len(pcm) < 0.3 * rate * 2:  # too short to hold a word
            return ""
        return clean(self._send(wav(pcm, rate)), wake, stop)


class Whisper:
    """The chosen model and the quick one: found on this computer, or downloaded in the
    background, the quick one first (it's smaller, and does both jobs until the other is
    there).

    ``transcriber`` writes each note down when it ends: the chosen model, or the quick one
    meanwhile, or None (the listener's words are used). ``quick`` writes the words shown
    while you speak, or is None (the listener's words are shown). ``status()`` is what the
    dashboard shows.
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
        self.chosen = ""  # what the dashboard says: a model, "auto" or "off"
        self.choice = ""  # the model that means here (or "off")
        self.transcriber: Transcriber | None = None
        self.quick: Transcriber | None = None
        self.meanwhile = False  # the quick model writes notes down until the chosen one is here
        self.state = "off"  # ready | downloading | error | off | unavailable
        self.getting = ""  # the model being downloaded
        self.progress = 0.0  # of that download
        self.message = ""
        self._lock = threading.Lock()
        self._made: dict[Path, Transcriber] = {}  # one per model folder
        self._downloading = False
        self._failed_at = 0.0

    def status(self) -> dict[str, Any]:
        state, message = self.state, self.message
        problem = getattr(self.transcriber, "problem", "")
        if state == "ready" and problem:
            state = "error"
            if self.quick is not None and self.quick is not self.transcriber:
                message = (
                    f"This model couldn't start ({problem}), so Magpie writes notes down "
                    "with the faster one. Choose that one to stop trying."
                )
            else:
                message = (
                    f"Whisper couldn't start ({problem}), so notes keep the words shown as "
                    "you speak."
                )
        return {
            "chosen": self.chosen,
            "model": self.choice,
            "state": state,
            "getting": self.getting,
            "progress": round(self.progress, 2),
            "live": self.quick is not None,
            "meanwhile": self.meanwhile,
            "message": message,
            "models": {name: model.label for name, model in MODELS.items()},
        }

    def choose(self, chosen: str) -> None:
        chosen = chosen if chosen in MODELS or chosen in (AUTO, OFF) else AUTO
        name = suited() if chosen == AUTO else chosen
        with self._lock:
            self.chosen = chosen
            if name == self.choice and self.state in ("ready", "downloading", "unavailable"):
                return
            self.choice = name
            self._settle()
        self.on_change()

    def _settle(self) -> None:
        """Bring the models in line with the choice, and download what's missing (holding
        the lock)."""
        name = self.choice
        self.message = ""
        self.meanwhile = False
        if name == OFF:
            self.transcriber, self.quick, self.state = None, None, "off"
            return
        if not self.available:
            self.transcriber, self.quick, self.state = None, None, "unavailable"
            self.message = "Install Magpie's desktop extras for the accurate speech model."
            return
        self.quick = self._ready(QUICK_MODEL)
        chosen = self.quick if name == QUICK_MODEL else self._ready(name)
        self.transcriber = chosen or self.quick
        self.meanwhile = chosen is None and self.quick is not None
        self.state = "ready" if chosen is not None else "downloading"
        missing = self._missing()
        if missing is not None and not self._downloading:
            self._downloading = True
            self.getting, self.progress = missing, 0.0
            threading.Thread(
                target=self._download, name="magpie-whisper-download", daemon=True
            ).start()

    def _ready(self, name: str) -> Transcriber | None:
        folder = find_whisper(self.paths, name)
        if folder is None:
            return None
        if folder not in self._made:
            self._made[folder] = self._make(folder)
        return self._made[folder]

    def _missing(self) -> str | None:
        """The next model to download: the quick one, then the chosen one."""
        if self.choice == OFF or not self.available:
            return None
        for name in dict.fromkeys((QUICK_MODEL, self.choice)):
            if find_whisper(self.paths, name) is None:
                return name
        return None

    def _download(self) -> None:
        while True:
            with self._lock:
                name = self._missing()
                self.getting, self.progress = name or "", 0.0
                if name is None:
                    self._downloading = False
                    return
            self.on_change()
            log.info("Downloading the %s speech model", name)
            try:
                self._fetch(self.paths, name, self._progress_of(name))
            except Exception as exc:
                log.warning("Downloading the %s speech model failed: %s", name, exc)
                with self._lock:
                    self._downloading = False
                    self.getting = ""
                    self._failed_at = time.monotonic()
                    if self.state == "downloading":
                        self.state = "error"
                        self.message = (
                            f"The speech model couldn't be downloaded ({exc}). Magpie "
                            + (
                                "uses the faster model meanwhile"
                                if self.meanwhile
                                else "keeps the words shown as you speak meanwhile"
                            )
                            + ", and tries again later."
                        )
                self.on_change()
                return
            log.info("The %s speech model is ready", name)
            with self._lock:
                self._settle()
            self.on_change()

    def _progress_of(self, name: str) -> Callable[[float], None]:
        def progress(value: float) -> None:
            if self.getting == name:
                self.progress = value

        return progress

    def tick(self) -> None:
        """Every minute or so: let go of idle models, and retry a failed download."""
        for transcriber in list(self._made.values()):  # also ones no longer chosen
            transcriber.release_if_idle()
        if self._failed_at and time.monotonic() - self._failed_at > RETRY_SECONDS:
            with self._lock:
                self._failed_at = 0.0
                self._settle()
            self.on_change()
