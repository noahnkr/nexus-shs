"""The cron jobs registry.

A cron tick is one of two kinds — this split is the discipline that keeps the model doing
judgment and plain code doing plumbing:

  - Deterministic job — a plain function, NO LLM (e.g. a connector poll-sync). Cheap,
    auditable. May MANUFACTURE stimuli that re-enter classify -> dispatch.
  - Agent job — wake the scheduled agent; the job NAME is its intent (daily-digest,
    weekly-summary, vault-health).

Register deterministic syncs in DETERMINISTIC_JOBS and agent jobs in AGENT_JOBS.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC

from starlette.responses import JSONResponse, Response

from nexus.connectors.goto_connect.sync import run_sync as goto_connect_sync
from nexus.connectors.welcomehome.sync import run_sync as welcomehome_sync

# Deterministic jobs: name -> plain async function (no LLM).
DETERMINISTIC_JOBS: dict[str, Callable[[], Awaitable[None]]] = {
    # WelcomeHome Prospect poll-sync — run every 5-10 min ("respond within the hour").
    "welcomehome-sync": welcomehome_sync,
    # GoTo Connect missed-call gap-fill behind the WS stream — every 15-30 min is plenty.
    "goto-connect-sync": goto_connect_sync,
}

# Agent jobs: the set of job names that wake the scheduled agent (intent == name).
AGENT_JOBS: set[str] = {"daily-digest", "weekly-summary", "vault-health"}


async def run_job(job: str) -> Response:
    """Execute a cron job by name: deterministic function or scheduled-agent wake."""
    if job in DETERMINISTIC_JOBS:
        await DETERMINISTIC_JOBS[job]()
        # A sync may have written entities/events without waking an agent — settle the
        # INDEX.md files at this batch boundary (no-op when nothing was written).
        from nexus.vault.index import regenerate_if_dirty

        regenerate_if_dirty()
        return JSONResponse({"status": "ran", "job": job, "type": "deterministic"})

    if job in AGENT_JOBS:
        from datetime import datetime

        from nexus.connectors.ingress.envelope import Stimulus
        from nexus.connectors.ingress.router import dispatch
        from nexus.connectors.ingress.rules import classify

        stim = Stimulus(source="cron", kind=job, received_at=datetime.now(UTC))
        await dispatch(stim, classify("cron", job))
        return JSONResponse({"status": "ran", "job": job, "type": "agent"})

    return JSONResponse({"error": "unknown job"}, status_code=404)
