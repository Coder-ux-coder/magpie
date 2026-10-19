"""A minimal Telegram Bot API client (just the methods Magpie uses)."""

from __future__ import annotations

import html
import re
from typing import Any

import httpx2

API = "https://api.telegram.org"
# Telegram allows 4096 characters (UTF-16 code units) per message; leave room.
MAX_MESSAGE = 4000
MAX_DOWNLOAD = 20 * 1024 * 1024  # the Bot API's own limit for getFile
_TAG = re.compile(r"<[^>]*>")


class TelegramError(Exception):
    def __init__(
        self, message: str, *, status: int | None = None, retry_after: int | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def escape(text: str) -> str:
    return html.escape(text, quote=False)


def plain(text: str) -> str:
    """Our HTML message as plain text, for when Telegram can't parse the markup."""
    return html.unescape(_TAG.sub("", text))


def _units(text: str) -> int:
    """Length as Telegram counts it (UTF-16 code units)."""
    return len(text.encode("utf-16-le")) // 2


def _cut(line: str, limit: int) -> int:
    """Where to split a line that is too long: at a space if possible, never in a tag."""
    units = end = 0
    for index, char in enumerate(line):
        units += 2 if ord(char) > 0xFFFF else 1
        if units > limit:
            break
        end = index + 1
    space = line.rfind(" ", 0, end)
    if space > end // 2:
        end = space + 1
    for opener, closer in (("<", ">"), ("&", ";")):
        start = line.rfind(opener, 0, end)
        if start > 0 and line.find(closer, start, end) == -1:
            end = start
    return max(end, 1)


def split_message(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Split on paragraph or line boundaries so each part fits in one message."""
    if _units(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        while _units(line) > limit:
            if current:
                parts.append(current)
                current = ""
            end = _cut(line, limit)
            parts.append(line[:end])
            line = line[end:]
        candidate = f"{current}\n{line}" if current else line
        if _units(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current:
        parts.append(current)
    return [part for part in parts if part.strip()] or [text[:limit]]


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
            # Only the type: the request URL contains the bot token.
            raise TelegramError(f"Telegram unreachable: {type(exc).__name__}") from None
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise TelegramError(
                f"Telegram returned HTTP {response.status_code}", status=response.status_code
            )
        if not data.get("ok"):
            parameters = data.get("parameters")
            retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
            raise TelegramError(
                str(data.get("description") or f"HTTP {response.status_code}"),
                status=data.get("error_code", response.status_code),
                retry_after=retry_after if isinstance(retry_after, int) else None,
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
        if not text.strip():
            text = "…"
        parts = split_message(text)
        result = None
        for index, part in enumerate(parts):
            last = index == len(parts) - 1
            result = await self._send_part(
                chat_id,
                part,
                html_mode=html_mode,
                buttons=buttons if last else None,
                reply_to=reply_to if index == 0 else None,
            )
        return result

    async def _send_part(
        self,
        chat_id: int,
        text: str,
        *,
        html_mode: bool,
        buttons: list[list[dict[str, str]]] | None,
        reply_to: int | None,
    ) -> Any:
        try:
            return await self.call(
                "sendMessage",
                chat_id=chat_id,
                text=text,
                parse_mode="HTML" if html_mode else None,
                link_preview_options={"is_disabled": True},
                reply_markup={"inline_keyboard": buttons} if buttons else None,
                reply_parameters=(
                    {"message_id": reply_to, "allow_sending_without_reply": True}
                    if reply_to
                    else None
                ),
            )
        except TelegramError as exc:
            if exc.status != 400:
                raise
            description = str(exc).lower()
            if html_mode and "parse" in description:
                # Never lose a message over markup: send it as plain text instead.
                return await self._send_part(
                    chat_id, plain(text) or "…", html_mode=False, buttons=buttons, reply_to=reply_to
                )
            if buttons and ("url" in description or "button" in description):
                # Telegram refuses some link buttons (e.g. a LAN address): drop those.
                kept = [[b for b in row if "url" not in b] for row in buttons]
                kept = [row for row in kept if row]
                if kept != buttons:
                    return await self._send_part(
                        chat_id, text, html_mode=html_mode, buttons=kept or None, reply_to=reply_to
                    )
            raise

    async def download_file(self, file_id: str, max_bytes: int = MAX_DOWNLOAD) -> bytes:
        info = await self.call("getFile", file_id=file_id)
        path = info.get("file_path") if isinstance(info, dict) else None
        if not path:
            raise TelegramError("Telegram did not return the file (it may be larger than 20 MB)")
        if (info.get("file_size") or 0) > max_bytes:
            raise TelegramError("The file is larger than 20 MB")
        try:
            response = await self.http.get(f"{API}/file/bot{self.token}/{path}", timeout=60)
        except httpx2.HTTPError as exc:
            raise TelegramError(f"File download failed: {type(exc).__name__}") from None
        if response.status_code != 200:
            raise TelegramError(f"File download failed ({response.status_code})")
        return response.content
