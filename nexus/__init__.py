"""Nexus — a forkable system of intelligence.

The server package. Layers (spec §2):
  - vault/       KNOWLEDGE LAYER (§3)
  - connectors/  EXTERNAL (§4) + INGRESS (§5) LAYERS
  - agents/      AGENTIC LAYER (§6)
  - tools/       MCP wrappers over the same plain functions (§3.5)
  - ingest/      raw sources -> draft notes (§3.7)
"""

__version__ = "0.1.0"
