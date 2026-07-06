# Workflows — conversational automation on top of the agent architecture

Zapier-shaped, but with no frontend: the owner designs and manages workflows entirely in
conversation (over the MCP surface), and the flowchart "preview" is a Mermaid diagram
rendered inline by the chat client.

## Concepts

| Term | Meaning |
|---|---|
| **Block** | One capability from the catalog: a *trigger* (starts a run), a *condition* (branches), or an *action* (does work). Core blocks wrap the vault write surface, owner notification, and the agent loop; **connectors contribute their own** (⚙ seam below). |
| **Workflow** | A saved definition: one trigger + a graph of steps, each referencing a block. Lifecycle: `draft → active → paused/archived`. |
| **Run** | One *instance* of a workflow. A workflow can have **many concurrent runs**; each has its own id, per-step audit trail, and file on disk. |

## The build loop (iterative, consistent, repeatable)

```
describe in prose ──> create_workflow ──> Mermaid preview
        ▲                                     │
        └───── revise_workflow (new version, back to draft) ◄──┘
                                              │ looks right
                              set_workflow_status(slug, "active")
```

- `create_workflow(request)` — one schema-constrained LLM call compiles prose into a
  `WorkflowSpec`. The model is *forced* through a tool whose input schema is the spec's
  JSON schema, then the graph and every block reference are validated deterministically
  (with automatic feedback retries) — that is what makes compilation repeatable.
- `revise_workflow(slug, instruction)` — recompiles the saved spec with your change,
  bumps the version, and drops it back to `draft` so edits are always re-approved.
- `set_workflow_status(slug, "active")` — re-validates all block refs, then goes live.

## Managing what exists

- `list_workflows(status?)` — every workflow, its status, and **how many instances are
  running right now**.
- `list_workflow_runs(workflow?, status?)` — `status="running"` answers "what is mid-run,
  and on which step?". Run state is persisted after *every* step, so a crash leaves a
  visible `running` record at the exact step it died on.
- `get_workflow_run(run_id)` / `cancel_workflow_run(run_id)` — drill into one instance's
  per-step results, or clear a stuck/crashed one.
- `run_workflow(slug, payload?)` — fire one instance manually (also how
  `trigger.manual` workflows run).
- `list_workflow_blocks()` — the catalog available to the builder.

## How runs start

`ingress.router.dispatch` — after the normal agent worker — matches every **active**
workflow's trigger against the stimulus: `trigger.webhook {source, kind?}`,
`trigger.cron {job}`, `trigger.manual` (explicit only). The run inherits the
**authoritative risk tier** from ingress classify; a workflow never decides its own
trust level.

## Trust (unchanged, structural)

No block can contact an outside party — the registry **refuses** any block flagged
`external_send`. External-facing steps compile to `vault.create_task` (a drafted,
one-click-approvable task), and `agent.run` steps execute through `agents/loop.py`,
whose toolset has no send tool. Same boundary, same mechanism.

## ⚙ Adding connector blocks (fork seam)

In a connector's webhook module (already registered in `ingress.routes.CONNECTORS`):

```python
from nexus.workflows.blocks import Block, BlockKind

def blocks() -> list[Block]:
    return [
        Block(
            name="example.get_record",
            kind=BlockKind.action,
            connector=NAME,
            description="Fetch one record from Example by id.",   # load-bearing
            config_schema={"type": "object", "properties": {"id": {"type": "string"}},
                           "required": ["id"]},
            fn=lambda config, ctx: ExampleClient().get_record(config["id"]),
        ),
    ]
```

Connector blocks must be **reads or draft-producers** — the same rule as the loop's
toolset. The catalog picks them up automatically; the builder can compose them the next
time it compiles.

## Storage

`vault/system/workflows/<slug>.json` (definitions) and
`vault/system/workflows/runs/<run_id>.json` (instances) — inside the one volume, outside
the note corpus (`system/` is in `NON_NOTE_DIRS`). Every read and write round-trips
through the Pydantic models (`extra="forbid"`), mirroring the vault write gate.

## Scaffold boundaries (deliberate, for now)

- Step configs support simple `{{trigger.payload.x}}` / `{{steps.<id>.output}}` refs —
  no expressions, loops, or fan-out.
- Runs execute inline in the dispatch background task; no scheduler/wait-states
  (delays, human-approval gates mid-run) yet — approval today is a `create_task` step.
- `cancel_workflow_run` is a cooperative state flag, not preemption.
- `list_runs` walks all run files; prune or paginate when volume demands it.
