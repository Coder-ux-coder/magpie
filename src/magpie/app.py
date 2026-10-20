"""The FastAPI application: API, dashboard and PWA files."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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
# The largest upload Magpie accepts is an import (50 MB); recordings are 25 MB.
MAX_BODY_BYTES = 55 * 1024 * 1024


class SafeJSONResponse(JSONResponse):
    """JSON that can always be sent, even when odd input (half a surrogate pair) is echoed."""

    def render(self, content: Any) -> bytes:
        return json.dumps(
            content, ensure_ascii=True, allow_nan=True, separators=(",", ":")
        ).encode()


def _describe(error: dict[str, Any]) -> str:
    where = ".".join(str(part) for part in error.get("loc", ()) if part not in ("body", "query"))
    message = str(error.get("msg", "invalid value")).removeprefix("Value error, ")
    return f"{where}: {message}" if where else message


class _TooLarge(Exception):
    pass


class BodyLimit:
    """Refuse request bodies over the limit before they fill memory or disk."""

    def __init__(self, app: ASGIApp, limit: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        length = dict(scope["headers"]).get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > self.limit):
            await self._refuse(scope, receive, send)
            return
        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    raise _TooLarge
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _TooLarge:
            if not started:
                await self._refuse(scope, receive, send)

    @staticmethod
    async def _refuse(scope: Scope, receive: Receive, send: Send) -> None:
        response = SafeJSONResponse({"detail": "The upload is too large."}, status_code=413)
        await response(scope, receive, send)


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
        default_response_class=SafeJSONResponse,
        title="Magpie",
        version=__version__,
        description="Voice-first notes with a Claude brain.",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.magpie = core

    @app.exception_handler(RequestValidationError)
    async def invalid_input(_: Request, exc: RequestValidationError) -> JSONResponse:
        # A readable sentence, and never the (possibly huge or unsendable) input echoed back.
        errors = [_describe(error) for error in exc.errors()]
        return SafeJSONResponse({"detail": "; ".join(errors[:5]) or "Invalid request."}, 422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code in (204, 304):
            return Response(status_code=exc.status_code, headers=exc.headers)
        return SafeJSONResponse({"detail": exc.detail}, exc.status_code, headers=exc.headers)

    @app.exception_handler(OverflowError)
    async def out_of_range(_: Request, __: OverflowError) -> JSONResponse:
        return SafeJSONResponse({"detail": "A number in the request is out of range."}, 422)

    @app.exception_handler(Exception)
    async def unexpected(_: Request, __: Exception) -> JSONResponse:
        # The traceback is logged by the server; the client gets a readable message.
        return SafeJSONResponse(
            {"detail": "Something went wrong on the server. Please try again."}, 500
        )

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
        elif request.url.path.startswith("/static/"):
            # Revalidate (cheap 304s) so an update never mixes old and new scripts.
            headers.setdefault("Cache-Control", "no-cache")
        return response

    app.add_middleware(BodyLimit)
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
