"""Deterministic risk classification (spec §5.4) — FORK SEAM (§7 step 2).

The moment a stimulus is parsed, its TIER is set by this static table — NEVER by an LLM
(§1.8). The tier is passed to the agent as authoritative context; the model never decides
its own trust level. Unknown pairs FAIL SAFE to supervised.

This is your domain's risk policy: default everything to supervised and relax only what
you've proven safe (the trust ratchet, §6.4).

Tier semantics map to the one trust rule (§1.7):
  - SUPERVISED  : external-facing -> human approval (becomes a create_task draft)
  - LOG_FLAG    : vault-only state update + a flag for attention
  - AUTONOMOUS  : owner-only notification / vault-only, runs without approval
"""

from __future__ import annotations

SUPERVISED = "supervised"
LOG_FLAG = "log_flag"
AUTONOMOUS = "autonomous"

# FORK: one row per (source, kind) you care about.
RULES: dict[tuple[str, str], str] = {
    ("example", "new_record"): SUPERVISED,  # external-facing -> human approval
    ("example", "record_changed"): LOG_FLAG,  # vault-only state update + flag
    ("cron", "daily-digest"): AUTONOMOUS,  # owner-only notification
    ("cron", "vault-health"): AUTONOMOUS,
}


def classify(source: str, kind: str) -> str:
    """Return the authoritative tier for (source, kind); unknown -> supervised (fail safe)."""
    return RULES.get((source, kind), SUPERVISED)
