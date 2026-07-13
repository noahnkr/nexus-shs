"""Inbound contract for goto_connect — registry entry + MCP tools seam.

Production delivery is the WebSocket consumer (stream.py), NOT this route: GoTo's webhook
deliveries are unsigned and its Call Events API refuses webhook channels. This module
still registers the connector in CONNECTORS so that

  (a) the per-connector MCP tools seam (tools/connectors.py) picks up `tools()`, and
  (b) a webhook notification channel CAN be pointed at /webhooks/goto_connect as a
      backstop later. GoTo cannot HMAC-sign bodies, so such a channel would only be
      viable if ingress grows token-in-URL verification; until then no secret is
      configured and the shared route refuses with 503 — never trust blindly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from nexus.connectors.goto_connect import NAME
from nexus.connectors.goto_connect.events import to_stimulus
from nexus.connectors.ingress.envelope import Stimulus

SIGNATURE_HEADER = "x-goto-signature"  # unused by GoTo; kept for the shared route contract


def secret(cfg) -> str | None:
    """No secret configured => ingress refuses (503). GoTo does not sign webhook bodies,
    so this stays unset unless/until token-in-URL verification lands in ingress."""
    return cfg.goto_connect_webhook_secret


def parse(payload: dict, headers, raw: bytes) -> Stimulus:
    """Map a notification-channel delivery (same envelope as the WS frames)."""
    stimulus = to_stimulus(payload)
    if stimulus is not None:
        return stimulus.model_copy(update={"raw": raw})
    return Stimulus(
        source=NAME,
        kind="unknown",  # fails safe to supervised in rules.classify
        received_at=datetime.now(UTC),
        payload=payload,
        raw=raw,
    )


def tools(target: Any) -> None:
    """Connector-specific MCP tools (⚙ seam, tools/connectors.py) — read-only."""
    from nexus.connectors.goto_connect.client import goto_get_voicemail, goto_lookup_history

    target.tool(name="goto_lookup_history")(goto_lookup_history)
    target.tool(name="goto_get_voicemail")(goto_get_voicemail)
