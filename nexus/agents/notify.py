"""Owner notification (spec §4.4).

Outbound notification to the OWNER (not an external party) is owner-only, therefore
autonomous. Keep the transport swappable (SMS, email, push) behind this one function; the
loop only ever calls notify(), never a transport directly.

Default transport logs the message (and records it to the event log so the audit trail is
complete). Swap `_send` for SMS/email/push in a fork.
"""

from __future__ import annotations

import logging

from nexus.config import settings

logger = logging.getLogger("nexus.notify")


def notify(message: str, owner: str | None = None) -> None:
    """Send `message` to the owner over the configured transport. Owner-only, autonomous."""
    owner = owner or settings.owner_contact or "owner"
    _send(owner, message)


def _send(owner: str, message: str) -> None:
    """The swappable transport. FORK: replace with SMS/email/push (e.g. Twilio, SES)."""
    logger.info("notify(%s): %s", owner, message)
