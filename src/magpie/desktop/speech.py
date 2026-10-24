"""Always-on listening: wait for the command word, then take down a note.

Three offline recognisers do the work. While Magpie waits, a cheap one that only knows
the command word (“notes on”) listens. When it hears it, a full recogniser writes down
what follows, starting from the exact moment the command word ended (the last few
seconds of audio are kept, so “notes on buy milk” said in one breath loses nothing).
Alongside it a second small recogniser listens only for “notes off” and “cancel note”,
which a full recogniser often mishears; their timings say exactly where the note ends.
The note's own sound is kept too, so the app can have it written down again by a much
more accurate (and slower) recogniser once it's over (transcribe.py).

``Listener`` is the state machine and is fed audio by one thread; ``VoiceEngine`` is that
thread, reading the microphone (or a WAV file in tests) and taking commands from others.
"""

from __future__ import annotations

import json
import logging
import queue
import sys
import threading
import time
import wave
from array import array
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from magpie.desktop.phrases import (
    CANCEL_PHRASES,
    DEFAULT_STOP,
    DEFAULT_WAKE,
    STOP_ALIASES,
    UNKNOWN,
    WAKE_ALIASES,
    find_in_words,
    phrase_list,
    tidy,
)

log = logging.getLogger(__name__)

RATE = 16000
CHUNK_SECONDS = 0.25
# Where to let Magpie use the microphone, when it hears only silence.
MIC_SETTINGS = {
    "win32": "Windows lets desktop apps use it (Settings → Privacy → Microphone)",
    "darwin": "Magpie may use it (System Settings → Privacy & Security → Microphone)",
}.get(sys.platform, "Magpie may use it")
NOTHING_SAID_SECONDS = 10.0  # after a false wake, give up this soon even with no timeout set
SETTLE_SECONDS = 0.5  # a stop word in a partial result must stand this long

Emit = Callable[[str, dict[str, Any]], None]


class Recognizer(Protocol):
    def accept(self, pcm: bytes) -> bool: ...
    def result(self) -> dict[str, Any]: ...
    def partial(self) -> dict[str, Any]: ...
    def final(self) -> dict[str, Any]: ...


class SpeechModel(Protocol):
    def knows(self, word: str) -> bool: ...
    def free(self, rate: int) -> Recognizer: ...
    def grammar(self, phrases: list[str], rate: int) -> Recognizer: ...


class VoskRecognizer:
    def __init__(self, recognizer: Any) -> None:
        recognizer.SetWords(True)
        recognizer.SetPartialWords(True)
        self._rec = recognizer

    def accept(self, pcm: bytes) -> bool:
        return bool(self._rec.AcceptWaveform(pcm))

    def result(self) -> dict[str, Any]:
        return json.loads(self._rec.Result())

    def partial(self) -> dict[str, Any]:
        return json.loads(self._rec.PartialResult())

    def final(self) -> dict[str, Any]:
        return json.loads(self._rec.FinalResult())


class VoskModel:
    """A Vosk model folder (such as vosk-model-small-en-us-0.15)."""

    def __init__(self, path: Path) -> None:
        import vosk

        vosk.SetLogLevel(-1)
        self._vosk = vosk
        self._model = vosk.Model(str(path))

    def knows(self, word: str) -> bool:
        if word == UNKNOWN:
            return True
        return self._model.vosk_model_find_word(word) >= 0

    def free(self, rate: int) -> Recognizer:
        return VoskRecognizer(self._vosk.KaldiRecognizer(self._model, rate))

    def grammar(self, phrases: list[str], rate: int) -> Recognizer:
        grammar = json.dumps([*phrases, UNKNOWN])
        return VoskRecognizer(self._vosk.KaldiRecognizer(self._model, rate, grammar))


def _words(result: dict[str, Any], key: str) -> list[dict[str, Any]]:
    items = result.get(key)
    if not isinstance(items, list):
        return []
    return [
        item
        for item in items
        if isinstance(item, dict) and isinstance(item.get("word"), str) and "start" in item
    ]


