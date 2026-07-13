"""The schema is the contract beneath everything.

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

Extending: change the enums and entity models below — `Kind`, the lifecycle
`Status`, your reference taxonomy, and the per-kind entity models. The JSON schema,
templates, validator, and tool hints all follow automatically.
"""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# A wikilink target, e.g. "[[Other Note]]" — kept as a plain string for now.
WikiLink = str


class Family(StrEnum):
    reference = "reference"
    entity = "entity"
    event = "event"
    task = "task"


class Status(StrEnum):
    """Generic lifecycle, plus the WelcomeHome prospect pipeline stages."""

    draft = "draft"
    published = "published"
    archived = "archived"
    open = "open"  # task queue
    done = "done"  # task queue

    # Prospect lead stages (mirrors WelcomeHome CRM), in pipeline order.
    inquiry = "inquiry"
    attempted = "attempted"
    contact_made = "contact_made"  # WelcomeHome: "Ct Made"
    visit_scheduled = "visit_scheduled"  # WelcomeHome: "Visit Schld"
    visit_completed = "visit_completed"  # WelcomeHome: "Visit Cmplt"
    soc = "soc"  # Start of Care — prospect becomes an active client


class Kind(StrEnum):
    """Entity kinds tracked by this fork."""

    prospect = "prospect"  # a WelcomeHome lead, from first inquiry through Start of Care


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


class ReferenceCategory(StrEnum):
    """Reference taxonomy for this fork."""

    intake_script = "intake_script"  # how to talk to a new inquiry at each lead stage
    pricing = "pricing"  # rate sheets, hourly rates by service line, minimums, packages
    service_sop = "service_sop"  # what each service line actually includes
    policy_voice = "policy_voice"  # general policy, brand voice/tone, FAQs
    memory = "memory"  # reserved: the agents' append_memory note (reference/memory.md)


class Audience(StrEnum):
    internal = "internal"  # staff-only: scripts, pricing, SOPs
    client_facing = "client_facing"  # safe to share directly with prospects/families


# Title normalization for reference notes: trademark glyphs vanish, curly quotes and long
# dashes become their ASCII forms, a subtitle colon becomes " - ", whitespace collapses.
_TM_GLYPHS = dict.fromkeys(map(ord, "®™©"))
_PUNCT_MAP = (
    {ord(c): "'" for c in "‘’"}
    | {ord(c): '"' for c in "“”"}
    | {ord(c): "-" for c in "–—"}
)


def _kebab(tag: str) -> str:
    """One canonical tag form: lowercase kebab-case, symbols dropped."""
    tag = re.sub(r"[^\w\s-]", "", tag.lower())
    return re.sub(r"[\s_-]+", "-", tag).strip("-")


class ReferenceNote(CoreNote):
    """Authored, slow-changing knowledge: SOPs, policy, pricing, voice.

    Titles and tags are normalized at validation time — so the gate guarantees one
    canonical form no matter which writer (ingest LLM, MCP tool, hand-rolled script)
    produced them: tags are lowercase kebab-case ("senior-care", never "senior care");
    titles lose trademark glyphs (®™©) and curly punctuation, and subtitle colons become
    " - " so the same brand name can't appear in three spellings.
    """

    family: Literal[Family.reference] = Family.reference
    category: ReferenceCategory | None = None
    audience: Audience = Audience.internal

    @field_validator("title")
    @classmethod
    def _normalize_title(cls, v: str) -> str:
        v = v.translate(_TM_GLYPHS).translate(_PUNCT_MAP)
        v = re.sub(r"\s*:\s*", " - ", v)
        return re.sub(r"\s+", " ", v).strip(" -") or "Untitled"

    @field_validator("tags")
    @classmethod
    def _normalize_tags(cls, v: list[str]) -> list[str]:
        seen: dict[str, None] = {}
        for tag in v:
            if k := _kebab(tag):
                seen.setdefault(k)
        return list(seen)


class EntityNote(CoreNote):
    """Current distilled state of one tracked thing. Nested union on `kind`."""

    family: Literal[Family.entity] = Family.entity
    kind: Kind  # <-- YOUR entity kinds live here
    # Add filterable frontmatter fields here as needs emerge (stage, owner, dates, ...).


class ServiceLine(StrEnum):
    """What the prospect is being served for — WelcomeHome's service-line taxonomy."""

    unknown = "Unknown"
    companion_care = "Companion Care"
    personal_care = "Personal Care"
    respite_care = "Respite Care"
    specialized_care = "Specialized Care"
    va_prospect = "VA Prospect"


class FamilyContact(BaseModel):
    """One family member/decision-maker tied to a prospect. A prospect can have several."""

    model_config = ConfigDict(extra="forbid")

    name: str
    phone: str | None = None
    email: str | None = None


class ProspectNote(EntityNote):
    """A WelcomeHome lead, from first inquiry through Start of Care.

    Identity: no stable WelcomeHome record ID is wired up yet, so dedup is on name plus
    phone/email (see `source_ref` on `CoreNote`) until the WelcomeHome connector lands.

    Medical/PHI detail (diagnoses, medications, detailed records) stays in WellSky — never
    in this note. `service_lines` and the body may carry a brief, non-sensitive care-need
    summary only (e.g. "has mobility needs"), per the data boundary in `vault/context/ORG.md`.
    """

    kind: Literal[Kind.prospect] = Kind.prospect
    status: Status = Status.inquiry  # current WelcomeHome lead stage
    referral_source: str | None = None  # aggregator, e.g. "A Place for Mom", "Care.com"
    service_lines: list[ServiceLine] = [ServiceLine.unknown]
    phone: str | None = None
    email: str | None = None
    family_contacts: list[FamilyContact] = []  # decision-makers, if different from prospect
    inquiry_date: date | None = None
    last_contact_date: date | None = None
    next_follow_up: date | None = None  # drives "respond within the hour" urgency


class EventNote(CoreNote):
    """Append-only history — one note per day. Entity state is its projection."""

    family: Literal[Family.event] = Family.event
    entries: list[str] = []  # chronological event-entry summaries for the day


class TaskNote(CoreNote):
    """A pending human decision — the approval inbox."""

    family: Literal[Family.task] = Family.task
    action: str | None = None  # what is proposed
    # Structured hand-off so approval is one-click: external-facing tasks carry
    # the channel, recipient, and drafted body.
    channel: str | None = None
    recipient: str | None = None
    body: str | None = None


# Entities are a discriminated union on `kind` (one member per Kind). With a single
# kind this is just ProspectNote; add members as more Kinds are tracked.
EntityUnion = ProspectNote

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
    """JSON schema constraining LLM structured output for this family."""
    return model_for(family).model_json_schema()


def template_for(family: Family) -> str:
    """A documented, empty frontmatter template for this family."""
    model = model_for(family)
    lines = ["---"]
    for name, info in model.model_fields.items():
        default = "" if info.is_required() else info.default
        lines.append(f"{name}: {default!r}" if default != "" else f"{name}:")
    lines += ["---", "", f"<{family.value} body>", ""]
    return "\n".join(lines)
