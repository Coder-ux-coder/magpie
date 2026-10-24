"""The Magpie server, running inside the desktop app, and a small client for it."""

from __future__ import annotations

import contextlib
import logging
import socket
import threading
import time
import uuid
from typing import Any

import httpx2

from magpie.config import Settings

log = logging.getLogger(__name__)


def free_port(preferred: int) -> int:
    """The preferred port if nothing else is using it, otherwise any free one."""
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return probe.getsockname()[1]
    raise RuntimeError("No free network port on this computer.")


class EmbeddedServer:
    """Magpie's web server and background work (reminders, Telegram) in a thread."""

    def __init__(self, settings: Settings) -> None:
        from magpie.app import create_app
        from magpie.core import Magpie

        self.settings = settings
        self.core = Magpie(settings)
        self.app = create_app(settings, magpie=self.core)
        self._server: Any = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.settings.port}"

    def __str__(self) -> str:
        return f"Magpie on {self.url}"

    def start(self, timeout: float = 60.0) -> None:
        import uvicorn

        config = uvicorn.Config(
            self.app,
            host="127.0.0.1",
            port=self.settings.port,
            log_config=None,
            access_log=False,
            lifespan="on",
            loop="asyncio",
            http="h11",
            ws="none",
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="magpie-server", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + timeout
        while not self._server.started:
            if not self._thread.is_alive():
                raise RuntimeError("Magpie's server stopped while starting. The log says why.")
            if time.monotonic() > deadline:
                raise RuntimeError("Magpie's server took too long to start.")
            time.sleep(0.05)

    def stop(self, timeout: float = 15.0) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout)


class LocalClient:
    """Talks to the Magpie server on this computer with its API token."""

    def __init__(self, base_url: str, token: str) -> None:
        self._http = httpx2.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}", "X-Magpie": "1"},
            timeout=30,
        )

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._http.close()

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._http.request(method, path, **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = None
            raise RuntimeError(str(detail or f"Magpie answered {response.status_code}."))
        return response.json() if response.content else None

    def create_note(self, text: str, source: str, captured_in: str = "") -> dict[str, Any]:
        body: dict[str, Any] = {"text": text, "source": source, "client_id": uuid.uuid4().hex}
        if captured_in:
            body["captured_in"] = captured_in
        return self._json("POST", "/api/notes", json=body)

    def note(self, note_id: int) -> dict[str, Any]:
        return self._json("GET", f"/api/notes/{int(note_id)}")

    def prefs(self) -> dict[str, Any]:
        return self._json("GET", "/api/prefs")

    def transcribe(self, wav: bytes) -> str:
        """A note's sound, written down by the speech service chosen in Settings → Voice."""
        response = self._http.post(
            "/api/transcribe", files={"file": ("note.wav", wav, "audio/wav")}, timeout=240
        )
        try:
            body = response.json()
        except ValueError:
            body = {}
        detail = body.get("detail") if isinstance(body, dict) else None
        if response.status_code == 422 and isinstance(detail, str):  # it heard nothing
            return ""
        if response.status_code >= 400:
            raise RuntimeError(str(detail or f"Magpie answered {response.status_code}."))
        return str(body.get("text") or "") if isinstance(body, dict) else ""

    def update_prefs(self, changes: dict[str, Any]) -> dict[str, Any]:
        return self._json("PUT", "/api/prefs", json=changes)

    def reminder(self, reminder_id: int, action: str) -> dict[str, Any]:
        if action not in ("done", "snooze", "tomorrow", "cancel"):
            raise ValueError(action)
        return self._json("POST", f"/api/reminders/{int(reminder_id)}/{action}")
