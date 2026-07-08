"""Vault read/write MCP tools (spec §3.5) — the shared plain functions.

Exposes the SAME plain functions as the loop (`vault.queries` reads + `writes`), using the
same descriptions as the loop's tool specs (`_SPECS`) so chat and the ambient loop agree.

Reads: `search_reference` · `get_note` · `get_entity` · `list_entities` · `list_reference` ·
`search_logs` · `list_open_tasks`. Writes (all gated, no external send): `append_log` ·
`update_entity` · `create_task` · `append_memory`.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from nexus import writes
from nexus.agents.toolset import _SPECS
from nexus.vault import queries


def _hits(items) -> list[dict]:
    return [asdict(h) for h in items]


def register(target: Any) -> None:
    """Register the vault read/write tools onto MCP server `target`."""

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
