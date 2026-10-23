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
from typing import Any

import httpx2
import pytest

from magpie.desktop import migrate, paths, phrases, system, update
from magpie.desktop.app import Bridge, DesktopApp
from magpie.desktop.speech import (
    NOTHING_SAID_SECONDS,
    Listener,
    MicrophoneError,
    VoiceEngine,
)
from magpie.notify import Notification

RATE = 16000
FRAME = 160  # the fake audio's samples count 10 ms frames, so a recogniser knows the time
REAL_MODEL = os.environ.get("MAGPIE_VOSK_MODEL")  # read before the fixtures clear MAGPIE_*


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

    def __init__(self, script: list, grammar: list[str] | None = None) -> None:
        self.script = script
        self.grammar = None if grammar is None else {w for p in grammar for w in p.split()}
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
        return [
            {
                "word": w if self.grammar is None or w in self.grammar else "[unk]",
                "start": round(s - self.origin, 3),
                "end": round(e - self.origin, 3),
                "conf": 1.0,
            }
            for w, s, e in self.script
            if s >= self.taken - 1e-6 and e <= self.now + 1e-6
        ]

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
    def __init__(self, script: list, unknown: tuple[str, ...] = ()) -> None:
        self.script = script
        self.unknown = set(unknown)

    def knows(self, word: str) -> bool:
        return word == "[unk]" or word not in self.unknown

    def free(self, rate: int) -> FakeRecognizer:
        return FakeRecognizer(self.script)

    def grammar(self, phrases: list[str], rate: int) -> FakeRecognizer:
        return FakeRecognizer(self.script, phrases)


def listen(script: list, seconds: float, unknown: tuple[str, ...] = (), **options):
    events: list[tuple[str, dict[str, Any]]] = []
    listener = Listener(
        FakeModel(script, unknown), lambda kind, data: events.append((kind, data)), **options
    )
    for pcm in audio(seconds):
        listener.feed(pcm)
    return listener, events


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


def test_a_note_said_in_one_breath_keeps_its_first_words():
    """The words right after the command word were already heard: none are lost."""
    _, events = listen(say("notes on buy milk", 1.0), 6, silence_seconds=2)
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


# ------------------------------------------------------------------- phrases


def test_phrase_matching():
    words = [{"word": w, "start": i, "end": i + 0.5} for i, w in enumerate(["ok", "notes", "on"])]
    hit = phrases.find_in_words(words, ["notes on"])
    assert (hit.index, hit.length, hit.start, hit.end) == (1, 2, 1, 2.5)
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
        time.sleep(0.3)
        engine.command("finish")
        recorder.wait_for("standby")
        deadline = time.monotonic() + 5
        while not any(k == "note" for k, _ in recorder.events) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ("note", {"text": "call james", "reason": "button"}) in recorder.events
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
    desktop.ui, desktop.client, desktop.engine = FakeUI(), FakeClient(), FakeEngine()
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

    assert (moved.notes, moved.settings, moved.stopped) == (3, 2, 2)
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
    return {
        "version": version,
        "windows": {
            "url": f"https://github.com/{paths.REPOSITORY}/releases/download/v{version}/MagpieSetup.exe",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            **windows,
        },
    }


def test_release_descriptions_are_checked():
    release = update.parse_latest(latest())
    assert release and release.version == "0.5.0" and release.size == 9
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


def updater(tmp_path, handler) -> update.Updater:
    http = httpx2.Client(transport=httpx2.MockTransport(handler))
    return update.Updater("0.4.0", tmp_path, idle=lambda: True, quit_app=lambda: None, http=http)


def test_updates_are_found_downloaded_and_checked(tmp_path):
    data = b"the new installer"

    def internet(request):
        if request.url.path.endswith("latest.json"):
            return httpx2.Response(200, json=latest(data=data))
        return httpx2.Response(200, content=data)

    found = updater(tmp_path, internet).check()
    assert found and found.version == "0.5.0"
    installer = updater(tmp_path, internet).download(found)
    assert installer.read_bytes() == data and installer.name == "MagpieSetup-0.5.0.exe"


def test_a_tampered_download_is_thrown_away(tmp_path):
    release = update.parse_latest(latest(data=b"the real one"))
    tampered = updater(tmp_path, lambda r: httpx2.Response(200, content=b"the fake one"))
    with pytest.raises(RuntimeError, match="doesn't match"):
        tampered.download(release)
    larger = updater(tmp_path, lambda r: httpx2.Response(200, content=b"x" * 100))
    with pytest.raises(RuntimeError, match="larger"):
        larger.download(release)
    assert list(tmp_path.iterdir()) == []


def test_no_update_when_github_says_nothing_useful(tmp_path):
    assert updater(tmp_path, lambda r: httpx2.Response(404)).check() is None
    assert updater(tmp_path, lambda r: httpx2.Response(200, content=b"<html>")).check() is None
    same = updater(tmp_path, lambda r: httpx2.Response(200, json=latest(version="0.4.0")))
    assert same.check() is None


def test_installing_runs_the_installer_silently_and_quits(tmp_path, monkeypatch):
    started, quit_called = [], []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: started.append(args))
    up = update.Updater(
        "0.4.0", tmp_path, idle=lambda: True, quit_app=lambda: quit_called.append(1)
    )
    up.install(tmp_path / "MagpieSetup-0.5.0.exe")
    assert started[0][1:] == ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"]
    assert quit_called == [1]


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
    assert heard.calls == [("bar", "save_text", "Buy milk"), ("alerts", "act", "done")]
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
