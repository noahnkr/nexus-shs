"""Acceptance tests mirroring the spec's §10 exit criteria.

These assert STRUCTURE, the implemented primitives, and end-to-end behavior of the
Knowledge and Ingress layers (no API key required). The agent loop test runs only when
ANTHROPIC_API_KEY is set, since it makes a real Messages-API call.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime

import pytest


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


# --- §10.1 host: imports clean, app builds, /health ---------------------------------


def test_app_imports_and_health_route_present():
    from nexus.app import app

    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/health" in paths
    assert "/webhooks/{source}" in paths


def test_mcp_exact_path_never_redirects():
    """`POST /mcp` (no trailing slash — how MCP clients are configured) must be served
    directly, NOT 307-redirected: behind Railway's edge the redirect round trip breaks
    MCP clients. The path lives inside the FastMCP app (http_app(path="/mcp")) mounted
    at "/", so there is no mount boundary to redirect across."""
    from starlette.testclient import TestClient

    from nexus.app import app
    from nexus.config import settings

    with TestClient(app) as client:  # context manager runs the MCP session lifespan
        resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "initialize",
                "id": 1,
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
            headers={
                "accept": "application/json, text/event-stream",
                "authorization": f"Bearer {settings.mcp_token}",
            },
            follow_redirects=False,
        )
    assert resp.status_code == 200, f"/mcp -> {resp.status_code}"
    # buffered JSON — never a redirect, never chunked SSE
    assert resp.headers.get("content-type", "").startswith("application/json")


def test_mcp_allows_public_host_header(monkeypatch):
    """fastmcp >= 3.4.3 rejects unknown Host headers with 421 Misdirected Request (DNS
    rebinding protection). The public domain from PUBLIC_URL must be allowlisted or every
    request through Railway's proxy dies at the guard. PUBLIC_URL is a bare domain on
    Railway (no scheme) — that form must parse too."""
    from starlette.testclient import TestClient

    from nexus import app as app_mod
    from nexus.config import settings

    domain = "myapp-production.up.railway.app"
    monkeypatch.setattr(settings, "public_url", domain)
    assert app_mod._public_host(domain) == domain

    client = TestClient(app_mod.build_app())
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
        headers={"accept": "application/json, text/event-stream", "host": domain},
        follow_redirects=False,
    )
    assert resp.status_code != 421, f"Host {domain!r} rejected by DNS-rebinding guard"


# --- §3.2 schema: discriminated union + extra="forbid" -------------------------------


def test_schema_validates_and_forbids_extras():
    from pydantic import TypeAdapter, ValidationError

    from nexus.vault.schema import AnyNote

    adapter = TypeAdapter(AnyNote)
    note = adapter.validate_python(
        {"title": "Pricing", "family": "reference", "created": date.today().isoformat(),
         "updated": date.today().isoformat()}
    )
    assert note.family == "reference"

    with pytest.raises(ValidationError):  # extra="forbid" guards LLM typos (§3.2)
        adapter.validate_python(
            {"title": "X", "family": "reference", "created": date.today().isoformat(),
             "updated": date.today().isoformat(), "nonexistent_field": "oops"}
        )


def test_json_schema_generates_for_every_family():
    from nexus.vault.schema import Family, json_schema_for

    for fam in Family:
        assert json_schema_for(fam)["type"] == "object"


# --- §5.1 / §5.4 ingress envelope + deterministic classification --------------------


def test_stimulus_envelope():
    from nexus.connectors.ingress.envelope import Stimulus

    s = Stimulus(source="example", kind="new_record", received_at=datetime.now(UTC))
    assert s.source == "example" and s.payload == {}


def test_classify_known_and_unknown_failsafe():
    from nexus.connectors.ingress.rules import AUTONOMOUS, SUPERVISED, classify

    assert classify("cron", "daily-digest") == AUTONOMOUS
    assert classify("totally", "unknown") == SUPERVISED  # fail safe (§5.4)


# --- §5.3 constant-time HMAC + replay window + idempotency ---------------------------


def test_hmac_verify_roundtrip():
    import hashlib
    import hmac

    from nexus.connectors.ingress.security import verify_hmac_sha256

    secret, body = "s3cr3t", b'{"id":1}'
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_hmac_sha256(secret, body, sig)
    assert verify_hmac_sha256(secret, body, f"sha256={sig}")
    assert not verify_hmac_sha256(secret, body, "deadbeef")


def test_replay_window_and_idempotency():
    from nexus.connectors.ingress.security import SeenCache, within_window

    assert within_window(1000.0, 300, now=1100.0)
    assert not within_window(1000.0, 300, now=2000.0)

    cache = SeenCache(ttl_seconds=10)
    assert cache.seen("example:1") is False
    assert cache.seen("example:1") is True


# --- §4.1 sample connector parse() --------------------------------------------------


def test_example_connector_parse_maps_kind():
    from nexus.connectors.example import webhook

    s = webhook.parse({"type": "record.created", "id": 7}, {}, b"{}")
    assert s.source == "example" and s.kind == "new_record" and s.external_id == "7"


# --- §3.5 / §6.3 structural trust boundary ------------------------------------------


def test_loop_toolset_has_no_external_send_tool():
    from nexus.agents.toolset import all_tools

    names = set(all_tools())
    assert "create_task" in names
    assert not any("send" in n for n in names)  # boundary is the ABSENCE of send


def test_mcp_registers_vault_tools_only():
    from nexus.tools import build_mcp

    names = sorted(t.name for t in asyncio.run(build_mcp().list_tools()))
    assert {"search_reference", "get_entity", "create_task", "append_log"} <= set(names)
    # the knowledge-base curation surface (MCP-only for ingest/publish)
    assert {"list_reference", "ingest_file", "ingest_batch", "set_note_status"} <= set(names)
    assert not any("send" in n for n in names)


def test_kb_curation_tools_are_mcp_only():
    """Ingest + publish are the OWNER's tools; the ambient loop must not self-publish."""
    from nexus.agents.toolset import all_tools

    names = set(all_tools())
    assert "list_reference" in names  # the read IS shared with the loop
    assert not {"ingest_file", "ingest_batch", "set_note_status"} & names


