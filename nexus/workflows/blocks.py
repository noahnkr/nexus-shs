"""The block registry — the capability catalog workflows compose (⚙ FORK SEAM).

A Block is one trigger/condition/action capability with a name, a description the builder
LLM routes on (descriptions are load-bearing, §1.5), a config JSON schema, and — for
conditions/actions — the plain function that executes it. Core blocks wrap the existing
vault write surface and the agent loop; connectors contribute their own by exposing a
module-level `blocks()` function (registered here, mirroring `ingress.routes.CONNECTORS`).

TRUST INVARIANT (§4.2 / §6.3, unchanged): no block contacts an outside party. The registry
structurally REFUSES any block flagged `external_send=True` — an external-facing step can
only be the `vault.create_task` block, which drafts for owner approval. Connector blocks
must be READS or draft-producers, exactly like the loop's toolset.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class BlockKind(StrEnum):
    trigger = "trigger"  # starts a run (matched by triggers.py; no fn)
    condition = "condition"  # fn returns truthy/falsy -> on_success / on_failure branch
    action = "action"  # fn does the work (vault write, connector read, agent step)


@dataclass(frozen=True)
class RunContext:
    """What a block fn sees beyond its own config: the run's live context."""

    tier: str
    trigger_payload: dict[str, Any]
    trigger_source: str
    trigger_kind: str
    step_outputs: dict[str, Any]  # step id -> prior output (for {{steps.x.output}} refs)


# Block fns are uniform: (resolved config, run context) -> JSON-safe-ish value.
BlockFn = Callable[[dict[str, Any], RunContext], Any]


@dataclass(frozen=True)
class Block:
    name: str  # "namespace.verb", e.g. "vault.create_task"
    kind: BlockKind
    connector: str  # "core" | connector NAME — where the capability comes from
    description: str  # load-bearing: the builder LLM routes on this
    config_schema: dict[str, Any] = field(default_factory=dict)
    fn: BlockFn | None = None  # None for triggers (matched, not executed)
    external_send: bool = False  # declared intent; True is REJECTED at registration


_REGISTRY: dict[str, Block] = {}
_CONNECTOR_BLOCKS_LOADED = False


def register(block: Block) -> None:
    """Add a block. Refuses external-send capability — the trust boundary is structural."""
    if block.external_send:
        raise ValueError(
            f"block '{block.name}' declares external_send — forbidden (§4.2). "
            "External-facing steps must compose vault.create_task instead."
        )
    if block.kind != BlockKind.trigger and block.fn is None:
        raise ValueError(f"block '{block.name}' is a {block.kind} but has no fn")
    _REGISTRY[block.name] = block


def get(name: str) -> Block | None:
    return registry().get(name)


def registry() -> dict[str, Block]:
    """The full catalog: core blocks + lazily-loaded connector blocks."""
    global _CONNECTOR_BLOCKS_LOADED
    if not _CONNECTOR_BLOCKS_LOADED:
        _CONNECTOR_BLOCKS_LOADED = True
        _load_connector_blocks()
    return _REGISTRY


def catalog() -> list[dict[str, Any]]:
    """JSON-safe listing (for the MCP tool and the builder prompt)."""
    return [
        {
            "name": b.name,
            "kind": b.kind.value,
            "connector": b.connector,
            "description": b.description,
            "config_schema": b.config_schema,
        }
        for b in sorted(registry().values(), key=lambda b: (b.kind, b.name))
    ]


def _load_connector_blocks() -> None:
    """⚙ FORK SEAM: pull blocks from registered connectors.

    A connector opts in by exposing `blocks() -> list[Block]` in its webhook module (or a
    dedicated `blocks` module registered in CONNECTORS). Failures are non-fatal — a broken
    connector must not take down the core catalog.
    """
    try:
        from nexus.connectors.ingress.routes import CONNECTORS
    except Exception:  # noqa: BLE001 — registry must work even if ingress can't import
        return
    for module in CONNECTORS.values():
        maker = getattr(module, "blocks", None)
        if maker is None:
            continue
        try:
            for block in maker():
                register(block)
        except Exception:  # noqa: BLE001 — one bad connector must not break the catalog
            continue


# --- context path lookup (shared by conditions and {{...}} templating) ----------------


def lookup_path(path: str, ctx: RunContext) -> Any:
    """Resolve a dotted ref against the run context.

    Roots: `trigger.payload.*`, `trigger.source`, `trigger.kind`, `steps.<id>.output[.*]`.
    Returns None if any segment is missing (conditions treat that as falsy).
    """
    parts = path.strip().split(".")
    value: Any
    if parts[0] == "trigger":
        value = {
            "payload": ctx.trigger_payload,
            "source": ctx.trigger_source,
            "kind": ctx.trigger_kind,
        }
        parts = parts[1:]
    elif parts[0] == "steps":
        if len(parts) < 2:
            return None
        value = {"output": ctx.step_outputs.get(parts[1])}
        parts = parts[2:]
    else:
        return None
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
        else:
            value = getattr(value, part, None)
        if value is None:
            return None
    return value


# --- core blocks -----------------------------------------------------------------------


def _vault_append_log(config: dict[str, Any], ctx: RunContext) -> str:
    from nexus.writes import append_log

    return str(append_log(config["summary"]))


def _vault_update_entity(config: dict[str, Any], ctx: RunContext) -> str:
    from nexus.writes import update_entity

    return str(update_entity(config["name"], config["kind"], config.get("changes", {})))


def _vault_create_task(config: dict[str, Any], ctx: RunContext) -> str:
    from nexus.writes import create_task

    return str(
        create_task(
            config["action"],
            channel=config.get("channel"),
            recipient=config.get("recipient"),
            body=config.get("body"),
        )
    )


