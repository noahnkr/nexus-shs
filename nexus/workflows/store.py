"""Validated persistence for workflow definitions and run state.

Documents live under `vault/system/workflows/` — inside the one volume, but under
`system/` so they stay out of the note corpus (io.NON_NOTE_DIRS).
Mirrors the write-gate discipline: every save round-trips through the Pydantic model
(`extra="forbid"`), and every load re-validates to catch hand edits.

Layout:
  vault/system/workflows/<slug>.json          one definition per workflow
  vault/system/workflows/runs/<run_id>.json   one file per run instance
"""

from __future__ import annotations

import json
from pathlib import Path

from nexus.vault import io
from nexus.workflows.schema import (
    RunState,
    RunStatus,
    WorkflowSpec,
    WorkflowStatus,
    utcnow,
    validate_graph,
)


def workflows_dir() -> Path:
    return io.vault_root() / "system" / "workflows"


def runs_dir() -> Path:
    return workflows_dir() / "runs"


def _write_json(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return path


# --- definitions -------------------------------------------------------------------------


def save_workflow(spec: WorkflowSpec) -> Path:
    """THE GATE for definitions: validate the model AND the graph, then persist."""
    problems = validate_graph(spec)
    if problems:
        raise ValueError(f"invalid workflow '{spec.slug}': {problems}")
    spec.updated = utcnow()
    validated = WorkflowSpec.model_validate(spec.model_dump(mode="json"))
    return _write_json(workflows_dir() / f"{spec.slug}.json", validated.model_dump(mode="json"))


def load_workflow(slug: str) -> WorkflowSpec | None:
    path = workflows_dir() / f"{io.slugify(slug)}.json"
    if not path.is_file():
        return None
    return WorkflowSpec.model_validate_json(path.read_text(encoding="utf-8"))


def list_workflows(status: str | None = None) -> list[WorkflowSpec]:
    specs: list[WorkflowSpec] = []
    root = workflows_dir()
    if not root.exists():
        return specs
    for path in sorted(root.glob("*.json")):
        try:
            spec = WorkflowSpec.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — a malformed file must not break the listing
            continue
        if status is None or spec.status == status:
            specs.append(spec)
    return specs


def set_workflow_status(slug: str, status: str) -> WorkflowSpec:
    """Move a definition through its lifecycle. Activation re-validates block refs —
    a draft referencing unknown blocks cannot go live."""
    spec = load_workflow(slug)
    if spec is None:
        raise FileNotFoundError(f"no workflow '{slug}'")
    new_status = WorkflowStatus(status)
    if new_status == WorkflowStatus.active:
        from nexus.workflows.blocks import get

        missing = [s.block for s in spec.steps if get(s.block) is None]
        if get(spec.trigger.block) is None:
            missing.append(spec.trigger.block)
        if missing:
            raise ValueError(f"cannot activate '{slug}': unknown blocks {sorted(set(missing))}")
    spec.status = new_status
    save_workflow(spec)
    return spec


# --- runs ---------------------------------------------------------------------------------


def save_run(run: RunState) -> Path:
    run.updated_at = utcnow()
    validated = RunState.model_validate(run.model_dump(mode="json"))
    return _write_json(runs_dir() / f"{run.run_id}.json", validated.model_dump(mode="json"))


def load_run(run_id: str) -> RunState | None:
    path = runs_dir() / f"{run_id}.json"
    if not path.is_file():
        return None
    return RunState.model_validate_json(path.read_text(encoding="utf-8"))


def list_runs(workflow: str | None = None, status: str | None = None) -> list[RunState]:
    """All run instances, newest first — filter by workflow slug and/or status.

    `status="running"` answers "what is mid-run right now?". Scaffold note: this walks
    every run file; add pruning/pagination when run volume demands it.
    """
    runs: list[RunState] = []
    root = runs_dir()
    if not root.exists():
        return runs
    for path in root.glob("*.json"):
        try:
            run = RunState.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — a malformed file must not break the listing
            continue
        if workflow is not None and run.workflow != workflow:
            continue
        if status is not None and run.status != status:
            continue
        runs.append(run)
    return sorted(runs, key=lambda r: r.started_at, reverse=True)


def active_run_count(workflow: str) -> int:
    return len(list_runs(workflow=workflow, status=RunStatus.running.value))
