"""The schema is the contract (spec §3.2) — THE PRIMARY FORK SEAM.

One Pydantic model set defines every note family. From it (and nothing hand-maintained
alongside it) you generate three things:

  1. JSON schema for LLM structured output  -> constrains the ingest classifier at
     generation time so it can only emit valid frontmatter (`json_schema_for`).
  2. Human/agent templates                    -> the documented shape of each note
     (`template_for`).
  3. The runtime validator                    -> every write crosses it (see vault/io.py);
     the index re-validates on read to catch hand edits.

Two load-bearing rules:
  - `extra="forbid"` turns every misspelled field into a loud error, not silent data loss
    — essential when an LLM is the writer.
  - Field DECLARATION ORDER == frontmatter key order; core fields are declared first, so
    every note reads consistently with zero formatting code.

FORKING (spec §7 step 1): change the enums and entity models below — `Kind`, the lifecycle
`Status`, your reference taxonomy, and the per-kind entity models. The JSON schema,
templates, validator, and tool hints all follow automatically.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# A wikilink target, e.g. "[[Other Note]]" — kept as a plain string for now.
WikiLink = str


class Family(StrEnum):
    reference = "reference"
    entity = "entity"
    event = "event"
    task = "task"


class Status(StrEnum):
    """Generic lifecycle. FORK: extend per family/entity as your domain needs."""

    draft = "draft"
    published = "published"
    archived = "archived"
    open = "open"  # task queue
    done = "done"  # task queue


class Kind(StrEnum):
    """Entity kinds — FORK SEAM (§7 step 1).

    What does this business track? Replace the placeholder with your real kinds, e.g.
    patients/providers/claims, deals/accounts/contacts, students/courses/cohorts. Each
    value should get a corresponding model in the entity discriminated union below.
    """

    thing = "thing"  # placeholder — replace with your domain's entity kinds


class CoreNote(BaseModel):
    """Fields shared by every family. Declaration order == frontmatter key order."""

    model_config = ConfigDict(use_enum_values=True, extra="forbid")  # forbid = typo guard

    title: str
    family: Family
    status: Status = Status.draft
    summary: str | None = None  # projected into the index row for every family
    tags: list[str] = []
    created: date
    updated: date
    related: list[WikiLink] = []  # untyped cross-links ([[Other Note]])
    last_reviewed: date | None = None  # drives the vault-health staleness sweep
    source_ref: str | None = None  # stable external id ("<system>:<type>:<id>") for sync


class ReferenceNote(CoreNote):
    """Authored, slow-changing knowledge: SOPs, policy, pricing, voice (§3.1)."""

    family: Literal[Family.reference] = Family.reference
    category: str | None = None  # FORK: your reference taxonomy
    audience: str | None = None


class EntityNote(CoreNote):
    """Current distilled state of one tracked thing (§3.1). Nested union on `kind`."""

    family: Literal[Family.entity] = Family.entity
    kind: Kind  # <-- YOUR entity kinds live here
    # FORK: add filterable frontmatter fields (stage, owner, area, dates, ...).


class EventNote(CoreNote):
    """Append-only history — one note per day (§3.1). Entity state is its projection."""

    family: Literal[Family.event] = Family.event
    entries: list[str] = []  # chronological event-entry summaries for the day


class TaskNote(CoreNote):
    """A pending human decision — the approval inbox (§3.1)."""

    family: Literal[Family.task] = Family.task
    action: str | None = None  # what is proposed
    # Structured hand-off so approval is one-click (§6.3): external-facing tasks carry
    # the channel, recipient, and drafted body.
    channel: str | None = None
    recipient: str | None = None
    body: str | None = None


# Entities are a discriminated union on `kind` (one member per Kind). With a single
# placeholder kind this is just EntityNote; add members as you fork.
EntityUnion = EntityNote

# Top-level: a discriminated union on `family`.
AnyNote = Annotated[
    ReferenceNote | EntityUnion | EventNote | TaskNote,
    Field(discriminator="family"),
]

_FAMILY_MODEL: dict[Family, type[CoreNote]] = {
    Family.reference: ReferenceNote,
    Family.entity: EntityNote,
    Family.event: EventNote,
    Family.task: TaskNote,
}


def model_for(family: Family) -> type[CoreNote]:
    return _FAMILY_MODEL[family]


def json_schema_for(family: Family) -> dict:
    """JSON schema constraining LLM structured output for this family (§3.2 #1)."""
    return model_for(family).model_json_schema()


def template_for(family: Family) -> str:
    """A documented, empty frontmatter template for this family (§3.2 #2)."""
    model = model_for(family)
    lines = ["---"]
    for name, info in model.model_fields.items():
        default = "" if info.is_required() else info.default
        lines.append(f"{name}: {default!r}" if default != "" else f"{name}:")
    lines += ["---", "", f"<{family.value} body>", ""]
    return "\n".join(lines)
