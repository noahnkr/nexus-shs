"""Workflows — conversational, connector-backed automation (the Zapier-without-a-frontend
layer).

A workflow is a declarative document: one TRIGGER block plus a graph of STEP blocks, each
step referencing a capability from the block registry (`blocks.py`). The owner describes
the steps in natural language over MCP chat; the builder (`builder.py`) compiles that into
a validated `WorkflowSpec`, and the renderer (`render.py`) returns a Mermaid flowchart the
owner can preview. The loop is iterative: draft -> preview -> revise -> activate.

Layer map:
  schema.py   the contract — WorkflowSpec (definition) and RunState (one instance)
  blocks.py   the capability registry: core vault blocks + per-connector blocks (⚙ seam)
  store.py    validated JSON persistence under vault/system/workflows/ (non-note dir)
  render.py   WorkflowSpec -> Mermaid flowchart (the conversational "preview")
  builder.py  natural language -> WorkflowSpec via schema-constrained LLM output
  engine.py   executes runs; one workflow can have many concurrent RunState instances
  triggers.py matches inbound Stimuli against active workflows (wired into dispatch)

Invariants inherited from the core (do not break):
  - No block may contact an outside party. External-facing steps compile to the
    `vault.create_task` block (a draft for owner approval) — same trust rule as the loop.
  - Agent steps (`agent.run`) delegate to `agents.loop.run_loop`, so they inherit the
    structural trust boundary (no send tool in the toolset).
  - Definitions and run state live under `vault/system/workflows/` — inside the volume,
    outside the note corpus (io.NON_NOTE_DIRS covers `system/`).
  - Every persisted document round-trips through its Pydantic model (`extra="forbid"`),
    mirroring the vault write gate.
"""
