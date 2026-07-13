"""The loop's tool surface.

Assembles the tools the loop may call. The same plain functions back both these specs and
the MCP wrappers (tools/__init__.py) — one source of truth, no self-MCP hop.

READ tools (stage 3, gather): the vault queries + each connector's READ client methods.
WRITE tools (stage 6, record): the vault write surface.

CRITICAL: DO NOT add send/write-external tools. External actions stay
external-facing and can only become a create_task draft. The trust boundary is the ABSENCE
of the capability here.

When adding a connector, append its READ client methods as tool specs below.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nexus.connectors.goto_connect.client import goto_get_voicemail, goto_lookup_history
from nexus.vault import queries
from nexus.writes import append_log, append_memory, create_task, update_entity


def read_tools() -> dict[str, Callable[..., Any]]:
    """Read-only tools used in stage 3 (gather)."""
    return {
        "search_reference": queries.search_reference,
        "get_note": queries.get_note,
        "get_entity": queries.get_entity,
        "list_entities": queries.list_entities,
        "list_reference": queries.list_reference,
        "search_logs": queries.search_logs,
        "list_open_tasks": queries.list_open_tasks,
        # GoTo Connect reads (docs/connectors/goto-connect.md) — read-only, never a send.
        "goto_lookup_history": goto_lookup_history,
        "goto_get_voicemail": goto_get_voicemail,
    }


def write_tools() -> dict[str, Callable[..., Any]]:
    """Write tools used in stage 6 (record). NOTE: no external-send tool, by design."""
    return {
        "append_log": append_log,
        "update_entity": update_entity,
        "create_task": create_task,  # the only path for external-facing actions
        "append_memory": append_memory,
    }


def all_tools() -> dict[str, Callable[..., Any]]:
    return {**read_tools(), **write_tools()}


# Name -> (description, input JSON schema). Descriptions are load-bearing: routing emerges
# from the model reading them against the request. NOTE the absence of any
# external-send tool — the trust boundary is structural.
_SPECS: dict[str, tuple[str, dict]] = {
    "search_reference": (
        "Search stable, authored business facts — pricing, policy, voice, how-to "
        "(the reference family). Use for 'what is our…/how do we…' questions.",
        {
            "type": "object",
            "properties": {"query": {"type": "string"}, "k": {"type": "integer", "default": 8}},
            "required": ["query"],
        },
    ),
    "get_note": (
        "Fetch one note's FULL content (frontmatter + body) by the `path` from a search "
        "hit. Use after search_reference/search_logs to read and quote the source text; "
        "the returned source_ref cites the archived original.",
        {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    ),
    "get_entity": (
        "Resolve a name, phone number, or email to one tracked thing's CURRENT state. "
        "Call this FIRST for any person/org-specific request (entity-first). Returns null "
        "if not found.",
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    ),
    "list_entities": (
        "Filter tracked things by kind/status — a pure metadata query (no search). Use for "
        "'list all X that are Y'.",
        {
            "type": "object",
            "properties": {"kind": {"type": "string"}, "status": {"type": "string"}},
        },
    ),
    "list_reference": (
        "Filter knowledge-base notes by category/status/audience — a pure metadata query "
        "(no search). Use to survey what reference material exists, e.g. status='draft' "
        "for ingested notes awaiting review.",
        {
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "status": {"type": "string"},
                "audience": {"type": "string"},
            },
        },
    ),
    "search_logs": (
        "Search the event log for what happened and when; optionally date-scoped (ISO "
        "dates). Use for 'when did we…/what happened with…'.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "since": {"type": "string"},
                "until": {"type": "string"},
            },
            "required": ["query"],
        },
    ),
    "list_open_tasks": (
        "List items awaiting human approval/decision (the queue). Use for 'what's "
        "outstanding/pending'.",
        {"type": "object", "properties": {}},
    ),
    "append_log": (
        "Record that a real event occurred (vault-only, autonomous). Stage 6 only.",
        {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
    ),
    "update_entity": (
        "Merge a state change into a tracked thing's frontmatter (vault-only, autonomous). "
        "Stage 6 only.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "kind": {"type": "string"},
                "changes": {"type": "object"},
            },
            "required": ["name", "kind", "changes"],
        },
    ),
    "create_task": (
        "Queue something that needs a human decision. THE ONLY way to handle an "
        "external-facing action: include channel + recipient + the drafted body so "
        "approval is one-click. Stage 6.",
        {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "channel": {"type": "string"},
                "recipient": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["action"],
        },
    ),
    "append_memory": (
        "Record a durable cross-cutting fact worth remembering (vault-only, autonomous).",
        {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]},
    ),
    "goto_lookup_history": (
        "Recent call + SMS history with a phone number from the GoTo Connect phone "
        "system. Use when a stimulus carries a phone number, to see prior contact before "
        "matching it to a prospect (get_entity) and drafting an informed reply.",
        {
            "type": "object",
            "properties": {"phone": {"type": "string"}, "days": {"type": "integer", "default": 14}},
            "required": ["phone"],
        },
    ),
    "goto_get_voicemail": (
        "Fetch one GoTo voicemail's metadata and transcription (when available) by the "
        "voicemail_id from a voicemail_received stimulus. Read-only.",
        {
            "type": "object",
            "properties": {"voicemail_id": {"type": "string"}},
            "required": ["voicemail_id"],
        },
    ),
}


def anthropic_tool_specs() -> list[dict]:
    """Translate the tool registry into Messages-API tool specs (name/description/schema).

    Descriptions are load-bearing: the routing emerges from the model reading them.
    """
    available = all_tools()
    return [
        {"name": name, "description": desc, "input_schema": schema}
        for name, (desc, schema) in _SPECS.items()
        if name in available
    ]
