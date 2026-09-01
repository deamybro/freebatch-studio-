"""Server-side token-bucket rate limiter.

Rate limits are keyed by ``provider:type`` (e.g. ``agnes_image:1k``,
``agnes_image:2k``, ``agnes_video:default``, ``ai_horde:submit``) and are
configurable at runtime through the settings table. Defaults match the Agnes
free-tier limits documented at the time of writing.

429 handling lives in the providers, but the limiter guarantees we never fire
faster than the configured rate in the first place.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

logger = logging.getLogger("freebatch")


class TokenBucket:
    def __init__(self, rate_per_minute: float):
        self.rate = max(rate_per_minute, 0.0001)
        self.capacity = max(self.rate, 1.0)
        self.tokens = self.capacity
        self.refill_per_sec = self.rate / 60.0
        self.last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.refill_per_sec)
            self.last = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return
            deficit = 1.0 - self.tokens
            wait = deficit / self.refill_per_sec
            self.tokens = 0.0
        if wait > 0:
            await asyncio.sleep(wait)


class RateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}
        self._rates: dict[str, float] = {}

    def load_rates(self, rates_json: str | None, defaults: dict[str, float]) -> None:
        rates = dict(defaults)
        if rates_json:
            try:
                parsed = json.loads(rates_json)
                if isinstance(parsed, dict):
                    rates.update({str(k): float(v) for k, v in parsed.items()})
            except (ValueError, TypeError):
                logger.warning("invalid rate_limits JSON ignored")
        self._rates = rates
        # Drop buckets whose rate changed so they get rebuilt lazily.
        self._buckets.clear()

    def set_rate(self, key: str, rpm: float) -> None:
        self._rates[key] = max(float(rpm), 0.0)
        self._buckets.pop(key, None)

    def get_rate(self, key: str) -> float:
        return self._rates.get(key, self._rates.get("default", 10.0))

    async def acquire(self, key: str) -> None:
        bucket = self._buckets.get(key)
        if bucket is None or abs(bucket.rate - self.get_rate(key)) > 1e-9:
            bucket = TokenBucket(self.get_rate(key))
            self._buckets[key] = bucket
        await bucket.acquire()

    def rpm(self, key: str) -> float:
        return self.get_rate(key)
