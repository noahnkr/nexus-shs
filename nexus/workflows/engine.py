"""The run engine — executes one RunState through a workflow's step graph.

Discipline inherited from ingress (§5.2 "log always" in spirit): run state is persisted
BEFORE the first step and after EVERY step, so a crash mid-run leaves a visible `running`
record at the exact step it died on, never silence. Multi-instance by construction: each
`start_run` mints a fresh run_id and file; concurrent runs of one workflow never share
state.

Steps route on block kind AS DATA (§1.1): condition -> branch on truthiness; action ->
execute and follow on_success/on_failure. `{{...}}` refs in step config resolve against
the run context (trigger payload + prior step outputs) just before execution.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from nexus.workflows import store
from nexus.workflows.blocks import BlockKind, RunContext, call_block, get, lookup_path
from nexus.workflows.schema import (
    MAX_STEPS_PER_RUN,
    RunState,
    RunStatus,
    StepOutcome,
    StepResult,
    WorkflowSpec,
    utcnow,
)

_REF = re.compile(r"\{\{\s*([\w.\-]+)\s*\}\}")


def _resolve(value: Any, ctx: RunContext) -> Any:
    """Substitute {{path}} refs in config values. A string that IS one ref keeps the
    looked-up value's type; mixed strings interpolate as text."""
    if isinstance(value, dict):
        return {k: _resolve(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, ctx) for v in value]
    if isinstance(value, str):
        whole = _REF.fullmatch(value.strip())
        if whole:
            return lookup_path(whole.group(1), ctx)
        return _REF.sub(lambda m: str(lookup_path(m.group(1), ctx) or ""), value)
    return value


def _json_safe(value: Any) -> Any:
    """Project a block's return value to something the RunState model can persist."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(v) for v in value]
    return str(value)


async def start_run(
    spec: WorkflowSpec,
    *,
    tier: str = "supervised",
    trigger_payload: dict[str, Any] | None = None,
    trigger_source: str = "manual",
    trigger_kind: str = "manual",
) -> RunState:
    """Mint a new run instance and execute it to a terminal state. Returns the RunState."""
    run = RunState(
        run_id=uuid.uuid4().hex[:12],
        workflow=spec.slug,
        workflow_version=spec.version,
        tier=tier,
        trigger_payload=trigger_payload or {},
        trigger_source=trigger_source,
        trigger_kind=trigger_kind,
        current_step=spec.entry,
        started_at=utcnow(),
        updated_at=utcnow(),
    )
    store.save_run(run)  # visible as `running` before any step executes
    try:
        await _execute(spec, run)
    finally:
        # Action blocks write through the gate (append_log/update_entity/create_task);
        # settle INDEX.md at run end — no agent loop wraps a workflow run.
        from nexus.vault.index import regenerate_if_dirty

        regenerate_if_dirty()
    return run


async def _execute(spec: WorkflowSpec, run: RunState) -> None:
    steps = {s.id: s for s in spec.steps}
    visited = 0
    while run.current_step is not None:
        if visited >= MAX_STEPS_PER_RUN:
            _finish(run, RunStatus.failed, error=f"exceeded {MAX_STEPS_PER_RUN} steps")
            return
        visited += 1

        step = steps.get(run.current_step)
        if step is None:
            _finish(run, RunStatus.failed, error=f"unknown step '{run.current_step}'")
            return
        block = get(step.block)
        if block is None or block.kind == BlockKind.trigger:
            _finish(run, RunStatus.failed, error=f"step '{step.id}': no such block")
            return

        ctx = RunContext(
            tier=run.tier,
            trigger_payload=run.trigger_payload,
            trigger_source=run.trigger_source,
            trigger_kind=run.trigger_kind,
            step_outputs={r.step_id: r.output for r in run.results},
        )
        started = utcnow()
        try:
            output = _json_safe(await call_block(block, _resolve(step.config, ctx), ctx))
            outcome, error = StepOutcome.succeeded, None
        except Exception as exc:  # noqa: BLE001 — a step failure is data, not a crash
            output, outcome, error = None, StepOutcome.failed, str(exc)

        run.results.append(
            StepResult(
                step_id=step.id,
                block=step.block,
                outcome=outcome,
                output=output,
                error=error,
                started_at=started,
                finished_at=utcnow(),
            )
        )

        if block.kind == BlockKind.condition and outcome == StepOutcome.succeeded:
            run.current_step = step.on_success if output else step.on_failure
        elif outcome == StepOutcome.succeeded:
            run.current_step = step.on_success
        elif step.on_failure is not None:
            run.current_step = step.on_failure  # explicit failure handler
        else:
            _finish(run, RunStatus.failed, error=f"step '{step.id}' failed: {error}")
            return
        store.save_run(run)  # crash-visible progress after every step

    _finish(run, RunStatus.succeeded)


def _finish(run: RunState, status: RunStatus, *, error: str | None = None) -> None:
    run.status = status
    run.error = error
    run.current_step = None
    run.finished_at = utcnow()
    store.save_run(run)


def cancel_run(run_id: str) -> RunState:
    """Mark a run cancelled. Scaffold: cooperative flag on state — the single-process
    engine finishes the in-flight step; a crashed run is also cleared this way."""
    run = store.load_run(run_id)
    if run is None:
        raise FileNotFoundError(f"no run '{run_id}'")
    if run.status == RunStatus.running:
        _finish(run, RunStatus.cancelled)
    return run
