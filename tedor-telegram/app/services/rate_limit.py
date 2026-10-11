"""In-process token buckets for the location search endpoints.

Two things make this a plain in-process limiter rather than a shared store:

* it is only ever consulted from a single process (the bot or the API, not
  both at once), and
* the limits exist to stop a scripted client burning the geocoding quota, not
  to enforce a security boundary — anything that would need one of those wants
  the auth layer, not this module.

The consequence is deliberate and documented rather than hidden: restarting the
process resets every bucket. Anything that must survive a restart belongs in
the database, not here.

Keys are built by the callers and must never embed a secret or a raw piece of
personal data; ``app.logging_config`` redacts the known identifiers, and the
callers here key on a Telegram user id or a client address only.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

__all__ = [
    "RateLimit",
    "allow",
    "bucket_status",
    "reset",
]


@dataclass(frozen=True)
class RateLimit:
    """The outcome of one check, safe to show to a user."""

    allowed: bool
    remaining: int
    #: Seconds until the next token is available. ``0`` when the call was allowed.
    retry_after: int

    @property
    def message(self) -> str:
        if self.allowed:
            return ""
        if self.retry_after > 60:
            minutes = max(1, round(self.retry_after / 60))
            return f"Too many requests. Please try again in about {minutes} minute(s)."
        return f"Too many requests. Please try again in {self.retry_after} second(s)."


@dataclass
class _Bucket:
    tokens: float
    updated_at: float

    def take(self, limit: int, window: int, now: float) -> tuple[bool, float]:
        """Refill to ``limit``, then spend one token. Returns (allowed, now)."""
        elapsed = max(0.0, now - self.updated_at)
        refill = limit * (min(elapsed, window) / window)
        self.tokens = min(float(limit), self.tokens + refill)
        self.updated_at = now
        if self.tokens < 1.0:
            # Time until the bucket holds one whole token again.
            return False, (1.0 - self.tokens) / limit * window
        self.tokens -= 1.0
        return True, 0.0


#: Buckets keyed by ``(scope, key)``. Bounded so a client that rotates its
#: identifier cannot grow the map without limit; the oldest bucket is evicted.
_MAX_BUCKETS = 4096

_buckets: "OrderedDict[tuple[str, str], _Bucket]" = OrderedDict()
_lock = threading.Lock()


def _prune(now: float, window: int) -> None:
    """Drop buckets that have refilled to full — they carry no information."""
    stale = [
        key
        for key, bucket in _buckets.items()
        if bucket.tokens >= 1.0 and now - bucket.updated_at > window
    ]
    for key in stale:
        del _buckets[key]


def allow(
    scope: str,
    key: str,
    *,
    limit: int,
    window: int,
    now: float | None = None,
) -> RateLimit:
    """Consume one token for ``(scope, key)``.

    ``limit`` is the number of calls permitted per ``window`` seconds. A
    non-positive ``limit`` or ``window`` denies everything rather than raising,
    so a misconfigured deployment fails closed.
    """
    moment = time.monotonic() if now is None else now
    if limit <= 0 or window <= 0:
        return RateLimit(allowed=False, remaining=0, retry_after=window)

    bucket_key = (scope, str(key))
    with _lock:
        _prune(moment, window)
        bucket = _buckets.get(bucket_key)
        if bucket is None:
            # A new caller starts full rather than empty: the first request is
            # always allowed.
            bucket = _Bucket(tokens=float(limit), updated_at=moment)
            _buckets[bucket_key] = bucket
        else:
            _buckets.move_to_end(bucket_key)
        allowed, retry_after = bucket.take(limit, window, moment)
        remaining = max(0, int(bucket.tokens))
        while len(_buckets) > _MAX_BUCKETS:
            _buckets.popitem(last=False)

    return RateLimit(
        allowed=allowed,
        remaining=remaining,
        retry_after=int(retry_after) + 1 if not allowed else 0,
    )


def bucket_status(
    scope: str, key: str, *, limit: int, window: int, now: float | None = None
) -> RateLimit:
    """Report the remaining allowance without consuming a token."""
    moment = time.monotonic() if now is None else now
    with _lock:
        bucket = _buckets.get((scope, str(key)))
        if bucket is None:
            return RateLimit(allowed=True, remaining=limit, retry_after=0)
        elapsed = max(0.0, moment - bucket.updated_at)
        refill = limit * (min(elapsed, window) / window)
        tokens = min(float(limit), bucket.tokens + refill)
    allowed = tokens >= 1.0
    return RateLimit(
        allowed=allowed,
        remaining=max(0, int(tokens)),
        retry_after=0 if allowed else int((1.0 - tokens) / limit * window) + 1,
    )


def reset(scope: str | None = None) -> None:
    """Forget every bucket, or only those in ``scope``. Used by the tests."""
    with _lock:
        if scope is None:
            _buckets.clear()
            return
        for key in [item for item in _buckets if item[0] == scope]:
            del _buckets[key]