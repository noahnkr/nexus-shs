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
    ("cron", "daily-digest"): AUTONOMOUS,  # owner-only notification
    ("cron", "vault-health"): AUTONOMOUS,
    # WelcomeHome poll-sync (docs/connectors/welcomehome.md) — vault-only, no outbound
    # write to WelcomeHome, so nothing here needs SUPERVISED.
    ("welcomehome", "new_prospect"): LOG_FLAG,  # new prospect entity created + flagged
    ("welcomehome", "stage_changed"): LOG_FLAG,  # prospect stage advanced
    ("welcomehome", "prospect_stale"): LOG_FLAG,  # no contact/movement within window
    # GoTo Connect (docs/connectors/goto-connect.md)
    ("goto_connect", "missed_call"): LOG_FLAG,  # vault-only log + flag, no outbound action
    ("goto_connect", "sms_received"): SUPERVISED,  # any reply is external-facing -> draft
    ("goto_connect", "voicemail_received"): SUPERVISED,  # a callback/reply needs approval
    ("goto_connect", "call_ended"): AUTONOMOUS,  # answered call: logged only, never dispatched
}


def classify(source: str, kind: str) -> str:
    """Return the authoritative tier for (source, kind); unknown -> supervised (fail safe)."""
    return RULES.get((source, kind), SUPERVISED)
