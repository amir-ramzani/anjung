"""Per-task token bucket (LLD 15.2 first cut). Documented limitation: per task, not global;
WAF rate rules at the edge are the real flood control."""

from __future__ import annotations

import threading
import time

from app.core.errors import RateLimited


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self._rate = per_minute / 60.0
        self._cap = float(per_minute)
        self._state: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._state.get(key, (self._cap, now))
            tokens = min(self._cap, tokens + (now - last) * self._rate)
            if tokens < 1:
                self._state[key] = (tokens, now)
                raise RateLimited(headers={"Retry-After": str(int((1 - tokens) / self._rate) + 1)})
            self._state[key] = (tokens - 1, now)
