"""Inbound contract for `example`.

The entire surface of a push connector: NAME, SIGNATURE_HEADER, secret(), parse(), and an
optional signed_timestamp(). The shared ingress route handles HMAC verification, the
replay window, idempotency, "log always", the <3s ACK, and dispatch generically — a new
push source is one small module plus one row in the CONNECTORS registry.
"""

from __future__ import annotations

from datetime import UTC, datetime

from nexus.connectors.ingress.envelope import Stimulus

NAME = "example"
SIGNATURE_HEADER = "x-example-signature"  # header carrying the HMAC

# vendor event type -> YOUR kind (the kinds your rules table and agents reason about).
_KIND_MAP = {
    "record.created": "new_record",
    "record.updated": "record_changed",
}


def secret(cfg) -> str | None:
    """The verification secret from config (None => ingress refuses with 503)."""
    return cfg.example_webhook_secret


def parse(payload: dict, headers, raw: bytes) -> Stimulus:
    """Map THIS vendor's wire format onto the universal envelope."""
    return Stimulus(
        source=NAME,
        kind=_KIND_MAP.get(payload.get("type", ""), "unknown"),
        received_at=datetime.now(UTC),
        external_id=str(payload.get("id")) if payload.get("id") is not None else None,
        payload=payload,
        raw=raw,
    )


def signed_timestamp(headers) -> float | None:
    """Optional: return the vendor-signed unix timestamp to enable the replay window."""
    ts = headers.get("x-example-timestamp")
    return float(ts) if ts else None
