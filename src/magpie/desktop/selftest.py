"""``Magpie.exe --self-test``: check that the app works, without a microphone or anyone there.

The App workflow runs it on the app it has just built and installed: the server starts
and saves a note, the speech model loads and takes a note down from a recording, Whisper
writes that note down as it's said (the quick model) and again once it's over (the chosen
one), the voice bar opens and answers, and everything the tray needs is there. It never
touches the user's own notes (it works in a temporary folder). The results are printed
and, with ``--report``, written as JSON; the exit code is 1 when a required check failed.
MAGPIE_WHISPER_MODEL (a downloaded Whisper model, with the quick one next to it) makes the
Whisper checks required.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from magpie import __version__
from magpie.desktop.paths import AppPaths, app_paths, find_model, frozen

log = logging.getLogger(__name__)

PAGES = [
    "/healthz",
    "/",
    "/static/desktop/bar.html",
    "/static/desktop/bar.js",
    "/static/js/phrases.js",  # the bar's rules for a dictation app's words
    "/static/desktop/reminders.html",
    "/static/desktop/reminders.js",
    "/static/desktop/desktop.css",
]
APP_MODULES = [
    "vosk",
    "sounddevice",
    "webview",
    "pystray",
    "PIL",
    "tzlocal",
    "faster_whisper",
    "ctranslate2",
    "onnxruntime",
    "huggingface_hub",
]
NOTE_WAIT = 60.0  # seconds for the recording to become a note


class Report:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def run(self, name: str, check: Callable[[], Any], *, required: bool = True) -> Any:
        started = time.monotonic()
        try:
            detail, ok = check(), True
        except Exception as exc:
            log.debug("Self-test %s failed", name, exc_info=True)
            detail, ok = f"{type(exc).__name__}: {exc}", False
        self.checks.append(
            {
                "name": name,
                "ok": ok,
                "required": required,
                "detail": str(detail),
                "seconds": round(time.monotonic() - started, 2),
            }
        )
        mark = "ok  " if ok else "FAIL" if required else "note"
        print(f"{mark} {name}: {detail}", flush=True)
        return detail if ok else None

    @property
    def passed(self) -> bool:
        return all(check["ok"] for check in self.checks if check["required"])

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "platform": sys.platform,
            "frozen": frozen(),
            "passed": self.passed,
            "checks": self.checks,
        }


def self_test(
    *,
    wav: Path | None = None,
    expect: str | None = None,
    windows: bool = False,
    report: Path | None = None,
) -> int:
    logging.basicConfig(level=logging.WARNING)
    results = Report()
    with tempfile.TemporaryDirectory(prefix="magpie-test-", ignore_cleanup_errors=True) as temp:
        paths = AppPaths(Path(temp))
        results.run("modules", check_modules, required=frozen())
        server = results.run("server", lambda: start_server(paths))
        if server is not None:
            try:
                results.run("pages", lambda: check_pages(server))
                results.run("note", lambda: check_note(server))
                results.run("app bridge", lambda: check_bridge(server))
                if windows:
                    results.run("voice bar window", lambda: check_window(server))
            finally:
                server.stop()
        model = find_model(paths)
        results.run("speech model", lambda: check_model(model), required=frozen() or bool(wav))
        heard: dict[str, Any] = {}
        if wav is not None:
            results.run("note from a recording", lambda: check_recording(model, wav, expect, heard))
        from magpie.desktop.transcribe import BEST_MODEL, QUICK_MODEL, find_whisper

        def whisper(name: str) -> Path | None:
            return find_whisper(paths, name) or find_whisper(app_paths(), name)

        downloaded = bool(os.environ.get("MAGPIE_WHISPER_MODEL"))
        results.run(
            "words written down as they're said",
            lambda: check_live(whisper(QUICK_MODEL), expect, heard),
            required=downloaded,
        )
        results.run(
            "note written down by Whisper",
            lambda: check_whisper(whisper(BEST_MODEL), wav, expect, heard),
            required=downloaded,
        )
        results.run("tray icon", check_tray, required=frozen())
        results.run("microphone", check_microphone, required=False)
    print("Self-test passed" if results.passed else "Self-test FAILED", flush=True)
    if report is not None:
        report.write_text(json.dumps(results.as_dict(), indent=2), encoding="utf-8")
    return 0 if results.passed else 1


# ------------------------------------------------------------------------ checks


def check_modules() -> str:
    import importlib

    missing = []
    for name in APP_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:  # sounddevice raises OSError without PortAudio
            missing.append(f"{name} ({exc})")
    if missing:
        raise RuntimeError("missing " + ", ".join(missing))
    return ", ".join(APP_MODULES)


def start_server(paths: AppPaths) -> Any:
    from magpie.config import Settings
    from magpie.desktop.server import EmbeddedServer, free_port

    settings = Settings(
        _env_file=None,
        data_dir=paths.data_dir,
        host="127.0.0.1",
        port=free_port(0),
        login=False,
        brain="basic",
        timezone="Europe/London",
    )
    server = EmbeddedServer(settings)
    server.start()
    return server


def check_pages(server: Any) -> str:
    import httpx2

    with httpx2.Client(base_url=server.url, timeout=20) as http:
        for path in PAGES:
            response = http.get(path)
            if response.status_code != 200:
                raise RuntimeError(f"{path} answered {response.status_code}")
    return f"{len(PAGES)} pages from {server.url}"


def _client(server: Any) -> Any:
    from magpie.desktop.server import LocalClient

    return LocalClient(server.url, server.core.secrets.api_token)


def check_note(server: Any) -> str:
    client = _client(server)
    try:
        note = client.create_note("Remind me to buy milk tomorrow at 9am", "desktop-text")["note"]
        deadline = time.monotonic() + 30
        while note.get("state") in ("pending", "processing") and time.monotonic() < deadline:
            time.sleep(0.2)
            note = client.note(int(note["id"]))
    finally:
        client.close()
    if note.get("state") != "ready":
        raise RuntimeError(f"the note is {note.get('state')}: {note.get('error') or ''}")
    return f"note #{note['id']} filed as {note.get('kind')}: {note.get('title')}"


def check_bridge(server: Any) -> str:
    """The dashboard reaches the app: status, the mic button, reminders."""
    import asyncio

    import httpx2

    from magpie.notify import Notification

    heard: list[Any] = []

    class Bridge:
        def status(self) -> dict[str, Any]:
            return {"state": "standby", "listening": True, "wake": "notes on"}

        def command(self, action: str) -> None:
            heard.append(action)

        def notify(self, payload: dict[str, Any]) -> None:
            heard.append(payload.get("title"))

        def prefs_changed(self, prefs: dict[str, Any]) -> None:
            heard.append("prefs")

    core = server.core
    core.desktop = Bridge()
    try:
        headers = {"Authorization": f"Bearer {core.secrets.api_token}", "X-Magpie": "1"}
        with httpx2.Client(base_url=server.url, headers=headers, timeout=20) as http:
            status = http.get("/api/status").json()
            if not status.get("desktop", {}).get("active"):
                raise RuntimeError("the dashboard doesn't see the app")
            if http.post("/api/desktop/command", json={"action": "start"}).status_code != 200:
                raise RuntimeError("the mic button doesn't reach the app")
        channel = core.notifier.get("desktop")
        if channel is None or not channel.configured:
            raise RuntimeError("reminders can't pop up from the app")
        notification = Notification(kind="reminder", title="Call James", body="")
        asyncio.run(channel.send(notification))
    finally:
        core.desktop = None
    if heard != ["start", "Call James"]:
        raise RuntimeError(f"unexpected calls: {heard}")
    return "status, the mic button and pop-ups reach the app"


def check_window(server: Any) -> str:
    """Open the voice bar in a real window and drive it, as the app does: the page
    reaches the app through the server's API, and the app runs scripts in the page."""
    import webview

    ready, reported, dictated = threading.Event(), threading.Event(), threading.Event()
    outcome: dict[str, Any] = {}

    class Bridge:
        def status(self) -> dict[str, Any]:
            return {"state": "standby"}

        def command(self, action: str) -> None:
            pass

        def notify(self, payload: dict[str, Any]) -> None:
            pass

        def prefs_changed(self, prefs: dict[str, Any]) -> None:
            pass

        def ui(self, window: str, action: str, details: dict[str, Any]) -> None:
            if action == "ready":
                ready.set()
            elif action == "report":
                outcome["shown"] = details.get("text")
                reported.set()
            elif action == "save_dictated":
                outcome["dictated"] = details.get("text")
                dictated.set()

    window = webview.create_window(
        "Magpie self-test",
        f"{server.url}/static/desktop/bar.html",
        width=680,
        height=172,
        frameless=True,
        easy_drag=False,
        on_top=True,
        focus=False,
        text_select=True,
    )

    def drive() -> None:
        try:
            if not ready.wait(60):
                outcome["error"] = "the bar page never said it was ready"
                return
            window.run_js(
                "window.magpieBar.recording({});"
                "window.magpieBar.words({base: '', final: 'buy milk', partial: 'tomorrow'});"
                "fetch('/api/desktop/ui', {method: 'POST', headers: {'Content-Type': "
                "'application/json', 'X-Magpie': '1'}, body: JSON.stringify({window: 'bar', "
                "action: 'report', text: document.getElementById('bar').dataset.state + '|' + "
                "document.getElementById('text').value})});"
            )
            if not reported.wait(30):
                outcome["error"] = "the bar didn't answer the app"
                return
            # A dictation app of the user's own types the note in, ending with the stop
            # phrase: the bar saves the words before it.
            window.run_js(
                "(() => { window.magpieBar.dictate({stop: 'notes off', seconds: 0});"
                "const box = document.getElementById('text');"
                "box.value = 'Buy milk, notes off.';"
                "box.dispatchEvent(new Event('input')); })();"
            )
            if not dictated.wait(30):
                outcome["error"] = "the bar didn't save what a dictation app typed into it"
        except Exception as exc:
            outcome["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            window.destroy()

    server.core.desktop = Bridge()
    try:
        webview.start(drive, private_mode=True)
    finally:
        server.core.desktop = None
    if "error" in outcome:
        raise RuntimeError(outcome["error"])
    if outcome.get("shown") != "recording|buy milk":
        raise RuntimeError(f"the bar showed {outcome.get('shown')!r}")
    if outcome.get("dictated") != "Buy milk":
        raise RuntimeError(f"the bar saved {outcome.get('dictated')!r} from a dictation app")
    return (
        "the bar opened, reached the app, showed the words it was sent, and saved what a "
        "dictation app typed into it"
    )


def check_model(model: Path | None) -> str:
    from magpie.desktop.speech import VoskModel

    if model is None:
        raise RuntimeError("the speech model isn't installed")
    loaded = VoskModel(model)
    unknown = [w for w in ("notes", "note", "on", "off", "cancel") if not loaded.knows(w)]
    if unknown:
        raise RuntimeError(f"the model doesn't know {unknown}")
    return f"{model.name} knows the command words"


def check_recording(
    model: Path | None, wav: Path, expect: str | None, heard: dict[str, Any] | None = None
) -> str:
    """Play a recording of “notes on … notes off” through the real listening thread."""
    from magpie.desktop.speech import VoiceEngine, WavFile

    if model is None:
        raise RuntimeError("the speech model isn't installed")
    events: list[tuple[str, dict[str, Any]]] = []
    done = threading.Event()

    def emit(kind: str, data: dict[str, Any]) -> None:
        events.append((kind, data))
        if kind in ("note", "empty", "cancelled") or data.get("state") == "error":
            done.set()

    engine = VoiceEngine(model, emit, source=lambda on_audio: WavFile(wav, on_audio))
    engine.start()
    try:
        done.wait(NOTE_WAIT)
    finally:
        engine.close()
    notes = [data for kind, data in events if kind == "note"]
    if not notes:
        seen = [kind if kind != "state" else data.get("state") for kind, data in events]
        raise RuntimeError(f"no note was taken (heard: {seen})")
    text = notes[0]["text"]
    if heard is not None:
        heard.update(notes[0])
    if expect and expect.lower() not in text.lower():
        raise RuntimeError(f"the note says {text!r}, without {expect!r}")
    return f"{text!r} (ended by {notes[0]['reason']})"


def check_whisper(
    folder: Path | None, wav: Path | None, expect: str | None, heard: dict[str, Any]
) -> str:
    """Whisper writes down the note the listener took (its own sound), or the whole
    recording when the listener took none."""
    from magpie.desktop.transcribe import Transcriber, suited

    if folder is None:
        raise RuntimeError("the Whisper model isn't downloaded (the app gets it when it starts)")
    transcriber = Transcriber(folder)
    started = time.monotonic()
    transcriber.load()
    loaded = time.monotonic() - started
    started = time.monotonic()
    if heard.get("audio"):
        source, pcm, rate = "the note's sound", heard["audio"], int(heard["rate"])
    elif wav is not None:
        import wave

        with wave.open(str(wav), "rb") as recording:
            rate = recording.getframerate()
            source, pcm = "the whole recording", recording.readframes(recording.getnframes())
    else:
        source, pcm, rate = "a second of silence", bytes(32000), 16000
    text = transcriber.transcribe(pcm, rate)
    took = time.monotonic() - started
    if expect and wav is not None and expect.lower() not in text.lower():
        raise RuntimeError(f"Whisper wrote {text!r} from {source}, without {expect!r}")
    seconds = len(pcm) / 2 / rate
    return (
        f"{text!r} from {source} ({seconds:.1f}s) in {took:.1f}s, after loading for "
        f"{loaded:.1f}s ({folder.name}, {transcriber.threads} threads; automatic would "
        f"choose {suited()} here)"
    )


def check_live(folder: Path | None, expect: str | None, heard: dict[str, Any]) -> str:
    """The quick model writes the note down a second at a time as its sound comes, as
    it does for the bar while you speak; says how long each go takes here."""
    from magpie.desktop.live import LiveWriter
    from magpie.desktop.transcribe import Transcriber

    if folder is None:
        raise RuntimeError("the quick Whisper model isn't downloaded")
    if heard.get("audio"):
        pcm, rate = heard["audio"], int(heard["rate"])
    else:  # no recording to go by: two seconds of silence, which should show nothing
        pcm, rate, expect = bytes(64000), 16000, None
    transcriber = Transcriber(folder)
    transcriber.load()
    shown: list[tuple[str, float]] = []
    live = LiveWriter(transcriber, lambda writer, text, *span: shown.append((text, span)))
    goes: list[float] = []
    chunk = int(rate * 0.25) * 2
    for offset in range(0, len(pcm), chunk):
        live.add(pcm[offset : offset + chunk], rate)
        if live.due() or offset + chunk >= len(pcm):
            started = time.monotonic()
            live.write()
            goes.append(time.monotonic() - started)
    if not goes:
        raise RuntimeError("there was no sound to write down")
    text = shown[-1][0] if shown else ""
    if expect and expect.lower() not in text.lower():
        raise RuntimeError(f"the words shown were {[t for t, _ in shown]!r}, without {expect!r}")
    return (
        f"{text!r} in {len(goes)} goes of {sum(goes) / len(goes):.1f}s on average, the "
        f"longest {max(goes):.1f}s ({folder.name}, {transcriber.threads} threads)"
    )


def check_tray() -> str:
    from magpie.desktop.ui import _icon_image

    sizes = {state: _icon_image(state).size for state in ("standby", "recording", "paused")}
    return f"icons {sizes}"


def check_microphone() -> str:
    import sounddevice

    device = sounddevice.query_devices(kind="input")
    return f"{device['name']} at {int(device['default_samplerate'])} Hz"
