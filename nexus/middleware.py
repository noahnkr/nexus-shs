"""Pure-ASGI middleware (spec §9).

Deliberately NOT Starlette's `BaseHTTPMiddleware`: pure-ASGI lets the webhook routes read
the raw request body twice (once for HMAC verification, once for parsing) and avoids the
extra task/anyio overhead BaseHTTPMiddleware imposes. Two concerns:

  - LoggingMiddleware: structured access log around each request.
  - BodyCapMiddleware: reject oversized bodies early (defense at the edge).
"""

from __future__ import annotations

import hmac
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


class McpAuthMiddleware:
    """Bearer-guard the `/mcp` control plane (spec §5.3, §8 the MCP seam).

    The MCP mount exposes read + vault-write tools; it is the privileged surface. This is
    the bearer check the connectors reference as "the MCP bearer auth" they deliberately
    bypass (each webhook/cron route self-authenticates instead). Only `/mcp` is guarded —
    `/health`, `/webhooks/*`, `/cron/*` pass straight through.

    Constant-time compare against `settings.mcp_token`. A missing/wrong token gets a 401
    with `WWW-Authenticate: Bearer` so clients know how to present credentials.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        from nexus.config import settings

        provided = ""
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                provided = value.decode("latin-1").removeprefix("Bearer ").strip()
                break

        if not hmac.compare_digest(provided, settings.mcp_token):
            await _send_401(send)
            return

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


async def _send_401(send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"text/plain"),
                (b"www-authenticate", b"Bearer"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": b"unauthorized"})