class Listener:
    """standby → (command word) → recording → (stop word, silence, Save) → standby.

    Events, through ``emit(kind, data)``:
      state      {"state": "standby" | "recording" | "paused", "by_voice": bool}
      words      {"final": str, "partial": str} while recording (settled, still changing)
      note       {"text": str, "reason": "phrase" | "silence" | "button" | "limit",
                  "audio": bytes, "rate": int}  (the note's sound, without the command words)
      empty      {"reason": ...}  recording ended with nothing said
      cancelled  {"text": str, "reason": "phrase" | "button" | "paused"}
    """

    def __init__(
        self,
        model: SpeechModel,
        emit: Emit,
        *,
        rate: int = RATE,
        wake: str | None = None,
        stop: str | None = None,
        silence_seconds: float = 8.0,
        max_seconds: float = 300.0,
        keep_seconds: float = 8.0,
    ) -> None:
        self.model = model
        self.emit = emit
        self.rate = rate
        self.max_seconds = max_seconds
        self.samples = 0  # audio fed so far, the listener's clock
        self.state = "standby"
        self._ring: deque[tuple[int, bytes]] = deque()
        self._ring_samples = int(keep_seconds * rate)
        self._wake_rec: Recognizer | None = None
        self._dict: Recognizer | None = None
        self._ctrl: Recognizer | None = None
        self.configure(wake, stop, silence_seconds)

    # ----------------------------------------------------------------- settings

    def configure(self, wake: str | None, stop: str | None, silence_seconds: float) -> None:
        self.wake_phrases = phrase_list(wake, DEFAULT_WAKE, WAKE_ALIASES)
        self.stop_phrases = phrase_list(stop, DEFAULT_STOP, STOP_ALIASES)
        try:
            self.silence_seconds = max(0.0, float(silence_seconds or 0))
        except (TypeError, ValueError):
            self.silence_seconds = 8.0
        # A grammar can only hold words the model knows; anything else is matched in the
        # full recogniser's text instead (slower, and a little less sure).
        self._wake_grammar = self._known(self.wake_phrases)
        self._control_grammar = self._known([*self.stop_phrases, *CANCEL_PHRASES])
        if self.state == "standby":
            self._standby(announce=False)

    def _known(self, phrases: list[str]) -> list[str]:
        return [p for p in phrases if all(self.model.knows(word) for word in p.split())]

    # -------------------------------------------------------------------- audio

    def feed(self, pcm: bytes) -> None:
        if not pcm or self.state == "paused":
            return
        start = self.samples
        self.samples += len(pcm) // 2
        self._ring.append((start, pcm))
        while self._ring and self._ring[0][0] < self.samples - self._ring_samples:
            self._ring.popleft()
        if self.state == "standby":
            self._listen(pcm)
        elif self.state == "recording":
            self._record(pcm)
            if self.state == "recording":
                self._check_time()

    def _standby(self, announce: bool = True) -> None:
        self.state = "standby"
        if self._wake_grammar:
            self._wake_rec = self.model.grammar(self._wake_grammar, self.rate)
        else:
            self._wake_rec = self.model.free(self.rate)
        self._wake_from = self.samples
        self._dict = self._ctrl = None
        self._sound = bytearray()
        if announce:
            self.emit("state", {"state": "standby", "by_voice": False})

    def _listen(self, pcm: bytes) -> None:
        rec = self._wake_rec
        if rec is None:
            return
        if rec.accept(pcm):
            words = _words(rec.result(), "result")
        else:
            words = _words(rec.partial(), "partial_result")
        hit = find_in_words(words, self.wake_phrases, fuzzy=not self._wake_grammar)
        # The command word starts what's said (allowing an "ok" or "um" before it), so
        # "I took notes on the meeting" doesn't start a note.
        if hit is None or hit.index > 1:
            return
        self._record_from(self._wake_from + int(hit.end * self.rate), by_voice=True)

    def _record_from(self, start: int, *, by_voice: bool) -> None:
        self.state = "recording"
        self._dict = self.model.free(self.rate)
        self._ctrl = (
            self.model.grammar(self._control_grammar, self.rate) if self._control_grammar else None
        )
        self._rec_from = start
        self._began = self.samples
        self._heard_at = self.samples
        self._heard_any = False
        self._settled: list[dict[str, Any]] = []
        self._partial: list[dict[str, Any]] = []
        self._cut = 0.0  # words before this (seconds into the note) are no longer shown
        self._sound = bytearray()  # everything heard since ``start``, for the note's sound
        self._pending: tuple[tuple[str, float], int] | None = None
        self._shown: tuple[str, str] | None = None
        self.emit("state", {"state": "recording", "by_voice": by_voice})
        # What was said right after the command word is already in the buffer.
        for chunk_start, chunk in list(self._ring):
            chunk_end = chunk_start + len(chunk) // 2
            if chunk_end <= start:
                continue
            if chunk_start < start:
                chunk = chunk[(start - chunk_start) * 2 :]
            self._record(chunk)
            if self.state != "recording":
                break

    def _record(self, pcm: bytes) -> None:
        assert self._dict is not None
        self._sound += pcm
        if self._dict.accept(pcm):
            self._settled += _words(self._dict.result(), "result")
            self._partial = []
        else:
            self._partial = _words(self._dict.partial(), "partial_result")
        self._skip_wake()
        self._show()
        if self._ctrl is not None:
            self._control(pcm)

    def _skip_wake(self) -> None:
        """A command word at the very start (said after pressing the mic) isn't the note."""
        words = [w for w in self._settled + self._partial if w["word"] != UNKNOWN]
        if not words or words[0]["start"] > 2.0 or self._cut > 0:
            return
        hit = find_in_words(words, self.wake_phrases, fuzzy=True)
        if hit is not None and hit.index == 0:
            self._cut = hit.end

    def _text(self, words: list[dict[str, Any]]) -> str:
        return tidy(
            " ".join(w["word"] for w in words if w["word"] != UNKNOWN and w["start"] >= self._cut)
        )

    def _show(self) -> None:
        shown = (self._text(self._settled), self._text(self._partial))
        if shown != self._shown:
            self._shown = shown
            if any(shown):
                self._heard_at = self.samples
                self._heard_any = True
            self.emit("words", {"final": shown[0], "partial": shown[1]})

    def _control(self, pcm: bytes) -> None:
        assert self._ctrl is not None
        settled = self._ctrl.accept(pcm)
        if settled:
            words = _words(self._ctrl.result(), "result")
        else:
            words = _words(self._ctrl.partial(), "partial_result")
        hit = find_in_words(words, self._control_grammar, last=True)
        # The stop word ends what's said: "notes off the record" doesn't save the note.
        if hit is None or hit.index + hit.length < len(words):
            self._pending = None
            return
        kind = "cancel" if hit.phrase in CANCEL_PHRASES else "stop"
        if settled:
            self._act(kind, hit.start)
            return
        key = (kind, round(hit.start, 1))
        if self._pending is not None and self._pending[0] == key:
            if self.samples - self._pending[1] >= SETTLE_SECONDS * self.rate:
                self._act(kind, hit.start)
        else:
            self._pending = (key, self.samples)

    def _act(self, kind: str, at: float) -> None:
        if kind == "cancel":
            self._cancel("phrase", cut=at)
        else:
            self._finish("phrase", cut=at)

    def _check_time(self) -> None:
        quiet = (self.samples - self._heard_at) / self.rate
        if not self._heard_any:
            limit = min(self.silence_seconds or NOTHING_SAID_SECONDS, NOTHING_SAID_SECONDS)
            if quiet >= limit:
                self._finish("silence")
        elif self.silence_seconds and quiet >= self.silence_seconds:
            self._finish("silence")
        elif (self.samples - self._began) / self.rate >= self.max_seconds:
            self._finish("limit")

    def _finish(self, reason: str, cut: float | None = None) -> None:
        assert self._dict is not None
        words = self._settled + _words(self._dict.final(), "result")
        if cut is not None:
            words = [w for w in words if w["start"] < cut - 0.05]
        text = self._text(words)
        sound = self._note_sound(words, cut)
        self._standby()
        if text:
            self.emit("note", {"text": text, "reason": reason, "audio": sound, "rate": self.rate})
        else:
            self.emit("empty", {"reason": reason})

    def _note_sound(self, words: list[dict[str, Any]], cut: float | None) -> bytes:
        """The part of the recording the note's words are in: after the command word (or
        what was typed over) and before the stop word, without the silence that ended it."""
        if cut is None:
            heard = [float(w.get("end", 0.0)) for w in words if w["word"] != UNKNOWN]
            cut = max(heard) + 0.5 if heard else None
        start = int(self._cut * self.rate) * 2
        end = len(self._sound) if cut is None else int(cut * self.rate) * 2
        return bytes(self._sound[start:end])

    def _cancel(self, reason: str, cut: float | None = None) -> None:
        words = self._settled + self._partial
        if cut is not None:
            words = [w for w in words if w["start"] < cut - 0.05]
        text = self._text(words)
        self._standby(announce=reason != "paused")
        self.emit("cancelled", {"text": text, "reason": reason})

    # ----------------------------------------------------------------- commands

    def start(self) -> None:
        """Start a note now (the mic button or the tray), without the command word."""
        if self.state == "standby":
            self._record_from(self.samples, by_voice=False)

    def finish(self) -> None:
        if self.state == "recording":
            self._finish("button")

    def cancel(self) -> None:
        if self.state == "recording":
            self._cancel("button")

    def rebase(self) -> None:
        """The words so far were typed over in the bar: only show what's said next."""
        if self.state == "recording":
            self._cut = (self.samples - self._rec_from) / self.rate
            self._shown = None

    def pause(self) -> None:
        if self.state == "recording":
            self._cancel("paused")
        self.state = "paused"
        self._wake_rec = None
        self.emit("state", {"state": "paused", "by_voice": False})

    def resume(self) -> None:
        if self.state == "paused":
            self._ring.clear()
            self._standby()