# --- §3.4 RRF fusion + hybrid (BM25-only) search ------------------------------------


def test_rrf_merge_ranks_by_position():
    from nexus.vault.search import rrf_merge

    fused = rrf_merge(["a", "b", "c"], ["b", "a", "d"])
    assert {doc for doc, _ in fused[:2]} == {"a", "b"}


def test_hybrid_search_bm25_and_family_scope():
    import nexus.writes as w
    from nexus.vault.schema import Family
    from nexus.vault.search import get_index

    w.update_entity("Acme Corp", "prospect", {"summary": "key enterprise healthcare account"})
    w.update_entity("Beta LLC", "prospect", {"summary": "small retail customer"})
    w.append_log("renewed the Acme enterprise contract")

    idx = get_index()
    top = idx.query("enterprise healthcare")
    assert top and top[0].title == "Acme Corp"
    # family scoping is a metadata filter, not a separate corpus
    assert all(h.family == "event" for h in idx.query("contract", family=Family.event))


# --- §3.5 write surface + §3.1 append-only events -----------------------------------


def test_writes_and_event_log():
    import nexus.writes as w
    from nexus.vault import io

    p = w.append_log("first")
    w.append_log("second")
    note, _ = io.read_note(p)
    assert len(note.entries) == 2  # append-only: both on today's note

    ep = w.update_entity("Acme Corp", "prospect", {"summary": "key account", "status": "published"})
    assert io.read_note(ep)[0].summary == "key account"

    tp = w.create_task("call back", channel="phone", recipient="555", body="ring them")
    assert io.read_note(tp)[0].status == "open"


# --- §10.2 reads: get_entity, list_entities (NO embedder), list_open_tasks ----------


