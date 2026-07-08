"""Workflows build & manage MCP tools (MCP-only) — the OWNER's automation surface.

Deliberately NOT in the loop's toolset: designing/activating automation is curation, not
ambient work. All still vault-only: no block can send externally (`workflows.blocks`
refuses).

Tools: `create_workflow` · `revise_workflow` · `preview_workflow` · `list_workflows` ·
`set_workflow_status` · `run_workflow` · `list_workflow_runs` · `get_workflow_run` ·
`cancel_workflow_run` · `list_workflow_blocks`.
"""

from __future__ import annotations

from typing import Any

from nexus.workflows import builder as wf_builder
from nexus.workflows import engine as wf_engine
from nexus.workflows import store as wf_store
from nexus.workflows.blocks import catalog as wf_catalog
from nexus.workflows.render import preview as wf_preview


def _wf_summary(spec) -> dict:
    return {
        "slug": spec.slug,
        "name": spec.name,
        "status": spec.status,
        "version": spec.version,
        "description": spec.description,
        "trigger": spec.trigger.model_dump(),
        "steps": len(spec.steps),
        "running_instances": wf_store.active_run_count(spec.slug),
    }


def register(target: Any) -> None:
    """Register the workflow build/manage tools onto MCP server `target`."""

    @target.tool(
        name="create_workflow",
        description=(
            "Compile a natural-language description (trigger + steps in prose) into a "
            "new DRAFT workflow using the available blocks, and return a Mermaid "
            "flowchart preview. Iterate with revise_workflow; go live with "
            "set_workflow_status(slug,'active')."
        ),
    )
    async def create_workflow(request: str) -> dict:
        spec = await wf_builder.create_workflow(request)
        return {"workflow": _wf_summary(spec), "preview": wf_preview(spec)}

    @target.tool(
        name="revise_workflow",
        description=(
            "Apply a natural-language change to a saved workflow (new version, back to "
            "draft for re-approval) and return the updated Mermaid preview."
        ),
    )
    async def revise_workflow(slug: str, instruction: str) -> dict:
        spec = await wf_builder.revise_workflow(slug, instruction)
        return {"workflow": _wf_summary(spec), "preview": wf_preview(spec)}

    @target.tool(
        name="preview_workflow",
        description="Return one workflow's full definition plus its Mermaid flowchart.",
    )
    def preview_workflow(slug: str) -> dict | None:
        spec = wf_store.load_workflow(slug)
        if spec is None:
            return None
        return {"spec": spec.model_dump(mode="json"), "preview": wf_preview(spec)}

    @target.tool(
        name="list_workflows",
        description=(
            "List workflows with status (draft/active/paused/archived) and how many "
            "instances of each are running right now. Optional status filter."
        ),
    )
    def list_workflows(status: str | None = None) -> list[dict]:
        return [_wf_summary(s) for s in wf_store.list_workflows(status=status)]

    @target.tool(
        name="set_workflow_status",
        description=(
            "Move a workflow through its lifecycle: activate a reviewed draft "
            "('active'), 'paused' to stop firing, 'archived' to retire, 'draft' to "
            "reopen. Activation re-validates every block reference."
        ),
    )
    def set_workflow_status(slug: str, status: str) -> dict:
        return _wf_summary(wf_store.set_workflow_status(slug, status))

    @target.tool(
        name="run_workflow",
        description=(
            "Manually fire one instance of a workflow now (any status except archived), "
            "with an optional trigger payload. Returns the finished run record."
        ),
    )
    async def run_workflow(slug: str, payload: dict | None = None) -> dict:
        spec = wf_store.load_workflow(slug)
        if spec is None:
            raise FileNotFoundError(f"no workflow '{slug}'")
        if spec.status == "archived":
            raise ValueError(f"workflow '{slug}' is archived")
        run = await wf_engine.start_run(spec, trigger_payload=payload or {})
        return run.model_dump(mode="json")

    @target.tool(
        name="list_workflow_runs",
        description=(
            "List run instances (newest first), filterable by workflow slug and status. "
            "status='running' shows everything currently mid-run and which step it is on."
        ),
    )
    def list_workflow_runs(workflow: str | None = None, status: str | None = None) -> list[dict]:
        return [
            {
                "run_id": r.run_id,
                "workflow": r.workflow,
                "status": r.status,
                "current_step": r.current_step,
                "started_at": str(r.started_at),
                "finished_at": str(r.finished_at) if r.finished_at else None,
                "error": r.error,
            }
            for r in wf_store.list_runs(workflow=workflow, status=status)
        ]

    @target.tool(
        name="get_workflow_run",
        description="Fetch one run's full record: per-step results, outputs, and errors.",
    )
    def get_workflow_run(run_id: str) -> dict | None:
        run = wf_store.load_run(run_id)
        return run.model_dump(mode="json") if run else None

    @target.tool(
        name="cancel_workflow_run",
        description="Cancel a running workflow instance (also clears a crashed 'running' run).",
    )
    def cancel_workflow_run(run_id: str) -> dict:
        return wf_engine.cancel_run(run_id).model_dump(mode="json")

    @target.tool(
        name="list_workflow_blocks",
        description=(
            "The block catalog: every trigger/condition/action workflows may compose, "
            "with config schemas — core blocks plus each connector's contributions."
        ),
    )
    def list_workflow_blocks() -> list[dict]:
        return wf_catalog()
