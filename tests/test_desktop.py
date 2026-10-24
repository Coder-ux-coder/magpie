"""The desktop app: listening for the command word, the app's glue, moving from Docker,
updates, and how the dashboard reaches the app. Nothing here needs a microphone, a
screen or the speech model: a scripted recogniser hears words at set times."""

from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import zipfile
from array import array
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest

from magpie.desktop import migrate, paths, phrases, system, transcribe, update
from magpie.desktop.app import Bridge, DesktopApp
from magpie.desktop.live import GIVE_UP, LONGEST_STEP, LiveWriter
from magpie.desktop.speech import (
    NOTHING_SAID_SECONDS,
    Listener,
    MicrophoneError,
    VoiceEngine,
    sound_level,
)
from magpie.notify import Notification

RATE = 16000
FRAME = 160  # the fake audio's samples count 10 ms frames, so a recogniser knows the time
REAL_MODEL = os.environ.get("MAGPIE_VOSK_MODEL", "")  # read before the fixtures clear MAGPIE_*


# ------------------------------------------------------------- a scripted recogniser


def say(text: str, at: float, word: float = 0.3, gap: float = 0.05) -> list:
    """Words said one after another from ``at`` seconds: [(word, start, end)]."""
    out, t = [], at
    for w in text.split():
        out.append((w, t, t + word))
        t += word + gap
    return out


