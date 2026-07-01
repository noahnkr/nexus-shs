"""Read-tool logic — plain functions (spec §3.5).

There is no central `search_everything`. Each layer has one narrow read tool whose
behavior (and, where wrapped, whose description) tells the agent when to reach for it; the
routing EMERGES from the model reading precise tool descriptions against the request.

These plain functions back BOTH the MCP tools (conversational agent) AND the server-side
agent loop — one source of truth, no self-MCP hop (§3.5).

Used in stage 3 (gather). All read-only: they never write.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nexus.vault import io
from nexus.vault.schema import Family
from nexus.vault.search import Hit, get_index


def _as_record(path, note, body: str = "") -> dict[str, Any]:
    rec = note.model_dump(mode="json")
    rec["_path"] = str(path)
    if body:
        rec["_body"] = body
    return rec


def search_reference(query: str, k: int = 8) -> list[Hit]:
    """Stable business facts — pricing, policy, voice, how-to (reference family)."""
    return get_index().query(query, k, family=Family.reference)


def get_entity(name: str) -> dict[str, Any] | None:
    """Resolve a name -> current entity state. The anchor for anything person-specific.

    Matches on title (case-insensitive) or on source_ref; identity resolves FIRST in
    stage 3 (entity-first, §6.1).
    """
    needle = name.strip().lower()
    for path, note, body in io.iter_notes(io.family_dir(Family.entity)):
        if note.title.strip().lower() == needle or (note.source_ref or "").lower() == needle:
            return _as_record(path, note, body)
    return None


def list_entities(
    kind: str | None = None, status: str | None = None, **attrs: Any
) -> list[dict[str, Any]]:
    """Filter entities by kind/status/attribute — a PURE METADATA query, NO embedder."""
    out: list[dict[str, Any]] = []
    for path, note, _ in io.iter_notes(io.family_dir(Family.entity)):
        rec = note.model_dump(mode="json")
        if kind is not None and rec.get("kind") != kind:
            continue
        if status is not None and rec.get("status") != status:
            continue
        if any(rec.get(key) != val for key, val in attrs.items()):
            continue
        out.append(_as_record(path, note))
    return sorted(out, key=lambda r: r.get("updated", ""), reverse=True)


def search_logs(query: str, since: str | None = None, until: str | None = None) -> list[Hit]:
    """'What happened / when did we…', date-scoped (event family).

    `since`/`until` are ISO dates compared against each day note's filename/created date.
    """
    hits = get_index().query(query, k=20, family=Family.event)
    if since is None and until is None:
        return hits

    def in_range(hit: Hit) -> bool:
        try:
            note, _ = io.read_note(Path(hit.path))
        except Exception:  # noqa: BLE001 — keep the hit if the date can't be read
            return True
        day = str(getattr(note, "created", ""))
        if since and day < since:
            return False
        if until and day > until:
            return False
        return True

    return [h for h in hits if in_range(h)]


def list_open_tasks() -> list[dict[str, Any]]:
    """'What's outstanding / awaiting approval' (task family)."""
    out: list[dict[str, Any]] = []
    for path, note, body in io.iter_notes(io.family_dir(Family.task)):
        if note.model_dump(mode="json").get("status") != "open":
            continue
        out.append(_as_record(path, note, body))
    return sorted(out, key=lambda r: r.get("created", ""), reverse=True)