# ------------------------------------------------------------------- audio input


class MicrophoneError(Exception):
    pass


class Microphone:
    """The default microphone, as 16-bit mono chunks of about a quarter second."""

    def __init__(self, on_audio: Callable[[bytes], None]) -> None:
        self.on_audio = on_audio
        self._stream: Any = None

    def open(self) -> int:
        try:
            import sounddevice as sd
        except (ImportError, OSError) as exc:  # no PortAudio on this system
            raise MicrophoneError(f"Sound isn't available: {exc}") from exc
        problem: Exception | None = None
        try:
            fallback = int(sd.query_devices(kind="input")["default_samplerate"])
        except Exception as exc:
            raise MicrophoneError("No microphone found.") from exc
        for rate in dict.fromkeys((RATE, fallback)):
            try:
                stream = sd.RawInputStream(
                    samplerate=rate,
                    blocksize=int(rate * CHUNK_SECONDS),
                    channels=1,
                    dtype="int16",
                    callback=self._callback,
                )
                stream.start()
            except Exception as exc:
                problem = exc
                continue
            self._stream = stream
            return rate
        raise MicrophoneError(f"The microphone couldn't be opened: {problem}")

    def _callback(self, indata: Any, frames: int, when: Any, status: Any) -> None:
        self.on_audio(bytes(indata))

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.debug("Closing the microphone failed", exc_info=True)

    @property
    def active(self) -> bool:
        return self._stream is not None and bool(getattr(self._stream, "active", False))


