"""The HTTP front door (spec §5.2) — /webhooks/{source} and /cron/{job}.

The inline path must be FAST and LOSSLESS (webhook senders time out in ~3s). The handler
does only fast, must-not-lose work and returns; all real work runs after the ACK:

  verify signature -> parse to Stimulus -> dedup (external_id) ->
    LOG ALWAYS (durable append to events/) -> schedule dispatch (background) -> 200

LOG ALWAYS happens inline, before acting, regardless of tier — so even if the agent later
crashes, the event is on disk (§5.2).

CONNECTORS is the per-source registry (FORK SEAM, §7 step 3): add one row per connector.
"""

from __future__ import annotations

from types import ModuleType

from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from nexus.config import settings
from nexus.connectors.example import webhook as example_webhook
from nexus.connectors.ingress import security
from nexus.connectors.ingress.router import dispatch
from nexus.connectors.ingress.rules import classify

# FORK: register each push connector's webhook module here, keyed by its NAME.
CONNECTORS: dict[str, ModuleType] = {
    example_webhook.NAME: example_webhook,
}


async def webhook(request: Request) -> Response:
    source = request.path_params["source"]
    conn = CONNECTORS.get(source)
    if conn is None:
        return JSONResponse({"error": "unknown source"}, status_code=404)

    raw = await request.body()

    # 1) verify — no secret configured => refuse (503), never trust blindly (§5.3).
    secret = conn.secret(settings)
    if not secret:
        return JSONResponse({"error": "no secret configured"}, status_code=503)
    sig = request.headers.get(conn.SIGNATURE_HEADER, "")
    if not security.verify_hmac_sha256(secret, raw, sig):
        return JSONResponse({"error": "bad signature"}, status_code=401)  # constant-time

    # 1b) optional replay window
    if hasattr(conn, "signed_timestamp"):
        ts = conn.signed_timestamp(request.headers)
        if ts is not None and not security.within_window(ts):
            return JSONResponse({"error": "stale"}, status_code=401)

    # 2) parse to the universal envelope
    stimulus = conn.parse(await _json(raw), request.headers, raw)

    # 3) dedup — vendor re-deliveries are skipped (still ACK 200)
    if security.already_seen(stimulus.source, stimulus.external_id):
        return JSONResponse({"status": "duplicate"}, status_code=200)

    # 4) LOG ALWAYS — durable, inline, before acting, regardless of tier
    tier = classify(stimulus.source, stimulus.kind)
    _log_always(stimulus, tier)

    # 5) schedule dispatch in the background and ACK fast
    return JSONResponse(
        {"status": "accepted", "tier": tier},
        status_code=200,
        background=BackgroundTask(dispatch, stimulus, tier),
    )


async def cron(request: Request) -> Response:
    """/cron/{job} — bearer-protected trigger for deterministic or agent jobs (§5.6)."""
    if request.headers.get("authorization") != f"Bearer {settings.cron_token}":
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    from nexus.connectors.ingress.jobs import run_job

    job = request.path_params["job"]
    return await run_job(job)


async def _json(raw: bytes) -> dict:
    import json

    try:
        return json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return {}


def _log_always(stimulus, tier: str) -> None:
    """Durable, inline append to the event log (§5.2). Best-effort, must not raise out."""
    from nexus.writes import append_log

    try:
        append_log(f"[{tier}] {stimulus.source}:{stimulus.kind} {stimulus.external_id or ''}")
    except NotImplementedError:
        pass  # skeleton: events.append_entry not yet implemented


routes = [
    Route("/webhooks/{source}", webhook, methods=["POST"]),
    Route("/cron/{job}", cron, methods=["POST"]),
]
