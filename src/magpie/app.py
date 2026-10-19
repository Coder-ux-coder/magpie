"""The FastAPI application: API, dashboard and PWA files."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from magpie import __version__, api
from magpie.config import Settings
from magpie.core import Magpie

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"
SESSION_DAYS = 30
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https:; media-src 'self' blob:; connect-src 'self'; "
    "worker-src 'self'; manifest-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'self'; object-src 'none'"
)


def create_app(
    settings: Settings | None = None,
    *,
    magpie: Magpie | None = None,
    background: bool = True,
) -> FastAPI:
    settings = settings or Settings()
    core = magpie or Magpie(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if background:
            await core.start()
        try:
            yield
        finally:
            await core.stop()

    app = FastAPI(
        title="Magpie",
        version=__version__,
        description="Voice-first notes with a Claude brain.",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.magpie = core
    app.add_middleware(
        SessionMiddleware,
        secret_key=core.secrets.secret_key,
        session_cookie="magpie_session",
        max_age=SESSION_DAYS * 24 * 3600,
        same_site="lax",
        https_only=settings.secure_cookies,
    )

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "same-origin")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Permissions-Policy", "microphone=(self), camera=(), geolocation=()")
        if not request.url.path.startswith("/api/docs"):
            headers.setdefault("Content-Security-Policy", CSP)
        if request.url.path.startswith("/api/"):
            headers.setdefault("Cache-Control", "no-store")
        return response

    app.include_router(api.router)
    app.include_router(api.public)
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    index = WEB_DIR / "index.html"

    @app.get("/", include_in_schema=False)
    @app.get("/share", include_in_schema=False)
    async def dashboard() -> FileResponse:
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    @app.get("/sw.js", include_in_schema=False)
    async def service_worker() -> FileResponse:
        return FileResponse(
            WEB_DIR / "sw.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
        )

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def manifest() -> FileResponse:
        return FileResponse(
            WEB_DIR / "manifest.webmanifest", media_type="application/manifest+json"
        )

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        return FileResponse(WEB_DIR / "static" / "icons" / "favicon.png", media_type="image/png")

    return app
