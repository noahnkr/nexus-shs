"""GoTo notification frame -> Stimulus mapping (spec §4.1). Pure functions, no IO.

Every push frame (WebSocket or webhook channel — same envelope) looks like:

    {"event": "Notification", "eventId": N, "timestamp": ..., "data":
        {"source": ..., "type": ..., "content": {...}}}

Discrimination is on (data.source, data.type) AS DATA (§1.1) via _MAPPERS. Shapes were
confirmed against live captures 2026-07-13 (docs/connectors/goto-connect.md).

CAUTION — UchEvent's caller/callee/direction are leg-relative and unintuitive (an inbound
external call reports the extension as `callee` with direction OUTBOUND). The external
party is therefore identified structurally: the side whose number is routable (>= 7
digits), never via `direction`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from nexus.connectors.goto_connect import NAME
from nexus.connectors.ingress.envelope import Stimulus

# Kinds that wake the reactive agent. Answered calls (`call_ended`) are logged (§1.7
# "log always") but do not dispatch — no judgment is needed for a completed conversation.
DISPATCH_KINDS = {"missed_call", "sms_received", "voicemail_received"}


def normalize_phone(number: str | None) -> str | None:
    """E.164-ish (+1XXXXXXXXXX) for routable numbers; None for extensions/absent.

    US-centric on purpose: this account's DIDs and callers are NANP. 3-6 digit strings
    are internal extensions, not phone numbers.
    """
    if not number:
        return None
    digits = "".join(ch for ch in str(number) if ch.isdigit())
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if len(digits) >= 12:  # already-international with country code
        return f"+{digits}"
    return None


def external_party(content: dict[str, Any]) -> tuple[str | None, str | None]:
    """(name, phone) of the non-extension side of a UchEvent, else (None, None)."""
    for side in ("caller", "callee"):
        info = content.get(side) or {}
        phone = normalize_phone(info.get("number"))
        if phone is not None:
            return info.get("name") or None, phone
    return None, None


def _map_call(content: dict[str, Any]) -> Stimulus | None:
    leg_id = content.get("legId")
    if leg_id is None:
        return None
    name, phone = external_party(content)
    if phone is None:
        return None  # extension-to-extension: internal plumbing, not a business event
    answered = bool(content.get("answerTime"))
    return Stimulus(
        source=NAME,
        kind="call_ended" if answered else "missed_call",
        received_at=datetime.now(UTC),
        external_id=f"call:{leg_id}",
        payload={
            "phone": phone,
            "name": name,
            "our_number": content.get("ownerPhoneNumber"),
            "start_time": content.get("startTime"),
            "answered": answered,
            "duration_ms": content.get("duration"),
        },
    )


def _map_message(content: dict[str, Any]) -> Stimulus | None:
    if content.get("direction") != "IN":
        return None  # our own outbound messages are not stimuli
    return Stimulus(
        source=NAME,
        kind="sms_received",
        received_at=datetime.now(UTC),
        external_id=f"sms:{content.get('id')}",
        payload={
            "phone": normalize_phone(content.get("authorPhoneNumber")),
            "our_number": content.get("ownerPhoneNumber"),
            "body": content.get("body"),
            "media_count": len(content.get("media") or []),
            "timestamp": content.get("timestamp"),
        },
    )


def _map_voicemail(content: dict[str, Any]) -> Stimulus | None:
    vid = content.get("voicemailId")
    if vid is None:
        return None
    return Stimulus(
        source=NAME,
        kind="voicemail_received",
        received_at=datetime.now(UTC),
        external_id=f"vm:{vid}",
        payload={
            "voicemail_id": vid,
            "phone": normalize_phone(content.get("callerNumber")),
            "name": content.get("callerName"),
            "our_number": content.get("calledNumber"),
            "extension": content.get("extensionNumber"),
            "duration_ms": content.get("durationMs"),
            "leg_id": content.get("legId"),  # links to the missed_call UchEvent
            "timestamp": content.get("timestamp"),
        },
    )


_MAPPERS = {
    ("call-history", "UchEvent"): _map_call,
    ("messaging", "message"): _map_message,
    ("VOICEMAIL", "NEW_VOICEMAIL"): _map_voicemail,
    # ("call-events", "call-state") deliberately unmapped: per-leg state churn; the
    # single UchEvent at call end carries everything the vault needs.
}


def to_stimulus(frame: dict[str, Any]) -> Stimulus | None:
    """Map one notification frame to a Stimulus, or None for frames we ignore."""
    data = frame.get("data") or {}
    mapper = _MAPPERS.get((data.get("source"), data.get("type")))
    if mapper is None:
        return None
    return mapper(data.get("content") or {})
