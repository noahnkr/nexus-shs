"""Dispatch — stimulus -> worker (spec §5.5).

A tiny router maps source -> worker and runs the loop in the BACKGROUND (after the ACK, in
the same vault-owning process; no broker, one volume). The blocking model call is offloaded
to a thread so it doesn't stall the event loop (§5.2).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from nexus.connectors.ingress.envelope import Stimulus

Worker = Callable[[Stimulus, str], Awaitable[None]]  # (stimulus, tier) -> None


def _workers() -> dict[str, Worker]:
    """source -> worker. Imported lazily to avoid import cycles at module load.

    most sources -> reactive agent; cron -> scheduled agent; chat -> conversational.
    """
    from nexus.agents.reactive import handle as reactive_handle
    from nexus.agents.scheduled import handle as scheduled_handle

    return {
        "cron": scheduled_handle,
        # default (any other source) -> reactive_handle, see dispatch()
        "__default__": reactive_handle,
    }


async def dispatch(stimulus: Stimulus, tier: str) -> None:
    """Route to the right worker and run its loop. Called as a background task post-ACK."""
    workers = _workers()
    worker = workers.get(stimulus.source, workers["__default__"])
    await worker(stimulus, tier)
