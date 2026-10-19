"""Finding, classifying and vetting URLs."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import ParseResult, parse_qs, urlparse, urlunparse

from magpie.content import FetchError

_URL = re.compile(r"(?i)\b((?:https?://|www\.)[^\s<>\"'`]+)")
_TRAILING = ".,;:!?'\"*"
_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


MAX_URL_LENGTH = 4000


def _parse(url: str) -> ParseResult | None:
    """urlparse, or None for text that only looks like a URL ("http://[::1")."""
    try:
        parsed = urlparse(url)
        _ = parsed.hostname, parsed.port  # both raise ValueError on a malformed authority
    except ValueError:
        return None
    return parsed


def _trim(url: str) -> str:
    end = len(url)
    while end and url[end - 1] in _TRAILING:
        end -= 1
    # Drop unbalanced closing brackets that belong to the surrounding text.
    for opener, closer in (("(", ")"), ("[", "]")):
        extra = url.count(closer, 0, end) - url.count(opener, 0, end)
        while extra > 0 and end and url[end - 1] == closer:
            end -= 1
            extra -= 1
    return url[:end]


def normalize_url(url: str) -> str:
    url = url.strip()
    if url.lower().startswith("www."):
        url = "https://" + url
    parsed = _parse(url)
    if parsed is None:
        return url
    return urlunparse(parsed._replace(scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower()))


def extract_urls(text: str) -> list[str]:
    found: list[str] = []
    for match in _URL.finditer(text or ""):
        url = _trim(match.group(1))
        if len(url) < 8 or len(url) > MAX_URL_LENGTH or _parse(url) is None:
            continue
        url = normalize_url(url)
        if url not in found:
            found.append(url)
    return found


def is_http_url(url: str) -> bool:
    if not isinstance(url, str) or len(url) > MAX_URL_LENGTH:
        return False
    parsed = _parse(url)
    return parsed is not None and parsed.scheme in ("http", "https") and bool(parsed.hostname)


def youtube_id(url: str) -> str | None:
    parsed = _parse(url)
    if parsed is None:
        return None
    host = (parsed.hostname or "").lower()
    candidate: str | None = None
    if host in ("youtu.be", "www.youtu.be"):
        candidate = parsed.path.lstrip("/").split("/")[0]
    elif host in _YOUTUBE_HOSTS:
        if parsed.path == "/watch":
            candidate = (parse_qs(parsed.query).get("v") or [None])[0]
        else:
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
                candidate = parts[1]
    if candidate and _YOUTUBE_ID.match(candidate):
        return candidate
    return None


def link_kind(url: str) -> str:
    if youtube_id(url):
        return "youtube"
    parsed = _parse(url)
    if parsed is not None and parsed.path.lower().endswith(".pdf"):
        return "pdf"
    return "article"


def _is_blocked(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"))
    )


async def ensure_public(url: str, *, allow_private: bool = False) -> None:
    """Refuse URLs that resolve to private, loopback or link-local addresses.

    Saved links are fetched by the server, so without this check a note could
    make Magpie read from the local network (SSRF).
    """
    parsed = _parse(url)
    if parsed is None or parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise FetchError(f"Unsupported URL: {url[:200]}")
    if allow_private:
        return
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        raise FetchError(f"Could not resolve {host}") from exc
    for info in infos:
        address = str(info[4][0])
        try:
            blocked = _is_blocked(address)
        except ValueError:
            blocked = True
        if blocked:
            raise FetchError(f"Refusing to fetch {host}: it resolves to a private address")
