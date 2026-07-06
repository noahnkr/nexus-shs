"""MCP wrappers — register_all aggregator (spec §3.5 / §7 step 4, the MCP seam).

Exposes the SAME plain functions (vault.queries reads + writes) as MCP tools, so the
conversational agent in a desktop client and the server-side loop share one source of
truth — no self-MCP network hop, no divergence between "what chat can do" and "what the
ambient agents can do."

CRITICAL: register read + vault-write tools only. No external-send tool (§4.2).
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from nexus import writes
from nexus.agents.toolset import _SPECS
from nexus.ingest import batch as ingest_mod_batch
from nexus.ingest import pipeline as ingest_mod
from nexus.vault import queries


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
    """Construct a FastMCP server with all vault tools registered."""
    from fastmcp import FastMCP

    mcp = FastMCP(name, instructions="Nexus vault tools — read context and record change.")
    register_all(mcp)
    return mcp
