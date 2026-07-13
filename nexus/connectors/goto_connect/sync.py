"""Deterministic gap-fill poll for GoTo Connect calls. NO LLM in this path.

The WebSocket stream can drop frames across restarts/outages; GoTo does not redeliver.
Every run pulls call history since the high-water mark and re-manufactures `missed_call`
stimuli through the same classify -> LOG ALWAYS -> dispatch path. The in-memory SeenCache
keeps stream-handled events from double-firing within its TTL; after a process restart a
re-polled event may log twice — acceptable, log-always never LOSES one.

Answered calls are not re-emitted here: they carry no action, and the stream already
logged the ones it saw. SMS/voicemail cannot be gap-filled (no list endpoints with the
needed shape) — their webhook-loss window is covered by the stream's reconnect logic.

State: `high_water` in vault/system/goto_connect/state.json (shared with stream.py).
Registered as "goto-connect-sync" in jobs.DETERMINISTIC_JOBS.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from nexus.config import settings
from nexus.connectors.goto_connect import NAME
from nexus.connectors.goto_connect.events import to_stimulus
from nexus.connectors.goto_connect.stream import _load_state, _save_state

log = logging.getLogger(__name__)

_REPOLL_OVERLAP = timedelta(minutes=5)  # absorbs clock skew between poll runs
_FIRST_RUN_WINDOW = timedelta(hours=24)  # how far back the very first poll reaches


async def run_sync(client=None) -> None:
    """Reconcile missed calls from call history. Registered in DETERMINISTIC_JOBS."""
    from nexus.connectors.goto_connect.client import GoToAuthError, GoToClient

    if client is None:
        if not settings.goto_connect_client_id:
            log.warning("goto_connect sync skipped: no OAuth client configured")
            return
        client = GoToClient()

    state = _load_state()
    run_started = datetime.now(UTC)
    since_raw = state.get("high_water")
    since = (
        datetime.fromisoformat(since_raw) if since_raw else run_started - _FIRST_RUN_WINDOW
    )

    try:
        rows = client.recent_calls(since=since)
    except GoToAuthError as exc:
        log.error("goto_connect sync: auth failure: %s", exc)
        return

    for row in rows:
        # Reuse the exact push-frame mapper: a history row IS a UchEvent content body.
        stimulus = to_stimulus(
            {"data": {"source": "call-history", "type": "UchEvent", "content": row}}
        )
        if stimulus is None or stimulus.kind != "missed_call":
            continue
        await _emit(stimulus)

    state["high_water"] = (run_started - _REPOLL_OVERLAP).isoformat()
    _save_state(state)


async def _emit(stimulus) -> None:
    """classify -> dedup -> LOG ALWAYS -> dispatch, same as the stream path."""
    from nexus.connectors.ingress import security
    from nexus.connectors.ingress.router import dispatch
    from nexus.connectors.ingress.rules import classify
    from nexus.writes import append_log

    if security.already_seen(NAME, stimulus.external_id):
        return  # the stream (or a prior poll) already handled it
    tier = classify(stimulus.source, stimulus.kind)
    try:
        who = stimulus.payload.get("phone") or ""
        append_log(f"[{tier}] {NAME}:{stimulus.kind} {stimulus.external_id or ''} {who}")
    except Exception:  # noqa: BLE001 — log-always is best-effort, must not block dispatch
        log.exception("goto_connect sync: event-log append failed")
    await dispatch(stimulus, tier)
