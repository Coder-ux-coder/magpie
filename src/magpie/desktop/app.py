"""The desktop app: one process with the server, the listener, the windows and the tray."""

from __future__ import annotations

import contextlib
import itertools
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from typing import Any

import httpx2

from magpie import __version__
from magpie.config import Settings
from magpie.desktop import system
from magpie.desktop.live import LiveWriter
from magpie.desktop.paths import AppPaths, DesktopState, find_model, frozen, launch_command
from magpie.desktop.phrases import DEFAULT_WAKE, append_text, tidy
from magpie.desktop.server import EmbeddedServer, LocalClient, free_port
from magpie.desktop.speech import RATE, VoiceEngine
from magpie.desktop.transcribe import AUTO, QUICK_MODEL, Transcriber, Whisper
from magpie.desktop.update import Updater

log = logging.getLogger("magpie.desktop")

SAVED_SECONDS = 5.0  # how long "Saved" stays up once the AI has replied
UNDO_SECONDS = 8.0
REPLY_WAIT = 8.0  # the bar doesn't wait longer than this for the AI's reply


def setup_logging(paths: AppPaths, *, verbose: bool = False) -> None:
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        paths.logs_dir / "magpie.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    if sys.stderr is not None and verbose:
        root.addHandler(logging.StreamHandler())
    root.setLevel(logging.DEBUG if verbose else logging.INFO)


def local_zone() -> str | None:
    """This computer's time zone as an IANA name (Europe/London), when it can tell."""
    try:
        from tzlocal import get_localzone_name

        from magpie.timeutil import is_valid_timezone

        name = get_localzone_name()
        # "Etc/UTC" and friends mean the computer doesn't say where it is.
        if not name or "/" not in name or name.startswith("Etc/"):
            return None
        return name if is_valid_timezone(name) else None
    except Exception:
        return None


