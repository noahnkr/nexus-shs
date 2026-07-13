"""Deterministic poll-sync for WelcomeHome (spec §4.3). NO LLM in this path.

Every run: read the high-water mark -> pull changed Prospect rows via the export API ->
upsert `prospect` entities keyed by `source_ref="welcomehome:prospect:<id>"` -> advance
the mark. Rows that represent a genuinely new Prospect or a stage change MANUFACTURE a
`Stimulus` back through the same classify -> log-always -> dispatch path a webhook would
have used, so the reactive agent wakes only for deltas, never per reconciled row.

A final sweep flags Prospects that have gone stale (no contact/movement within
STALE_AFTER) as `prospect_stale` — once per staleness episode, tracked in the state file.

Sync state lives at `vault/system/welcomehome/state.json` — inside the one volume but
under `system/` so it stays out of the note corpus (io.NON_NOTE_DIRS).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from nexus.config import settings
from nexus.connectors.welcomehome import NAME as SOURCE
from nexus.connectors.welcomehome.client import WelcomeHomeClient
from nexus.vault.schema import Status


class ProspectSource(Protocol):
    """What run_sync needs from a client — WelcomeHomeClient satisfies this."""

    def export_prospects(self, since: datetime | None = None) -> list[dict[str, str]]: ...
    def export_residents(self) -> list[dict[str, str]]: ...
    def export_activities(self, since: datetime | None = None) -> list[dict[str, str]]: ...
    def list_stages(self) -> list[dict[str, Any]]: ...
    def list_lead_sources(self) -> list[dict[str, Any]]: ...

log = logging.getLogger(__name__)

# How long a pipeline-active Prospect may sit without contact/movement before the sync
# manufactures a `prospect_stale` flag. Provisional — confirm the window with Brennen
# (docs/connectors/welcomehome.md, open questions). Vault dates are day-granular, so this
# is expressed in whole days.
STALE_AFTER = timedelta(days=2)

# Safety overlap when advancing the high-water mark from wall clock (the Prospects export
# rows carry no updated_at column): absorbs local/server clock skew and in-flight edits.
_REPOLL_OVERLAP = timedelta(minutes=5)

# Stages where a Prospect is actively being worked and staleness matters. Terminal states
# (soc, archived) never go stale.
_STALE_ELIGIBLE = {
    Status.inquiry.value,
    Status.attempted.value,
    Status.contact_made.value,
    Status.visit_scheduled.value,
    Status.visit_completed.value,
}

# WelcomeHome stage NAME -> vault Status. Stages are account-configurable in WelcomeHome,
# so this maps the labels this account uses (plus spelled-out variants); keys are
# normalized (lowercase, single spaces). Unknown labels leave the entity's status
# untouched rather than guessing.
_STAGE_TO_STATUS: dict[str, Status] = {
    "inquiry": Status.inquiry,
    "new lead": Status.inquiry,
    "attempted": Status.attempted,
    "attempted contact": Status.attempted,
    "contact attempted": Status.attempted,  # this account's label (confirmed via /stages)
    "ct made": Status.contact_made,
    "contact made": Status.contact_made,
    "visit schld": Status.visit_scheduled,
    "visit scheduled": Status.visit_scheduled,
    "home visit scheduled": Status.visit_scheduled,  # this account's label
    "visit cmplt": Status.visit_completed,
    "visit completed": Status.visit_completed,
    "home visit completed": Status.visit_completed,  # this account's label
    "soc": Status.soc,
    "start of care": Status.soc,
}


async def run_sync(client: ProspectSource | None = None) -> None:
    """Reconcile WelcomeHome Prospects into the vault. Registered in DETERMINISTIC_JOBS."""
    if client is None:
        if not settings.welcomehome_api_key:
            log.warning("welcomehome sync skipped: WELCOMEHOME_API_KEY not set")
            return
        client = WelcomeHomeClient(settings.welcomehome_api_key)

    state = _load_state()
    since = _parse_dt(state.get("high_water"))
    run_started = datetime.now(UTC)
    rows = client.export_prospects(since=since)

    stages: dict[str, str] = {}
    lead_sources: dict[str, str] = {}
    residents: dict[str, dict[str, str | None]] = {}
    if rows:  # translation/enrichment maps, only fetched when there is something to map
        stages = _id_name_map(client.list_stages)
        lead_sources = _id_name_map(client.list_lead_sources)
        residents = _residents_map(client)

    high_water = since
    for row in rows:
        prospect = _map_row(row, stages, lead_sources, residents)
        if prospect is None:
            continue
        delta = _upsert(prospect)
        if delta is not None:
            await _emit(delta, prospect)
        updated = prospect.get("updated_at")
        if updated is not None and (high_water is None or updated > high_water):
            high_water = updated

    # Completed activities advance last_contact_date even when the Prospect row itself
    # didn't change (e.g. a logged call doesn't touch the exported columns).
    _apply_last_contacts(client, since)

    # The Prospects export has no updated_at column, so rows rarely advance the mark.
    # Fall back to the poll start time minus an overlap window; re-pulled rows are quiet
    # (upsert without change manufactures no delta), so the overlap is harmless.
    new_mark = run_started - _REPOLL_OVERLAP
    if high_water is None or new_mark > high_water:
        high_water = new_mark
    state["high_water"] = high_water.isoformat()

    await _flag_stale(state)
    _save_state(state)


# --- row mapping ---------------------------------------------------------------------------


def _map_row(
    row: dict[str, str],
    stages: dict[str, str],
    lead_sources: dict[str, str],
    residents: dict[str, dict[str, str | None]],
) -> dict[str, Any] | None:
    """Normalize one export CSV row. Returns None for rows Nexus should ignore.

    Real headers (verified against a live export) are table-prefixed: `prospects.id`,
    `prospects.status`, `stages.name`, `lead_sources.name`, ... The Prospect row carries
    NO name/contact columns — those come from the Residents export (`residents` map,
    keyed by prospect id). Bare candidates are kept for tolerance to header changes.
    """
    prospect_id = _col(row, "prospects.id", "id", "prospect_id")
    if prospect_id is None:
        return None

    # WelcomeHome record status: `draft` and `marketing_qualified` are explicitly
    # documented as not-real-prospects-yet; skip them entirely.
    wh_status = (_col(row, "prospects.status", "status") or "open").strip().lower()
    if wh_status in {"draft", "marketing_qualified"}:
        return None

    person = residents.get(prospect_id, {})
    name = person.get("name") or _col(row, "name", "prospect_name", "resident_name")
    if name is None:
        name = f"WelcomeHome Prospect {prospect_id}"

    stage_name = _col(row, "stages.name", "stage", "stage_name")
    if stage_name is None:
        stage_id = _col(row, "stages.id", "stage_id")
        stage_name = stages.get(stage_id) if stage_id else None
    status = _STAGE_TO_STATUS.get(_norm(stage_name)) if stage_name else None
    # Record status overrides stage: moved_in is Start of Care, closed/lost is archived.
    if wh_status == "moved_in":
        status = Status.soc
    elif wh_status == "closed":
        status = Status.archived

    # A discarded or merged-away Prospect is a deletion/duplicate, not a pipeline outcome:
    # archive it if we already track it (so it can't linger active or trip the stale
    # sweep), but never create an entity for one (_upsert honors `defunct`).
    defunct = (
        _col(row, "prospects.discarded_at", "discarded_at") is not None
        or _col(row, "prospects.merged_into_prospect_id", "merged_into_prospect_id") is not None
    )
    if defunct:
        status = Status.archived

    referral = _col(
        row, "lead_sources.name", "lead_source", "lead_source_name", "referral_source"
    )
    if referral is None:
        ls_id = _col(row, "lead_sources.id", "lead_source_id")
        referral = lead_sources.get(ls_id) if ls_id else None

    inquiry = _col(row, "prospects.inquiry_date", "inquiry_date")
    if inquiry is None:
        created = _parse_dt(_col(row, "prospects.created_at", "created_at"))
        inquiry = created.date().isoformat() if created else None
    last_contact = _parse_dt(_col(row, "last_contact_at", "last_contact_date"))
    return {
        "id": prospect_id,
        "source_ref": f"{SOURCE}:prospect:{prospect_id}",
        "name": name,
        "status": status.value if status else None,
        "stage_name": stage_name,
        "referral_source": referral,
        "phone": person.get("phone") or _col(row, "phone", "cell_phone", "phone_number"),
        "email": person.get("email") or _col(row, "email"),
        "inquiry_date": inquiry[:10] if inquiry else None,
        "last_contact_date": last_contact.date().isoformat() if last_contact else None,
        "updated_at": _parse_dt(_col(row, "prospects.updated_at", "updated_at")),
        "defunct": defunct,
    }


def _upsert(prospect: dict[str, Any]) -> str | None:
    """Upsert the entity by source_ref; return the manufactured delta kind, if any."""
    from nexus.vault.queries import get_entity
    from nexus.writes import update_entity

    existing = get_entity(prospect["source_ref"])
    changes = {
        k: v
        for k, v in prospect.items()
        if k in {"source_ref", "status", "referral_source", "phone", "email"}
        and v is not None
    }
    for field in ("inquiry_date", "last_contact_date"):
        if prospect[field] is not None and (existing is None or not existing.get(field)):
            changes[field] = prospect[field]  # set once from the CRM; never regress

    if existing is None:
        if prospect["defunct"]:
            return None  # never create an entity for a discarded/merged record
        title = prospect["name"]
        if _title_taken(title):  # distinct prospect, same person name: don't merge files
            title = f"{title} ({SOURCE} {prospect['id']})"
        update_entity(title, "prospect", changes)
        return "new_prospect"
    # Key by the existing note's title so a rename in WelcomeHome can't fork the file.
    update_entity(existing["title"], "prospect", changes)
    if prospect["defunct"]:
        return None  # archiving a defunct record is hygiene, not a delta worth waking for
    new_status = changes.get("status")
    if new_status is not None and new_status != existing.get("status"):
        return "stage_changed"
    return None


def _residents_map(client: ProspectSource) -> dict[str, dict[str, str | None]]:
    """prospect_id -> {name, phone, email} from the Residents export.

    A Prospect can have multiple residents; the row flagged `first_resident` wins.
    Never fatal — enrichment only (entities fall back to a placeholder name).
    """
    out: dict[str, dict[str, str | None]] = {}
    try:
        rows = client.export_residents()
    except Exception:  # noqa: BLE001 — enrichment, not a dependency
        log.exception("welcomehome sync: could not fetch Residents export")
        return out
    for row in rows:
        pid = _col(row, "residents.prospect_id", "prospect_id")
        if pid is None or _col(row, "people.discarded_at") is not None:
            continue
        is_first = (_col(row, "residents.first_resident", "first_resident") or "").lower()
        if pid in out and is_first != "true":
            continue
        first = _col(row, "people.first_name", "first_name") or ""
        last = _col(row, "people.last_name", "last_name") or ""
        out[pid] = {
            "name": f"{first} {last}".strip() or None,
            "phone": _col(
                row, "people.cell_phone", "people.home_phone", "people.work_phone",
                "cell_phone", "home_phone",
            ),
            "email": _col(row, "people.email", "email"),
        }
    return out


def _apply_last_contacts(client: ProspectSource, since: datetime | None) -> None:
    """Advance last_contact_date from completed Prospect activities since `since`.

    The Prospects export has no last-contact column; a completed activity (call, visit,
    note) is the CRM's record of contact. Only moves the date forward, never back.
    """
    from nexus.vault.queries import get_entity
    from nexus.writes import update_entity

    try:
        rows = client.export_activities(since=since)
    except Exception:  # noqa: BLE001 — enrichment, not a dependency
        log.exception("welcomehome sync: could not fetch Activities export")
        return

    latest: dict[str, str] = {}  # prospect_id -> max completed_at date
    for row in rows:
        if (_col(row, "activities.record_type", "record_type") or "") != "Prospect":
            continue
        completed = _parse_dt(_col(row, "activities.completed_at", "completed_at"))
        pid = _col(row, "activities.record_id", "record_id")
        if pid is None or completed is None:
            continue
        day = completed.date().isoformat()
        if day > latest.get(pid, ""):
            latest[pid] = day

    for pid, day in latest.items():
        source_ref = f"{SOURCE}:prospect:{pid}"
        existing = get_entity(source_ref)
        if existing is not None and (existing.get("last_contact_date") or "") < day:
            update_entity(
                existing["title"], "prospect",
                {"source_ref": source_ref, "last_contact_date": day},
            )


# --- stale sweep ---------------------------------------------------------------------------


async def _flag_stale(state: dict[str, Any]) -> None:
    """Manufacture `prospect_stale` for worked-but-unmoved Prospects — once per episode.

    An episode is keyed by (status, last activity date): flagging again only happens after
    the Prospect moves or is contacted and THEN goes quiet again.
    """
    from nexus.vault.queries import list_entities

    flagged: dict[str, str] = state.setdefault("stale_flagged", {})
    cutoff = (datetime.now(UTC) - STALE_AFTER).date().isoformat()
    for rec in list_entities(kind="prospect"):
        source_ref = rec.get("source_ref") or ""
        if not source_ref.startswith(f"{SOURCE}:") or rec.get("status") not in _STALE_ELIGIBLE:
            continue
        last_activity = (
            rec.get("last_contact_date") or rec.get("inquiry_date") or rec.get("updated")
        )
        if not last_activity or last_activity > cutoff:
            continue
        marker = f"{rec.get('status')}:{last_activity}"
        if flagged.get(source_ref) == marker:
            continue  # already flagged this episode
        flagged[source_ref] = marker
        await _emit(
            "prospect_stale",
            {
                "source_ref": source_ref,
                "name": rec.get("title"),
                "status": rec.get("status"),
                "last_activity": last_activity,
                "stale_after_days": STALE_AFTER.days,
            },
        )


# --- dispatch ------------------------------------------------------------------------------


async def _emit(kind: str, payload: dict[str, Any]) -> None:
    """Manufacture a Stimulus through the same path a webhook takes (§5.2 discipline):
    classify -> LOG ALWAYS -> dispatch."""
    from nexus.connectors.ingress.envelope import Stimulus
    from nexus.connectors.ingress.router import dispatch
    from nexus.connectors.ingress.rules import classify
    from nexus.writes import append_log

    payload = {k: v for k, v in payload.items() if v is not None and not isinstance(v, datetime)}
    stimulus = Stimulus(
        source=SOURCE,
        kind=kind,
        received_at=datetime.now(UTC),
        external_id=f"{payload.get('source_ref', '')}:{kind}",
        payload=payload,
    )
    tier = classify(SOURCE, kind)
    try:
        append_log(f"[{tier}] {SOURCE}:{kind} {stimulus.external_id or ''}")
    except Exception:  # noqa: BLE001 — log-always is best-effort, must not block dispatch
        log.exception("welcomehome sync: event-log append failed")
    await dispatch(stimulus, tier)


# --- state file ----------------------------------------------------------------------------


def _state_path() -> Path:
    return settings.vault_path / "system" / SOURCE / "state.json"


def _load_state() -> dict[str, Any]:
    path = _state_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.warning("welcomehome sync: corrupt state file, starting from scratch")
        return {}


def _save_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


# --- small helpers -------------------------------------------------------------------------


def _col(row: dict[str, str], *names: str) -> str | None:
    """Tolerant CSV column lookup: case-insensitive, spaces == underscores."""
    lowered = {k.strip().lower().replace(" ", "_"): v for k, v in row.items() if k}
    for name in names:
        value = lowered.get(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _title_taken(title: str) -> bool:
    """True if an entity note file already claims this title's slug."""
    from nexus.vault.io import family_dir, slugify

    return (family_dir("entity") / f"{slugify(title)}.md").exists()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            return datetime.combine(date.fromisoformat(value[:10]), datetime.min.time(), UTC)
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _id_name_map(fetch) -> dict[str, str]:
    """id -> name from a list endpoint; empty (never fatal) if the call fails."""
    try:
        return {str(item["id"]): item["name"] for item in fetch() if item.get("name")}
    except Exception:  # noqa: BLE001 — translation maps are an enrichment, not a dependency
        log.exception("welcomehome sync: could not fetch id->name map")
        return {}