def test_queries_and_metadata_filter_skips_embedder(monkeypatch):
    import nexus.writes as w
    from nexus.vault import embeddings, queries

    # If list_entities ever calls the embedder, this blows up — proving it's metadata-only.
    def _boom(*_a, **_k):
        raise AssertionError("embedder called")

    monkeypatch.setattr(embeddings, "embed", _boom)

    w.update_entity("Acme Corp", "prospect", {"summary": "key account", "status": "published"})
    w.update_entity("Beta LLC", "prospect", {"summary": "retail"})
    w.create_task("approve refund")

    assert queries.get_entity("acme corp")["summary"] == "key account"
    assert queries.get_entity("nobody") is None
    assert [e["title"] for e in queries.list_entities(status="published")] == ["Acme Corp"]
    assert len(queries.list_entities(kind="prospect")) == 2
    assert [t["action"] for t in queries.list_open_tasks()] == ["approve refund"]


# --- §3.3 generated indexes ---------------------------------------------------------


def test_index_empty_queue_signal_and_leaf_table():
    import nexus.writes as w
    from nexus.vault import index, io

    # Empty task queue is a SIGNAL, not a blank table.
    tasks_idx = index.regenerate(io.vault_root() / "tasks")
    assert index.NO_OPEN_TASKS in tasks_idx.read_text(encoding="utf-8")

    w.update_entity("Acme Corp", "prospect", {"summary": "key account"})
    ent_idx = index.regenerate(io.vault_root() / "entity")
    body = ent_idx.read_text(encoding="utf-8")
    assert "| Title |" in body and "Acme Corp" in body


# --- §3.7 ingest: text extraction + draft assembly (no LLM) -------------------------


def test_ingest_extract_and_assemble(tmp_path):
    from nexus.ingest.extract import extract_text
    from nexus.ingest.pipeline import assemble
    from nexus.vault.schema import Family, Status

    src = tmp_path / "policy.txt"
    src.write_text("Refunds are issued within 30 days.", encoding="utf-8")
    assert "Refunds" in extract_text(src)

    note = assemble({"title": "Refund Policy", "summary": "30-day refunds"},
                    family=Family.reference, source_ref="file:reference:policy.txt")
    assert note.status == Status.draft and note.source_ref == "file:reference:policy.txt"


def test_ingest_preserves_extracted_body_for_binary_formats(tmp_path, monkeypatch):
    """Binary/HTML sources must land with the EXTRACTED TEXT as the note body — a
    bodiless note is invisible to search and can never be quoted in an answer."""
    from nexus.ingest import pipeline
    from nexus.vault import io
    from nexus.vault.search import get_index

    src = tmp_path / "rates.html"
    src.write_text("<html><body>Companion Care is $32 per hour.</body></html>", encoding="utf-8")
    monkeypatch.setattr(
        pipeline, "classify",
        lambda text, hint_family: {"title": "Rate Sheet", "summary": "hourly rates"},
    )
    path = pipeline.ingest_file(src, overrides={"category": "pricing"})

    note, body = io.read_note(path)
    assert "$32 per hour" in body  # extracted text survives as the searchable body
    assert note.category == "pricing"  # curator override beat the classifier
    assert (io.vault_root() / "system" / "attachments" / "rates.html").exists()
    assert any(h.title == "Rate Sheet" for h in get_index().query("companion care hourly rate"))


def test_kb_lifecycle_ingest_list_publish(tmp_path, monkeypatch):
    """The full curation loop: ingest a draft -> list it -> publish it -> verify state."""
    import pytest as _pytest

    from nexus import writes
    from nexus.ingest import pipeline
    from nexus.vault import io, queries

    src = tmp_path / "onboarding.txt"
    src.write_text("New caregivers shadow a senior caregiver for two shifts.", encoding="utf-8")
    monkeypatch.setattr(
        pipeline, "classify",
        lambda text, hint_family: {"title": "Caregiver Onboarding", "summary": "shadowing"},
    )
    path = pipeline.ingest_file(src, overrides={"category": "service_sop"})

    drafts = queries.list_reference(status="draft")
    assert any(r["title"] == "Caregiver Onboarding" for r in drafts)
    assert queries.list_reference(status="draft", category="pricing") == []  # filters compose

    writes.set_note_status(str(path), "published")
    note, _ = io.read_note(path)
    assert note.status == "published"
    assert queries.list_reference(status="draft") == []

    with _pytest.raises(ValueError):
        writes.set_note_status(str(path), "open")  # task statuses rejected for reference
    with _pytest.raises(FileNotFoundError):
        writes.set_note_status("../outside.md", "published")  # can't escape the vault


