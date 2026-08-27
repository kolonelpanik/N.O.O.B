"""Small bounded token-bucket limiter for the single authenticated principal."""

from __future__ import annotations

import asyncio
import time


class TokenBucket:
    def __init__(self, rate: float, burst: float, *, clock=time.monotonic) -> None:
        if rate <= 0 or burst <= 0:
            raise ValueError("rate and burst must be positive")
        self._rate = float(rate)
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._updated = clock()
        self._clock = clock
        self._lock = asyncio.Lock()

    async def allow(self, cost: float = 1.0) -> bool:
        if cost <= 0 or cost > self._capacity:
            return False
        async with self._lock:
            now = self._clock()
            elapsed = max(0.0, now - self._updated)
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._updated = now
            if self._tokens < cost:
                return False
            self._tokens -= cost
            return True
