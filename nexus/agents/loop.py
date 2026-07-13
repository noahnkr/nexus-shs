"""The six-stage loop (spec §6.1) — the cognitive engine shared by all three agents.

A standard Messages-API tool-use loop, but the PRESCRIBED parts are owned explicitly in
the system prompt and tool design, not left to the model's discretion:

  1 Receive : what arrived, about what? (first understanding of intent + subject)
  2 Plan    : what must I know? entity-first — person-specific requests resolve the entity
              before anything else.
  3 Gather  : pull context in dependency order (identity first). Sufficiency check ↺: enough
              to act? if no and search can fix it, loop; if no and only a human can (genuinely
              ambiguous AND consequential), jump to stage 5 and ask.
  4 Decide  : the interpretive core — choose the action, read off its consequence tier.
  5 Deliver : reply / queue a draft / notify / act — gated by the trust rule.
  6 Record  : the change-test — real event -> append_log; state changed -> update_entity;
              needs a human -> create_task; durable fact -> append_memory; nothing -> write
              nothing. Reads never write.

Implementation notes carried into a fork (§6.1):
  - the system prompt + tool specs are a large, byte-stable prefix -> PROMPT-CACHE it;
  - REINDEX ONCE after the loop, since writes during the loop changed the corpus.

The trust gate is STRUCTURAL (§6.3): external-facing actions cannot send — the loop's
toolset contains no send tool, so external-facing can only produce a create_task draft.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import StrEnum
from pathlib import Path

from nexus.agents.toolset import all_tools, anthropic_tool_specs
from nexus.config import settings
from nexus.connectors.ingress.envelope import Stimulus

MAX_TURNS = 12

# The domain-neutral preamble (the prescribed loop). Forks append a business paragraph via
# the agent system_prompt; this part never changes (§7 "what you do not touch").
_LOOP_PREAMBLE = """\
You run a fixed six-stage loop: receive, plan, gather, decide, deliver, record.
- Resolve any named person/org with get_entity BEFORE other lookups (entity-first).
- Gather only what you need; stop when you have enough to act.
- The risk TIER below is authoritative — you do not decide your own trust level.
- TRUST RULE: anything that would reach an outside party is external-facing. You have NO
  tool that contacts outsiders. For external-facing actions, your only move is create_task
  with channel + recipient + a drafted body, then stop.
- RECORD only genuine change: a real event -> append_log; a changed state -> update_entity;
  a needed human decision -> create_task; a durable fact -> append_memory; nothing changed
  -> write nothing. Reads never write.
"""


class Consequence(StrEnum):
    vault_only = "vault-only"  # autonomous
    owner_only = "owner-only"  # autonomous
    external_facing = "external-facing"  # -> create_task draft + notify ONLY


def _to_text(value) -> str:
    """Serialize a tool's return value to text for a tool_result block."""
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    elif isinstance(value, list):
        value = [asdict(v) if is_dataclass(v) and not isinstance(v, type) else v for v in value]
    elif isinstance(value, Path):
        value = str(value)
    try:
        return json.dumps(value, default=str)
    except TypeError:
        return str(value)


async def run_loop(
    stimulus: Stimulus,
    *,
    system_prompt: str,
    tier: str,
    model: str,
) -> dict:
    """Run the six-stage tool-use loop for one stimulus; return a result summary.

    `tier` is authoritative context from ingress (§5.4) — the model never decides its own
    trust level. The toolset (agents.toolset) deliberately excludes any external-send tool,
    so the trust boundary holds structurally regardless of what the model decides.
    """
    import anthropic

    from nexus.agents.context import load_context

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    tools = anthropic_tool_specs()
    funcs = all_tools()

    # Byte-stable prefix -> prompt-cache it (§6.1). cache_control on the final system block.
    # Order: agent role prompt -> fixed loop rules -> always-on context (SOUL/USER).
    system_text = system_prompt + "\n\n" + _LOOP_PREAMBLE
    always_on = load_context()
    if always_on:
        system_text += "\n\n# Always-on context (persona, owner, house rules)\n" + always_on
    system = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]
    stim_view = {
        "source": stimulus.source,
        "kind": stimulus.kind,
        "external_id": stimulus.external_id,
        "payload": stimulus.payload,
    }
    user_text = f"AUTHORITATIVE TIER: {tier}\nSTIMULUS:\n{json.dumps(stim_view, default=str)}"
    messages: list[dict] = [{"role": "user", "content": user_text}]

    result: dict = {"tool_calls": [], "writes": [], "stop_reason": None, "text": ""}

    for _ in range(MAX_TURNS):
        resp = await client.messages.create(
            model=model, max_tokens=2048, system=system, tools=tools, messages=messages
        )
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            result["text"] = "".join(b.text for b in resp.content if b.type == "text")
            result["stop_reason"] = resp.stop_reason
            break

        tool_results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            result["tool_calls"].append(block.name)
            fn = funcs.get(block.name)
            try:
                out = fn(**block.input) if fn else {"error": f"unknown tool {block.name}"}
                if block.name in {"append_log", "update_entity", "create_task", "append_memory"}:
                    result["writes"].append(block.name)
                content, is_err = _to_text(out), False
            except Exception as exc:  # noqa: BLE001 — surface tool errors back to the model
                content, is_err = f"error: {exc}", True
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                    "is_error": is_err,
                }
            )
        messages.append({"role": "user", "content": tool_results})

    # Writes during the loop dirtied the indexes via the gate; settle them once here
    # (§6.1). Search rebuilds lazily on its next query; INDEX.md regenerates now.
    from nexus.vault.index import regenerate_if_dirty

    regenerate_if_dirty()
    return result
