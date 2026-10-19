import socket

import httpx2
import pytest

from magpie.config import Settings
from magpie.content import FetchError
from magpie.content import fetch as fetch_module
from magpie.content.fetch import Fetcher
from magpie.content.links import ensure_public, extract_urls, link_kind, youtube_id

ARTICLE = (
    """<!doctype html><html><head><title>Do Things That Don't Scale</title>
<meta name="author" content="Paul Graham"><meta property="og:site_name" content="paulgraham.com">
</head><body><nav>menu</nav><article><h1>Do Things That Don't Scale</h1>
"""
    + "".join(
        f"<p>Point {i}: one of the most common types of advice we give at Y Combinator is to do "
        f"things that don't scale, and founder number {i} learned it the hard way.</p>"
        for i in range(30)
    )
    + """
</article><footer>footer</footer></body></html>"""
)


@pytest.fixture
def fake_dns(monkeypatch):
    table = {"example.com": "93.184.216.34", "evil.example": "10.0.0.5", "localhost": "127.0.0.1"}

    def getaddrinfo(host, port, *args, **kwargs):
        if host not in table:
            raise socket.gaierror("unknown host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (table[host], port))]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    return table


def test_extract_urls():
    text = (
        "watch https://www.youtube.com/watch?v=dQw4w9WgXcQ, and read (https://example.com/a_(b)) "
        "plus www.Example.org/page. Also https://example.com/a_(b) again."
    )
    assert extract_urls(text) == [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://example.com/a_(b)",
        "https://www.example.org/page",
    ]
    assert extract_urls("no links here") == []


@pytest.mark.parametrize(
    ("url", "video"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?si=abc", "dQw4w9WgXcQ"),
        ("https://m.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/@channel", None),
        ("https://notyoutube.com/watch?v=dQw4w9WgXcQ", None),
    ],
)
def test_youtube_id(url, video):
    assert youtube_id(url) == video


def test_link_kind():
    assert link_kind("https://youtu.be/dQw4w9WgXcQ") == "youtube"
    assert link_kind("https://arxiv.org/pdf/1706.03762.PDF") == "pdf"
    assert link_kind("https://example.com/post") == "article"


async def test_private_addresses_are_refused(fake_dns):
    await ensure_public("https://example.com/x")
    for url in (
        "http://evil.example/",
        "http://localhost:8000/api",
        "http://127.0.0.1/",
        "http://[::1]/",
    ):
        with pytest.raises(FetchError):
            await ensure_public(url)
    with pytest.raises(FetchError):
        await ensure_public("file:///etc/passwd")
    await ensure_public("http://localhost/", allow_private=True)


async def test_fetch_article(fake_dns, router):
    router.add(
        "GET",
        "https://example.com/essay",
        lambda r: httpx2.Response(
            200, text=ARTICLE, headers={"content-type": "text/html; charset=utf-8"}
        ),
    )
    fetcher = Fetcher(
        httpx2.AsyncClient(transport=httpx2.MockTransport(router)), Settings(_env_file=None)
    )
    content = await fetcher.fetch("https://example.com/essay")
    assert content.kind == "article"
    assert content.title == "Do Things That Don't Scale"
    assert "do things that don't scale" in content.text.lower()
    assert "menu" not in content.text
    assert content.minutes and content.minutes >= 1
    assert content.has_body


async def test_redirect_to_private_address_is_blocked(fake_dns, router):
    router.add(
        "GET",
        "https://example.com/r",
        lambda r: httpx2.Response(302, headers={"location": "http://localhost/admin"}),
    )
    fetcher = Fetcher(
        httpx2.AsyncClient(transport=httpx2.MockTransport(router)), Settings(_env_file=None)
    )
    with pytest.raises(FetchError, match="private"):
        await fetcher.fetch("https://example.com/r")


async def test_fetch_pdf(fake_dns, router):
    router.add(
        "GET",
        "https://example.com/paper.pdf",
        lambda r: httpx2.Response(
            200, content=b"%PDF-1.4 fake", headers={"content-type": "application/pdf"}
        ),
    )
    fetcher = Fetcher(
        httpx2.AsyncClient(transport=httpx2.MockTransport(router)), Settings(_env_file=None)
    )
    content = await fetcher.fetch("https://example.com/paper.pdf")
    assert content.kind == "pdf"
    assert content.pdf.startswith(b"%PDF")
    assert content.title == "paper.pdf"


async def test_http_errors_raise(fake_dns, router):
    router.add("GET", "https://example.com/missing", lambda r: httpx2.Response(404))
    fetcher = Fetcher(
        httpx2.AsyncClient(transport=httpx2.MockTransport(router)), Settings(_env_file=None)
    )
    with pytest.raises(FetchError, match="404"):
        await fetcher.fetch("https://example.com/missing")


async def test_fetch_youtube(monkeypatch, router):
    router.add(
        "GET", "https://www.youtube.com/oembed", {"title": "Never Gonna", "author_name": "Rick"}
    )
    monkeypatch.setattr(
        fetch_module, "_youtube_transcript", lambda video: ("never gonna give you up", 4, "English")
    )
    fetcher = Fetcher(
        httpx2.AsyncClient(transport=httpx2.MockTransport(router)), Settings(_env_file=None)
    )
    content = await fetcher.fetch("https://youtu.be/dQw4w9WgXcQ")
    assert content.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert (content.title, content.author, content.minutes) == ("Never Gonna", "Rick", 4)
    assert content.text == "never gonna give you up"


async def test_youtube_without_transcript_still_returns_metadata(monkeypatch, router):
    from youtube_transcript_api import TranscriptsDisabled

    router.add(
        "GET", "https://www.youtube.com/oembed", {"title": "Quiet video", "author_name": "Someone"}
    )

    def no_transcript(video):
        raise TranscriptsDisabled(video)

    monkeypatch.setattr(fetch_module, "_youtube_transcript", no_transcript)
    fetcher = Fetcher(
        httpx2.AsyncClient(transport=httpx2.MockTransport(router)), Settings(_env_file=None)
    )
    content = await fetcher.fetch("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert content.title == "Quiet video"
    assert content.text == ""
    assert "No transcript" in content.note
