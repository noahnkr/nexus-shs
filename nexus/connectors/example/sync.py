"""Deterministic poll-sync for `example` (spec §4.3).

For pull-shaped sources without reliable webhooks. The SAME downstream pipeline
(classify -> log -> dispatch) handles push and pull. This sync:

  - reconciles bulk exports into vault state with NO LLM — cheap, idempotent, and the safe
    place to keep any data-sensitivity boundary;
  - upserts by `source_ref` (create-or-update keyed by external id);
  - stores a per-table HIGH-WATER MARK on the volume so incremental re-polls only fetch
    what changed;
  - MANUFACTURES the few high-salience deltas (a genuinely new record, a notable state
    change) back into the same classify -> dispatch path a webhook would have used — so a
    pull source still wakes the reactive agent without a model call per reconciled row.

This is the single most reusable connector pattern for any CRM/ERP that lacks webhooks.
"""

from __future__ import annotations


async def run_sync() -> None:
    """Reconcile, upsert by source_ref, advance the high-water mark, manufacture deltas.

    Register this in jobs.DETERMINISTIC_JOBS to run it on a clock (§5.5).
    """
    raise NotImplementedError(
        "§4.3 — read high-water mark from the volume; pull changed rows via ExampleClient; "
        "upsert entities by source_ref through nexus.writes.update_entity; for each "
        "high-salience delta, build a Stimulus and call ingress.router.dispatch(); "
        "persist the new high-water mark. NO LLM in this path."
    )
