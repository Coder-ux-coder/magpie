"""A minimal Telegram Bot API client (just the methods Magpie uses)."""

from __future__ import annotations

import html
from typing import Any

import httpx2

API = "https://api.telegram.org"
MAX_MESSAGE = 4096


class TelegramError(Exception):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def escape(text: str) -> str:
    return html.escape(text, quote=False)


def split_message(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Split on paragraph or line boundaries so each part fits in one message."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


class TelegramApi:
    def __init__(self, http: httpx2.AsyncClient, token: str) -> None:
        self.http = http
        self.token = token

    async def call(self, method: str, *, http_timeout: float = 30, **params: Any) -> Any:
        clean = {key: value for key, value in params.items() if value is not None}
        try:
            response = await self.http.post(
                f"{API}/bot{self.token}/{method}", json=clean, timeout=http_timeout
            )
        except httpx2.HTTPError as exc:
            raise TelegramError(f"Telegram unreachable: {type(exc).__name__}") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise TelegramError(
                f"Telegram returned HTTP {response.status_code}", status=response.status_code
            ) from exc
        if not data.get("ok"):
            raise TelegramError(
                data.get("description", f"HTTP {response.status_code}"),
                status=data.get("error_code", response.status_code),
            )
        return data.get("result")

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        buttons: list[list[dict[str, str]]] | None = None,
        reply_to: int | None = None,
        html_mode: bool = True,
    ) -> Any:
        parts = split_message(text)
        result = None
        for index, part in enumerate(parts):
            last = index == len(parts) - 1
            result = await self.call(
                "sendMessage",
                chat_id=chat_id,
                text=part,
                parse_mode="HTML" if html_mode else None,
                link_preview_options={"is_disabled": True},
                reply_markup={"inline_keyboard": buttons} if buttons and last else None,
                reply_parameters=(
                    {"message_id": reply_to, "allow_sending_without_reply": True}
                    if reply_to and index == 0
                    else None
                ),
            )
        return result

    async def download_file(self, file_id: str, max_bytes: int = 25 * 1024 * 1024) -> bytes:
        info = await self.call("getFile", file_id=file_id)
        path = info.get("file_path")
        if not path:
            raise TelegramError("Telegram did not return a file path")
        if info.get("file_size") and info["file_size"] > max_bytes:
            raise TelegramError("The file is too large")
        response = await self.http.get(f"{API}/file/bot{self.token}/{path}", timeout=60)
        if response.status_code != 200:
            raise TelegramError(f"File download failed ({response.status_code})")
        return response.content
