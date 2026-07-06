"""The write surface (spec §3.5 / §6 stage 6).

Used ONLY in stage 6 (record). Every write crosses the validating gate
(`nexus.vault.io.write_note`). These plain functions back both the MCP tools and the
server-side loop.

The decisive property (§3.5): there is deliberately NO write tool that contacts an outside
party. An external action can only become a `create_task` draft. The trust boundary is not
a prompt instruction the model might forget — it is the ABSENCE of a capability.

The stage-6 change-test (§1.6 / §6.1): log always; write only genuine change.
  - real event occurred        -> append_log        (vault-only, autonomous)
  - tracked thing's state moved -> update_entity     (vault-only, autonomous)
  - needs a human decision      -> create_task       (the supervised mechanism)
  - durable cross-cutting fact  -> append_memory     (vault-only, autonomous)
  - nothing changed             -> write nothing
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from nexus.vault import io
from nexus.vault.schema import (
    EntityUnion,
    Family,
    Kind,
    ReferenceCategory,
    ReferenceNote,
    Status,
    TaskNote,
)

# Validate through the kind-discriminated union, NOT the base EntityNote — otherwise
# extra="forbid" rejects every kind-specific field (phone, stage dates, ...).
_ENTITY_ADAPTER: TypeAdapter = TypeAdapter(EntityUnion)


def append_log(summary: str) -> Path:
    """A real event occurred -> append to today's event note. Vault-only, autonomous."""
    from nexus.vault.events import append_entry

    return append_entry(summary)


def update_entity(name: str, kind: str, changes: dict[str, Any]) -> Path:
    """A tracked thing's state changed -> merge frontmatter. Vault-only, autonomous.

    Load-or-create the entity note for (name, kind), merge `changes` into its frontmatter,
    bump `updated`, and persist through the gate. Unknown fields are rejected by the schema
    (`extra="forbid"`), which is the intended typo guard.
    """
    today = datetime.now(UTC).date()
    path = io.family_dir(Family.entity) / f"{io.slugify(name)}.md"

    if path.exists():
        existing, body = io.read_note(path)
        data = existing.model_dump(mode="json")
    else:
        body = ""
        data = {"title": name, "kind": Kind(kind), "created": today.isoformat()}

    data.update(changes)
    data["updated"] = today.isoformat()
    note = _ENTITY_ADAPTER.validate_python(data)
    return io.write_note(note, path, body)


def create_task(
    action: str,
    *,
    channel: str | None = None,
    recipient: str | None = None,
    body: str | None = None,
) -> Path:
    """Something needs a human -> write to the approval queue. THE supervised mechanism.

    For an external-facing action this is the ONLY available output: the structured
    hand-off (channel + recipient + drafted body) makes approval one-click (§6.3).
    """
    now = datetime.now(UTC)
    today = now.date()
    note = TaskNote(
        title=action[:80],
        status=Status.open,
        created=today,
        updated=today,
        action=action,
        channel=channel,
        recipient=recipient,
        body=body,
    )
    fname = f"{today.isoformat()}-{io.slugify(action)[:40]}-{now.strftime('%H%M%S')}.md"
    path = io.family_dir(Family.task) / fname
    return io.write_note(note, path, body or "")


def append_memory(fact: str) -> Path:
    """A durable cross-cutting fact was learned. Vault-only, autonomous.

    Stored as a single long-lived reference note (`reference/memory.md`); facts are
    appended as dated bullets to its body.
    """
    today = datetime.now(UTC).date()
    path = io.family_dir(Family.reference) / "memory.md"

    if path.exists():
        note, body = io.read_note(path)
    else:
        note = ReferenceNote(
            title="Memory",
            status=Status.published,
            summary="Durable cross-cutting facts learned by the agents.",
            category=ReferenceCategory.memory,
            created=today,
            updated=today,
        )
        body = "# Memory\n"

    body = body.rstrip() + f"\n- {today.isoformat()}: {fact}\n"
    note.updated = today
    return io.write_note(note, path, body)
