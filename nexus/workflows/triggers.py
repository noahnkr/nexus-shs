"""Trigger matching — stimuli in, run instances out.

Wired into `ingress.router.dispatch`: after the normal agent worker is chosen, every
ACTIVE workflow whose trigger matches the stimulus fires a new run instance. Matching is
data-driven off the trigger block + config (no if-ladders over transports, §1.1):

  trigger.webhook  matches source (and kind, if pinned) of any non-cron stimulus
  trigger.cron     matches source == "cron" and kind == config.job
  trigger.manual   never matches ambient stimuli — only run_workflow fires it

The tier passed to each run is the AUTHORITATIVE tier from ingress classify — a workflow
never decides its own trust level (§5.4).
"""

from __future__ import annotations

import logging

from nexus.connectors.ingress.envelope import Stimulus
from nexus.workflows import engine, store
from nexus.workflows.schema import RunState, WorkflowSpec, WorkflowStatus

logger = logging.getLogger("nexus.workflows")


def matches(spec: WorkflowSpec, stimulus: Stimulus) -> bool:
    block, cfg = spec.trigger.block, spec.trigger.config
    if block == "trigger.cron":
        return stimulus.source == "cron" and stimulus.kind == cfg.get("job")
    if block == "trigger.webhook":
        if stimulus.source == "cron" or stimulus.source != cfg.get("source"):
            return False
        return cfg.get("kind") in (None, stimulus.kind)
    return False  # trigger.manual (and unknown trigger blocks) never auto-fire


async def fire_matching(stimulus: Stimulus, tier: str) -> list[RunState]:
    """Start a run for every active workflow matching this stimulus. Never raises —
    workflow failures must not disturb core dispatch."""
    runs: list[RunState] = []
    try:
        active = store.list_workflows(status=WorkflowStatus.active.value)
    except Exception:  # noqa: BLE001 — a broken store must not break ingress
        logger.exception("workflow trigger scan failed")
        return runs
    for spec in active:
        if not matches(spec, stimulus):
            continue
        try:
            run = await engine.start_run(
                spec,
                tier=tier,
                trigger_payload=stimulus.payload,
                trigger_source=stimulus.source,
                trigger_kind=stimulus.kind,
            )
            runs.append(run)
        except Exception:  # noqa: BLE001 — one workflow's failure must not block others
            logger.exception("workflow '%s' failed to run", spec.slug)
    return runs
