"""Accurate words in the bar while you speak.

The listener's own words come at once but are often wrong, above all with an accent. So
while a note is taken, Whisper's quick model writes down the note so far, again and again
as it grows: each time from the start, so a word it got wrong is put right once more of
the sentence is heard. The bar shows Whisper's words, followed by the listener's words for
whatever was said after Whisper's last go (the app puts the two together).

Each go takes a moment (Whisper listens to up to 30 seconds at a time), and one follows
another as soon as a second more has been said. When nothing changes (a pause), the goes
come less often, until the listener hears words again.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

STEP_SECONDS = 1.0  # another go once this much more has been said
LONGEST_STEP = 4.0  # in a pause, the goes come less often, down to one every 4 seconds
WINDOW_SECONDS = 28.0  # Whisper hears 30 seconds at once: a longer note shows its end
GIVE_UP = 3  # failed goes in a row before the listener's words are left to do the job

Show = Callable[["LiveWriter", str, float], None]


class LiveWriter:
    """Fed the note's sound as it comes (``add``); calls ``show(writer, text, until)``
    with Whisper's words for the note's sound up to ``until`` seconds into it.

    ``start()`` runs it on its own thread; ``write()`` is one go (the thread's work, and
    what tests call).
    """

    def __init__(
        self,
        transcriber: Any,
        show: Show,
        *,
        wake: str | None = None,
        stop: str | None = None,
        step: float = STEP_SECONDS,
        window: float = WINDOW_SECONDS,
    ) -> None:
        self.transcriber = transcriber
        self.show = show
        self.wake = wake
        self.stop_phrase = stop
        self.step = step
        self.window = window
        self.rate = 0
        self.text = ""  # Whisper's words at its last go
        self._sound = bytearray()  # since the note started, or since the bar was typed over
        self._skipped = 0  # bytes of the note's sound before that
        self._written = 0  # how much of the note's sound the last go had
        self._wait = step  # how much more must be said before the next go
        self._generation = 0
        self._lock = threading.Lock()
        self._more = threading.Event()
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ from the app

    def start(self) -> LiveWriter:
        self._thread = threading.Thread(target=self._run, name="magpie-live", daemon=True)
        self._thread.start()
        return self

    def add(self, pcm: bytes, rate: int) -> None:
        """More of the note's sound (16-bit mono, as the listener heard it)."""
        with self._lock:
            if self.rate and rate != self.rate:  # another microphone, and a new listener
                self._sound, self._skipped, self._written = bytearray(), 0, 0
                self._generation += 1
            self.rate = rate
            self._sound += pcm
        self._more.set()

    def heard(self) -> None:
        """The listener heard new words: no more waiting as if in a pause."""
        self._wait = self.step

    def reset(self) -> None:
        """What was said so far was typed over: only write down what's said next."""
        with self._lock:
            self._skipped += len(self._sound)
            self._written = self._skipped
            self._sound = bytearray()
            self._generation += 1
            self.text = ""

    def stop(self) -> None:
        self._stopped.set()
        self._more.set()

    @property
    def stopped(self) -> bool:
        return self._stopped.is_set()

    # ------------------------------------------------------------- the writing

    def due(self) -> bool:
        with self._lock:
            new = self._skipped + len(self._sound) - self._written
            return bool(self.rate) and new >= self._wait * self.rate * 2

    def write(self) -> None:
        """One go: Whisper writes down the note so far (or its last 28 seconds)."""
        with self._lock:
            if not self.rate:
                return
            rate, generation = self.rate, self._generation
            sound = bytes(self._sound[-int(self.window * rate) * 2 :])
            windowed = len(sound) < len(self._sound)
            self._written = self._skipped + len(self._sound)
            until = self._written / (rate * 2)
        text = self.transcriber.transcribe(
            sound, rate, wake=self.wake, stop=self.stop_phrase, quick=True
        )
        if windowed and text:
            text = f"… {text}"
        with self._lock:
            if self.stopped or generation != self._generation:
                return  # the note ended, or was typed over, meanwhile
            self._wait = min(LONGEST_STEP, self._wait * 2) if text == self.text else self.step
            self.text = text
        self.show(self, text, until)

    def _run(self) -> None:
        failures = 0
        while not self.stopped:
            self._more.wait(0.5)
            self._more.clear()
            if self.stopped or not self.due():
                continue
            try:
                self.write()
                failures = 0
            except Exception as exc:
                failures += 1
                log.warning("Whisper couldn't write down the words as they came: %s", exc)
                if failures >= GIVE_UP:
                    return
