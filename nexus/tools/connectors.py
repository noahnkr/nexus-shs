"""Per-connector MCP tools (⚙ FORK SEAM).

Mirrors the `workflows.blocks` connector seam: a connector opts into the MCP surface by
exposing `tools(target) -> None` in its webhook module (or a dedicated module registered in
`ingress.routes.CONNECTORS`), registering its own tools onto `target`. Most connectors add
nothing here — their reads already flow through the shared vault tools. Use this only for
source-specific capabilities that don't fit the generic vault surface.

Failures are non-fatal: one broken connector must not take down the core tool set.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("nexus.mcp")


def register(target: Any) -> None:
    """Register each connector's optional `tools(target)` seam onto MCP server `target`."""
    try:
        from nexus.connectors.ingress.routes import CONNECTORS
    except Exception:  # noqa: BLE001 — tool set must work even if ingress can't import
        return
    for name, module in CONNECTORS.items():
        register_tools = getattr(module, "tools", None)
        if register_tools is None:
            continue
        try:
            register_tools(target)
        except Exception:  # noqa: BLE001 — one bad connector must not break the tool set
            logger.exception("connector '%s' failed to register MCP tools", name)
            continue
