"""Download a saved link and turn it into text Claude can review."""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx2
import trafilatura
from youtube_transcript_api import YouTubeTranscriptApi, YouTubeTranscriptApiException

from magpie.config import Settings
from magpie.content import FetchError, LinkContent
from magpie.content.links import ensure_public, link_kind, youtube_id

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; MagpieNotes/0.1; +https://github.com/Coder-ux-coder/magpie)"
MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_REDIRECTS = 5
# httpx timeouts apply to each read; a server that trickles bytes needs an overall limit.
DOWNLOAD_SECONDS = 90
WORDS_PER_MINUTE = 230
TRANSCRIPT_LANGUAGES = ("en", "en-GB", "en-US")


class Fetcher:
    def __init__(self, http: httpx2.AsyncClient, settings: Settings) -> None:
        self.http = http
        self.settings = settings

    async def fetch(self, url: str) -> LinkContent:
        if link_kind(url) == "youtube":
            return await self.fetch_youtube(url)
        return await self.fetch_page(url)

    # ---------------------------------------------------------------- YouTube

    async def fetch_youtube(self, url: str) -> LinkContent:
        video = youtube_id(url)
        if video is None:
            raise FetchError(f"Not a YouTube video URL: {url}")
        canonical = f"https://www.youtube.com/watch?v={video}"
        content = LinkContent(url=canonical, kind="youtube", site="YouTube")
        try:
            response = await self.http.get(
                "https://www.youtube.com/oembed",
                params={"url": canonical, "format": "json"},
                timeout=15,
            )
            data = response.json() if response.status_code == 200 else None
            if isinstance(data, dict):
                content.title = str(data.get("title") or "")
                content.author = str(data.get("author_name") or "")
        except (httpx2.HTTPError, ValueError) as exc:
            log.info("YouTube oEmbed failed for %s: %s", video, exc)
        try:
            text, minutes, language = await asyncio.to_thread(_youtube_transcript, video)
            content.text = text
            content.minutes = minutes
            if language:
                content.note = f"Transcript language: {language}."
        except YouTubeTranscriptApiException as exc:
            log.info("No transcript for %s: %s", video, type(exc).__name__)
            content.note = "No transcript was available, only the title and channel."
        except Exception as exc:  # the library raises plain errors on network trouble
            log.warning("Transcript fetch failed for %s: %s", video, exc)
            content.note = "The transcript could not be downloaded."
        return content

    # ------------------------------------------------------- articles and PDFs

    async def fetch_page(self, url: str) -> LinkContent:
        try:
            async with asyncio.timeout(DOWNLOAD_SECONDS):
                response, body = await self._download(url)
        except TimeoutError as exc:
            raise FetchError("The page took too long to download.") from exc
        except httpx2.HTTPError as exc:
            raise FetchError(f"Could not download the page ({type(exc).__name__}).") from exc
        except (ValueError, UnicodeError) as exc:  # e.g. a host name httpx can't encode
            raise FetchError(f"Could not download the page: {exc}") from exc
        final_url = str(response.url)
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type == "application/pdf" or body[:5] == b"%PDF-":
            if len(body) > MAX_PDF_BYTES:
                raise FetchError("The PDF is larger than 20 MB.")
            name = urlparse(final_url).path.rsplit("/", 1)[-1] or "document.pdf"
            return LinkContent(
                url=final_url, kind="pdf", title=name, pdf=body, site=_site(final_url)
            )
        if content_type and not (
            content_type.startswith("text/") or "html" in content_type or "xml" in content_type
        ):
            raise FetchError(f"Unsupported content type: {content_type}")
        try:
            html = body.decode(response.encoding or "utf-8", errors="replace")
        except LookupError:  # an unknown charset name
            html = body.decode("utf-8", errors="replace")
        text, title, author, site = await asyncio.to_thread(_extract_article, html, final_url)
        words = len(text.split())
        return LinkContent(
            url=final_url,
            kind="article",
            title=title,
            author=author,
            site=site or _site(final_url),
            text=text,
            minutes=max(1, round(words / WORDS_PER_MINUTE)) if words else None,
        )

    async def _download(self, url: str) -> tuple[httpx2.Response, bytes]:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            await ensure_public(current, allow_private=self.settings.allow_private_urls)
            async with self.http.stream(
                "GET",
                current,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.5",
                },
                follow_redirects=False,
                timeout=30,
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise FetchError("Redirect without a location")
                    current = urljoin(current, location)
                    continue
                if response.status_code >= 400:
                    raise FetchError(f"The page returned HTTP {response.status_code}")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_PDF_BYTES:
                        raise FetchError("The page is too large to fetch.")
                    chunks.append(chunk)
                    content_type = response.headers.get("content-type", "")
                    if "pdf" not in content_type and size > MAX_HTML_BYTES:
                        break
                return response, b"".join(chunks)
        raise FetchError("Too many redirects")


def _youtube_transcript(video: str) -> tuple[str, int | None, str]:
    transcripts = YouTubeTranscriptApi().list(video)
    try:
        transcript = transcripts.find_transcript(TRANSCRIPT_LANGUAGES)
    except YouTubeTranscriptApiException:
        transcript = next(iter(transcripts), None)
        if transcript is None:
            raise FetchError("The video has no transcripts") from None
    fetched = transcript.fetch()
    text = " ".join(snippet.text.replace("\n", " ") for snippet in fetched)
    text = re.sub(r"\s+", " ", text).strip()
    end = max((s.start + s.duration for s in fetched), default=0)
    minutes = round(end / 60) if end else None
    return text, minutes, transcript.language


def _extract_article(html: str, url: str) -> tuple[str, str, str, str]:
    try:
        text = (
            trafilatura.extract(
                html,
                url=url,
                include_comments=False,
                include_tables=True,
                favor_precision=True,
            )
            or ""
        )
    except Exception as exc:  # odd markup can trip the parser; Claude can still try
        log.info("Could not extract the article text from %s: %s", url, exc)
        text = ""
    title = author = site = ""
    try:
        metadata = trafilatura.extract_metadata(html, default_url=url)
    except Exception:  # metadata is a bonus; never fail the fetch over it
        metadata = None
    if metadata is not None:
        title = metadata.title or ""
        author = metadata.author or ""
        site = metadata.sitename or ""
    if not title:
        match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        title = re.sub(r"\s+", " ", match.group(1)).strip() if match else ""
    return text, title, author, site


def _site(url: str) -> str:
    host = urlparse(url).hostname or ""
    return host.removeprefix("www.")
