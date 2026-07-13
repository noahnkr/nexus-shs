"""Index-consistency tests: the write gate marks dirty, readers never see stale state.

The property under test (raised as a defect 2026-07-13): no matter which path performs a
write — connector sync, webhook log, workflow action, MCP tool — search reflects it on
the very next query, and INDEX.md regenerates at the next batch boundary. No caller may
need to remember to reindex.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def vault(tmp_path, monkeypatch):
    """Fresh temp vault; reset the search index singleton and both dirty flags."""
    from nexus.config import settings
    from nexus.vault import index, search

    monkeypatch.setattr(settings, "vault_path", tmp_path)
    monkeypatch.setattr(search, "_index", None)
    monkeypatch.setattr(search, "_dirty", False)
    monkeypatch.setattr(index, "_dirty", False)
    for fam in ("reference", "entity", "events", "tasks"):
        (tmp_path / fam).mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_search_sees_gate_writes_without_explicit_reindex():
    from nexus.vault.queries import search_logs
    from nexus.vault.search import get_index
    from nexus.writes import append_log

    get_index()  # warm the singleton so laziness (not first-build) is what's under test
    append_log("unicorn sighting on the north lawn")
    hits = search_logs("unicorn sighting")
    assert hits, "a gate write must be visible to the very next search query"


def test_entity_write_marks_search_dirty():
    from nexus.vault import search
    from nexus.vault.search import get_index
    from nexus.writes import update_entity

    get_index()
    assert search._dirty is False
    update_entity("Alma Petersen", "prospect", {"status": "inquiry"})
    assert search._dirty is True, "the write gate must dirty the search corpus"
    get_index()
    assert search._dirty is False, "get_index must settle the dirty flag"


def test_index_md_regenerates_only_when_dirty(vault):
    from nexus.vault import index
    from nexus.writes import update_entity

    index.regenerate_if_dirty()  # settle whatever fixture setup did
    assert index.regenerate_if_dirty() == [], "clean vault must be a no-op"

    update_entity("Gene Ryan", "prospect", {"status": "inquiry"})
    written = index.regenerate_if_dirty()
    assert written, "a gate write must dirty INDEX.md regeneration"
    entity_index = vault / "entity" / "INDEX.md"
    assert entity_index.is_file()
    assert "Gene Ryan" in entity_index.read_text(encoding="utf-8")
    assert index.regenerate_if_dirty() == [], "boundary must settle the flag"


def test_stream_log_only_event_settles_index_md(vault):
    """The goto stream's answered-call path (log, no dispatch) must settle INDEX.md."""
    import asyncio
    import json

    from nexus.connectors.goto_connect.stream import _process

    frame = {
        "event": "Notification",
        "data": {
            "source": "call-history",
            "type": "UchEvent",
            "content": {
                "legId": "leg-idx-1",
                "caller": {"name": "WIRELESS CALLER", "number": "6306999881"},
                "callee": {"name": "Brennen Roberts", "number": "1000"},
                "answerTime": "2026-07-13T21:52:43.000Z",
                "startTime": "2026-07-13T21:52:39.106Z",
                "duration": 10736,
                "ownerPhoneNumber": "+16303602784",
            },
        },
    }
    asyncio.run(_process(json.dumps(frame)))
    events_index = vault / "events" / "INDEX.md"
    assert events_index.is_file(), "log-only stream events must settle INDEX.md"
