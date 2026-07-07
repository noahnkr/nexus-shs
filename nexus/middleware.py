"""Pure-ASGI middleware (spec §9).

Deliberately NOT Starlette's `BaseHTTPMiddleware`: pure-ASGI lets the webhook routes read
the raw request body twice (once for HMAC verification, once for parsing) and avoids the
extra task/anyio overhead BaseHTTPMiddleware imposes. Two concerns:

  - LoggingMiddleware: structured access log around each request.
  - BodyCapMiddleware: reject oversized bodies early (defense at the edge).
  - MountSlashMiddleware: serve `/mcp` at the exact path, no 307 (proxy-edge safety).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

logger = logging.getLogger("nexus.http")

Receive = Callable[[], Awaitable[dict]]
Send = Callable[[dict], Awaitable[None]]
ASGIApp = Callable[[dict, Receive, Send], Awaitable[None]]


class LoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.monotonic()
        status_holder: dict[str, int] = {}

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        await self.app(scope, receive, send_wrapper)
        dur_ms = (time.monotonic() - start) * 1000
        logger.info(
            "%s %s -> %s (%.1fms)",
            scope.get("method"),
            scope.get("path"),
            status_holder.get("status"),
            dur_ms,
        )


class MountSlashMiddleware:
    """Rewrite an exact mount path ('/mcp') to its slashed form ('/mcp/') before routing.

    Without this, Starlette's redirect_slashes answers `POST /mcp` with a 307 whose
    Location is built from the ASGI scope. Behind Railway's edge that redirect is where
    MCP clients die: Claude's connector fetcher POSTs to the no-slash URL, and bouncing
    it back through the edge (worst case with an `http://` Location when proxy headers
    aren't trusted) surfaces as `421 Misdirected Request`. Serving the exact path
    directly removes the round trip entirely — MCP requests never redirect.
    """

    def __init__(self, app: ASGIApp, paths: tuple[str, ...] = ("/mcp",)) -> None:
        self.app = app
        self.paths = paths

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope.get("path") in self.paths:
            scope = dict(scope)
            scope["path"] = scope["path"] + "/"
            scope["raw_path"] = scope["path"].encode()
        await self.app(scope, receive, send)


class BodyCapMiddleware:
    """Reject requests whose body exceeds `max_bytes` with a 413."""

    def __init__(self, app: ASGIApp, max_bytes: int = 5 * 1024 * 1024) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Fast path: trust a sane Content-Length when present.
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    if int(value) > self.max_bytes:
                        await _send_413(send)
                        return
                except ValueError:
                    pass
                break

        await self.app(scope, receive, send)


async def _send_413(send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": b"payload too large"})
