"""MCP wrappers — register_all aggregator (spec §3.5 / §7 step 4, the MCP seam).

Exposes the SAME plain functions (vault.queries reads + writes) as MCP tools, so the
conversational agent in a desktop client and the server-side loop share one source of
truth — no self-MCP network hop, no divergence between "what chat can do" and "what the
ambient agents can do."

The surface is split by usage into sibling modules, each exposing `register(target)`:

  - `vault`          — read + vault-write tools (shared with the loop's toolset)
  - `knowledge_base` — MCP-only KB curation (`ingest_file` · `ingest_batch` · `set_note_status`)
  - `workflows`      — MCP-only workflow build & manage tools
  - `connectors`     — ⚙ per-connector `tools()` seam (fork seam)

CRITICAL: register read + vault-write tools only. No external-send tool (§4.2).
"""

from __future__ import annotations

import logging
from typing import Any

from nexus.tools import connectors as connector_tools
from nexus.tools import knowledge_base, vault, workflows

logger = logging.getLogger("nexus.mcp")


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

    vault.register(target)
    knowledge_base.register(target)
    workflows.register(target)
    connector_tools.register(target)


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
