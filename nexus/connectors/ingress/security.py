"""Edge security primitives — generic, shared across all connectors.

Custom routes bypass the MCP bearer auth, so each handler enforces its own. These three
primitives are domain-neutral and implemented for real (high-value, low-risk):

  - verify_hmac_sha256: per-source HMAC over the raw body, CONSTANT-TIME compare.
  - within_window: replay-window check for sources that sign a timestamp.
  - SeenCache: TTL idempotency cache on source:external_id to skip vendor re-deliveries.

No secret configured => the caller refuses (503); never trust blindly.
"""

from __future__ import annotations

import hashlib
import hmac
import time


def verify_hmac_sha256(secret: str, raw: bytes, signature: str) -> bool:
    """Constant-time HMAC-SHA256 verification of `raw` against `signature` (hex)."""
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    # Strip common "sha256=" prefixes some vendors prepend.
    provided = signature.split("=", 1)[-1].strip()
    return hmac.compare_digest(expected, provided)


def within_window(signed_ts: float, window_seconds: int = 300, *, now: float | None = None) -> bool:
    """True if `signed_ts` is within ±window of now — rejects replays."""
    now = now if now is not None else time.time()
    return abs(now - signed_ts) <= window_seconds


class SeenCache:
    """In-memory TTL set keyed by `source:external_id` for idempotency.

    A missed dedup at worst RE-PROCESSES an event; combined with "log always" it never
    LOSES one.
    """

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self.ttl = ttl_seconds
        self._seen: dict[str, float] = {}

    def seen(self, key: str, *, now: float | None = None) -> bool:
        """Return True if `key` was seen within TTL; otherwise record it and return False."""
        now = now if now is not None else time.time()
        self._evict(now)
        if key in self._seen:
            return True
        self._seen[key] = now
        return False

    def _evict(self, now: float) -> None:
        dead = [k for k, t in self._seen.items() if now - t > self.ttl]
        for k in dead:
            del self._seen[k]


_default_cache = SeenCache()


def already_seen(source: str, external_id: str | None) -> bool:
    """Convenience over the module-level cache; un-id'd events are never deduped."""
    if not external_id:
        return False
    return _default_cache.seen(f"{source}:{external_id}")