def test_get_note_drills_into_a_search_hit():
    from nexus.vault import queries
    from nexus.vault.io import family_dir, write_note
    from nexus.vault.schema import Family, ReferenceNote, Status

    note = ReferenceNote(
        title="FAQ", status=Status.published, summary="common questions",
        created=date.today(), updated=date.today(), source_ref="file:reference:faq.pdf",
    )
    path = write_note(note, family_dir(Family.reference) / "faq.md", "We serve Naperville.")

    rec = queries.get_note(str(path))
    assert rec and "We serve Naperville." in rec["_body"]
    assert rec["source_ref"] == "file:reference:faq.pdf"  # the citable backlink
    assert queries.get_note("../outside.md") is None  # can't escape the vault
    assert queries.get_note("system/attachments/x.pdf") is None  # non-note dirs blocked


def test_get_entity_resolves_phone_and_email():
    import nexus.writes as w
    from nexus.vault import queries

    w.update_entity("Mary Smith", "prospect", {
        "phone": "(630) 555-0142", "email": "mary@example.com",
        "family_contacts": [{"name": "Tom Smith", "phone": "630-555-0199"}],
    })
    assert queries.get_entity("+16305550142")["title"] == "Mary Smith"  # caller-ID lookup
    assert queries.get_entity("MARY@example.com")["title"] == "Mary Smith"
    assert queries.get_entity("6305550199")["title"] == "Mary Smith"  # family contact
    assert queries.get_entity("6305550000") is None


def test_append_memory_is_schema_valid_and_retrievable():
    import nexus.writes as w
    from nexus.vault import queries

    w.append_memory("Aggregator leads convert best when called within the hour.")
    w.append_memory("Tom prefers SMS over email.")  # second append must also validate
    hits = queries.search_reference("aggregator leads convert")
    assert any(h.title == "Memory" for h in hits)


# --- vault/context: always-on context injection + non-note exclusion ---------------


def test_context_loads_and_is_excluded_from_corpus():
    import nexus.writes as w
    from nexus.agents.context import context_dir, load_context
    from nexus.vault import io
    from nexus.vault.search import get_index

    context_dir().mkdir(parents=True, exist_ok=True)
    (context_dir() / "SOUL.md").write_text("You are terse and precise.", encoding="utf-8")

    # Always-on context is readable for prompt injection...
    assert "terse and precise" in load_context()

    # ...but context/ is NOT part of the searchable/indexable corpus.
    w.update_entity("Acme Corp", "prospect", {"summary": "terse account"})
    paths = {p for p, _n, _b in io.iter_notes()}
    assert not any("context" in p.parts for p in paths)
    assert all(h.family != "context" for h in get_index().query("terse"))


def test_reference_subfolder_is_walked_and_searchable():
    from datetime import date

    from nexus.vault import io
    from nexus.vault.schema import Family, ReferenceCategory, ReferenceNote, Status
    from nexus.vault.search import get_index

    note = ReferenceNote(
        title="PTO Policy", status=Status.published, summary="paid time off rules",
        category=ReferenceCategory.policy_voice, created=date.today(), updated=date.today(),
    )
    path = io.family_dir(Family.reference) / "policy" / "pto-policy.md"
    io.write_note(note, path, "Employees accrue PTO monthly.")

    titles = [n.title for _p, n, _b in io.iter_notes()]
    assert "PTO Policy" in titles  # subfolders are walked (rglob)
    assert any(h.title == "PTO Policy" for h in get_index().query("paid time off"))


# --- §10.4 agent loop (full Messages-API round-trip; needs a key) -------------------


@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="requires ANTHROPIC_API_KEY")
def test_agent_loop_read_only_writes_nothing():
    from nexus.agents.loop import run_loop
    from nexus.connectors.ingress.envelope import Stimulus

    stim = Stimulus(source="chat", kind="chat", received_at=datetime.now(UTC),
                    payload={"text": "what reference do we have about pricing?"})
    result = asyncio.run(run_loop(stim, system_prompt="You are a test agent.",
                                  tier="autonomous", model="claude-haiku-4-5-20251001"))
    assert result["writes"] == []  # a read-only query records nothing (§6.1 change-test)
