"""MCP wrappers — register_all aggregator (spec §3.5 / §7 step 4, the MCP seam).

Exposes the SAME plain functions (vault.queries reads + writes) as MCP tools, so the
conversational agent in a desktop client and the server-side loop share one source of
truth — no self-MCP network hop, no divergence between "what chat can do" and "what the
ambient agents can do."

CRITICAL: register read + vault-write tools only. No external-send tool (§4.2).
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from nexus import writes
from nexus.agents.toolset import _SPECS
from nexus.ingest import batch as ingest_mod_batch
from nexus.ingest import pipeline as ingest_mod
from nexus.vault import queries

logger = logging.getLogger("nexus.mcp")


def _hits(items) -> list[dict]:
    return [asdict(h) for h in items]


def register_all(target: Any) -> None:
    """Register every vault read/write tool onto the MCP server `target`.

    `target` is a FastMCP instance (anything exposing a `.tool` decorator). Uses the same
    descriptions as the loop's tool specs so chat and the ambient loop agree. Does NOT
    register any external-send capability.
    """
    if not hasattr(target, "tool"):
        raise NotImplementedError(
            "§3.5 — register_all expects a FastMCP server (a .tool decorator). Pass the "
            "instance from build_mcp()."
        )

    def d(name: str) -> str:
        return _SPECS[name][0]

    @target.tool(name="search_reference", description=d("search_reference"))
    def search_reference(query: str, k: int = 8) -> list[dict]:
        return _hits(queries.search_reference(query, k))

    @target.tool(name="get_note", description=d("get_note"))
    def get_note(path: str) -> dict | None:
        return queries.get_note(path)

    @target.tool(name="get_entity", description=d("get_entity"))
    def get_entity(name: str) -> dict | None:
        return queries.get_entity(name)

    @target.tool(name="list_entities", description=d("list_entities"))
    def list_entities(kind: str | None = None, status: str | None = None) -> list[dict]:
        return queries.list_entities(kind=kind, status=status)

    @target.tool(name="list_reference", description=d("list_reference"))
    def list_reference(
        category: str | None = None, status: str | None = None, audience: str | None = None
    ) -> list[dict]:
        return queries.list_reference(category=category, status=status, audience=audience)

    @target.tool(name="search_logs", description=d("search_logs"))
    def search_logs(query: str, since: str | None = None, until: str | None = None) -> list[dict]:
        return _hits(queries.search_logs(query, since, until))

    @target.tool(name="list_open_tasks", description=d("list_open_tasks"))
    def list_open_tasks() -> list[dict]:
        return queries.list_open_tasks()

    @target.tool(name="append_log", description=d("append_log"))
    def append_log(summary: str) -> str:
        return str(writes.append_log(summary))

    @target.tool(name="update_entity", description=d("update_entity"))
    def update_entity(name: str, kind: str, changes: dict) -> str:
        return str(writes.update_entity(name, kind, changes))

    @target.tool(name="create_task", description=d("create_task"))
    def create_task(
        action: str,
        channel: str | None = None,
        recipient: str | None = None,
        body: str | None = None,
    ) -> str:
        return str(writes.create_task(action, channel=channel, recipient=recipient, body=body))

    @target.tool(name="append_memory", description=d("append_memory"))
    def append_memory(fact: str) -> str:
        return str(writes.append_memory(fact))

    # --- knowledge-base curation (MCP-only, §3.7) ------------------------------------
    # The OWNER's surface, deliberately NOT in the loop's toolset: ingest takes
    # server-local file paths (ambient stimuli never carry those), and publication is the
    # human review step the ingest contract promises — the ambient agents must not
    # publish what ingest drafted. Still vault-only writes: no external send.

    @target.tool(
        name="ingest_file",
        description=(
            "Ingest one document (md/txt/html/pdf/docx) from a server-local path into the "
            "knowledge base as a status:draft reference note: extract text -> classify -> "
            "validate -> archive the original -> reindex. `overrides` pins frontmatter "
            "you already know (e.g. category/audience) over the classifier's guess; "
            "`subfolder` files it under reference/<subfolder>/ for human browsing. "
            "Review the draft, then publish it with set_note_status."
        ),
    )
    def ingest_file(
        path: str, subfolder: str | None = None, overrides: dict | None = None
    ) -> str:
        return str(ingest_mod.ingest_file(Path(path), subfolder=subfolder, overrides=overrides))

    @target.tool(
        name="ingest_batch",
        description=(
            "Ingest many server-local documents into the knowledge base as drafts, "
            "reindexing once at the end. Unsupported formats are skipped, not fatal. "
            "Returns the created note paths."
        ),
    )
    def ingest_batch(
        paths: list[str], subfolder: str | None = None, overrides: dict | None = None
    ) -> list[str]:
        sources = [Path(p) for p in paths]
        results = ingest_mod_batch.ingest_batch(sources, subfolder=subfolder, overrides=overrides)
        return [str(p) for p in results]

    # --- workflows: build & manage (MCP-only) -----------------------------------------
    # The OWNER's conversational surface for the workflows layer — deliberately NOT in
    # the loop's toolset: designing/activating automation is curation, not ambient work.
    # All still vault-only: no block can send externally (workflows.blocks refuses).

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

    @target.tool(
        name="set_note_status",
        description=(
            "Move a knowledge-base note through its lifecycle: publish a reviewed draft "
            "('published'), retire stale guidance ('archived'), or send a note back to "
            "'draft'. Reference notes only — tasks/entities have their own lifecycles."
        ),
    )
    def set_note_status(path: str, status: str) -> str:
        return str(writes.set_note_status(path, status))


def build_mcp(name: str = "nexus"):
    """Construct a FastMCP server with all vault tools registered.

    The /mcp control plane is bearer-guarded with FastMCP's native StaticTokenVerifier
    (spec §5.3) — the privileged surface exposing read + vault-write tools. A request must
    present `Authorization: Bearer <MCP_TOKEN>`; FastMCP rejects the rest with a
    spec-compliant 401 + resource-metadata. Only when the token is unset (dev) does /mcp
    run open, and we log that loudly.
    """
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

    from nexus.config import settings

    auth = None
    if settings.mcp_token:
        auth = StaticTokenVerifier(
            tokens={settings.mcp_token: {"sub": "owner", "client_id": "claude-desktop"}}
        )
    else:
        logger.warning("MCP_TOKEN unset — /mcp is UNAUTHENTICATED (dev only).")

    mcp = FastMCP(
        name, instructions="Nexus vault tools — read context and record change.", auth=auth
    )
    register_all(mcp)
    return mcp
