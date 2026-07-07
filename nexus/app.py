"""Thin entrypoint (spec §10.1).

Wires FastMCP + Starlette into one ASGI app bound to one volume:
  - register_all() exposes the vault read/write functions as MCP tools (§3.5).
  - the ingress routes own every non-MCP HTTP entry (§5).
  - /health is the boot smoke test (build-order step 1 exit criterion).

Run: `uvicorn nexus.app:app`
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from nexus.config import settings
from nexus.middleware import BodyCapMiddleware, LoggingMiddleware, MountSlashMiddleware


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "env": settings.nexus_env})


def build_app() -> Starlette:
    from starlette.routing import Mount

    from nexus.connectors.ingress.routes import routes as ingress_routes

    routes = [Route("/health", health, methods=["GET"]), *ingress_routes]
    lifespan = None

    # MCP tool surface mounted at /mcp — the SAME plain functions back chat and the
    # server-side loop (§3.5). Its lifespan is threaded into the parent so the MCP session
    # manager starts/stops correctly. Resilient: a bare HTTP server still boots without it.
    try:
        from nexus.tools import build_mcp

        # json_response + stateless_http: reply with a single buffered application/json
        # body instead of an SSE stream. Railway's edge proxy rejects the chunked
        # text/event-stream response with 421 Misdirected Request; buffered JSON passes
        # cleanly, and a tools-only server needs no server-initiated SSE channel.
        mcp_app = build_mcp().http_app(path="/", json_response=True, stateless_http=True)
        routes.append(Mount("/mcp", app=mcp_app))
        lifespan = mcp_app.lifespan
    except Exception:  # noqa: BLE001 — MCP is optional for the bare HTTP skeleton
        pass

    # Pure-ASGI middleware, outermost first (§9). Starlette builds the stack so there is
    # no self-referential wrapping (which would recurse infinitely). The /mcp bearer guard
    # lives inside the FastMCP app (StaticTokenVerifier, see tools.build_mcp), not here.
    # MountSlashMiddleware serves the exact /mcp path with no 307 — behind Railway's edge
    # that redirect round-trip is what surfaced as 421 Misdirected Request for MCP clients.
    middleware = [
        Middleware(LoggingMiddleware),
        Middleware(BodyCapMiddleware),
        Middleware(MountSlashMiddleware),
    ]
    return Starlette(routes=routes, middleware=middleware, lifespan=lifespan)


app = build_app()
