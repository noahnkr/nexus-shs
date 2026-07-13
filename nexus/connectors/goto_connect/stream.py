"""Persistent WebSocket consumer — GoTo's push feed into the ingress path (spec §5.2).

Why a WebSocket and not /webhooks/goto_connect: GoTo's Call Events API refuses webhook
channels outright, and its webhook deliveries are UNSIGNED — a WS channel is authenticated
by the OAuth token at creation and needs no public endpoint. One mechanism for all four
event sources.

Frames re-enter the exact discipline a webhook would (§5.2):
    parse (events.to_stimulus) -> dedup -> classify -> LOG ALWAYS -> dispatch
`call_ended` is logged but never dispatched (events.DISPATCH_KINDS).

Channel state persists at vault/system/goto_connect/state.json (channel URL + id) so a
process restart reuses the live channel and its subscriptions; any connect failure falls
back to creating a fresh channel. GoTo sends WEBSOCKET_REFRESH_REQUIRED ~every 10 min —
a plain reconnect to the same URL preserves the channel (confirmed 2026-07-13).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus.config import settings

log = logging.getLogger(__name__)

_RECV_TIMEOUT = 60.0  # seconds between frames before we just loop (keepalive is ping/pong)
_BACKOFF_START = 5.0
_BACKOFF_MAX = 300.0


def _state_path() -> Path:
    return settings.vault_path / "system" / "goto_connect" / "state.json"


def _load_state() -> dict[str, Any]:
    path = _state_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _ensure_channel(force_new: bool = False) -> str:
    """Return a wss:// URL for a subscribed channel, creating one when needed (sync)."""
    from nexus.connectors.goto_connect.client import GoToClient

    state = _load_state()
    if not force_new and state.get("channel_url"):
        return state["channel_url"]

    client = GoToClient()
    nickname = f"nexus-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    chan = client.create_channel(nickname)
    url = chan.get("channelData", {}).get("channelURL")
    channel_id = chan.get("channelId")
    if not url or not channel_id:
        raise RuntimeError(f"goto_connect: channel response missing URL/id: {chan}")
    client.subscribe_all(channel_id)
    state.update({"channel_id": channel_id, "channel_url": url, "channel_nickname": nickname})
    _save_state(state)
    log.info("goto_connect: created notification channel %s", nickname)
    return url


async def _process(raw_frame: str | bytes) -> None:
    """One frame through parse -> dedup -> classify -> log always -> dispatch."""
    from nexus.connectors.goto_connect import NAME
    from nexus.connectors.goto_connect.events import DISPATCH_KINDS, to_stimulus
    from nexus.connectors.ingress import security
    from nexus.connectors.ingress.router import dispatch
    from nexus.connectors.ingress.rules import classify
    from nexus.writes import append_log

    try:
        frame = json.loads(raw_frame)
    except (ValueError, TypeError):
        log.warning("goto_connect: undecodable frame (%d bytes)", len(raw_frame))
        return
    stimulus = to_stimulus(frame)
    if stimulus is None:
        return
    if security.already_seen(NAME, stimulus.external_id):
        return
    tier = classify(stimulus.source, stimulus.kind)
    try:
        who = stimulus.payload.get("phone") or ""
        append_log(f"[{tier}] {NAME}:{stimulus.kind} {stimulus.external_id or ''} {who}")
    except Exception:  # noqa: BLE001 — log-always is best-effort, must not block dispatch
        log.exception("goto_connect: event-log append failed")
    if stimulus.kind in DISPATCH_KINDS:
        await dispatch(stimulus, tier)
    else:
        # Log-only kinds (answered calls) still wrote the event note; settle INDEX.md
        # here since no agent loop follows to do it (dispatched kinds settle in run_loop).
        from nexus.vault.index import regenerate_if_dirty

        regenerate_if_dirty()


async def run_stream() -> None:
    """Long-lived consumer task; started from the app lifespan. Never raises out."""
    if not settings.goto_connect_client_id or not settings.goto_connect_client_secret:
        log.info("goto_connect: stream disabled (no OAuth client configured)")
        return

    import websockets

    from nexus.connectors.goto_connect.client import GoToAuthError

    backoff = _BACKOFF_START
    force_new = False
    while True:
        try:
            url = await asyncio.to_thread(_ensure_channel, force_new)
            force_new = False
            async with websockets.connect(url, ping_interval=20) as ws:
                log.info("goto_connect: stream connected")
                backoff = _BACKOFF_START
                while True:
                    try:
                        frame = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT)
                    except TimeoutError:
                        continue
                    if isinstance(frame, str) and "WEBSOCKET_REFRESH_REQUIRED" in frame:
                        log.debug("goto_connect: refresh requested — reconnecting")
                        break  # reconnect to the same channel URL
                    await _process(frame)
        except asyncio.CancelledError:
            raise  # app shutdown
        except GoToAuthError as exc:
            # Not self-healing (needs re-authorize or .env fix) — park, retry rarely.
            log.error("goto_connect: stream auth failure: %s", exc)
            await asyncio.sleep(_BACKOFF_MAX)
        except Exception:  # noqa: BLE001 — connection/HTTP hiccups: back off, then rebuild
            log.exception("goto_connect: stream error — reconnecting in %.0fs", backoff)
            await asyncio.sleep(backoff)
            if backoff >= _BACKOFF_MAX:
                force_new = True  # channel may be dead server-side; rebuild it
            backoff = min(backoff * 2, _BACKOFF_MAX)
