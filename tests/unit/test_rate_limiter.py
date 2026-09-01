"""Unit tests for the token-bucket rate limiter."""

from __future__ import annotations

import asyncio

from app.rate_limiter import RateLimiter, TokenBucket


class TestTokenBucket:
    async def test_acquire_within_rate_is_fast(self):
        bucket = TokenBucket(600)  # 10/sec
        start = asyncio.get_event_loop().time()
        for _ in range(3):
            await bucket.acquire()
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed < 0.5

    async def test_acquire_slow_at_low_rate(self):
        bucket = TokenBucket(2)  # 2 per minute => 30s per token
        # capacity is filled (2 tokens), so two acquires are instant
        await bucket.acquire()
        await bucket.acquire()
        assert bucket.tokens < 1.0  # capacity drained


class TestRateLimiter:
    def test_load_rates_and_defaults(self):
        rl = RateLimiter()
        rl.load_rates(None, {"agnes_image:1k": 10, "default": 5})
        assert rl.get_rate("agnes_image:1k") == 10
        assert rl.get_rate("unknown:key") == 5  # falls to default

    def test_load_rates_from_json(self):
        rl = RateLimiter()
        rl.load_rates('{"a:1": 20}', {"a:1": 1, "default": 5})
        assert rl.get_rate("a:1") == 20

    def test_load_rates_invalid_json_ignored(self):
        rl = RateLimiter()
        rl.load_rates("{not json", {"default": 7})
        assert rl.get_rate("anything") == 7

    def test_set_rate(self):
        rl = RateLimiter()
        rl.set_rate("k", 99)
        assert rl.rpm("k") == 99

    async def test_acquire_high_rate_fast(self):
        rl = RateLimiter()
        rl.load_rates(None, {"k": 1000})
        start = asyncio.get_event_loop().time()
        for _ in range(5):
            await rl.acquire("k")
        assert asyncio.get_event_loop().time() - start < 1.0

    async def test_acquire_key_built_lazily(self):
        rl = RateLimiter()
        rl.load_rates(None, {"default": 1000})
        await rl.acquire("new-key")
        assert "new-key" in rl._buckets
