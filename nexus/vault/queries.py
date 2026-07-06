"""Read-tool logic — plain functions (spec §3.5).

There is no central `search_everything`. Each layer has one narrow read tool whose
behavior (and, where wrapped, whose description) tells the agent when to reach for it; the
routing EMERGES from the model reading precise tool descriptions against the request.

These plain functions back BOTH the MCP tools (conversational agent) AND the server-side
agent loop — one source of truth, no self-MCP hop (§3.5).

Used in stage 3 (gather). All read-only: they never write.
"""

from __future__ import annotations

import re
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


def get_note(path: str) -> dict[str, Any] | None:
    """Fetch one note's FULL content (frontmatter + body) by the path from a search hit.

    Search hits carry only title + summary; this is the drill-down that returns the
    underlying text so answers can quote the source. `source_ref` on the record cites the
    archived original. Paths outside the vault (or in non-note dirs) return None.
    """
    root = io.vault_root().resolve()
    given = Path(path)
    # Hits carry paths as emitted by the vault walk (relative to CWD when vault_path is
    # relative); also accept absolute paths and vault-root-relative paths.
    candidates = [given] if given.is_absolute() else [given, root / given]
    target = next((c.resolve() for c in candidates if c.is_file()), None)
    if target is None or target.name == io.INDEX_FILENAME:
        return None
    try:
        rel = target.relative_to(root)
    except ValueError:
        return None  # escapes the vault
    if rel.parts and rel.parts[0] in io.NON_NOTE_DIRS:
        return None  # attachments/context are not queryable notes
    note, body = io.read_note(target)
    return _as_record(target, note, body)


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _contact_match(needle: str, rec: dict[str, Any]) -> bool:
    """Match an email or phone number against the entity and its family contacts."""
    contacts = [rec] + list(rec.get("family_contacts") or [])
    if "@" in needle:
        return needle in {(c.get("email") or "").lower() for c in contacts}
    nd = _digits(needle)
    if len(nd) < 7:
        return False  # too short to be a phone number — avoid false positives
    for c in contacts:
        pd = _digits(c.get("phone") or "")
        if len(pd) >= 7 and (pd.endswith(nd) or nd.endswith(pd)):
            return True  # suffix match tolerates +1 / country-code prefixes
    return False


def get_entity(name: str) -> dict[str, Any] | None:
    """Resolve a name/phone/email -> current entity state. The anchor for person-specific asks.

    Exact title or source_ref match wins (case-insensitive); otherwise falls back to
    phone/email matching across the entity and its family contacts, so a webhook that only
    carries a caller's number still resolves. Identity resolves FIRST in stage 3 (§6.1).
    """
    needle = name.strip().lower()
    notes = list(io.iter_notes(io.family_dir(Family.entity)))
    for path, note, body in notes:
        if note.title.strip().lower() == needle or (note.source_ref or "").lower() == needle:
            return _as_record(path, note, body)
    for path, note, body in notes:
        if _contact_match(needle, note.model_dump(mode="json")):
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