class WavFile:
    """A WAV file played into the listener (tests and the self-test), then silence."""

    def __init__(
        self, path: Path, on_audio: Callable[[bytes], None], *, silence_after: float = 12.0
    ) -> None:
        self.path = path
        self.on_audio = on_audio
        self.silence_after = silence_after
        self._thread: threading.Thread | None = None

    def open(self) -> int:
        with wave.open(str(self.path), "rb") as wav:
            if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
                raise MicrophoneError("The test recording must be 16-bit mono.")
            rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
        step = int(rate * CHUNK_SECONDS) * 2
        silence = bytes(step)

        def play() -> None:
            for i in range(0, len(frames), step):
                self.on_audio(frames[i : i + step])
            for _ in range(int(self.silence_after / CHUNK_SECONDS)):
                self.on_audio(silence)

        self._thread = threading.Thread(target=play, name="magpie-wav", daemon=True)
        self._thread.start()
        return rate

    def close(self) -> None:
        pass

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


def loudest(pcm: bytes) -> int:
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    return max((abs(x) for x in (max(samples, default=0), min(samples, default=0))), default=0)


# ------------------------------------------------------------------------ engine


class VoiceEngine:
    """The listening thread: owns the microphone and the Listener, takes commands.

    Commands (thread-safe): start, finish, cancel, rebase, pause, resume,
    configure(wake, stop, silence_seconds). Besides the Listener's events it emits
    ``state`` {"state": "starting" | "no-mic" | "muted" | "error", "message": str}.
    """

    def __init__(
        self,
        model_path: Path | None,
        emit: Emit,
        *,
        source: Callable[[Callable[[bytes], None]], Any] = Microphone,
        model_factory: Callable[[Path], SpeechModel] = VoskModel,
        listening: bool = True,
        retry_seconds: float = 10.0,
    ) -> None:
        self.model_path = model_path
        self.emit = emit
        self._source_factory = source
        self._model_factory = model_factory
        self._commands: queue.Queue[tuple[str, tuple[Any, ...]]] = queue.Queue()
        self._audio: queue.Queue[bytes] = queue.Queue(maxsize=400)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._listening = listening
        self._retry = retry_seconds
        self._config: tuple[str | None, str | None, float] = (None, None, 8.0)
        self._want_start = False  # "start a note" arrived before the microphone was open
        self._model_ready = threading.Event()
        self._model_problem = ""
        if model_path is not None:
            self._model_ready.set()
        self.listener: Listener | None = None
        self.state = "starting"
        self.message = ""

    # thread-safe API
    def set_model(self, path: Path | None, problem: str = "") -> None:
        """The speech model is ready (or couldn't be had: ``problem`` says why)."""
        self.model_path = path
        self._model_problem = problem
        self._model_ready.set()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="magpie-voice", daemon=True)
        self._thread.start()

    def command(self, name: str, *args: Any) -> None:
        self._commands.put((name, args))

    def close(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def listening(self) -> bool:
        return self._listening

    # the thread
    def _set(self, state: str, message: str = "") -> None:
        if (state, message) != (self.state, self.message):
            self.state, self.message = state, message
            self.emit("state", {"state": state, "message": message, "by_voice": False})

    def _on_event(self, kind: str, data: dict[str, Any]) -> None:
        if kind == "state":
            self.state, self.message = data["state"], ""
        self.emit(kind, data)

    def _on_audio(self, pcm: bytes) -> None:
        try:
            self._audio.put_nowait(pcm)
        except queue.Full:  # the recogniser fell behind; drop the oldest audio
            try:
                self._audio.get_nowait()
                self._audio.put_nowait(pcm)
            except (queue.Empty, queue.Full):
                pass

    def _run(self) -> None:
        if not self._model_ready.is_set():
            self._set("starting", "Getting speech recognition ready…")
            while not self._model_ready.wait(0.5):
                if self._stop.is_set():
                    return
        if self.model_path is None or not Path(self.model_path).exists():
            self._set(
                "error", self._model_problem or "The speech model is missing. Reinstall Magpie."
            )
            return
        try:
            model = self._model_factory(Path(self.model_path))
        except Exception as exc:
            log.exception("Loading the speech model failed")
            self._set("error", f"The speech model couldn't be loaded: {exc}")
            return
        source = None
        silent_since: float | None = None
        next_try = 0.0
        while not self._stop.is_set():
            self._commands_now()
            if not self._listening:
                if source is not None:
                    source.close()
                    source = None
                    self._drain_audio()
                if self.listener is not None and self.listener.state != "paused":
                    self.listener.pause()
                elif self.listener is None:
                    self._set("paused")
                self._stop.wait(0.2)
                continue
            if source is None:
                if time.monotonic() < next_try:
                    self._stop.wait(0.2)
                    continue
                source = self._source_factory(self._on_audio)
                try:
                    rate = source.open()
                except MicrophoneError as exc:
                    source = None
                    next_try = time.monotonic() + self._retry
                    self._set("no-mic", str(exc))
                    continue
                if self.listener is None or self.listener.rate != rate:
                    self.listener = Listener(model, self._on_event, rate=rate)
                    self.listener.configure(*self._config)
                    self._set("standby")
                else:
                    self.listener.resume()
                silent_since = None
                if self._want_start:
                    self._want_start = False
                    self.listener.start()
            try:
                pcm = self._audio.get(timeout=0.2)
            except queue.Empty:
                if not source.active and not isinstance(source, WavFile):
                    source.close()
                    source = None
                    self._set("no-mic", "The microphone stopped. Trying again…")
                    next_try = time.monotonic() + self._retry
                continue
            # A microphone Windows won't share delivers nothing but zeros.
            if loudest(pcm) == 0:
                silent_since = silent_since or time.monotonic()
                if time.monotonic() - silent_since > 6 and self.state == "standby":
                    self._set(
                        "muted",
                        "Magpie hears only silence. Check that the microphone isn't muted, "
                        f"and that {MIC_SETTINGS}.",
                    )
            else:
                silent_since = None
                if self.state == "muted":
                    self._set("standby")
            assert self.listener is not None
            self.listener.feed(pcm)
        if source is not None:
            source.close()

    def _drain_audio(self) -> None:
        while True:
            try:
                self._audio.get_nowait()
            except queue.Empty:
                return

    def _commands_now(self) -> None:
        while True:
            try:
                name, args = self._commands.get_nowait()
            except queue.Empty:
                return
            if name == "configure":
                self._config = (args[0], args[1], args[2])
                if self.listener is not None:
                    self.listener.configure(*self._config)
            elif name == "pause":
                self._listening = False
            elif name == "resume":
                self._listening = True
            elif name == "start":
                if self.listener is not None and self.listener.state == "standby":
                    self.listener.start()
                elif self.listener is None or self.listener.state == "paused":
                    self._want_start = True
            elif self.listener is not None and name in ("finish", "cancel", "rebase"):
                getattr(self.listener, name)()