def audio(seconds: float, start: float = 0.0, chunk: float = 0.25):
    first, total, step = int(start * RATE), int(seconds * RATE), int(chunk * RATE)
    for offset in range(0, total, step):
        index = first + offset
        n = min(step, total - offset)
        yield array("h", (((index + i) // FRAME) % 32768 for i in range(n))).tobytes()


class FakeRecognizer:
    """Hears the script's words when they've been said, with Vosk's word timings.

    A grammar recogniser hears only its own words ("[unk]" for anything else), and an
    utterance settles (a final result) after half a second without words.
    """

    def __init__(
        self, script: list, grammar: list[str] | None = None, misheard: dict | None = None
    ) -> None:
        self.script = script
        self.grammar = None if grammar is None else {w for p in grammar for w in p.split()}
        self.misheard = misheard or {}  # what a grammar takes words for: {"no it's": "notes"}
        self.origin: float | None = None
        self.now = 0.0
        self.taken = 0.0
        self._result: list[dict[str, Any]] = []

    def _feed(self, pcm: bytes) -> None:
        samples = array("h")
        samples.frombytes(pcm)
        start = samples[0] * FRAME / RATE
        if self.origin is None:
            self.origin = self.taken = start
        self.now = start + len(samples) / RATE

    def _heard(self) -> list[dict[str, Any]]:
        assert self.origin is not None
        script = self.script if self.grammar is None else self._mishear(self.script)
        return [
            {
                "word": w if self.grammar is None or w in self.grammar else "[unk]",
                "start": round(s - self.origin, 3),
                "end": round(e - self.origin, 3),
                "conf": 1.0,
            }
            for w, s, e in script
            if s >= self.taken - 1e-6 and e <= self.now + 1e-6
        ]

    def _mishear(self, script: list) -> list:
        out, i = [], 0
        while i < len(script):
            for said, heard in self.misheard.items():
                size = len(said.split())
                if " ".join(w for w, _, _ in script[i : i + size]) == said:
                    out.append((heard, script[i][1], script[i + size - 1][2]))
                    i += size
                    break
            else:
                out.append(script[i])
                i += 1
        return out

    @staticmethod
    def _text(words: list[dict[str, Any]]) -> str:
        return " ".join(w["word"] for w in words)

    def accept(self, pcm: bytes) -> bool:
        self._feed(pcm)
        pending = self._heard()
        if pending and self.now - (self.origin + pending[-1]["end"]) >= 0.5:
            self._result, self.taken = pending, self.origin + pending[-1]["end"] + 0.001
            return True
        return False

    def result(self) -> dict[str, Any]:
        words, self._result = self._result, []
        return {"text": self._text(words), "result": words}

    def partial(self) -> dict[str, Any]:
        words = self._heard()
        return {"partial": self._text(words), "partial_result": words}

    def final(self) -> dict[str, Any]:
        words = self._heard() if self.origin is not None else []
        self.taken = self.now
        return {"text": self._text(words), "result": words}


class FakeModel:
    def __init__(
        self, script: list, unknown: tuple[str, ...] = (), misheard: dict | None = None
    ) -> None:
        self.script = script
        self.unknown = set(unknown)
        self.misheard = misheard
        self.free_made = 0

    def knows(self, word: str) -> bool:
        return word == "[unk]" or word not in self.unknown

    def free(self, rate: int) -> FakeRecognizer:
        self.free_made += 1
        return FakeRecognizer(self.script)

    def grammar(self, phrases: list[str], rate: int) -> FakeRecognizer:
        return FakeRecognizer(self.script, phrases, self.misheard)


def listen(script: list, seconds: float, unknown: tuple[str, ...] = (), misheard=None, **options):
    events: list[tuple[str, dict[str, Any]]] = []
    listener = Listener(
        FakeModel(script, unknown, misheard),
        lambda kind, data: events.append((kind, data)),
        **options,
    )
    for pcm in audio(seconds):
        listener.feed(pcm)
    return listener, events


def heard_between(sound: bytes) -> tuple[float, float]:
    """When a note's sound starts and ends: the fake audio's samples count 10 ms frames."""
    samples = array("h")
    samples.frombytes(sound)
    return samples[0] / 100, (samples[-1] + 1) / 100


def outcome(events: list) -> list:
    """The events that matter, in order: states and how each note ended."""
    out = []
    for kind, data in events:
        if kind == "state":
            out.append(data["state"])
        elif kind in ("note", "cancelled", "empty"):
            out.append((kind, data.get("text", ""), data["reason"]))
    return out


# ---------------------------------------------------------------- the listener


def test_notes_on_then_notes_off_takes_down_exactly_the_note():
    script = say("notes on", 1.0) + say("buy milk tomorrow", 2.0) + say("notes off", 3.5)
    _, events = listen(script, 7)
    assert outcome(events) == ["recording", "standby", ("note", "buy milk tomorrow", "phrase")]
    started = next(data for kind, data in events if kind == "state")
    assert started["by_voice"] is True
    shown = [data for kind, data in events if kind == "words"]
    assert any("buy milk" in (w["final"] + " " + w["partial"]) for w in shown)
    # When each word began (seconds into the note), for the bar to line them up with
    # Whisper's words.
    timed = max((w["timed"] for w in shown), key=len)
    assert timed[:3] == [("buy", 0.35), ("milk", 0.7), ("tomorrow", 1.05)]


def test_a_note_said_in_one_breath_keeps_its_first_words():
    """The words right after the command word were already heard: none are lost."""
    _, events = listen(say("notes on buy milk", 1.0), 6, silence_seconds=2)
    assert ("note", "buy milk", "silence") in outcome(events)


def test_something_that_only_sounds_like_the_command_word_starts_nothing():
    """The command-word recogniser takes “no, it's on” for “notes on”; the full
    recogniser, listening to the same moment again, doesn't, so no note starts."""
    script = say("no it's on", 1.0) + say("the kettle is boiling", 2.4)
    listener, events = listen(script, 8, misheard={"no it's": "notes"})
    assert "recording" not in outcome(events) and listener.state == "standby"
    assert isinstance(listener.model, FakeModel)
    assert listener.model.free_made == 1  # it was checked once, not again and again
    # Said properly, the same command word starts a note straight away.
    _, events = listen(say("notes on", 1.0) + say("buy milk", 2.0), 7, silence_seconds=2)
    assert ("note", "buy milk", "silence") in outcome(events)


def test_the_command_word_in_the_middle_of_a_sentence_does_nothing():
    _, events = listen(say("i took notes on the meeting", 1.0), 6)
    assert "recording" not in outcome(events)


def test_one_word_before_the_command_word_is_fine():
    _, events = listen(say("okay notes on", 1.0) + say("call james", 2.5), 7, silence_seconds=2)
    assert ("note", "call james", "silence") in outcome(events)


def test_a_misheard_command_word_still_works():
    _, events = listen(say("nodes on", 1.0) + say("water the plants", 2.0), 7, silence_seconds=2)
    assert ("note", "water the plants", "silence") in outcome(events)


def test_cancel_note_discards_it_and_keeps_the_words_for_undo():
    script = say("notes on", 1.0) + say("call mum", 2.0) + say("cancel note", 3.2)
    _, events = listen(script, 6)
    assert outcome(events)[-1] == ("cancelled", "call mum", "phrase")


def test_the_stop_word_only_counts_at_the_end_of_what_is_said():
    script = say("notes on", 1.0) + say("this is notes off the record", 2.0)
    _, events = listen(script, 9, silence_seconds=2)
    assert ("note", "this is notes off the record", "silence") in outcome(events)


def test_silence_saves_the_note():
    _, events = listen(say("notes on", 1.0) + say("call james", 2.0), 9, silence_seconds=3)
    assert ("note", "call james", "silence") in outcome(events)


def test_saying_nothing_after_the_command_word_gives_up():
    _, events = listen(say("notes on", 1.0), 4 + NOTHING_SAID_SECONDS, silence_seconds=0)
    assert outcome(events) == ["recording", "standby", ("empty", "", "silence")]


def test_a_very_long_note_is_saved_at_the_limit():
    script = say(" ".join(["word"] * 30), 2.0) + say("notes on", 0.5)
    script.sort(key=lambda item: item[1])
    _, events = listen(script, 14, silence_seconds=0, max_seconds=4)
    assert outcome(events)[-1][0::2] == ("note", "limit")


def test_the_mic_button_starts_and_saves_without_the_command_word():
    events: list = []
    listener = Listener(FakeModel(say("call the dentist", 1.5)), lambda k, d: events.append((k, d)))
    chunks = list(audio(4))
    for pcm in chunks[:4]:  # one second
        listener.feed(pcm)
    listener.start()
    for pcm in chunks[4:]:
        listener.feed(pcm)
    listener.finish()
    assert outcome(events) == ["recording", "standby", ("note", "call the dentist", "button")]
    assert events[0][1]["by_voice"] is False


def test_the_command_word_said_after_pressing_the_mic_is_not_part_of_the_note():
    events: list = []
    listener = Listener(
        FakeModel(say("notes on call the bank", 0.5)), lambda k, d: events.append((k, d))
    )
    listener.start()
    for pcm in audio(4):
        listener.feed(pcm)
    listener.finish()
    assert ("note", "call the bank", "button") in outcome(events)


def test_typing_over_the_words_keeps_only_what_is_said_next():
    events: list = []
    listener = Listener(
        FakeModel(say("buy milk", 1.0) + say("and eggs", 3.0)), lambda k, d: events.append((k, d))
    )
    chunks = list(audio(5))
    listener.start()
    for pcm in chunks[:10]:  # 2.5 seconds: "buy milk" is in the bar, then typed over
        listener.feed(pcm)
    listener.rebase()
    for pcm in chunks[10:]:
        listener.feed(pcm)
    listener.finish()
    assert ("note", "and eggs", "button") in outcome(events)


def test_pausing_drops_a_note_in_progress_and_ignores_the_microphone():
    events: list = []
    listener = Listener(FakeModel(say("notes on", 0.5)), lambda k, d: events.append((k, d)))
    chunks = list(audio(6))
    for pcm in chunks[:8]:
        listener.feed(pcm)
    assert listener.state == "recording"
    listener.pause()
    for pcm in chunks[8:]:
        listener.feed(pcm)
    assert listener.state == "paused"
    assert outcome(events)[-2:] == [("cancelled", "", "paused"), "paused"]
    listener.resume()
    assert listener.state == "standby" and outcome(events)[-1] == "standby"


def test_a_command_word_the_model_does_not_know_is_found_in_the_full_text():
    script = say("hey magpie buy bread", 1.0)
    _, events = listen(script, 7, unknown=("magpie",), wake="hey magpie", silence_seconds=2)
    assert ("note", "buy bread", "silence") in outcome(events)


def test_a_new_command_word_works_straight_away():
    events: list = []
    listener = Listener(FakeModel(say("hello magpie", 1.0) + say("call james", 2.2)),
                        lambda k, d: events.append((k, d)), silence_seconds=2)  # fmt: skip
    listener.configure("hello magpie", None, 2)
    for pcm in audio(7):
        listener.feed(pcm)
    assert ("note", "call james", "silence") in outcome(events)


def test_the_note_comes_with_its_own_sound_without_the_command_words():
    """Whisper writes down exactly the stretch the note was said in."""
    script = say("notes on", 1.0) + say("buy milk tomorrow", 2.0) + say("notes off", 3.5)
    _, events = listen(script, 7)
    note = next(data for kind, data in events if kind == "note")
    assert note["rate"] == RATE
    assert heard_between(note["audio"]) == (1.65, 3.5)  # after "on", up to "notes off"
    # The same sound comes as it's heard, for Whisper to write down while you speak.
    streamed = b"".join(data["pcm"] for kind, data in events if kind == "sound")
    assert streamed.startswith(note["audio"]) and heard_between(streamed)[0] == 1.65

    # Ended by silence: up to half a second after the last word, not the quiet after it.
    _, events = listen(say("notes on", 1.0) + say("call james", 2.0), 9, silence_seconds=3)
    note = next(data for kind, data in events if kind == "note")
    assert heard_between(note["audio"]) == (1.65, 3.15)

    # The command word said after pressing the mic, and words typed over, are left out.
    events.clear()
    listener = Listener(
        FakeModel(say("notes on call the bank", 0.5)), lambda k, d: events.append((k, d))
    )
    listener.start()
    for pcm in audio(4):
        listener.feed(pcm)
    listener.finish()
    note = next(data for kind, data in events if kind == "note")
    assert heard_between(note["audio"]) == (1.15, 2.7)


# ------------------------------------------------------------------- phrases


def test_phrase_matching():
    words = [{"word": w, "start": i, "end": i + 0.5} for i, w in enumerate(["ok", "notes", "on"])]
    hit = phrases.find_in_words(words, ["notes on"])
    assert hit is not None and (hit.index, hit.length, hit.start, hit.end) == (1, 2, 1, 2.5)
    fuzzy = [{"word": w} for w in ("nodes", "on")]
    assert phrases.find_in_words(fuzzy, ["notes on"], fuzzy=True) is not None
    for wrong in (["notes", "one"], ["notes", "of"]):
        assert phrases.find_in_words([{"word": w} for w in wrong], ["notes on"], fuzzy=True) is None
    assert phrases.phrase_list("Hello Magpie", "notes on", phrases.WAKE_ALIASES) == ["hello magpie"]
    assert "nodes on" in phrases.phrase_list(None, "notes on", phrases.WAKE_ALIASES)
    assert phrases.after_wake("OK, notes on: buy milk", ["notes on"]) == "buy milk"
    assert phrases.before_stop("buy milk notes off", ["notes off"]) == (True, "buy milk")
    assert phrases.append_text("Shopping:\n", "eggs") == "Shopping:\neggs"
    assert phrases.append_text("buy milk", "and eggs") == "buy milk and eggs"


# ------------------------------------------------------------- the voice engine


class FakeSource:
    def __init__(self, on_audio, *, fail: str = "") -> None:
        self.on_audio = on_audio
        self.fail = fail
        self.opened = False

    def open(self) -> int:
        if self.fail:
            raise MicrophoneError(self.fail)
        self.opened = True
        return RATE

    def close(self) -> None:
        self.opened = False

    @property
    def active(self) -> bool:
        return self.opened


class Recorder:
    """Collects events from another thread and waits for one."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self._changed = threading.Condition()

    def __call__(self, kind: str, data: dict[str, Any]) -> None:
        with self._changed:
            self.events.append((kind, data))
            self._changed.notify_all()

    def states(self) -> list[str]:
        return [data["state"] for kind, data in self.events if kind == "state"]

    def wait_for(self, state: str, timeout: float = 5.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                for kind, data in self.events:
                    if kind == "state" and data["state"] == state:
                        return data
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AssertionError(f"no {state!r} state; got {self.states()}")
                self._changed.wait(left)


def engine_with(tmp_path, script=(), **options) -> tuple[VoiceEngine, Recorder, list]:
    recorder, sources = Recorder(), []
    fail = options.pop("fail", "")

    def source(on_audio):
        made = FakeSource(on_audio, fail=fail)
        sources.append(made)
        return made

    engine = VoiceEngine(
        tmp_path,
        recorder,
        source=source,
        model_factory=lambda path: FakeModel(list(script)),
        retry_seconds=0.1,
        **options,
    )
    return engine, recorder, sources


def test_the_engine_listens_and_takes_commands(tmp_path):
    engine, recorder, sources = engine_with(tmp_path, say("call james", 1.0))
    engine.command("start")  # before the microphone is even open
    engine.start()
    try:
        recorder.wait_for("recording")
        for pcm in audio(3):
            sources[0].on_audio(pcm)
        deadline = time.monotonic() + 5
        while not engine._audio.empty() and time.monotonic() < deadline:
            time.sleep(0.01)  # every chunk heard before "finish" (it's taken in between)
        engine.command("finish")
        recorder.wait_for("standby")
        deadline = time.monotonic() + 5
        while not any(k == "note" for k, _ in recorder.events) and time.monotonic() < deadline:
            time.sleep(0.05)
        note = next(data for kind, data in recorder.events if kind == "note")
        assert (note["text"], note["reason"], note["rate"]) == ("call james", "button", RATE)
        assert heard_between(note["audio"]) == (0.0, 1.65 + 0.5)  # "james" ends at 1.65
        engine.command("pause")
        recorder.wait_for("paused")
        assert engine.listening is False and not sources[0].opened
        engine.command("resume")
        deadline = time.monotonic() + 5
        while recorder.states()[-1] != "standby" and time.monotonic() < deadline:
            time.sleep(0.05)
        assert recorder.states()[-1] == "standby"
    finally:
        engine.close()


def test_the_engine_says_when_there_is_no_microphone(tmp_path):
    engine, recorder, _ = engine_with(tmp_path, fail="No microphone found.")
    engine.start()
    try:
        assert recorder.wait_for("no-mic")["message"] == "No microphone found."
    finally:
        engine.close()


def test_the_engine_waits_for_the_speech_model(tmp_path):
    recorder = Recorder()
    engine = VoiceEngine(None, recorder, source=lambda cb: FakeSource(cb))
    engine.start()
    try:
        recorder.wait_for("starting")
        engine.set_model(None, "Speech recognition isn't ready: no internet.")
        assert recorder.wait_for("error")["message"].endswith("no internet.")
    finally:
        engine.close()


def test_a_broken_speech_model_is_reported(tmp_path):
    recorder = Recorder()

    def broken(path):
        raise RuntimeError("bad model")

    engine = VoiceEngine(tmp_path, recorder, source=lambda cb: FakeSource(cb), model_factory=broken)
    engine.start()
    try:
        assert "bad model" in recorder.wait_for("error")["message"]
    finally:
        engine.close()


@pytest.mark.skipif(not REAL_MODEL, reason="needs the speech model (MAGPIE_VOSK_MODEL)")
def test_the_real_speech_model_knows_the_command_words():
    from magpie.desktop.speech import VoskModel

    model = VoskModel(Path(REAL_MODEL))
    assert all(model.knows(word) for word in ("notes", "note", "on", "off", "cancel"))
    assert not model.knows("xqzvbn")


# ------------------------------------------------- writing notes down (Whisper)


def test_whispers_text_loses_the_command_words_and_what_it_hears_in_silence():
    clean = transcribe.clean
    assert clean("Notes on, buy milk tomorrow. Notes off.") == "Buy milk tomorrow."
    assert clean("Note's on. Call Mum about Sunday.") == "Call Mum about Sunday."
    assert clean("Pay the rent at 9. Notes off. Notes off.") == "Pay the rent at 9."
    assert clean("Call James at 5 p.m. Cancel note.") == "Call James at 5 p.m."
    # Only at the very start and the very end.
    assert clean("This is notes off the record.") == "This is notes off the record."
    assert clean("iPhone charger is on the desk") == "iPhone charger is on the desk"
    assert clean("Hello Magpie, call the bank. Over and out.", "hello magpie", "over and out") == (
        "Call the bank."
    )
    for nothing in ("Thank you.", " Okay. ", "...", "Notes on.", "", "you"):
        assert clean(nothing) == ""


def test_whisper_hears_sound_at_its_own_rate():
    np = pytest.importorskip("numpy")
    second = array("h", [16384] * 48000).tobytes()  # a second at 48 kHz
    audio = transcribe.to_float(second, 48000)
    assert audio.dtype == np.float32 and len(audio) == 16000
    assert abs(float(audio[100]) - 0.5) < 1e-3
    assert len(transcribe.to_float(second[:-1], 16000)) == 47999  # half a sample is dropped


def tone(np, hertz: float, rate: int, level: float, seconds: float = 1.0):
    return level * np.sin(2 * np.pi * hertz * np.arange(int(rate * seconds)) / rate)


def strength(np, audio, hertz: float, rate: int = 16000) -> float:
    spectrum = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
    return float(spectrum[int(round(hertz * len(audio) / rate))])


def test_sounds_too_high_for_whisper_do_not_fold_back_over_the_words():
    np = pytest.importorskip("numpy")
    # A voice at 1 kHz and a whine at 12 kHz, heard at 48 kHz: at 16 kHz, the whine
    # would come back as a 4 kHz one unless it's filtered out first.
    sound = tone(np, 1000, 48000, 0.3) + tone(np, 12000, 48000, 0.3)
    pcm = (sound * 32767).astype(np.int16).tobytes()
    audio = transcribe.to_float(pcm, 48000)
    voice, whine = strength(np, audio, 1000), strength(np, audio, 4000)
    assert whine < voice / 300  # more than 50 dB down


def test_a_quiet_microphone_is_brought_up_and_a_loud_one_left_alone():
    np = pytest.importorskip("numpy")

    def level(peak: float) -> float:
        pcm = (tone(np, 440, 16000, peak) * 32767).astype(np.int16).tobytes()
        return float(np.max(np.abs(transcribe.to_float(pcm, 16000))))

    assert level(0.05) == pytest.approx(0.5, rel=0.01)  # brought up to half the most
    assert level(0.001) == pytest.approx(0.02, rel=0.05)  # but never more than 20 times
    assert level(0.8) == pytest.approx(0.8, rel=0.01)  # never turned down
    assert not np.any(transcribe.to_float(bytes(32000), 16000))  # silence stays silent


class FakeSegment:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeWhisperModel:
    def __init__(self, *texts: str, slow: float = 0.0) -> None:
        self.texts = texts
        self.slow = slow
        self.calls: list[dict[str, Any]] = []

    def transcribe(self, audio, **options):
        self.calls.append(options)

        def segments():
            for text in self.texts:
                time.sleep(self.slow)
                yield FakeSegment(text)

        return segments(), None


def test_the_transcriber_loads_whisper_once_and_lets_go_when_idle(tmp_path):
    pytest.importorskip("numpy")
    model = FakeWhisperModel(" Notes on, remind me to call James", " at 5 p.m. tomorrow. ")
    loads: list[tuple] = []
    transcriber = transcribe.Transcriber(
        tmp_path, threads=2, load=lambda folder, threads: loads.append((folder, threads)) or model
    )
    second = bytes(32000)
    assert transcriber.transcribe(second, 16000) == "Remind me to call James at 5 p.m. tomorrow."
    assert transcriber.transcribe(second, 16000, wake="notes on") != ""
    assert loads == [(tmp_path, 2)]  # loaded once
    options = model.calls[0]
    assert options["language"] == "en" and options["vad_filter"] is True
    assert options["beam_size"] == 5 and "temperature" not in options
    assert "hotwords" not in options
    transcriber.transcribe(second, 16000, words=["James", "Kinnaird Road"])
    assert model.calls[-1]["hotwords"] == "James, Kinnaird Road"  # words to listen out for
    transcriber.transcribe(second, 16000, quick=True)  # the words shown as you speak
    assert model.calls[-1]["beam_size"] == 1 and model.calls[-1]["temperature"] == 0.0
    assert transcriber.transcribe(bytes(3200), 16000) == ""  # a tenth of a second: nothing
    transcriber.release_if_idle(idle=60)
    assert transcriber.loaded
    transcriber.release_if_idle(idle=0)
    assert not transcriber.loaded


def test_a_slow_transcription_gives_up_so_the_listeners_words_are_kept(tmp_path):
    pytest.importorskip("numpy")
    model = FakeWhisperModel("one", "two", "three", slow=0.05)
    transcriber = transcribe.Transcriber(tmp_path, load=lambda folder, threads: model)
    with pytest.raises(TimeoutError):
        transcriber.transcribe(bytes(32000), 16000, budget=0.01)


def whisper_files(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("model.bin", "tokenizer.json", "config.json"):
        (folder / name).write_bytes(b"x" * 1000)


def made(model: transcribe.Transcriber | None) -> transcribe.Transcriber:
    """A model the Whisper manager has (it has none while it's off or downloading)."""
    assert model is not None
    return model


def test_the_whisper_model_is_downloaded_whole_or_not_at_all(tmp_path, monkeypatch):
    monkeypatch.delenv("MAGPIE_WHISPER_MODEL", raising=False)
    home = paths.AppPaths(tmp_path)
    assert transcribe.find_whisper(home, "small.en") is None
    asked: list = []

    def download(repo, folder, patterns):
        asked.append((repo, folder.name, patterns))
        whisper_files(folder)
        (folder / ".cache").mkdir()

    heard: list[float] = []
    folder = transcribe.fetch_whisper(home, "small.en", heard.append, download=download)
    assert folder == home.models_dir / "whisper-small.en" and transcribe.complete(folder)
    assert not (folder / ".cache").exists() and not folder.with_name(folder.name + ".part").exists()
    assert asked == [("Systran/faster-whisper-small.en", "whisper-small.en.part", transcribe.FILES)]
    assert heard[-1] == 1.0
    assert transcribe.find_whisper(home, "small.en") == folder
    assert transcribe.find_whisper(home, "large-v3-turbo") is None
    assert transcribe.fetch_whisper(home, "small.en", download=download) == folder  # once

    def broken(repo, folder, patterns):
        (folder / "model.bin").write_bytes(b"half")
        raise OSError("connection reset")

    with pytest.raises(OSError):
        transcribe.fetch_whisper(home, "large-v3-turbo", download=broken)
    assert transcribe.find_whisper(home, "large-v3-turbo") is None  # the rest comes next time
    with pytest.raises(RuntimeError, match="incomplete"):
        transcribe.fetch_whisper(home, "large-v3-turbo", download=lambda *args: None)
    # MAGPIE_WHISPER_MODEL points at a model elsewhere (the builds' tests), used for every
    # model unless the one asked for is next to it.
    elsewhere = tmp_path / "elsewhere" / "whisper-large-v3-turbo"
    whisper_files(elsewhere)
    monkeypatch.setenv("MAGPIE_WHISPER_MODEL", str(elsewhere))
    assert transcribe.find_whisper(home, "large-v3-turbo") == elsewhere
    assert transcribe.find_whisper(home, "small.en") == elsewhere
    whisper_files(elsewhere.with_name("whisper-small.en"))
    assert transcribe.find_whisper(home, "small.en") == elsewhere.with_name("whisper-small.en")
    monkeypatch.setenv("MAGPIE_WHISPER_MODEL", "/")  # nothing useful
    assert transcribe.find_whisper(home, "small.en") == folder


def wait_until(check, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.01)


def test_the_chosen_whisper_model_is_downloaded_in_the_background(tmp_path, monkeypatch):
    monkeypatch.delenv("MAGPIE_WHISPER_MODEL", raising=False)
    home = paths.AppPaths(tmp_path)
    whisper_files(transcribe.model_folder(home, "small.en"))
    gate, fail = threading.Event(), []

    def fetch(app_paths, name, progress):
        progress(0.4)
        gate.wait(5)
        if fail:
            raise RuntimeError(fail[0])
        whisper_files(transcribe.model_folder(app_paths, name))
        return transcribe.model_folder(app_paths, name)

    changes: list[str] = []
    whisper = transcribe.Whisper(
        home,
        on_change=lambda: changes.append(whisper.state),
        fetch=fetch,
        make=lambda folder: transcribe.Transcriber(folder, load=lambda *a: None),
        available=True,
    )
    whisper.choose("small.en")  # already here
    assert whisper.state == "ready" and made(whisper.transcriber).folder.name == "whisper-small.en"
    small = whisper.transcriber
    assert whisper.quick is small  # it also writes the words shown as you speak
    assert whisper.status()["live"] is True and whisper.status()["meanwhile"] is False

    whisper.choose("large-v3-turbo")
    status = whisper.status()
    assert status["state"] == "downloading" and status["getting"] == "large-v3-turbo"
    wait_until(lambda: whisper.progress == 0.4)
    assert whisper.transcriber is small and whisper.meanwhile  # the faster one meanwhile
    gate.set()
    wait_until(lambda: whisper.state == "ready")
    best = made(whisper.transcriber)
    assert best.folder.name == "whisper-large-v3-turbo"
    assert whisper.quick is small and not whisper.meanwhile and whisper.getting == ""

    def no_memory(folder, threads):
        raise MemoryError("not enough memory")

    best._load_model = no_memory
    best.warm()  # loading fails in the background, quietly
    wait_until(lambda: best.problem)
    status = whisper.status()  # says why, rather than "ready"
    assert status["state"] == "error" and "not enough memory" in status["message"]
    assert "faster one" in status["message"]  # which writes notes down instead

    whisper.choose("off")
    assert whisper.state == "off" and whisper.transcriber is None and whisper.quick is None
    monkeypatch.setattr(transcribe.os, "cpu_count", lambda: 16)
    whisper.choose("something else")  # an unknown choice means the default: automatic
    assert whisper.chosen == "auto" and whisper.choice == "large-v3-turbo"
    assert whisper.state == "ready" and whisper.status()["chosen"] == "auto"
    assert changes[-1] == "ready"


def test_automatic_means_the_most_accurate_model_on_a_computer_quick_enough(monkeypatch):
    assert transcribe.suited(16) == transcribe.suited(8) == "large-v3-turbo"
    assert transcribe.suited(4) == transcribe.suited(2) == "small.en"
    monkeypatch.setattr(transcribe.os, "cpu_count", lambda: None)  # it can't tell
    assert transcribe.suited() == "small.en"


def test_the_quick_model_comes_first_and_does_both_jobs_until_the_other_is_here(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("MAGPIE_WHISPER_MODEL", raising=False)
    home = paths.AppPaths(tmp_path)
    gates = {"small.en": threading.Event(), "large-v3-turbo": threading.Event()}
    asked: list[str] = []

    def fetch(app_paths, name, progress):
        asked.append(name)
        progress(0.5)
        gates[name].wait(5)
        whisper_files(transcribe.model_folder(app_paths, name))

    whisper = transcribe.Whisper(
        home,
        fetch=fetch,
        make=lambda folder: transcribe.Transcriber(folder, load=lambda *a: None),
        available=True,
    )
    whisper.choose("large-v3-turbo")  # a new install: nothing is here yet
    wait_until(lambda: whisper.progress == 0.5)
    status = whisper.status()
    assert status["state"] == "downloading" and status["getting"] == "small.en"
    assert whisper.transcriber is None and whisper.quick is None

    gates["small.en"].set()
    wait_until(lambda: whisper.getting == "large-v3-turbo")
    assert whisper.quick is not None and whisper.transcriber is whisper.quick
    assert whisper.status()["meanwhile"] and whisper.state == "downloading"

    gates["large-v3-turbo"].set()
    wait_until(lambda: whisper.state == "ready")
    best, quick = made(whisper.transcriber), made(whisper.quick)
    assert best.folder.name == "whisper-large-v3-turbo" and quick.folder.name == "whisper-small.en"
    assert asked == ["small.en", "large-v3-turbo"]
    wait_until(lambda: not whisper._downloading)

    # Both are let go when they've been idle a while (the clock may have started recently).
    for model in (best, quick):
        model._model = object()
        model._used = time.monotonic() - transcribe.IDLE_SECONDS - 1
    whisper.tick()
    assert not best.loaded and not quick.loaded


def test_a_failed_whisper_download_is_tried_again_later(tmp_path, monkeypatch):
    monkeypatch.delenv("MAGPIE_WHISPER_MODEL", raising=False)
    home = paths.AppPaths(tmp_path)
    tries: list[str] = []

    def fetch(app_paths, name, progress):
        tries.append(name)
        if len(tries) == 1:
            raise RuntimeError("no internet")
        whisper_files(transcribe.model_folder(app_paths, name))

    whisper = transcribe.Whisper(
        home, fetch=fetch, make=lambda folder: transcribe.Transcriber(folder), available=True
    )
    whisper.choose("small.en")
    wait_until(lambda: whisper.state == "error")
    message = whisper.status()["message"]
    assert "no internet" in message and "tries again later" in message
    assert whisper.transcriber is None
    whisper.tick()
    assert tries == ["small.en"]  # not straight away
    whisper._failed_at -= transcribe.RETRY_SECONDS + 1
    whisper.tick()
    wait_until(lambda: whisper.state == "ready")
    assert tries == ["small.en", "small.en"]

    missing = transcribe.Whisper(home, available=False)
    missing.choose("small.en")
    assert missing.state == "unavailable" and missing.transcriber is None


# ------------------------------------------------------------------ the app


class FakeUI:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.bar_visible = False

    def bar_call(self, name, payload=None):
        self.calls.append(("bar", name, payload))

    def alerts_call(self, name, payload):
        self.calls.append(("alerts", name, payload))

    def show_bar(self, keyboard=False):
        self.bar_visible = True
        self.calls.append(("show", keyboard))

    def hide_bar(self):
        self.bar_visible = False
        self.calls.append(("hide",))

    def show_main(self, path="/"):
        self.calls.append(("main", path))

    def bar(self, name):
        return [payload for kind, n, *payload in self.calls if kind == "bar" and n == name]


class FakeClient:
    def __init__(self, fail: str = "") -> None:
        self.fail = fail
        self.notes: list[tuple] = []
        self.reminders: list[tuple] = []
        self.changes: list[dict] = []

    def update_prefs(self, changes):
        self.changes.append(changes)
        return {"wake_phrase": "notes on", **changes}

    def transcribe(self, wav):  # the speech service chosen in Settings → Voice
        self.sent = getattr(self, "sent", []) + [wav]
        if isinstance(self.heard, Exception):
            raise self.heard
        return self.heard

    heard: Any = ""

    def create_note(self, text, source, captured_in=""):
        if self.fail:
            raise RuntimeError(self.fail)
        self.notes.append((text, source))
        return {"note": {"id": 1, "state": "ready", "title": "Buy milk", "reply": "Saved."}}

    def note(self, note_id):
        return {"id": note_id, "state": "ready", "title": "Buy milk", "reply": "Saved."}

    def reminder(self, reminder_id, action):
        self.reminders.append((reminder_id, action))
        return {}


class FakeEngine:
    def __init__(self) -> None:
        self.commands: list[tuple] = []

    def command(self, name, *args):
        self.commands.append((name, *args))


@pytest.fixture
def app(tmp_path):
    desktop = DesktopApp(paths.AppPaths(tmp_path))
    desktop.ui, desktop.client, desktop.engine = cast(Any, (FakeUI(), FakeClient(), FakeEngine()))
    desktop.ui_ready("bar")
    desktop.ui_ready("alerts")
    yield desktop
    desktop._quitting.set()  # as when the app quits: late timers do nothing
    desktop._work.shutdown(wait=True)
    desktop._ui_queue.shutdown(wait=True)


def settle(app, until=lambda: True, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app._ui_queue.submit(lambda: None).result(timeout)
        if until():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


def square(amplitude: int, seconds: float = 0.25) -> bytes:
    """A square wave this loud (out of 32767), as 16-bit sound."""
    samples = array(
        "h", [amplitude if (i // 20) % 2 else -amplitude for i in range(int(RATE * seconds))]
    )
    return samples.tobytes()


def test_how_loud_sound_is_on_a_meters_scale():
    assert sound_level(b"") == 0.0 and sound_level(bytes(8000)) == 0.0  # silence
    assert sound_level(square(32767)) == 1.0  # as loud as it gets
    speech = sound_level(square(3000))  # about -21 dB: someone talking near the microphone
    assert 0.55 < speech < 0.75
    assert 0 < sound_level(square(60)) < 0.15  # a quiet room
    assert sound_level(square(3000) + b"\x01") == speech  # half a sample at the end is ignored


def test_the_bar_shows_how_loud_the_microphone_is_while_recording(app, monkeypatch):
    """A few times a second, so you can see Magpie hears you; never between notes."""
    now = [1000.0]
    monkeypatch.setattr("magpie.desktop.app.time.monotonic", lambda: now[0])
    app._on_voice("sound", {"pcm": square(3000), "rate": RATE})  # not recording: nothing
    app._on_voice("state", {"state": "recording", "by_voice": True})
    for _ in range(4):  # a second of sound, in quarter seconds
        app._on_voice("sound", {"pcm": square(3000), "rate": RATE})
        now[0] += 0.25
    app._on_voice("sound", {"pcm": square(3000), "rate": RATE})
    app._on_voice("sound", {"pcm": bytes(4000), "rate": RATE})  # too soon after: skipped
    now[0] += 0.25
    app._on_voice("sound", {"pcm": bytes(4000), "rate": RATE})
    settle(app)
    levels = [payload[0]["level"] for payload in app.ui.bar("level")]
    assert len(levels) == 6 and all(0.55 < level < 0.75 for level in levels[:5])
    assert levels[-1] == 0.0
    voice_note(app, "buy milk")  # the note ends: no more levels
    app._on_voice("sound", {"pcm": square(3000), "rate": RATE})
    now[0] += 1
    app._on_voice("sound", {"pcm": square(3000), "rate": RATE})
    settle(app)
    assert len(app.ui.bar("level")) == 6


def test_a_voice_note_shows_in_the_bar_and_is_saved(app):
    app._on_voice("state", {"state": "recording", "by_voice": True})
    app._on_voice("words", {"final": "buy milk", "partial": ""})
    app._on_voice("note", {"text": "buy milk", "reason": "phrase"})
    settle(app, lambda: app.ui.bar("saved") and app.ui.bar("saved")[-1][0]["reply"] == "Saved.")
    assert app.client.notes == [("buy milk", "desktop-voice")]
    assert ("show", False) in app.ui.calls  # shown without taking the keyboard
    assert app.ui.bar("recording") and app.ui.bar("words")


def test_a_note_that_cannot_be_saved_comes_back_to_the_bar(app):
    app.client.fail = "Magpie answered 500."
    app._on_voice("note", {"text": "call james", "reason": "phrase"})
    settle(app, lambda: app.ui.bar("typing"))
    typing = app.ui.bar("typing")[-1][0]
    assert typing["text"] == "call james" and typing["status"].startswith("Not saved")


def test_a_discarded_note_can_be_undone(app):
    app._on_voice("cancelled", {"text": "call mum", "reason": "phrase"})
    app.undo()
    settle(app, lambda: app.ui.bar("typing"))
    assert app.ui.bar("discarded")[-1][0]["text"] == "call mum"
    assert app.ui.bar("typing")[-1][0]["text"] == "call mum"


def test_typed_words_and_dictation_are_saved_together(app):
    app._on_voice("state", {"state": "recording", "by_voice": False})
    app.bar_edited("Shopping list:\n")
    app._on_voice("note", {"text": "eggs and bread", "reason": "phrase"})
    settle(app, lambda: app.client.notes)
    assert app.client.notes == [("Shopping list:\neggs and bread", "desktop-voice")]
    assert ("rebase",) in app.engine.commands


class FakeTranscriber:
    def __init__(
        self,
        text: str = "",
        *,
        fail: str = "",
        wait: threading.Event | None = None,
        live: list[str] | None = None,
    ):
        self.text, self.fail, self.wait = text, fail, wait
        self.texts = list(live or [text])  # for the words shown as you speak, in turn
        self.heard: list[tuple] = []
        self.live: list[int] = []  # how much sound each go while you spoke had
        self.warmed = 0

    def warm(self) -> None:
        self.warmed += 1

    def transcribe(self, pcm, rate, *, wake=None, stop=None, quick=False, words=()):
        self.words = list(words)
        if quick:
            self.live.append(len(pcm))
            return self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]
        self.heard.append((len(pcm), rate, wake, stop))
        if self.wait is not None:
            self.wait.wait(5)
        if self.fail:
            raise RuntimeError(self.fail)
        return self.text


class FakeWhisper:
    def __init__(self, transcriber: Any = None, quick: Any = None) -> None:
        self.transcriber = transcriber
        self.quick = quick
        self.state = "ready" if transcriber else "off"
        self.getting, self.progress = "", 0.0
        self.chosen: list[str] = []

    def choose(self, name):
        self.chosen.append(name)

    def status(self):
        return {"model": "small.en", "state": self.state}


def voice_note(app, text: str, seconds: float = 2.0) -> None:
    sound = bytes(int(RATE * seconds) * 2)
    app._on_voice("note", {"text": text, "reason": "phrase", "audio": sound, "rate": RATE})


def test_whisper_writes_the_note_down_again_before_it_is_saved(app):
    whisper = FakeTranscriber("Buy milk tomorrow at 9.")
    app.whisper = FakeWhisper(whisper)
    app.apply_prefs({"wake_phrase": "hello magpie", "stop_phrase": "over and out"})
    app._on_voice("state", {"state": "recording", "by_voice": True})
    assert whisper.warmed == 1  # loading while the note is said
    voice_note(app, "by milk tomorrow at nine")
    settle(app, lambda: app.ui.bar("saved"))  # shown after everything before it
    assert app.client.notes == [("Buy milk tomorrow at 9.", "desktop-voice")]
    assert whisper.heard == [(64000, RATE, "hello magpie", "over and out")]
    saving = app.ui.bar("saving")
    assert saving[0][0] == {"text": "by milk tomorrow at nine", "status": "Writing it down…"}
    assert saving[-1][0] == {"text": "Buy milk tomorrow at 9."}
    assert app.whisper.chosen == ["auto"]  # the default: what suits this computer


def test_the_listeners_words_are_saved_when_whisper_fails_or_hears_nothing(app):
    app.whisper = FakeWhisper(FakeTranscriber(fail="out of memory"))
    voice_note(app, "call james")
    settle(app, lambda: app.client.notes)
    app.whisper = FakeWhisper(FakeTranscriber(""))
    voice_note(app, "water the plants")
    settle(app, lambda: len(app.client.notes) == 2)
    app.whisper = FakeWhisper(None)  # no model yet
    voice_note(app, "pay the rent")
    settle(app, lambda: len(app.client.notes) == 3)
    saved = [text for text, _ in app.client.notes]
    assert saved == ["call james", "water the plants", "pay the rent"]


def test_typed_words_and_whispers_text_are_saved_together(app):
    app.whisper = FakeWhisper(FakeTranscriber("Eggs and bread."))
    app._on_voice("state", {"state": "recording", "by_voice": False})
    app.bar_edited("Shopping list:\n")
    voice_note(app, "eggs and bred")
    settle(app, lambda: app.client.notes)
    assert app.client.notes == [("Shopping list:\nEggs and bread.", "desktop-voice")]


def test_a_new_note_started_while_whisper_works_keeps_the_bar(app):
    gate = threading.Event()
    app.whisper = FakeWhisper(FakeTranscriber("Call James.", wait=gate))
    voice_note(app, "call james")
    app._on_voice("state", {"state": "recording", "by_voice": True})  # the next note
    gate.set()
    settle(app, lambda: app.client.notes)
    assert app.client.notes == [("Call James.", "desktop-voice")]
    time.sleep(0.1)
    settle(app)
    bar = [call[1] for call in app.ui.calls if call[0] == "bar"]
    assert bar[-1] == "recording"  # the old note was saved without taking the bar
    assert Bridge(app).status()["speech"] == {"model": "small.en", "state": "ready"}


def sound(app, seconds: float, chunk: float = 0.25) -> None:
    for _ in range(round(seconds / chunk)):
        app._on_voice("sound", {"pcm": bytes(int(RATE * chunk) * 2), "rate": RATE})


def shown_words(app) -> list[dict]:
    return [payload[0] for payload in app.ui.bar("words")]


def last_words(app) -> dict:
    shown = shown_words(app)
    return shown[-1] if shown else {}


def test_whispers_words_show_in_the_bar_as_you_speak(app):
    quick = FakeTranscriber(live=["Buy milk."])
    app.whisper = FakeWhisper(FakeTranscriber("Buy milk tomorrow at 9."), quick=quick)
    app._on_voice("state", {"state": "recording", "by_voice": True})
    assert quick.warmed == 1 and app.whisper.transcriber.warmed == 1  # both get ready
    timed = [("by", 0.2), ("milk", 0.5)]
    app._on_voice("words", {"final": "", "partial": "by milk", "timed": timed})
    settle(app, lambda: last_words(app).get("partial") == "by milk")  # until Whisper's
    sound(app, 1.5)
    settle(app, lambda: last_words(app).get("final") == "Buy milk.")
    assert quick.live and quick.live[0] >= RATE * 2  # once a second had been said
    # The listener's words for what Whisper hasn't reached yet come after Whisper's.
    timed += [("to", 1.6), ("morrow", 1.8)]
    app._on_voice("words", {"final": "by milk", "partial": "to morrow", "timed": timed})
    settle(app)
    assert last_words(app) == {"base": "", "final": "Buy milk.", "partial": "to morrow"}
    # The note ends: Whisper's words stay up while the chosen model writes it all down.
    voice_note(app, "by milk to morrow at nine")
    settle(app, lambda: app.client.notes)
    assert app.ui.bar("saving")[0][0] == {"text": "Buy milk.", "status": "Writing it down…"}
    assert app.client.notes == [("Buy milk tomorrow at 9.", "desktop-voice")]
    assert app._live is None


def test_the_faster_model_writes_the_note_down_when_the_chosen_one_fails(app):
    quick = FakeTranscriber("Call James at 5.")
    app.whisper = FakeWhisper(FakeTranscriber(fail="not enough memory"), quick=quick)
    voice_note(app, "call james at five")
    settle(app, lambda: app.client.notes)
    assert app.client.notes == [("Call James at 5.", "desktop-voice")]
    assert app.whisper.transcriber.heard and quick.heard  # tried in that order


def test_typing_over_the_words_starts_whisper_again_from_what_is_said_next(app):
    quick = FakeTranscriber(live=["Shopping list.", "Eggs."])
    app.whisper = FakeWhisper(FakeTranscriber("Eggs and bread."), quick=quick)
    app._on_voice("state", {"state": "recording", "by_voice": False})
    sound(app, 1.0)
    settle(app, lambda: last_words(app).get("final") == "Shopping list.")
    app.bar_edited("Shopping list:\n")
    sound(app, 1.0)
    settle(app, lambda: last_words(app).get("final") == "Eggs.")
    assert last_words(app)["base"] == "Shopping list:\n"
    assert quick.live[-1] == RATE * 2  # only the second said after typing
    voice_note(app, "eggs and bred")
    settle(app, lambda: app.client.notes)
    assert app.client.notes == [("Shopping list:\nEggs and bread.", "desktop-voice")]


def test_whispers_words_from_a_note_that_has_ended_are_not_shown(app):
    app.whisper = FakeWhisper(FakeTranscriber("Call James."), quick=FakeTranscriber())
    app._on_voice("state", {"state": "recording", "by_voice": True})
    live = app._live
    app._on_voice("state", {"state": "standby", "by_voice": False})
    assert app._live is None and live.stopped
    app._live_words(live, "Too late.", 0.0, 2.0)
    settle(app)
    assert all(words["final"] != "Too late." for words in shown_words(app))


def test_a_long_note_keeps_the_listeners_words_for_what_whisper_no_longer_hears(app):
    app.whisper = FakeWhisper(FakeTranscriber("Call James."), quick=FakeTranscriber())
    app._on_voice("state", {"state": "recording", "by_voice": True})
    timed = [("first", 0.5), ("part", 1.0), ("and", 30.5), ("then", 31.0), ("more", 33.0)]
    app._on_voice("words", {"final": "first part and then", "partial": "more", "timed": timed})
    app._live_words(app._live, "And then.", 3.0, 32.0)  # the last 28 seconds
    settle(app, lambda: last_words(app).get("final") == "first part And then.")
    assert last_words(app)["partial"] == "more"
    voice_note(app, "first part and then more")
    settle(app, lambda: app.client.notes)
    # Kept in the bar while it's written down: nothing of the note goes missing.
    assert app.ui.bar("saving")[0][0]["text"] == "first part And then."


def test_the_bar_says_the_words_get_better_once_whisper_is_downloaded(app):
    app.whisper = FakeWhisper()
    app.whisper.getting, app.whisper.progress = "small.en", 0.25
    app._on_voice("state", {"state": "recording", "by_voice": True})
    settle(app, lambda: app.ui.bar("recording"))
    assert "once Whisper is downloaded (25%)" in app.ui.bar("recording")[-1][0]["hint"]
    app.whisper = FakeWhisper(FakeTranscriber("x"), quick=FakeTranscriber("x"))
    assert "notes off" in app._hint()


def test_installs_from_before_move_once_to_the_model_that_suits_the_computer(app):
    prefs = app._upgrade_speech({"speech_model": "small.en"})
    assert prefs["speech_model"] == "auto"
    assert app.client.changes == [{"speech_model": "auto"}]
    assert paths.DesktopState(app.paths.state_file).get("speech_v2") is True
    # Only once: choosing the faster model again afterwards sticks.
    assert app._upgrade_speech({"speech_model": "small.en"})["speech_model"] == "small.en"
    assert len(app.client.changes) == 1
    # Whisper turned off stays off.
    app.state.set(speech_v2=False)
    assert app._upgrade_speech({"speech_model": "off"}) == {"speech_model": "off"}
    assert len(app.client.changes) == 1 and app.state.get("speech_v2") is True


# ------------------------------------------------------- words as you speak


class LiveModel:
    def __init__(self, *texts: str, fail: str = "", wait: threading.Event | None = None):
        self.texts, self.fail, self.wait = list(texts), fail, wait
        self.calls: list[tuple[float, bool]] = []

    def transcribe(self, pcm, rate, *, wake=None, stop=None, quick=False, words=()):
        self.calls.append((len(pcm) / (2 * rate), quick))
        if self.wait is not None:
            self.wait.wait(5)
        if self.fail:
            raise RuntimeError(self.fail)
        return self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]


def writer(model, **options) -> tuple[LiveWriter, list]:
    shown: list[tuple[str, float]] = []
    live = LiveWriter(model, lambda w, text, since, until: shown.append((text, until)), **options)
    return live, shown


QUARTER = bytes(int(RATE * 0.25) * 2)


def test_whisper_writes_the_note_so_far_again_as_it_grows():
    model = LiveModel("Buy", "Buy milk.", "Buy milk.", "Buy milk.")
    live, shown = writer(model, wake="notes on", stop="notes off")
    for _ in range(3):
        live.add(QUARTER, RATE)
    assert not live.due()  # not a second yet
    live.add(QUARTER, RATE)
    assert live.due()
    live.write()
    assert shown == [("Buy", 1.0)] and model.calls == [(1.0, True)]
    for _ in range(4):
        live.add(QUARTER, RATE)
    live.write()  # from the start of the note again, so earlier words can be put right
    assert shown[-1] == ("Buy milk.", 2.0) and model.calls[-1] == (2.0, True)
    # Nothing changed (a pause): the next go waits longer, up to a limit.
    for _ in range(4):
        live.add(QUARTER, RATE)
    live.write()
    for _ in range(4):
        live.add(QUARTER, RATE)
    assert not live.due()  # it now waits for two seconds more
    live.heard()  # the listener heard new words: no more waiting
    assert live.due()
    for _ in range(4):
        live.write()
    assert live._wait == LONGEST_STEP


def test_whisper_writes_down_the_end_of_a_long_note():
    model = LiveModel("And that's the end.")
    spans: list = []
    live = LiveWriter(model, lambda w, text, since, until: spans.append((text, since, until)))
    live.window = 5.0
    live.add(bytes(RATE * 2 * 8), RATE)  # eight seconds
    live.write()
    assert model.calls == [(5.0, True)]
    assert spans == [("And that's the end.", 3.0, 8.0)]  # the last five seconds


def test_words_typed_over_are_not_written_down_again():
    model = LiveModel("Eggs.")
    live, shown = writer(model)
    live.add(bytes(RATE * 2 * 2), RATE)
    live.reset()
    assert not live.due() and live.text == ""
    live.add(bytes(RATE * 2), RATE)
    live.write()
    assert model.calls == [(1.0, True)] and shown == [("Eggs.", 3.0)]


def test_words_written_after_the_note_ended_or_was_typed_over_are_not_shown():
    gate = threading.Event()
    live, shown = writer(LiveModel("Too late.", wait=gate))
    live.add(bytes(RATE * 2), RATE)
    going = threading.Thread(target=live.write)
    going.start()
    live.stop()
    gate.set()
    going.join(5)
    gate.clear()
    live, shown2 = writer(LiveModel("Typed over.", wait=gate))
    live.add(bytes(RATE * 2), RATE)
    going = threading.Thread(target=live.write)
    going.start()
    live.reset()
    gate.set()
    going.join(5)
    assert shown == [] and shown2 == []


def test_live_words_run_on_their_own_and_give_up_quietly_when_whisper_fails():
    model = LiveModel("Call James.")
    live, shown = writer(model, step=0.25)
    live.start()
    live.add(bytes(RATE), RATE)  # half a second
    wait_until(lambda: shown)
    live.stop()
    assert shown[0] == ("Call James.", 0.5)

    broken = LiveModel(fail="out of memory")
    live, shown = writer(broken, step=0.25)
    live.start()
    thread = live._thread
    assert thread is not None
    deadline = time.monotonic() + 5
    while thread.is_alive() and time.monotonic() < deadline:  # as you go on speaking
        live.add(QUARTER, RATE)
        time.sleep(0.02)
    assert not thread.is_alive()
    assert len(broken.calls) == GIVE_UP and shown == []


def test_a_speech_service_writes_the_note_down_and_whisper_steps_in_when_it_fails(app):
    app.whisper = FakeWhisper(FakeTranscriber("Call James at 5."))
    app.apply_prefs({"speech_model": "service", "vocabulary": "James, Kinnaird Road"})
    assert app.whisper.chosen == ["small.en"]  # still there for the words as you speak
    app.client.heard = "Notes on, call James at five on Kinnaird Road. Notes off."
    voice_note(app, "call james at five")
    settle(app, lambda: app.client.notes)
    assert app.client.notes == [("Call James at five on Kinnaird Road.", "desktop-voice")]
    assert app.client.sent[0].startswith(b"RIFF")  # the note's sound, as a WAV file
    assert app.whisper.transcriber.heard == []  # Whisper wasn't needed

    def said(words: str) -> bool:  # what the bar said while saving
        return any(words in p[0].get("status", "") for p in app.ui.bar("saving"))

    app.client.heard = RuntimeError("Groq refused the key.")
    voice_note(app, "call james at five")
    settle(app, lambda: len(app.client.notes) == 2 and said("Groq refused the key."))
    assert app.client.notes[-1] == ("Call James at 5.", "desktop-voice")
    assert app.whisper.transcriber.words == ["James", "Kinnaird Road"]
    assert said("Groq refused the key. Whisper wrote it down instead.")

    app.whisper = FakeWhisper()  # no Whisper either: the listener's words, and it says so
    app.client.heard = RuntimeError("your speech service couldn't be reached: no route to host")
    voice_note(app, "water the plants")
    settle(app, lambda: len(app.client.notes) == 3 and said("The note keeps the words heard."))
    assert app.client.notes[-1] == ("water the plants", "desktop-voice")
    assert said(
        "Your speech service couldn't be reached: no route to host. The note keeps the words heard."
    )


def test_a_dictation_app_of_your_own_types_the_note_into_the_bar(app):
    app.whisper = FakeWhisper(FakeTranscriber("x"))
    app.apply_prefs(
        {"speech_model": "dictation", "stop_phrase": "over and out", "silence_seconds": 5}
    )
    assert app.whisper.chosen == ["off"]  # no model needed here
    app._on_voice("state", {"state": "recording", "by_voice": True})
    settle(app, lambda: app.ui.bar("dictate"))
    assert app.ui.bar("dictate")[-1][0] == {"stop": "over and out", "seconds": 5}
    assert ("show", True) in app.ui.calls  # the bar takes the keyboard for the app to type in
    assert ("release",) in app.engine.commands  # Magpie's listener lets go of the note
    app._on_voice("words", {"final": "rough words", "partial": ""})  # not shown over it
    app._on_voice("state", {"state": "recording", "by_voice": True})  # heard again
    settle(app)
    assert len(app.ui.bar("dictate")) == 1 and not app.ui.bar("words")

    bridge = Bridge(app)
    assert bridge.ui("bar", "save_dictated", {"text": "Buy milk"}) is None
    settle(app, lambda: app.client.notes)
    assert app.client.notes == [("Buy milk", "desktop-voice")]
    bridge.ui("bar", "discard", {"text": "Call Mum"})
    app.undo()
    settle(app, lambda: app.ui.bar("typing"))
    assert app.ui.bar("discarded")[-1][0]["text"] == "Call Mum"
    assert app.ui.bar("typing")[-1][0]["text"] == "Call Mum"

    app.voice_state = "no-mic"  # the tray's "Start a voice note" needs no microphone here
    app.start_note()
    settle(app, lambda: len(app.ui.bar("dictate")) == 2)


def test_the_listener_can_let_go_of_a_note_without_making_one():
    events: list = []
    listener = Listener(FakeModel(say("buy milk", 1.0)), lambda k, d: events.append((k, d)))
    listener.start()
    for pcm in list(audio(2)):
        listener.feed(pcm)
    listener.release()
    assert listener.state == "standby"
    assert outcome(events) == ["recording", "standby"]  # no note, nothing cancelled


def test_a_note_for_a_speech_service_is_a_wav_file_and_tiny_ones_are_not_sent():
    import wave

    sent: list[bytes] = []
    service = transcribe.SpeechService(lambda data: sent.append(data) or "Notes on, buy milk.")
    pcm = bytes(int(RATE * 2) * 2)
    assert service.transcribe(pcm, RATE, wake="notes on") == "Buy milk."
    with wave.open(io.BytesIO(sent[0])) as recording:
        assert (recording.getframerate(), recording.getnchannels(), recording.getnframes()) == (
            RATE,
            1,
            RATE * 2,
        )
    assert service.transcribe(bytes(200), RATE) == "" and len(sent) == 1  # too short to send
    # A microphone at 48 kHz: sent at 16 kHz, which is all a service listens at.
    pytest.importorskip("numpy")
    service.transcribe(bytes(48000 * 2 * 2), 48000)
    with wave.open(io.BytesIO(sent[-1])) as recording:
        assert (recording.getframerate(), recording.getnframes()) == (RATE, RATE * 2)


def test_the_apps_client_reaches_the_speech_service_through_the_server():
    from magpie.desktop.server import LocalClient

    answers = iter(
        [
            httpx2.Response(200, json={"text": "Call James."}),
            httpx2.Response(422, json={"detail": "No speech was detected in the recording."}),
            httpx2.Response(502, json={"detail": "Groq refused the key."}),
        ]
    )
    seen: list = []

    def handler(request):
        seen.append(request)
        return next(answers)

    client = LocalClient("http://127.0.0.1:8000", "token")
    client._http = httpx2.Client(
        base_url="http://127.0.0.1:8000", transport=httpx2.MockTransport(handler)
    )
    assert client.transcribe(b"RIFF") == "Call James."
    assert seen[0].url.path == "/api/transcribe" and b'filename="note.wav"' in seen[0].content
    assert client.transcribe(b"RIFF") == ""  # it heard nothing
    with pytest.raises(RuntimeError, match="Groq refused the key."):
        client.transcribe(b"RIFF")


def test_reminders_pop_up_and_their_buttons_work(app):
    app.show_alert({"title": "Call James", "reminder_id": 7, "snooze_minutes": 30, "due": "5 PM"})
    settle(app, lambda: any(c[:2] == ("alerts", "add") for c in app.ui.calls))
    card = next(c[2] for c in app.ui.calls if c[:2] == ("alerts", "add"))
    assert card["snooze"] == "30 min" and card["reminder"] is True
    assert app.alert_action(card["key"], "snooze") is None
    assert app.client.reminders == [(7, "snooze")]
    settle(app, lambda: ("alerts", "remove", card["key"]) in app.ui.calls)
    assert app.alert_action(card["key"], "done") is None  # already gone: nothing happens
    assert app.client.reminders == [(7, "snooze")]


def test_the_bar_and_pop_ups_reach_the_app_through_the_bridge(app):
    bridge = Bridge(app)
    assert bridge.ui("bar", "save_text", {"text": " Call James "}) is None
    settle(app, lambda: app.client.notes)
    assert app.client.notes == [("Call James", "desktop-text")]
    assert bridge.ui("bar", "save", {}) is None and ("finish",) in app.engine.commands
    assert bridge.ui("bar", "launch", {}) == "Magpie doesn't know 'launch'."
    app.show_alert({"title": "Call James", "reminder_id": 3})
    settle(app, lambda: any(c[:2] == ("alerts", "add") for c in app.ui.calls))
    key = next(c[2]["key"] for c in app.ui.calls if c[:2] == ("alerts", "add"))
    assert bridge.ui("alerts", "act", {"key": key, "text": "done"}) is None
    assert app.client.reminders == [(3, "done")]


def test_the_dashboard_drives_the_app_through_the_bridge(app):
    bridge = Bridge(app)
    app.voice_state = "standby"
    bridge.command("start")
    bridge.command("pause")
    bridge.prefs_changed({"wake_phrase": "hello magpie", "stop_phrase": "", "silence_seconds": 5})
    assert ("start",) in app.engine.commands and ("pause",) in app.engine.commands
    assert ("configure", "hello magpie", "", 5) in app.engine.commands
    assert app.listening is False and app.state.get("listening") is False
    status = bridge.status()
    assert status["wake"] == "hello magpie" and status["listening"] is False
    bridge.command("listen")
    assert app.listening is True and ("resume",) in app.engine.commands


# ------------------------------------------------------------ moving from Docker


def done(code: int = 0, out: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, out, "")


class FakeDocker(migrate.Docker):
    """Docker with one Magpie container, whose database is a local file."""

    def __init__(self, database: Path, env: list[str], *, running: bool = True) -> None:
        super().__init__("docker")
        self.database, self.env, self.running = database, env, running
        self.calls: list[tuple] = []

    def run(self, *args: str, timeout: float = 60) -> subprocess.CompletedProcess:
        self.calls.append(args)
        if args[0] == "info":
            return done(0 if self.running else 1)
        if args[:2] == ("ps", "-a"):
            item = {
                "ID": "abc123",
                "Image": "ghcr.io/coder-ux-coder/magpie:latest",
                "State": "running",
                "Labels": "com.docker.compose.project=magpie,com.docker.compose.service=magpie",
            }
            return done(0, json.dumps(item) + "\n")
        if args[:2] == ("ps", "-q"):
            return done(0, "abc123\ndef456\n")
        if args[0] == "exec":
            return done(0)
        if args[0] == "cp":
            Path(args[2]).write_bytes(self.database.read_bytes())
            return done(0)
        if args[0] == "inspect":
            return done(0, json.dumps(self.env))
        if args[0] in ("update", "stop"):
            return done(0)
        return done(1)


def old_database(path: Path, notes: int) -> Path:
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT)")
    db.executemany("INSERT INTO notes (text) VALUES (?)", [(f"note {i}",) for i in range(notes)])
    db.commit()
    db.close()
    return path


def test_notes_and_settings_come_over_from_docker_without_the_password(tmp_path, monkeypatch):
    env = [
        "PATH=/usr/bin",
        "MAGPIE_DATA_DIR=/data",
        "MAGPIE_PASSWORD=old-secret",
        "MAGPIE_LOGIN=true",
        "ANTHROPIC_API_KEY=sk-ant-test",
        "MAGPIE_TELEGRAM_BOT_TOKEN=123:abc",
        "MAGPIE_TIMEZONE=Europe/London",
    ]
    docker = FakeDocker(old_database(tmp_path / "old.db", 3), env)
    monkeypatch.setattr(migrate, "find_docker", lambda: docker)
    forgotten = []
    monkeypatch.setattr(migrate, "forget_old_startup", lambda: forgotten.append(1))
    told: list[str] = []
    env_file = tmp_path / "app" / "magpie.env"
    env_file.parent.mkdir()
    env_file.write_text("MAGPIE_TIMEZONE=Europe/Paris\n")  # what the app has wins

    moved = migrate.migrate(tmp_path / "app" / "data", env_file, tell=told.append)

    assert moved is not None and (moved.notes, moved.settings, moved.stopped) == (3, 2, 2)
    text = env_file.read_text()
    assert "ANTHROPIC_API_KEY=sk-ant-test" in text and "MAGPIE_TELEGRAM_BOT_TOKEN" in text
    assert "MAGPIE_TIMEZONE=Europe/Paris" in text
    for gone in ("PASSWORD", "MAGPIE_LOGIN", "MAGPIE_DATA_DIR", "PATH="):
        assert gone not in text
    assert told == ["Bringing over your notes and settings from the old Magpie…"]
    assert ("update", "--restart=no", "abc123", "def456") in docker.calls
    assert forgotten == [1]
    copied = sqlite3.connect(tmp_path / "app" / "data" / "magpie.db")
    assert copied.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 3
    copied.close()
    # Never twice, and never over notes the app already has.
    assert migrate.migrate(tmp_path / "app" / "data", env_file) is None


def test_the_old_magpie_no_longer_opens_at_sign_in(tmp_path, monkeypatch):
    shortcut = tmp_path / "Startup" / "Magpie.lnk"
    monkeypatch.setattr(migrate, "startup_shortcut", lambda: shortcut)
    assert migrate.forget_old_startup() is False  # nothing there
    shortcut.parent.mkdir()
    shortcut.write_bytes(b"old shortcut")
    other = shortcut.parent / "Something else.lnk"
    other.write_bytes(b"not ours")
    assert migrate.forget_old_startup() is True
    assert not shortcut.exists() and other.exists()


def test_nothing_moves_without_docker(tmp_path, monkeypatch):
    monkeypatch.setattr(migrate, "find_docker", lambda: None)
    assert migrate.migrate(tmp_path / "data", tmp_path / "magpie.env") is None
    stopped = FakeDocker(old_database(tmp_path / "old.db", 1), [], running=False)
    monkeypatch.setattr(migrate, "find_docker", lambda: stopped)
    monkeypatch.setattr(migrate, "DOCKER_DESKTOP", {})
    assert migrate.migrate(tmp_path / "data", tmp_path / "magpie.env") is None
    assert not (tmp_path / "data" / "magpie.db").exists()


def test_a_damaged_old_database_is_not_brought_over(tmp_path, monkeypatch):
    broken = tmp_path / "old.db"
    broken.write_bytes(b"not a database at all" * 100)
    monkeypatch.setattr(migrate, "find_docker", lambda: FakeDocker(broken, []))
    with pytest.raises(sqlite3.DatabaseError):
        migrate.migrate(tmp_path / "data", tmp_path / "magpie.env")


# ------------------------------------------------------------------ updates


def latest(version="0.5.0", data=b"installer", **windows) -> dict:
    base = f"https://github.com/{paths.REPOSITORY}/releases/download/v{version}"
    return {
        "version": version,
        "windows": {
            "url": f"{base}/MagpieSetup.exe",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            **windows,
        },
        "mac": {
            "url": f"{base}/Magpie-mac.zip",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        },
    }


def test_release_descriptions_are_checked():
    release = update.parse_latest(latest())
    assert release and release.version == "0.5.0" and release.size == 9
    assert release.url.endswith("/v0.5.0/MagpieSetup.exe")
    mac = update.parse_latest(latest(), "mac")
    assert mac is not None and mac.url.endswith("/v0.5.0/Magpie-mac.zip")
    assert update.parse_latest({**latest(), "mac": None}, "mac") is None
    assert update.newer(release, "0.4.0") and not update.newer(release, "0.5.0")
    assert update.version_key("0.10.0") > update.version_key("0.9.9")
    for bad in (
        None,
        [],
        {"version": "0.5.0"},
        latest(url="https://evil.example/MagpieSetup.exe"),
        latest(url="https://github.com/someone-else/magpie/releases/download/v1/x.exe"),
        latest(sha256="abc"),
        latest(size=0),
        latest(size=10**10),
        latest(version="new"),
    ):
        assert update.parse_latest(bad) is None


def updater(tmp_path, handler, **options) -> update.Updater:
    http = httpx2.Client(transport=httpx2.MockTransport(handler))
    options.setdefault("system", "windows")
    return update.Updater(
        "0.4.0", tmp_path, idle=lambda: True, quit_app=lambda: None, http=http, **options
    )


def test_updates_are_found_downloaded_and_checked(tmp_path):
    data, downloads = b"the new installer", []

    def internet(request):
        if request.url.path.endswith("latest.json"):
            return httpx2.Response(200, json=latest(data=data))
        downloads.append(request.url.path)
        return httpx2.Response(200, content=data)

    found = updater(tmp_path, internet).check()
    assert found and found.version == "0.5.0"
    installer = updater(tmp_path, internet).download(found)
    assert installer.read_bytes() == data and installer.name == "MagpieSetup-0.5.0.exe"
    assert updater(tmp_path, internet).download(found) == installer  # not fetched again
    assert len(downloads) == 1
    mac = updater(tmp_path, internet, system="mac")
    found = mac.check()
    assert found is not None and mac.download(found).name == "Magpie-0.5.0-mac.zip"


def test_a_tampered_download_is_thrown_away(tmp_path):
    release = update.parse_latest(latest(data=b"the real one"))
    assert release is not None
    tampered = updater(tmp_path, lambda r: httpx2.Response(200, content=b"the fake one"))
    with pytest.raises(RuntimeError, match="doesn't match"):
        tampered.download(release)
    larger = updater(tmp_path, lambda r: httpx2.Response(200, content=b"x" * 100))
    with pytest.raises(RuntimeError, match="larger"):
        larger.download(release)
    assert list(tmp_path.iterdir()) == []


def test_github_saying_nothing_useful_is_a_problem_not_an_update(tmp_path):
    with pytest.raises(RuntimeError, match="404"):
        updater(tmp_path, lambda r: httpx2.Response(404)).check()
    with pytest.raises(RuntimeError, match="doesn't describe"):
        updater(tmp_path, lambda r: httpx2.Response(200, content=b"<html>")).check()
    same = updater(tmp_path, lambda r: httpx2.Response(200, json=latest(version="0.4.0")))
    assert same.check() is None


def test_installing_on_windows_runs_the_installer_silently_and_quits(tmp_path, monkeypatch):
    started, quit_called = [], []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: started.append(args))
    up = update.Updater(
        "0.4.0", tmp_path, idle=lambda: True, quit_app=lambda: quit_called.append(1),
        system="windows",
    )  # fmt: skip
    up.install(tmp_path / "MagpieSetup-0.5.0.exe")
    assert started[0][1:] == ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"]
    assert quit_called == [1]


def fake_app(folder: Path, version: str) -> Path:
    binary = folder / "Magpie.app" / "Contents" / "MacOS" / "Magpie"
    binary.parent.mkdir(parents=True)
    binary.write_text(version)
    return folder / "Magpie.app"


@pytest.mark.skipif(sys.platform == "win32", reason="the swap is a shell script for a Mac")
def test_on_a_mac_the_new_app_takes_the_old_ones_place(tmp_path, monkeypatch):
    installed = fake_app(tmp_path / "Applications", "0.4.0")
    archive = tmp_path / "Magpie-0.5.0-mac.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("Magpie.app/Contents/MacOS/Magpie", "0.5.0")

    def ditto(args, **options):  # ditto -x -k <zip> <folder>, as a Mac would
        assert args[:3] == ["/usr/bin/ditto", "-x", "-k"]
        zipfile.ZipFile(args[3]).extractall(args[4])

    swaps, quit_called = [], []
    monkeypatch.setattr(update, "mac_bundle", lambda: installed)
    monkeypatch.setattr(subprocess, "run", ditto)
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: swaps.append(args))
    up = update.Updater(
        "0.4.0", tmp_path / "updates", idle=lambda: True,
        quit_app=lambda: quit_called.append(1), system="mac",
    )  # fmt: skip
    up.install(archive)
    assert quit_called == [1]
    shell, script, pid, app_path, new = swaps[0]
    assert (shell, int(pid), app_path) == ("/bin/sh", os.getpid(), str(installed))

    # The script itself, with stand-ins for macOS's xattr and open.
    tools = tmp_path / "bin"
    tools.mkdir()
    for tool in ("xattr", "open"):
        (tools / tool).write_text(f'#!/bin/sh\necho "{tool} $*" >> "{tmp_path}/tools.log"\n')
        (tools / tool).chmod(0o755)
    env = {**os.environ, "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}"}
    monkeypatch.undo()  # the real subprocess again
    quit_magpie = subprocess.Popen(["true"])  # stands for the Magpie that has quit
    quit_magpie.wait()
    pid = str(quit_magpie.pid)
    subprocess.run(["/bin/sh", script, pid, app_path, new], env=env, check=True, timeout=30)
    assert (installed / "Contents" / "MacOS" / "Magpie").read_text() == "0.5.0"
    assert not Path(app_path + ".old").exists() and not Path(new).exists()
    assert f"open {installed} --args --background" in (tmp_path / "tools.log").read_text()

    # A new app that isn't there leaves the old one where it was.
    subprocess.run(  # mv says what it couldn't move
        ["/bin/sh", script, pid, app_path, new], env=env, check=True, stderr=subprocess.DEVNULL
    )
    assert (installed / "Contents" / "MacOS" / "Magpie").read_text() == "0.5.0"


def test_the_update_button_checks_now_and_installs_on_request(tmp_path, monkeypatch):
    data = b"the new installer"

    def internet(request):
        if request.url.path.endswith("latest.json"):
            return httpx2.Response(200, json=latest(data=data))
        return httpx2.Response(200, content=data)

    monkeypatch.setattr(update, "FIRST_CHECK", 3600.0)  # only when asked
    installed, states = [], []
    up = updater(tmp_path, internet, on_change=lambda: states.append(up.state))
    up.idle = lambda: False  # a note is being taken: no automatic install
    monkeypatch.setattr(up, "why_not", lambda: "")
    monkeypatch.setattr(up, "install", installed.append)
    up.start()
    try:
        up.check_now()
        wait_until(lambda: up.state == "ready")
        status = up.status()
        assert status["available"] == "0.5.0" and status["supported"] and up.asked
        assert states[:3] == ["checking", "downloading", "ready"] and not installed
        up.install_now()
        wait_until(lambda: installed)
        assert installed[0].name == "MagpieSetup-0.5.0.exe" and up.state == "installing"
    finally:
        up.stop()


def test_an_up_to_date_app_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "FIRST_CHECK", 3600.0)
    up = updater(tmp_path, lambda r: httpx2.Response(200, json=latest(version="0.4.0")))
    monkeypatch.setattr(up, "why_not", lambda: "")
    up.start()
    try:
        up.check_now()
        wait_until(lambda: up.state == "current")
        assert up.message == "Magpie 0.4.0 is the latest version." and up.status()["checked"]
    finally:
        up.stop()
    python = updater(tmp_path, lambda r: httpx2.Response(404))
    python.run()  # a copy that runs from Python can't replace itself
    assert python.state == "unsupported" and "Python" in python.message


def test_the_update_button_reaches_the_updater_and_says_what_it_found(app):
    class FakeUpdater:
        asked, state, message = True, "current", "Magpie 0.5.0 is the latest version."
        installing_now = False

        def __init__(self) -> None:
            self.calls: list[str] = []

        def check_now(self):
            self.calls.append("check")

        def install_now(self):
            self.calls.append("install")

        def status(self):
            return {"state": self.state}

    class FakeTray:
        def __init__(self) -> None:
            self.said: list[str] = []

        def notify(self, message):
            self.said.append(message)

        def show(self, state, tip):
            pass

    app.updater, app.tray = FakeUpdater(), FakeTray()
    bridge = Bridge(app)
    bridge.command("check_update")
    bridge.command("install_update")
    assert app.updater.calls == ["check", "install"]
    assert bridge.status()["update"] == {"state": "current"}
    app._update_changed()
    assert app.tray.said == ["Magpie 0.5.0 is the latest version."]
    app.updater.asked = False  # a check nobody asked for stays quiet
    app._update_changed()
    assert len(app.tray.said) == 1


# ------------------------------------------------------------ paths and state


def test_the_app_keeps_its_things_in_one_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGPIE_DESKTOP_HOME", str(tmp_path))
    where = paths.app_paths()
    assert where.data_dir == tmp_path / "data" and where.env_file == tmp_path / "magpie.env"
    assert paths.launch_command("--background")[-3:] == ["magpie", "desktop", "--background"]


def test_desktop_state_survives_damage(tmp_path):
    state_file = tmp_path / "desktop.json"
    state = paths.DesktopState(state_file)
    assert state.get("autostart") is True and state.get("listening") is True
    state.set(listening=False, port=8123)
    again = paths.DesktopState(state_file)
    assert again.get("listening") is False and again.get("port") == 8123
    state_file.write_text('{"listening": "yes", "port": 9000}')
    assert paths.DesktopState(state_file).get("listening") is True  # wrong type: ignored
    state_file.write_text("{not json")
    assert paths.DesktopState(state_file).get("port") == 8000


def test_the_speech_model_is_found(tmp_path, monkeypatch):
    model = tmp_path / "my-model"
    (model / "am").mkdir(parents=True)
    where = paths.AppPaths(tmp_path / "app")
    assert paths.find_model(where) is None
    (model / "am" / "final.mdl").write_bytes(b"x")
    monkeypatch.setenv("MAGPIE_VOSK_MODEL", str(model))
    assert paths.find_model(where) == model


def model_zip(*extra: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{paths.MODEL_NAME}/am/final.mdl", "model")
        archive.writestr(f"{paths.MODEL_NAME}/conf/model.conf", "conf")
        for name in extra:
            archive.writestr(name, "should not be unpacked")
    return buffer.getvalue()


def test_the_speech_model_download_stays_in_its_folder(tmp_path):
    where = paths.AppPaths(tmp_path / "app")
    archive = model_zip("../outside.txt", "other-folder/file.txt")
    http = httpx2.Client(
        transport=httpx2.MockTransport(lambda r: httpx2.Response(200, content=archive))
    )
    target = paths.fetch_model(where, http)
    assert (target / "am" / "final.mdl").read_text() == "model"
    assert (
        not (tmp_path / "app" / "outside.txt").exists() and not (tmp_path / "outside.txt").exists()
    )
    assert sorted(p.name for p in where.models_dir.iterdir()) == [paths.MODEL_NAME]


def test_a_failed_speech_model_download_leaves_nothing_behind(tmp_path):
    where = paths.AppPaths(tmp_path / "app")
    missing = httpx2.Client(transport=httpx2.MockTransport(lambda r: httpx2.Response(404)))
    with pytest.raises(RuntimeError, match="404"):
        paths.fetch_model(where, missing)
    garbage = httpx2.Client(
        transport=httpx2.MockTransport(lambda r: httpx2.Response(200, content=b"no zip"))
    )
    with pytest.raises(RuntimeError, match="couldn't be downloaded"):
        paths.fetch_model(where, garbage)
    assert list(where.models_dir.iterdir()) == []


# ---------------------------------------------------------------- the system


def test_only_one_copy_runs_at_a_time(tmp_path):
    first, second = system.InstanceLock(tmp_path / "lock"), system.InstanceLock(tmp_path / "lock")
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


def test_the_running_copy_says_where_it_is(tmp_path):
    system.write_running(tmp_path / "running.json", port=8001, version="0.4.0")
    assert system.read_running(tmp_path / "running.json")["port"] == 8001
    (tmp_path / "running.json").write_text("[1, 2")
    assert system.read_running(tmp_path / "running.json") == {}


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the Linux way of starting up")
def test_starting_with_the_computer(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert system.set_autostart(
        True, ["/usr/bin/python3", "-m", "magpie", "desktop", "--background"]
    )
    entry = (tmp_path / "autostart" / "magpie.desktop").read_text()
    assert "Exec=/usr/bin/python3 -m magpie desktop --background" in entry
    assert system.autostart_enabled()
    assert system.set_autostart(False, [])
    assert not system.autostart_enabled()


# ----------------------------------------------------- the dashboard and the app


class Heard:
    """What the server sees of the app (Magpie.desktop), recording what it's asked."""

    def __init__(self, broken: bool = False) -> None:
        self.calls: list[Any] = []
        self.broken = broken

    def status(self) -> dict[str, Any]:
        if self.broken:
            raise RuntimeError("the app is confused")
        return {"state": "standby", "listening": True, "wake": "notes on", "version": "0.4.0"}

    def command(self, action: str) -> None:
        if self.broken:
            raise RuntimeError("the app is confused")
        self.calls.append(action)

    def notify(self, payload: dict[str, Any]) -> None:
        self.calls.append(payload)

    def prefs_changed(self, prefs: dict[str, Any]) -> None:
        self.calls.append(("prefs", prefs["wake_phrase"]))

    def ui(self, window: str, action: str, details: dict[str, Any]) -> str | None:
        self.calls.append((window, action, details["text"]))
        return "That reminder is gone." if action == "act" else None


def test_the_dashboard_sees_and_drives_the_app(client, core):
    assert client.get("/api/desktop").json() == {"active": False}
    no_app = client.post("/api/desktop/command", json={"action": "start"})
    assert no_app.status_code == 409 and "isn't running" in no_app.json()["detail"]

    core.desktop = heard = Heard()
    status = client.get("/api/status").json()
    assert status["desktop"]["active"] is True and status["desktop"]["state"] == "standby"
    assert isinstance(status["docker"], bool)
    answer = client.post("/api/desktop/command", json={"action": "start"})
    assert answer.status_code == 200 and answer.json()["active"] is True
    assert client.post("/api/desktop/command", json={"action": "shutdown"}).status_code == 422
    client.put("/api/prefs", json={"wake_phrase": "hello magpie"})
    assert heard.calls == ["start", ("prefs", "hello magpie")]


def test_the_apps_own_windows_reach_it_through_the_api(client, core):
    call = {"window": "bar", "action": "save_text", "text": "Buy milk"}
    assert client.post("/api/desktop/ui", json=call).status_code == 409  # no app
    core.desktop = heard = Heard()
    assert client.post("/api/desktop/ui", json=call).json() == {"ok": True, "problem": None}
    act = {"window": "alerts", "action": "act", "key": "a1", "text": "done"}
    assert client.post("/api/desktop/ui", json=act).json()["problem"] == "That reminder is gone."
    for bad in (
        {"window": "main", "action": "ready"},
        {"window": "bar", "action": "rm -rf"},
        {"window": "bar", "action": "save_text", "text": "x" * 50001},
        {"window": "alerts", "action": "resize", "height": -1},
    ):
        assert client.post("/api/desktop/ui", json=bad).status_code == 422
    dictated = {"window": "bar", "action": "save_dictated", "text": "Call James"}
    assert client.post("/api/desktop/ui", json=dictated).json() == {"ok": True, "problem": None}
    assert heard.calls == [
        ("bar", "save_text", "Buy milk"),
        ("alerts", "act", "done"),
        ("bar", "save_dictated", "Call James"),  # what a dictation app typed into the bar
    ]
    client.headers.pop("X-Magpie")
    assert client.post("/api/desktop/ui", json=call).status_code == 403  # no CSRF header


def test_a_confused_app_never_breaks_the_dashboard(client, core):
    core.desktop = Heard(broken=True)
    assert client.get("/api/status").json()["desktop"] == {"active": True, "state": "error"}
    answer = client.post("/api/desktop/command", json={"action": "open"})
    assert answer.status_code == 500 and "Magpie app" in answer.json()["detail"]


async def test_reminders_pop_up_from_the_app(core):
    channel = core.notifier.get("desktop")
    assert channel is not None and not channel.configured
    core.desktop = heard = Heard()
    assert channel.configured
    report = await core.notifier.send(
        Notification(title="Call James", kind="reminder", reminder_id=4, due="today 5:00 PM"),
        only="desktop",
    )
    assert report.ok
    payload = heard.calls[0]
    assert payload["title"] == "Call James" and payload["reminder_id"] == 4
    assert payload["due"] == "today 5:00 PM" and payload["snooze_minutes"] == 60


# ------------------------------------------------------------- start-up and self-test


def test_magpie_desktop_has_its_own_options(capsys):
    from magpie import __version__, cli
    from magpie.desktop.main import build_parser

    with pytest.raises(SystemExit) as code:
        cli.main(["desktop", "--version"])
    assert code.value.code == 0 and capsys.readouterr().out.strip() == f"Magpie {__version__}"
    options = build_parser().parse_args(["--background", "--home", "x"])
    assert options.background and options.home == Path("x")


def test_the_self_test_checks_the_server_and_the_bridge(tmp_path):
    from magpie.desktop.selftest import self_test

    report = tmp_path / "report.json"
    assert self_test(report=report) == 0
    results = json.loads(report.read_text())
    assert results["passed"] is True
    passed = {check["name"] for check in results["checks"] if check["ok"]}
    assert {"server", "pages", "note", "app bridge"} <= passed
