"""The universal envelope (spec §5.1).

A chat message, a signed webhook, a poll-sync delta, and a cron tick all become this one
object — so nothing downstream branches on origin (§1.1). Downstream code branches on
source/kind AS DATA (lookup tables), never with if-ladders over transports.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class Stimulus(BaseModel):
    source: str  # "example" | "phone" | "cron" | "chat" | ...
    kind: str  # "new_record" | "voicemail" | "daily-digest" | ...
    received_at: datetime
    external_id: str | None = None  # vendor delivery/event id — the idempotency key
    payload: dict = {}  # parsed, source-specific body
    raw: bytes | None = None  # raw bytes, retained for audit
