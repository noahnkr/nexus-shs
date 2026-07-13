"""WelcomeHome connector tests (docs/connectors/welcomehome.md step 8).

The client is exercised against an httpx.MockTransport serving captured-shape CSV export
pages (Link-header cursor pagination). The sync is exercised with a fake client — no
network, no LLM: dispatch is intercepted to assert the manufactured deltas.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
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


@pytest.fixture
def dispatched(monkeypatch):
    """Capture (stimulus, tier) pairs instead of waking real agents."""
    calls: list[tuple] = []

    async def fake_dispatch(stimulus, tier):
        calls.append((stimulus, tier))

    from nexus.connectors.ingress import router

    monkeypatch.setattr(router, "dispatch", fake_dispatch)
    return calls


CSV_HEADER = (
    "id,name,stage_id,status,lead_source_id,cell_phone,email,"
    "created_at,last_contact_at,updated_at"
)
ROW_ALMA = (
    "101,Alma Petersen,1,open,7,630-555-0101,alma@example.com,"
    "2026-07-12T14:00:00Z,2026-07-12T15:00:00Z,2026-07-12T15:00:00Z"
)
ROW_GENE = (
    "102,Gene Ryan,3,open,8,,gene@example.com,"
    "2026-07-11T09:00:00Z,2026-07-13T10:00:00Z,2026-07-13T10:00:00Z"
)

STAGES = [
    {"id": 1, "name": "Inquiry", "position": 0, "system_type": "new_lead"},
    {"id": 2, "name": "Attempted", "position": 1, "system_type": ""},
    {"id": 3, "name": "Ct Made", "position": 2, "system_type": ""},
    {"id": 4, "name": "Visit Schld", "position": 3, "system_type": ""},
]
LEAD_SOURCES = [
    {"id": 7, "name": "A Place for Mom", "category": "aggregator"},
    {"id": 8, "name": "Care.com", "category": "aggregator"},
]


class FakeClient:
    """Duck-typed WelcomeHomeClient for sync tests."""

    def __init__(self, rows, residents=None, activities=None):
        self.rows = rows
        self.residents = residents or []
        self.activities = activities or []
        self.export_calls: list = []

    def export_prospects(self, since=None):
        self.export_calls.append(since)
        return self.rows

    def export_residents(self):
        return self.residents

    def export_activities(self, since=None):
        return self.activities

    def list_stages(self):
        return STAGES

    def list_lead_sources(self):
        return LEAD_SOURCES


def _csv_rows(*lines: str) -> list[dict[str, str]]:
    import csv
    from io import StringIO

    return list(csv.DictReader(StringIO("\n".join([CSV_HEADER, *lines]))))


# --- client: auth header, pagination, CSV parsing ----------------------------------------


def test_client_paginates_via_link_header_and_parses_csv():
    from nexus.connectors.welcomehome.client import WelcomeHomeClient

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        # The cursor URL has the SAME path — branch on the cursor param, or the mock
        # would serve page 1 (with its Link header) forever.
        if "cursor" in request.url.params:
            return httpx.Response(200, text="\n".join([CSV_HEADER, ROW_GENE]))
        return httpx.Response(
            200,
            text="\n".join([CSV_HEADER, ROW_ALMA]),
            headers={
                "Link": '<https://crm.welcomehomesoftware.com/api/exports/community/all/'
                'table/Prospects?cursor=abc>; rel="next"',
                "content-type": "text/csv",
            },
        )

    client = WelcomeHomeClient("sekret", transport=httpx.MockTransport(handler))
    rows = client.export_prospects(since=datetime(2026, 7, 1, tzinfo=UTC))

    assert [r["id"] for r in rows] == ["101", "102"]
    assert seen[0].headers["authorization"] == "Token token=sekret"
    assert seen[0].url.params["filters[updated_at_after]"] == "2026-07-01T00:00:00+00:00"
    assert seen[1].url.params["cursor"] == "abc"  # second page came from the Link header


# --- sync: upsert, deltas, high-water mark ------------------------------------------------


def test_sync_creates_prospects_and_dispatches_new_prospect(vault, dispatched):
    from nexus.connectors.welcomehome.sync import _state_path, run_sync
    from nexus.vault.queries import get_entity

    client = FakeClient(_csv_rows(ROW_ALMA, ROW_GENE))
    asyncio.run(run_sync(client=client))

    alma = get_entity("welcomehome:prospect:101")
    assert alma is not None
    assert alma["title"] == "Alma Petersen"
    assert alma["status"] == "inquiry"  # stage_id 1 -> "Inquiry" via /stages map
    assert alma["referral_source"] == "A Place for Mom"
    assert alma["phone"] == "630-555-0101"
    assert alma["inquiry_date"] == "2026-07-12"

    gene = get_entity("welcomehome:prospect:102")
    assert gene is not None
    assert gene["status"] == "contact_made"  # "Ct Made" translation

    kinds = [s.kind for s, _ in dispatched]
    assert kinds == ["new_prospect", "new_prospect"]
    assert all(tier == "log_flag" for _, tier in dispatched)

    state = json.loads(_state_path().read_text(encoding="utf-8"))
    # Mark is max(row updated_at, poll start - overlap) — never behind the newest row.
    assert datetime.fromisoformat(state["high_water"]) >= datetime(
        2026, 7, 13, 10, 0, tzinfo=UTC
    )


def test_sync_is_idempotent_and_detects_stage_change(vault, dispatched):
    from nexus.connectors.welcomehome.sync import run_sync
    from nexus.vault.queries import get_entity

    asyncio.run(run_sync(client=FakeClient(_csv_rows(ROW_ALMA))))
    dispatched.clear()

    # Same row again: upsert only, no manufactured delta.
    asyncio.run(run_sync(client=FakeClient(_csv_rows(ROW_ALMA))))
    assert dispatched == []

    # Stage advanced (1 -> 4): entity moves and exactly one stage_changed fires.
    advanced = ROW_ALMA.replace(",1,open,", ",4,open,")
    asyncio.run(run_sync(client=FakeClient(_csv_rows(advanced))))
    assert [s.kind for s, _ in dispatched] == ["stage_changed"]
    alma = get_entity("welcomehome:prospect:101")
    assert alma is not None and alma["status"] == "visit_scheduled"


def test_sync_passes_high_water_to_next_poll(vault, dispatched):
    from nexus.connectors.welcomehome import sync
    from nexus.connectors.welcomehome.sync import run_sync

    before = datetime.now(UTC)
    asyncio.run(run_sync(client=FakeClient(_csv_rows(ROW_ALMA))))
    second = FakeClient([])
    asyncio.run(run_sync(client=second))
    (since,) = second.export_calls
    # Wall-clock fallback mark (rows carry no updated_at in the real export).
    assert since is not None and since >= before - sync._REPOLL_OVERLAP


def test_sync_skips_draft_and_marketing_qualified(vault, dispatched):
    from nexus.connectors.welcomehome.sync import run_sync
    from nexus.vault.queries import list_entities

    draft = ROW_ALMA.replace(",1,open,", ",1,draft,")
    mql = ROW_GENE.replace(",3,open,", ",3,marketing_qualified,")
    asyncio.run(run_sync(client=FakeClient(_csv_rows(draft, mql))))
    assert list_entities(kind="prospect") == []
    assert dispatched == []


def test_real_export_shape_dotted_headers_and_residents_join(vault, dispatched):
    """Mirror the live export: table-prefixed columns, no name/contact/updated_at on the
    Prospect row — name/phone/email joined from Residents, last contact from Activities."""
    from nexus.connectors.welcomehome.sync import run_sync
    from nexus.vault.queries import get_entity

    rows = [
        {
            "prospects.id": "47421125",
            "prospects.created_at": "2026-05-26 00:00:00",
            "prospects.inquiry_date": "2026-05-26",
            "prospects.discarded_at": "",
            "prospects.status": "open",
            "stages.id": "25090",
            "stages.name": "Contact Attempted",
            "lead_sources.id": "146799",
            "lead_sources.name": "A Place for Mom",
            "prospects.merged_into_prospect_id": "",
        }
    ]
    residents = [
        {
            "residents.prospect_id": "47421125",
            "residents.first_resident": "true",
            "people.first_name": "Alma",
            "people.last_name": "Petersen",
            "people.cell_phone": "630-555-0101",
            "people.email": "alma@example.com",
            "people.discarded_at": "",
        }
    ]
    activities = [
        {
            "activities.record_type": "Prospect",
            "activities.record_id": "47421125",
            "activities.completed_at": "2026-06-02 15:30:00",
        },
        {  # Referrer activity must be ignored
            "activities.record_type": "Referrer",
            "activities.record_id": "47421125",
            "activities.completed_at": "2026-06-09 15:30:00",
        },
    ]
    client = FakeClient(rows, residents=residents, activities=activities)
    asyncio.run(run_sync(client=client))

    alma = get_entity("welcomehome:prospect:47421125")
    assert alma is not None
    assert alma["title"] == "Alma Petersen"
    assert alma["status"] == "attempted"  # "Contact Attempted" label
    assert alma["referral_source"] == "A Place for Mom"
    assert alma["phone"] == "630-555-0101"
    assert alma["email"] == "alma@example.com"
    assert alma["inquiry_date"] == "2026-05-26"
    assert alma["last_contact_date"] == "2026-06-02"  # from Activities, not the row
    assert [s.kind for s, _ in dispatched][0] == "new_prospect"


def test_discarded_or_merged_rows_archive_but_never_create(vault, dispatched):
    from nexus.connectors.welcomehome.sync import run_sync
    from nexus.vault.queries import get_entity, list_entities

    asyncio.run(run_sync(client=FakeClient(_csv_rows(ROW_ALMA))))
    dispatched.clear()

    header = CSV_HEADER + ",discarded_at,merged_into_prospect_id"
    discarded_alma = ROW_ALMA + ",2026-07-13T11:00:00Z,"
    merged_new = ROW_GENE + ",,101"
    import csv
    from io import StringIO

    rows = list(csv.DictReader(StringIO("\n".join([header, discarded_alma, merged_new]))))
    asyncio.run(run_sync(client=FakeClient(rows)))

    alma = get_entity("welcomehome:prospect:101")
    assert alma is not None and alma["status"] == "archived"
    assert get_entity("welcomehome:prospect:102") is None  # merged row never created
    assert len(list_entities(kind="prospect")) == 1
    assert dispatched == []  # hygiene, not deltas


def test_same_name_distinct_prospects_get_distinct_entities(vault, dispatched):
    from nexus.connectors.welcomehome.sync import run_sync
    from nexus.vault.queries import get_entity, list_entities

    twin = ROW_ALMA.replace("101,Alma Petersen", "109,Alma Petersen")
    asyncio.run(run_sync(client=FakeClient(_csv_rows(ROW_ALMA, twin))))

    assert len(list_entities(kind="prospect")) == 2
    a, b = get_entity("welcomehome:prospect:101"), get_entity("welcomehome:prospect:109")
    assert a is not None and b is not None and a["title"] != b["title"]


def test_stale_prospect_flagged_once_per_episode(vault, dispatched):
    from nexus.connectors.welcomehome import sync
    from nexus.connectors.welcomehome.sync import run_sync

    old = (datetime.now(UTC) - sync.STALE_AFTER - timedelta(days=1)).strftime(
        "%Y-%m-%dT09:00:00Z"
    )
    stale_row = (
        f"103,Ida Moss,2,open,7,630-555-0103,ida@example.com,{old},{old},{old}"
    )
    asyncio.run(run_sync(client=FakeClient(_csv_rows(stale_row))))
    kinds = [s.kind for s, _ in dispatched]
    assert kinds == ["new_prospect", "prospect_stale"]

    # Re-poll with no changes: the same episode is not re-flagged.
    dispatched.clear()
    asyncio.run(run_sync(client=FakeClient([])))
    assert dispatched == []


def test_sync_without_api_key_is_a_safe_no_op(vault, dispatched, monkeypatch):
    from nexus.config import settings
    from nexus.connectors.welcomehome.sync import run_sync

    monkeypatch.setattr(settings, "welcomehome_api_key", None)
    asyncio.run(run_sync())  # must not raise
    assert dispatched == []


def test_registered_as_deterministic_job():
    from nexus.connectors.ingress.jobs import DETERMINISTIC_JOBS

    assert "welcomehome-sync" in DETERMINISTIC_JOBS
