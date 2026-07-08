"""Knowledge-base curation MCP tools (MCP-only, spec §3.7) — the OWNER's surface.

Deliberately NOT in the loop's toolset: ingest takes server-local file paths (ambient
stimuli never carry those), and publication is the human review step the ingest contract
promises — the ambient agents must not publish what ingest drafted. Still vault-only
writes: no external send.

Tools: `ingest_file` · `ingest_batch` · `set_note_status`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nexus import writes
from nexus.ingest import batch as ingest_mod_batch
from nexus.ingest import pipeline as ingest_mod


def register(target: Any) -> None:
    """Register the knowledge-base curation tools onto MCP server `target`."""

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
