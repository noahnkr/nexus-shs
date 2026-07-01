"""The reactive agent (spec §6.2).

Wakes on an inbound webhook (or a manufactured poll-sync delta). LEAN prompt, minimal
context, CHEAP model tier (it fires often). A thin wrapper over run_loop.

Output (stage 5): log + queue a draft + notify, OR an autonomous act — gated by tier.
"""

from __future__ import annotations

from nexus.agents.loop import run_loop
from nexus.connectors.ingress.envelope import Stimulus

MODEL = "claude-haiku-4-5-20251001"  # cheap; fires often

SYSTEM_PROMPT = """You are the reactive agent for a Nexus system of intelligence.
You wake on a single inbound event. Run the six-stage loop. Resolve any named entity FIRST.
External-facing actions can only be queued as a task for human approval — you cannot send.
Record only genuine change.
[FORK: one paragraph describing THIS business and its salient event types.]
"""


async def handle(stimulus: Stimulus, tier: str) -> None:
    """Worker entry point (registered in ingress.router for non-cron sources)."""
    await run_loop(stimulus, system_prompt=SYSTEM_PROMPT, tier=tier, model=MODEL)
