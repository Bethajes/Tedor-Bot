"""A small TTL cache for provider responses.

It exists for two reasons, both of them about the provider's quota:

* a tutor retyping the same neighbourhood while picking several locations
  should cost one request, not four, and
* a repeated query from the API should be answerable without spending anything.

Entries are keyed by the whole request — query, country, limit, proximity —
so a cached response can only ever be replayed for an identical ask. Expiry is
lazy, so there is no background timer to leak.

Scope: this process only. It is a cost optimisation, not a correctness one; a
miss just means one more provider request.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

__all__ = ["TTLCache", "build_cache_key"]


def build_cache_key(
    text: str, *, limit: int, country_code: str, proximity: tuple[float, float] | None
) -> str:
    """A cache key covering every input that changes the response."""
    bias = f"{proximity[0]:.4f},{proximity[1]:.4f}" if proximity else "-"
    return f"{text.casefold()}|{limit}|{country_code.casefold()}|{bias}"


@dataclass
class TTLCache:
    """A bounded, lazily-expired key/value store."""

    ttl_seconds: float = 86400.0
    max_entries: int = 2048

    def __post_init__(self) -> None:
        self._entries: "OrderedDict[str, tuple[float, object]]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str, *, now: float | None = None) -> object | None:
        """The cached value, or ``None`` when absent or expired."""
        moment = time.monotonic() if now is None else now
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            expires_at, value = entry
            if expires_at <= moment:
                del self._entries[key]
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: str, value: object, *, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        with self._lock:
            self._entries[key] = (moment + self.ttl_seconds, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self.hits = 0
            self.misses = 0

    def __len__(self) -> int:  # pragma: no cover - diagnostics only
        return len(self._entries)