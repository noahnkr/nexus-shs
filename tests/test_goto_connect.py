"""GoTo Connect connector tests (docs/connectors/goto-connect.md step 9).

Frame -> Stimulus mapping is exercised with synthetic frames shaped exactly like the live
captures from the 2026-07-13 WebSocket recon (no real customer data). The stream processor
and gap-fill sync are exercised with dispatch intercepted — no network, no LLM.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest

from nexus.connectors.goto_connect.events import (
    DISPATCH_KINDS,
    external_party,
    normalize_phone,
    to_stimulus,
)


@pytest.fixture(autouse=True)
def vault(tmp_path, monkeypatch):
    """Isolate each test on a fresh temp vault and reset the in-process search index."""
    from nexus.config import settings
    from nexus.vault import search

    monkeypatch.setattr(settings, "vault_path", tmp_path)
    monkeypatch.setattr(search, "_index", None)
    for fam in ("reference", "entity", "events", "tasks"):
        (tmp_path / fam).mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def dispatched(monkeypatch):
    """Capture (stimulus, tier) pairs instead of waking real agents."""
    calls: list[tuple] = []

    async def fake_dispatch(stimulus, tier):
        calls.append((stimulus, tier))

    from nexus.connectors.ingress import router

    monkeypatch.setattr(router, "dispatch", fake_dispatch)
    return calls


def _frame(source: str, type_: str, content: dict) -> dict:
    return {
        "event": "Notification",
        "eventId": 1,
        "timestamp": "2026-07-13T21:53:26Z",
        "data": {"source": source, "type": type_, "content": content},
    }


def _call_content(leg: str, *, answered: bool, external_side: str = "caller") -> dict:
    external = {"name": "WIRELESS CALLER", "number": "6306999881"}
    extension = {"name": "Brennen Roberts", "number": "1000"}
    content = {
        "legId": leg,
        "caller": external if external_side == "caller" else extension,
        "callee": extension if external_side == "caller" else external,
        "direction": "OUTBOUND",  # leg-relative and misleading by design — must be ignored
        "startTime": "2026-07-13T21:52:39.106Z",
        "duration": 10736 if answered else 0,
        "hangupCause": 16,
        "ownerPhoneNumber": "+16303602784",
        "accountKey": "6327799820468129299",
    }
    if answered:
        content["answerTime"] = "2026-07-13T21:52:43.000Z"
    return content


# --- phone + party extraction ----------------------------------------------------------


def test_normalize_phone():
    assert normalize_phone("6306999881") == "+16306999881"
    assert normalize_phone("16306999881") == "+16306999881"
    assert normalize_phone("+16306999881") == "+16306999881"
    assert normalize_phone("630-699-9881") == "+16306999881"
    assert normalize_phone("1000") is None  # extension
    assert normalize_phone("") is None
    assert normalize_phone(None) is None


def test_external_party_ignores_direction_and_position():
    for side in ("caller", "callee"):
        name, phone = external_party(_call_content("leg", answered=False, external_side=side))
        assert phone == "+16306999881"
        assert name == "WIRELESS CALLER"


# --- frame -> Stimulus mapping ---------------------------------------------------------


def test_missed_call_maps_on_absent_answer_time():
    stim = to_stimulus(_frame("call-history", "UchEvent", _call_content("leg-1", answered=False)))
    assert stim is not None
    assert stim.kind == "missed_call"
    assert stim.external_id == "call:leg-1"
    assert stim.payload["phone"] == "+16306999881"
    assert stim.payload["answered"] is False


def test_answered_call_maps_to_call_ended():
    stim = to_stimulus(_frame("call-history", "UchEvent", _call_content("leg-2", answered=True)))
    assert stim is not None
    assert stim.kind == "call_ended"
    assert stim.kind not in DISPATCH_KINDS


def test_internal_call_is_ignored():
    content = _call_content("leg-3", answered=True)
    content["caller"] = {"name": "Paige Green", "number": "1001"}
    content["callee"] = {"name": "Brennen Roberts", "number": "1000"}
    assert to_stimulus(_frame("call-history", "UchEvent", content)) is None


def test_inbound_sms_maps_and_outbound_is_ignored():
    content = {
        "ownerPhoneNumber": "+16303602784",
        "contactPhoneNumbers": ["+16306999881"],
        "authorPhoneNumber": "+16306999881",
        "id": "msg-1",
        "timestamp": "2026-07-13T21:43:09.486186Z",
        "direction": "IN",
        "body": "hello",
        "media": [],
    }
    stim = to_stimulus(_frame("messaging", "message", content))
    assert stim is not None
    assert stim.kind == "sms_received"
    assert stim.external_id == "sms:msg-1"
    assert stim.payload["phone"] == "+16306999881"
    assert stim.payload["body"] == "hello"

    assert to_stimulus(_frame("messaging", "message", {**content, "direction": "OUT"})) is None


def test_new_voicemail_maps_with_leg_linkage():
    content = {
        "voicemailId": "vm-abc",
        "voicemailboxId": "box-1",
        "extensionName": "Brennen Roberts",
        "extensionNumber": "1000",
        "calledNumber": "+16303602784",
        "callerName": "WIRELESS CALLER",
        "callerNumber": "6306999881",
        "legId": "leg-4",
        "durationMs": 10000,
        "timestamp": "2026-07-13T21:53:13Z",
        "status": "NEW",
    }
    stim = to_stimulus(_frame("VOICEMAIL", "NEW_VOICEMAIL", content))
    assert stim is not None
    assert stim.kind == "voicemail_received"
    assert stim.external_id == "vm:vm-abc"
    assert stim.payload["phone"] == "+16306999881"
    assert stim.payload["leg_id"] == "leg-4"


def test_housekeeping_and_state_frames_are_ignored():
    assert to_stimulus(_frame("notification-websocket", "WEBSOCKET_REFRESH_REQUIRED", {})) is None
    assert to_stimulus(_frame("call-events", "call-state", {"metadata": {}, "state": {}})) is None


# --- risk tiers ------------------------------------------------------------------------


def test_goto_tiers_are_deterministic():
    from nexus.connectors.ingress.rules import AUTONOMOUS, LOG_FLAG, SUPERVISED, classify

    assert classify("goto_connect", "missed_call") == LOG_FLAG
    assert classify("goto_connect", "sms_received") == SUPERVISED
    assert classify("goto_connect", "voicemail_received") == SUPERVISED
    assert classify("goto_connect", "call_ended") == AUTONOMOUS
    assert classify("goto_connect", "unknown") == SUPERVISED  # fail safe


# --- webhook module (registry entry / backstop) ------------------------------------------


def test_webhook_parse_falls_back_to_unknown():
    from nexus.connectors.goto_connect import webhook

    stim = webhook.parse({"data": {"source": "???", "type": "???"}}, {}, b"{}")
    assert stim.kind == "unknown"
    assert stim.source == "goto_connect"

    frame = _frame("call-history", "UchEvent", _call_content("leg-5", answered=False))
    stim = webhook.parse(frame, {}, b"{}")
    assert stim.kind == "missed_call"
    assert stim.raw == b"{}"


def test_webhook_refuses_without_secret():
    from nexus.config import settings
    from nexus.connectors.goto_connect import webhook

    assert webhook.secret(settings) in (None, "")  # unset => shared route 503s


# --- stream processor --------------------------------------------------------------------


def test_stream_processes_dispatches_and_dedups(dispatched):
    from nexus.connectors.goto_connect.stream import _process

    frame = _frame("call-history", "UchEvent", _call_content("leg-stream-1", answered=False))
    asyncio.run(_process(json.dumps(frame)))
    asyncio.run(_process(json.dumps(frame)))  # duplicate delivery
    assert len(dispatched) == 1
    stim, tier = dispatched[0]
    assert stim.kind == "missed_call"
    assert tier == "log_flag"


def test_stream_logs_but_never_dispatches_answered_calls(dispatched):
    from nexus.connectors.goto_connect.stream import _process

    frame = _frame("call-history", "UchEvent", _call_content("leg-stream-2", answered=True))
    asyncio.run(_process(json.dumps(frame)))
    assert dispatched == []


# --- gap-fill sync -----------------------------------------------------------------------


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.since_seen = None

    def recent_calls(self, since, until=None):
        self.since_seen = since
        return self.rows


def test_sync_emits_only_missed_calls_and_advances_mark(dispatched, vault):
    from nexus.connectors.goto_connect.stream import _load_state
    from nexus.connectors.goto_connect.sync import run_sync

    rows = [
        _call_content("leg-sync-1", answered=False),
        _call_content("leg-sync-2", answered=True),
    ]
    asyncio.run(run_sync(client=FakeClient(rows)))
    assert [s.kind for s, _ in dispatched] == ["missed_call"]
    assert dispatched[0][0].external_id == "call:leg-sync-1"

    state = _load_state()
    assert state.get("high_water") is not None
    assert datetime.fromisoformat(state["high_water"]).tzinfo is not None


def test_sync_dedups_against_stream(dispatched, vault):
    from nexus.connectors.goto_connect.stream import _process
    from nexus.connectors.goto_connect.sync import run_sync

    content = _call_content("leg-sync-3", answered=False)
    frame = _frame("call-history", "UchEvent", content)
    asyncio.run(_process(json.dumps(frame)))  # stream handled it first
    asyncio.run(run_sync(client=FakeClient([content])))
    assert len(dispatched) == 1  # the poll did not double-fire


def test_first_sync_window_is_bounded(dispatched, vault):
    from nexus.connectors.goto_connect.sync import run_sync

    client = FakeClient([])
    asyncio.run(run_sync(client=client))
    age = datetime.now(UTC) - client.since_seen
    assert age.total_seconds() <= 24 * 3600 + 60
