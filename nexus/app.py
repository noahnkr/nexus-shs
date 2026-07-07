"""Thin entrypoint (spec §10.1).

Wires FastMCP + Starlette into one ASGI app bound to one volume:
  - register_all() exposes the vault read/write functions as MCP tools (§3.5).
  - the ingress routes own every non-MCP HTTP entry (§5).
  - /health is the boot smoke test (build-order step 1 exit criterion).

Run: `uvicorn nexus.app:app`
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from nexus.config import settings
from nexus.middleware import BodyCapMiddleware, LoggingMiddleware


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "env": settings.nexus_env})


def _public_host(url: str) -> str | None:
    """Hostname from PUBLIC_URL, tolerating a bare domain with no scheme."""
    if "://" not in url:
        url = f"//{url}"
    return urlsplit(url).hostname


def build_app() -> Starlette:
    from starlette.routing import Mount

    from nexus.connectors.ingress.routes import routes as ingress_routes

    routes = [Route("/health", health, methods=["GET"]), *ingress_routes]
    lifespan = None

    # MCP tool surface at /mcp — the SAME plain functions back chat and the server-side
    # loop (§3.5). The FastMCP app owns the path *inside itself* (http_app(path="/mcp"))
    # and is mounted at "/" as the catch-all, so `/mcp` is served at the exact path with
    # no trailing-slash mount and no 307 — the redirect a Mount("/mcp") would emit is
    # what MCP clients behind Railway's edge choke on. Its lifespan is threaded into the
    # parent so the MCP session manager starts/stops correctly. Resilient: a bare HTTP
    # server still boots without it.
    try:
        from nexus.tools import build_mcp

        # json_response + stateless_http: reply with a single buffered application/json
        # body instead of an SSE stream — a tools-only server needs no server-initiated
        # SSE channel. allowed_hosts: fastmcp >= 3.4.3 ships default-on DNS-rebinding
        # protection that 421s any Host outside localhost + the bind address, so the
        # public domain must be allowlisted or every proxied request is rejected with
        # 421 Misdirected Request.
        host = _public_host(settings.public_url)
        mcp_app = build_mcp().http_app(
            path="/mcp",
            json_response=True,
            stateless_http=True,
            allowed_hosts=[host] if host else None,
        )
        routes.append(Mount("/", app=mcp_app))
        lifespan = mcp_app.lifespan
    except Exception:  # noqa: BLE001 — MCP is optional for the bare HTTP skeleton
        pass

    # Pure-ASGI middleware, outermost first (§9). Starlette builds the stack so there is
    # no self-referential wrapping (which would recurse infinitely). The /mcp bearer guard
    # lives inside the FastMCP app (StaticTokenVerifier, see tools.build_mcp), not here.
    middleware = [
        Middleware(LoggingMiddleware),
        Middleware(BodyCapMiddleware),
    ]
    return Starlette(routes=routes, middleware=middleware, lifespan=lifespan)


app = build_app()