def hand_over(paths: AppPaths, action: str = "open", wait: float = 60.0) -> bool:
    """Ask the copy of Magpie that's already running to show itself."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        port = system.read_running(paths.running_file).get("port")
        if isinstance(port, int):
            try:
                response = httpx2.post(
                    f"http://127.0.0.1:{port}/api/desktop/command",
                    json={"action": action},
                    headers={"X-Magpie": "1"},
                    timeout=5,
                )
                if response.status_code == 200:
                    return True
            except httpx2.HTTPError:
                pass
        time.sleep(0.5)
    return False


class Bridge:
    """What the server sees of the app (Magpie.desktop): status, commands, pop-ups."""

    def __init__(self, app: DesktopApp) -> None:
        self._app = app

    def status(self) -> dict[str, Any]:
        app = self._app
        return {
            "state": app.voice_state,
            "listening": app.listening,
            "message": app.voice_message,
            "wake": app.wake_phrase,
            "version": __version__,
            "speech": app.whisper.status() if app.whisper is not None else None,
            "update": app.updater.status() if app.updater is not None else None,
        }

    def command(self, action: str) -> None:
        app = self._app
        if action == "start":
            app.start_note()
        elif action in ("finish", "cancel"):
            app.voice(action)
        elif action == "write":
            app.write_note()
        elif action == "open":
            app.open_notes()
        elif action in ("listen", "pause"):
            app.set_listening(action == "listen")
        elif action == "check_update":
            app.check_for_updates()
        elif action == "install_update" and app.updater is not None:
            app.updater.install_now()

    def notify(self, payload: dict[str, Any]) -> None:
        self._app.show_alert(payload)

    def ui(self, window: str, action: str, details: dict[str, Any]) -> str | None:
        """Calls from the app's own windows, which reach it through the server's API.

        Returns a problem to show, or None.
        """
        app = self._app
        text = str(details.get("text") or "")
        if window == "bar":
            handlers: dict[str, Any] = {
                "ready": lambda: app.ui_ready("bar"),
                "save": lambda: app.voice("finish"),
                "save_text": lambda: app.save_typed(text),
                "cancel": lambda: app.voice("cancel"),
                "close": app.hide_bar,
                "undo": app.undo,
                "edited": lambda: app.bar_edited(text),
                "focus": lambda: app.ui and app.ui.take_keyboard(),
                "open_notes": lambda: app.open_notes(),
            }
        else:
            handlers = {
                "ready": lambda: app.ui_ready("alerts"),
                "act": lambda: app.alert_action(str(details.get("key") or ""), text),
                "resize": lambda: app.ui and app.ui.fit_alerts(details.get("height")),
            }
        handler = handlers.get(action)
        if handler is None:
            return f"Magpie doesn't know {action!r}."
        result = handler()
        return result if isinstance(result, str) else None

    def prefs_changed(self, prefs: dict[str, Any]) -> None:
        self._app.apply_prefs(prefs)


class DesktopApp:
    def __init__(self, paths: AppPaths, *, background: bool = False) -> None:
        self.paths = paths
        self.background = background
        self.state = DesktopState(paths.state_file)
        self.lock = system.InstanceLock(paths.lock_file)
        self.server: EmbeddedServer | None = None
        self.client: LocalClient | None = None
        self.engine: VoiceEngine | None = None
        self.whisper: Whisper | None = None  # writes each note down again, accurately
        self.ui: Any = None
        self.tray: Any = None
        self.updater: Updater | None = None
        self.voice_state = "starting"
        self.voice_message = ""
        self.wake_phrase = DEFAULT_WAKE
        self.stop_phrase: str | None = None
        self.listening = bool(self.state.get("listening"))
        self.autostart = bool(self.state.get("autostart"))
        self._typed = ""  # what was typed into the bar during this recording
        self._said_in = ""  # the window in front when the note was started
        self._live: LiveWriter | None = None  # Whisper's words while you speak
        self._live_text = ""
        self._live_span = (0.0, 0.0)  # seconds into the note that Whisper's words are from
        self._live_shown = ""  # those words, after the listener's for any time before
        self._heard: dict[str, Any] = {}  # the listener's latest words
        self._words_lock = threading.Lock()
        self._discarded = ""
        self._bar_gen = itertools.count()
        self._bar_token = 0
        self._ready: dict[str, bool] = {"bar": False, "alerts": False}
        self._pending: dict[str, list[tuple[str, Any]]] = {"bar": [], "alerts": []}
        self._alerts: dict[str, dict[str, Any]] = {}
        self._alert_ids = itertools.count(1)
        self._ui_queue = ThreadPoolExecutor(max_workers=1, thread_name_prefix="magpie-ui")
        self._work = ThreadPoolExecutor(max_workers=4, thread_name_prefix="magpie-work")
        self._lock = threading.Lock()
        self._quitting = threading.Event()
        self._moved: Any = None

    # ------------------------------------------------------------------ start-up

    def run(self) -> int:
        if not self.lock.acquire():
            log.info("Magpie is already running; asking it to show itself")
            if hand_over(self.paths):
                return 0
            system.message_box(
                "Magpie is starting",
                "Magpie is still getting ready. Its icon is by the clock; open it from there "
                "in a moment.",
            )
            return 1
        try:
            return self._run()
        finally:
            self._shutdown()

    def _run(self) -> int:
        from magpie.desktop.ui import Tray

        log.info("Magpie %s starting (%s)", __version__, sys.platform)
        self.tray = Tray(self)
        self.tray.start()
        self._migrate()
        port = free_port(int(self.state.get("port") or 8000))
        if port != self.state.get("port"):
            self.state.set(port=port)
        settings = Settings(
            _env_file=self.paths.env_file if self.paths.env_file.exists() else None,
            data_dir=self.paths.data_dir,
            host="127.0.0.1",
            port=port,
            login=False,  # no password: Magpie only answers on this computer
        )
        self.server = EmbeddedServer(settings)
        try:
            self.server.start()
        except Exception as exc:
            log.exception("The server didn't start")
            where = f"The log is in {self.paths.logs_dir}."
            system.message_box("Magpie couldn't start", f"{exc}\n\n{where}")
            return 1
        self.server.core.desktop = Bridge(self)
        self.client = LocalClient(self.server.url, self.server.core.secrets.api_token)
        system.write_running(self.paths.running_file, port=port, version=__version__)
        if self._moved is not None:
            self._quiet_browser_popups()
        self._follow_clock()
        if frozen() and self.autostart and self.state.get("first_run"):
            system.set_autostart(True, launch_command("--background"))
        model = find_model(self.paths)
        self.engine = VoiceEngine(model, self._on_voice, listening=self.listening)
        self.whisper = Whisper(self.paths, on_change=self._update_tray)
        self.apply_prefs(self._upgrade_speech(self.client.prefs()))
        self.engine.start()
        if model is None:
            threading.Thread(target=self._fetch_model, name="magpie-model", daemon=True).start()
        self.updater = Updater(
            __version__,
            self.paths.updates_dir,
            idle=self._idle,
            quit_app=self.quit,
            on_change=self._update_changed,
        )
        self.updater.start()
        threading.Thread(target=self._clock_loop, name="magpie-clock", daemon=True).start()

        from magpie.desktop.ui import DesktopUI

        first = bool(self.state.get("first_run"))
        try:
            self.ui = DesktopUI(self, self.server.url, show_main=first or not self.background)
        except Exception as exc:
            log.exception("The windows couldn't be created")
            system.message_box(
                "Magpie couldn't open its windows",
                "Magpie needs Microsoft Edge WebView2. Install it from "
                "https://developer.microsoft.com/microsoft-edge/webview2/ and start Magpie "
                f"again.\n\n({exc})",
            )
            return 1
        if first:
            self.state.set(first_run=False)
        self.ui.run(self._windows_ready, self.paths.webview_dir)
        return 0

    def _windows_ready(self) -> None:
        log.info("Magpie is ready on %s", self.server.url if self.server else "?")
        self._update_tray()
        if self._moved is not None and self._moved.notes:
            self._bar_message(
                f"Your {self._moved.notes} notes from the old Magpie are here.", seconds=10
            )

    def _migrate(self) -> None:
        if self.state.get("migrated"):
            return
        from magpie.desktop.migrate import migrate

        def tell(message: str) -> None:
            if self.tray is not None:
                self.tray.show("starting", message)
                self.tray.notify(message)

        try:
            self._moved = migrate(self.paths.data_dir, self.paths.env_file, tell=tell)
        except Exception:
            log.exception("Bringing over notes from Docker failed; the old Magpie is untouched")
            self._moved = None
        self.state.set(migrated=True)

    def _shutdown(self) -> None:
        self._quitting.set()
        for stop in (
            lambda: self.updater and self.updater.stop(),
            lambda: self.engine and self.engine.close(),
            lambda: self.server and self.server.stop(),
            lambda: self.client and self.client.close(),
            lambda: self.tray and self.tray.stop(),
        ):
            with contextlib.suppress(Exception):
                stop()
        self._work.shutdown(wait=False, cancel_futures=True)
        self._ui_queue.shutdown(wait=False, cancel_futures=True)
        with contextlib.suppress(OSError):
            self.paths.running_file.unlink()
        self.lock.release()
        log.info("Magpie stopped")

    def quit(self) -> None:
        if self.ui is not None:
            self.ui.quit()
        else:
            os._exit(0)

    # ---------------------------------------------------------------- settings

    def apply_prefs(self, prefs: dict[str, Any]) -> None:
        wake = str(prefs.get("wake_phrase") or DEFAULT_WAKE)
        self.wake_phrase = wake
        self.stop_phrase = prefs.get("stop_phrase") or None
        if self.engine is not None:
            self.engine.command(
                "configure", wake, prefs.get("stop_phrase"), prefs.get("silence_seconds", 8)
            )
        if self.whisper is not None:
            self.whisper.choose(str(prefs.get("speech_model") or AUTO))
        self._update_tray()

    def _upgrade_speech(self, prefs: dict[str, Any]) -> dict[str, Any]:
        """Once: the smaller model was everyone's default before 0.7; now it's the most
        accurate model the computer is quick enough for (Settings → Voice changes it)."""
        if self.state.get("speech_v2") or self.client is None:
            return prefs
        self.state.set(speech_v2=True)
        if prefs.get("speech_model") != QUICK_MODEL:
            return prefs
        try:
            prefs = self.client.update_prefs({"speech_model": AUTO})
            log.info("Notes are now written down with the speech model that suits this computer")
        except Exception:
            log.warning("Couldn't choose the speech model that suits this computer", exc_info=True)
        return prefs

    def set_listening(self, on: bool) -> None:
        self.listening = on
        self.state.set(listening=on)
        if self.engine is not None:
            self.engine.command("resume" if on else "pause")
        self._update_tray()

    def set_autostart(self, on: bool) -> None:
        if system.set_autostart(on, launch_command("--background")):
            self.autostart = on
            self.state.set(autostart=on)
        self._update_tray()

    def _fetch_model(self) -> None:
        from magpie.desktop.paths import fetch_model

        assert self.engine is not None
        if frozen():  # the installed app always ships with it
            self.engine.set_model(None, "The speech model is missing. Reinstall Magpie.")
            return
        try:
            self.engine.set_model(fetch_model(self.paths))
        except Exception as exc:
            log.warning("Downloading the speech model failed: %s", exc)
            self.engine.set_model(None, f"Speech recognition isn't ready: {exc}")

    def _quiet_browser_popups(self) -> None:
        """After moving from Docker: a browser's pop-ups would repeat every one of the app's."""
        assert self.client is not None
        try:
            channels = dict(self.client.prefs().get("channels") or {})
            if channels.get("webpush", True):
                self.client.update_prefs({"channels": {**channels, "webpush": False}})
        except Exception:
            log.debug("Couldn't turn off the browser's pop-ups", exc_info=True)

    def _follow_clock(self) -> None:
        """Reminders follow this computer's clock, wherever it is."""
        zone = local_zone()
        if zone is None or self.client is None:
            return
        try:
            if self.client.prefs().get("timezone") != zone:
                self.client.update_prefs({"timezone": zone})
                log.info("Time zone set to %s from this computer", zone)
        except Exception:
            log.debug("Couldn't follow the computer's time zone", exc_info=True)

    def _clock_loop(self) -> None:
        minutes = 0
        while not self._quitting.wait(60):
            minutes += 1
            if self.whisper is not None:
                self.whisper.tick()
            if minutes % 15 == 0:
                self._follow_clock()

    # ------------------------------------------------------------------- voice

    def voice(self, command: str) -> None:
        if self.engine is not None:
            self.engine.command(command)

    def start_note(self) -> None:
        """The mic button or the tray: take a note now, without the command word."""
        if self.voice_state in ("no-mic", "error"):
            self._bar_message(self.voice_message or "Magpie can't hear a microphone.", error=True)
            return
        if not self.listening:
            self.set_listening(True)
        self.voice("start")

    def check_for_updates(self) -> None:
        """The update button (and the tray's menu): look now, and say what was found."""
        if self.updater is not None:
            self.updater.check_now()

    def _update_changed(self) -> None:
        updater = self.updater
        if updater is None or not updater.asked or self.tray is None:
            return
        if updater.state == "ready" and updater.installing_now:
            return  # it restarts in a moment anyway
        if updater.state in ("current", "ready", "error", "unsupported"):
            self.tray.notify(updater.message)

    def _idle(self) -> bool:
        return self.voice_state != "recording" and not (self.ui and self.ui.bar_visible)

    def _update_tray(self) -> None:
        if self.tray is None:
            return
        wake = self.wake_phrase
        tips = {
            "standby": f"Magpie: listening for “{wake}”",
            "recording": "Magpie: taking a note…",
            "paused": "Magpie: not listening (turn it on from this menu)",
            "starting": "Magpie is starting…",
        }
        tip = tips.get(self.voice_state) or f"Magpie: {self.voice_message or self.voice_state}"
        if self.whisper is not None and self.whisper.state == "downloading":
            tip += " (getting the accurate speech model)"
        self.tray.show(self.voice_state, tip)

    def _on_voice(self, kind: str, data: dict[str, Any]) -> None:
        """Events from the listening thread."""
        if kind == "state":
            state = data.get("state", "")
            self.voice_state = state
            self.voice_message = data.get("message", "")
            self._update_tray()
            if state == "recording":
                self._typed = ""
                self._said_in = system.active_window() if data.get("by_voice") else ""
                self._bar("recording", {"hint": self._hint()}, show=True)
                self._start_live()
            else:
                self._stop_live()
            return
        if kind == "sound":
            live = self._live
            if live is not None:
                live.add(data["pcm"], int(data.get("rate") or RATE))
        elif kind == "words":
            with self._words_lock:
                self._heard = data
                if self._live is not None:
                    self._live.heard()
                self._show_words()
        elif kind == "note":
            self._stop_live()
            self._save_voice(data)
        elif kind == "empty":
            self._stop_live()
            typed, self._typed = self._typed.strip(), ""
            if typed:
                self._save(typed, "desktop-text")
            else:
                self._bar("empty", {}, hide_after=3.0)
        elif kind == "cancelled":
            self._stop_live()
            text = append_text(self._typed, data.get("text", "")).strip()
            self._typed = ""
            self._discarded = text
            if data.get("reason") == "paused":
                self._hide_bar()
            elif text:
                self._bar("discarded", {"text": text}, hide_after=UNDO_SECONDS)
            else:
                self._hide_bar()

    def _hint(self) -> str:
        whisper = self.whisper
        if whisper is not None and whisper.quick is None and whisper.getting:
            done = round(whisper.progress * 100)
            return f"the words get better once Whisper is downloaded ({done}%)"
        return "say “notes off” to save, “cancel note” to discard"

    # ------------------------------------------------------ words as you speak

    def _start_live(self) -> None:
        """Whisper's quick model writes the note down as it's said (live.py), and the
        chosen one gets ready to write it down once more at the end."""
        whisper = self.whisper
        quick = whisper.quick if whisper is not None else None
        if whisper is not None:
            for model in (whisper.quick, whisper.transcriber):
                if model is not None:
                    model.warm()
        with self._words_lock:
            if self._live is not None:
                self._live.stop()
            self._live, self._live_text, self._live_shown, self._heard = None, "", "", {}
            if quick is not None:
                self._live = LiveWriter(
                    quick, self._live_words, wake=self.wake_phrase, stop=self.stop_phrase
                ).start()

    def _stop_live(self) -> None:
        with self._words_lock:
            live, self._live = self._live, None
        if live is not None:
            live.stop()

    def _live_words(self, live: LiveWriter, text: str, since: float, until: float) -> None:
        with self._words_lock:
            if live is not self._live:  # from a note that has ended
                return
            self._live_text, self._live_span = text, (since, until)
            self._show_words()

    def _show_words(self) -> None:
        """Whisper's words, then the listener's for what was said after Whisper's last go,
        and before it for a long note (holding the words lock)."""
        heard = self._heard
        final, partial = str(heard.get("final", "")), str(heard.get("partial", ""))
        if self._live is not None and self._live_text:
            since, until = self._live_span
            timed = heard.get("timed", ())
            before = tidy(" ".join(word for word, start in timed if start < since))
            final = self._live_shown = append_text(before, self._live_text)
            partial = tidy(" ".join(word for word, start in timed if start >= until))
        self._bar("words", {"base": self._typed, "final": final, "partial": partial})

    # ----------------------------------------------------------------- the bar

    def _on_ui(self, work: Any, *args: Any, **kwargs: Any) -> None:
        """Windows are changed one thing at a time, in order, off the caller's thread."""
        if self._quitting.is_set():
            return
        with contextlib.suppress(RuntimeError):  # shutting down meanwhile
            self._ui_queue.submit(work, *args, **kwargs)

    def ui_ready(self, which: str) -> None:
        with self._lock:
            self._ready[which] = True
            pending, self._pending[which] = self._pending[which], []
        for name, payload in pending:
            self._ui_call(which, name, payload)

    def _ui_call(self, which: str, name: str, payload: Any) -> None:
        with self._lock:
            if not self._ready[which]:
                self._pending[which].append((name, payload))
                return
        if self.ui is None:
            return
        target = self.ui.bar_call if which == "bar" else self.ui.alerts_call
        self._on_ui(target, name, payload)

    def _bar(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        show: bool = False,
        keyboard: bool = False,
        hide_after: float | None = None,
    ) -> None:
        token = next(self._bar_gen)
        self._bar_token = token
        self._ui_call("bar", name, payload)
        if (show or keyboard) and self.ui is not None:
            self._on_ui(self.ui.show_bar, keyboard=keyboard)
        if hide_after is not None:  # a timer, so waiting never holds up saving notes
            timer = threading.Timer(hide_after, self._hide_if_current, args=(token,))
            timer.daemon = True
            timer.start()

    def _hide_if_current(self, token: int) -> None:
        if token == self._bar_token:  # nothing new was shown in the meantime
            self._hide_bar()

    def _hide_bar(self) -> None:
        self._bar_token = next(self._bar_gen)
        if self.ui is not None:
            self._on_ui(self.ui.hide_bar)

    def hide_bar(self) -> None:
        if self.voice_state == "recording":
            self.voice("cancel")
        else:
            self._hide_bar()

    def _bar_message(self, text: str, *, error: bool = False, seconds: float = 6.0) -> None:
        self._bar("message", {"text": text, "error": error}, show=True, hide_after=seconds)

    def bar_edited(self, text: str) -> None:
        self._typed = text
        with self._words_lock:
            self._live_text = self._live_shown = ""
            if self._live is not None:
                self._live.reset()
        self.voice("rebase")

    def write_note(self) -> None:
        if self.voice_state == "recording":
            return
        self._bar("typing", {"text": ""}, keyboard=True)

    def undo(self) -> None:
        text, self._discarded = self._discarded, ""
        self._bar("typing", {"text": text}, keyboard=True)

    def save_typed(self, text: str) -> None:
        if text.strip():
            self._save(text.strip(), "desktop-text")

    def open_notes(self, path: str = "/") -> None:
        if self.ui is not None:
            self._on_ui(self.ui.show_main, path)

    # ------------------------------------------------------------------ saving

    def _save(self, text: str, source: str) -> None:
        self._bar("saving", {"text": text}, show=True)
        self._work.submit(self._submit, text, source, self._said_in)

    def _save_voice(self, data: dict[str, Any]) -> None:
        heard = append_text(self._typed, data.get("text", ""))
        whisper = self.whisper
        models: list[Transcriber] = []
        for model in (whisper.transcriber, whisper.quick) if whisper is not None else ():
            if model is not None and model not in models:
                models.append(model)
        if not models or not data.get("audio"):
            self._save(heard, "desktop-voice")
            return
        live = self._live_shown  # Whisper's words as you spoke, closer than the listener's
        shown = append_text(self._typed, live) if live else heard
        self._bar("saving", {"text": shown, "status": "Writing it down…"}, show=True)
        self._work.submit(
            self._write_down, models, self._typed, data, self._bar_token, self._said_in
        )

    def _write_down(
        self,
        models: list[Transcriber],
        typed: str,
        data: dict[str, Any],
        token: int,
        said_in: str,
    ) -> None:
        """Whisper writes the note down again, far more accurately than the listener heard
        it: the chosen model, or the quick one if that fails. When neither can (or they're
        too slow), the listener's words are saved instead."""
        text = ""
        for model in models:
            try:
                text = model.transcribe(
                    data["audio"],
                    int(data.get("rate") or RATE),
                    wake=self.wake_phrase,
                    stop=self.stop_phrase,
                )
                break
            except Exception as exc:
                log.warning("Whisper couldn't write the note down: %s", exc)
        note = append_text(typed, text or str(data.get("text", "")))
        if token == self._bar_token:  # the bar still shows this note
            self._bar("saving", {"text": note})
            self._submit(note, "desktop-voice", said_in)
        else:  # a new note started meanwhile: save this one without taking the bar
            self._submit(note, "desktop-voice", said_in, quiet=True)

    def _submit(self, text: str, source: str, said_in: str, *, quiet: bool = False) -> None:
        assert self.client is not None
        try:
            result = self.client.create_note(text, source, said_in)
        except Exception as exc:
            log.warning("Saving a note failed: %s", exc)
            # Keep the words: they're back in the bar to try again or copy.
            self._bar(
                "typing",
                {"text": text, "status": f"Not saved: {exc} Press Enter to try again."},
                show=True,
            )
            return
        if quiet:
            return
        note = result.get("note") or {}
        self._bar("saved", {"title": note.get("title") or "", "reply": "Filing it…"})
        token = self._bar_token
        deadline = time.monotonic() + REPLY_WAIT
        while note.get("state") in ("pending", "processing") and time.monotonic() < deadline:
            time.sleep(1.0)
            if token != self._bar_token:
                return  # a new note started meanwhile
            try:
                note = self.client.note(int(note["id"]))
            except Exception:
                break
        if token != self._bar_token:
            return
        reply = note.get("reply") or note.get("error") or result.get("reply", "")
        self._bar(
            "saved", {"title": note.get("title") or "", "reply": reply}, hide_after=SAVED_SECONDS
        )

    # ----------------------------------------------------------------- pop-ups

    def show_alert(self, payload: dict[str, Any]) -> None:
        key = f"a{next(self._alert_ids)}"
        self._alerts[key] = payload
        minutes = int(payload.get("snooze_minutes") or 60)
        from magpie.notify import snooze_label

        self._ui_call(
            "alerts",
            "add",
            {
                "key": key,
                "title": payload.get("title") or "Reminder",
                "body": payload.get("body") or "",
                "due": payload.get("due") or "",
                "kind": payload.get("kind") or "reminder",
                "snooze": snooze_label(minutes),
                "reminder": bool(payload.get("reminder_id")),
            },
        )
        system.beep()

    def alert_action(self, key: str, action: str) -> str | None:
        payload = self._alerts.get(key)
        if payload is None:
            return None
        try:
            if action in ("done", "snooze") and payload.get("reminder_id"):
                assert self.client is not None
                self.client.reminder(int(payload["reminder_id"]), action)
            elif action == "open":
                note = payload.get("note_id")
                self.open_notes(f"/#/note/{int(note)}" if note else "/")
        except Exception as exc:
            return f"That didn't work: {exc}"
        self._alerts.pop(key, None)
        self._ui_call("alerts", "remove", key)
        return None
