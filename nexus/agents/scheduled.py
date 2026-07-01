"""The scheduled agent (spec §6.2).

Wakes on a cron tick whose job NAME is its intent (daily-digest, weekly-summary,
vault-health). Job-name-driven prompt, MID model tier. A thin wrapper over run_loop.

Output (stage 5): a digest or summary to the owner + vault writes.
"""

from __future__ import annotations

from nexus.agents.loop import run_loop
from nexus.connectors.ingress.envelope import Stimulus

MODEL = "claude-sonnet-5"

_BASE_PROMPT = """You are the scheduled agent for a Nexus system of intelligence.
You wake on a cron job whose name is your intent. Run the six-stage loop and deliver a
concise result to the owner plus any vault writes. External-facing actions can only be
queued for approval. Record only genuine change.
"""


def _prompt_for(job: str) -> str:
    return f"{_BASE_PROMPT}\nJob (intent): {job}\n[FORK: describe what this job should produce.]"


async def handle(stimulus: Stimulus, tier: str) -> None:
    """Worker entry point (registered in ingress.router for source 'cron')."""
    await run_loop(
        stimulus,
        system_prompt=_prompt_for(stimulus.kind),
        tier=tier,
        model=MODEL,
    )