def _vault_append_memory(config: dict[str, Any], ctx: RunContext) -> str:
    from nexus.writes import append_memory

    return str(append_memory(config["fact"]))


def _notify_owner(config: dict[str, Any], ctx: RunContext) -> Any:
    from nexus.agents.notify import notify

    return notify(config["message"])


def _payload_match(config: dict[str, Any], ctx: RunContext) -> bool:
    """Deterministic branch: compare a context path against an expected value."""
    actual = lookup_path(config["path"], ctx)
    if "equals" in config:
        return actual == config["equals"]
    if "contains" in config:
        return config["contains"] in (actual or "")
    return bool(actual)  # bare existence/truthiness check


async def _agent_run(config: dict[str, Any], ctx: RunContext) -> dict:
    """The judgment step: one six-stage loop pass over the run's context.

    Inherits the loop's structural trust boundary — its toolset has no send tool, so an
    external-facing decision inside a workflow still lands as a create_task draft.
    """
    from nexus.agents.loop import run_loop
    from nexus.agents.scheduled import MODEL
    from nexus.connectors.ingress.envelope import Stimulus
    from nexus.workflows.schema import utcnow

    stimulus = Stimulus(
        source="workflow",
        kind="agent_step",
        received_at=utcnow(),
        payload={"instructions": config["prompt"], "trigger": ctx.trigger_payload},
    )
    system_prompt = (
        "You are one step inside a predefined workflow for a Nexus system of intelligence. "
        "The stimulus payload carries this step's instructions plus the trigger context. "
        "Do exactly what the instructions ask via the six-stage loop, then stop."
    )
    result = await run_loop(
        stimulus, system_prompt=system_prompt, tier=ctx.tier, model=config.get("model", MODEL)
    )
    return {"text": result.get("text", ""), "writes": result.get("writes", [])}


_CORE_BLOCKS: list[Block] = [
    Block(
        name="trigger.webhook",
        kind=BlockKind.trigger,
        connector="core",
        description=(
            "Start the workflow when a webhook stimulus arrives. Config matches the "
            "envelope: {source, kind} (kind optional = any kind from that source)."
        ),
        config_schema={
            "type": "object",
            "properties": {"source": {"type": "string"}, "kind": {"type": "string"}},
            "required": ["source"],
        },
    ),
    Block(
        name="trigger.cron",
        kind=BlockKind.trigger,
        connector="core",
        description="Start the workflow on a cron tick. Config: {job} — the /cron/{job} name.",
        config_schema={
            "type": "object",
            "properties": {"job": {"type": "string"}},
            "required": ["job"],
        },
    ),
    Block(
        name="trigger.manual",
        kind=BlockKind.trigger,
        connector="core",
        description="Start only when the owner runs it explicitly (run_workflow). No config.",
        config_schema={"type": "object", "properties": {}},
    ),
    Block(
        name="core.payload_match",
        kind=BlockKind.condition,
        connector="core",
        description=(
            "Deterministic branch (no LLM). Config: {path, equals?|contains?} — path is a "
            "context ref like 'trigger.payload.type' or 'steps.<id>.output'. True -> "
            "on_success branch, false -> on_failure branch."
        ),
        config_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "equals": {},
                "contains": {"type": "string"},
            },
            "required": ["path"],
        },
        fn=_payload_match,
    ),
    Block(
        name="vault.append_log",
        kind=BlockKind.action,
        connector="core",
        description="Record that a real event occurred (append to today's event note).",
        config_schema={
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
        fn=_vault_append_log,
    ),
    Block(
        name="vault.update_entity",
        kind=BlockKind.action,
        connector="core",
        description="Merge a state change into a tracked entity's frontmatter.",
        config_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "kind": {"type": "string"},
                "changes": {"type": "object"},
            },
            "required": ["name", "kind"],
        },
        fn=_vault_update_entity,
    ),
    Block(
        name="vault.create_task",
        kind=BlockKind.action,
        connector="core",
        description=(
            "Queue a human decision — THE ONLY way a workflow handles anything "
            "external-facing: include channel + recipient + drafted body so approval "
            "is one-click. Nothing is sent."
        ),
        config_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "channel": {"type": "string"},
                "recipient": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["action"],
        },
        fn=_vault_create_task,
    ),
    Block(
        name="vault.append_memory",
        kind=BlockKind.action,
        connector="core",
        description="Record a durable cross-cutting fact worth remembering.",
        config_schema={
            "type": "object",
            "properties": {"fact": {"type": "string"}},
            "required": ["fact"],
        },
        fn=_vault_append_memory,
    ),
    Block(
        name="core.notify_owner",
        kind=BlockKind.action,
        connector="core",
        description="Send the OWNER a notification (never an outside party).",
        config_schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
        fn=_notify_owner,
    ),
    Block(
        name="agent.run",
        kind=BlockKind.action,
        connector="core",
        description=(
            "The judgment step: run one six-stage agent loop with the given prompt over "
            "the trigger context. Use when a step needs interpretation, drafting, or "
            "vault research — not for deterministic plumbing."
        ),
        config_schema={
            "type": "object",
            "properties": {"prompt": {"type": "string"}, "model": {"type": "string"}},
            "required": ["prompt"],
        },
        fn=_agent_run,
    ),
]

for _b in _CORE_BLOCKS:
    register(_b)


async def call_block(block: Block, config: dict[str, Any], ctx: RunContext) -> Any:
    """Invoke a block fn, sync or async, uniformly."""
    assert block.fn is not None
    out = block.fn(config, ctx)
    if inspect.isawaitable(out):
        out = await out
    return out
