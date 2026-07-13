"""The workflow contract — definition (`WorkflowSpec`) and instance (`RunState`).

Mirrors the vault-schema discipline: one Pydantic model set is the single source of
truth. From it we derive (1) the JSON schema constraining the builder's LLM output, (2) the
runtime validator every persisted document crosses, and (3) the shape the renderer walks.
`extra="forbid"` everywhere — an LLM is the writer.

Definition vs instance: a `WorkflowSpec` is the reusable recipe; a `RunState` is ONE
execution of it. One workflow may have many concurrent RunStates (multi-instance by
construction — every run gets its own id and file).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

MAX_STEPS_PER_RUN = 50  # loop guard: a run visits at most this many steps


class WorkflowStatus(StrEnum):
    """Definition lifecycle. Only `active` workflows are matched against stimuli."""

    draft = "draft"  # being built/revised in conversation
    active = "active"  # live: trigger matching fires runs
    paused = "paused"  # kept, but never fires (existing runs finish)
    archived = "archived"  # retired


class RunStatus(StrEnum):
    """Instance lifecycle."""

    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class StepOutcome(StrEnum):
    succeeded = "succeeded"
    failed = "failed"


class TriggerSpec(BaseModel):
    """What starts a run: a trigger block plus its match config.

    Core trigger blocks: `trigger.webhook` (config: source, kind), `trigger.cron`
    (config: job), `trigger.manual` (fires only via run_workflow). Connectors may
    register more (⚙ blocks.py seam).
    """

    model_config = ConfigDict(use_enum_values=True, extra="forbid")

    block: str
    config: dict[str, Any] = {}


class StepSpec(BaseModel):
    """One node in the workflow graph.

    `on_success` / `on_failure` name the next step id (None = end / fail the run). For
    condition blocks the same pair reads as true-branch / false-branch. Config values may
    reference run context with `{{trigger.payload.x}}` or `{{steps.<id>.output}}` —
    resolved by the engine at execution time.
    """

    model_config = ConfigDict(use_enum_values=True, extra="forbid")

    id: str
    block: str
    config: dict[str, Any] = {}
    label: str | None = None  # short human label for the flowchart node
    on_success: str | None = None
    on_failure: str | None = None


class WorkflowDraft(BaseModel):
    """The builder-facing subset — what the LLM is allowed to author.

    Lifecycle fields (status/version/timestamps) are owned by the store, never the model.
    `WorkflowDraft.model_json_schema()` is the structured-output constraint for the
    compile call (same pattern as `vault.schema.json_schema_for`).
    """

    model_config = ConfigDict(use_enum_values=True, extra="forbid")

    slug: str = Field(description="kebab-case identifier, stable across revisions")
    name: str
    description: str
    trigger: TriggerSpec
    entry: str = Field(description="id of the first step")
    steps: list[StepSpec]


class WorkflowSpec(WorkflowDraft):
    """The persisted definition: a draft plus store-owned lifecycle fields."""

    status: WorkflowStatus = WorkflowStatus.draft
    version: int = 1
    created: datetime
    updated: datetime


class StepResult(BaseModel):
    """The audit record of one executed step inside a run."""

    model_config = ConfigDict(use_enum_values=True, extra="forbid")

    step_id: str
    block: str
    outcome: StepOutcome
    output: Any = None  # JSON-safe projection of the block's return value
    error: str | None = None
    started_at: datetime
    finished_at: datetime


class RunState(BaseModel):
    """One instance of a workflow. Persisted after EVERY step (crash-visible progress).

    `current_step` is the step about to execute (or executing) — the answer to "is this
    workflow mid-run, and where?". Terminal runs have `finished_at` set.
    """

    model_config = ConfigDict(use_enum_values=True, extra="forbid")

    run_id: str
    workflow: str  # WorkflowSpec.slug
    workflow_version: int
    status: RunStatus = RunStatus.running
    tier: str = "supervised"  # authoritative tier inherited from ingress classify
    trigger_payload: dict[str, Any] = {}
    trigger_source: str = "manual"
    trigger_kind: str = "manual"
    current_step: str | None = None
    results: list[StepResult] = []
    error: str | None = None
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None


def utcnow() -> datetime:
    return datetime.now(UTC)


def validate_graph(spec: WorkflowDraft) -> list[str]:
    """Structural checks the JSON schema can't express. Returns problems (empty = valid).

    Checks: unique step ids, entry exists, every on_success/on_failure edge targets a real
    step. Cycles are permitted in the graph but bounded at runtime by MAX_STEPS_PER_RUN.
    """
    problems: list[str] = []
    ids = [s.id for s in spec.steps]
    if not spec.steps:
        problems.append("workflow has no steps")
        return problems
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        problems.append(f"duplicate step ids: {sorted(dupes)}")
    known = set(ids)
    if spec.entry not in known:
        problems.append(f"entry '{spec.entry}' is not a step id")
    for step in spec.steps:
        for edge in ("on_success", "on_failure"):
            target = getattr(step, edge)
            if target is not None and target not in known:
                problems.append(f"step '{step.id}' {edge} -> unknown step '{target}'")
    return problems
