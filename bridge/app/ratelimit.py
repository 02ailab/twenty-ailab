# Tiny in-memory fixed-window rate limiter for the public panel API. The bridge
# Deployment runs a single replica, so process-local state is sufficient; if the
# panel is ever scaled out, move this to Redis. Purpose: blunt id-enumeration of
# /panel/api/contact/{id} (sequential ids + a leaked secret would otherwise let a
# caller scrape PII at full speed).
from __future__ import annotations

import time


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self._limit = limit
        self._window = window_seconds
        self._hits: dict[str, list[float]] = {}
        self._calls_since_purge = 0

    def allow(self, key: str, now: float | None = None) -> bool:
        # limit <= 0 disables the gate entirely (escape hatch via config).
        if self._limit <= 0:
            return True
        t = now if now is not None else time.monotonic()
        cutoff = t - self._window
        bucket = self._hits.get(key)
        if bucket is None:
            bucket = []
            self._hits[key] = bucket
        # Drop timestamps older than the window (list is ascending).
        while bucket and bucket[0] <= cutoff:
            bucket.pop(0)
        self._maybe_purge(cutoff)
        if len(bucket) >= self._limit:
            return False
        bucket.append(t)
        return True

    def _maybe_purge(self, cutoff: float) -> None:
        # Bound memory: a public endpoint sees many distinct IPs, each leaving an
        # empty bucket once its window passes. Sweep them out periodically.
        self._calls_since_purge += 1
        if self._calls_since_purge < 1000:
            return
        self._calls_since_purge = 0
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
            del self._hits[key]
