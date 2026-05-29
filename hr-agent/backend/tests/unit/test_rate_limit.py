"""Rate-limit guard tests.

We back the Redis dependency with fakeredis-style stub (a dict-based fake
that implements the tiny subset we use) so the test has no external deps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

import pytest


class _FakeRedis:
    """Minimal async Redis stand-in: get/incr/incrby/expire."""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}

    async def get(self, key: str):
        v = self.store.get(key)
        return str(v) if v is not None else None

    async def incr(self, key: str) -> int:
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    async def incrby(self, key: str, amount: int) -> int:
        self.store[key] = int(self.store.get(key, 0)) + amount
        return self.store[key]

    async def expire(self, key: str, ttl: int) -> bool:
        return True


@pytest.fixture
def fake_redis(monkeypatch):
    from src.services import rate_limit

    redis = _FakeRedis()
    monkeypatch.setattr(rate_limit, "get_redis", lambda: redis)
    return redis


@pytest.mark.asyncio
async def test_minute_limit_trips(fake_redis, monkeypatch):
    from src.config import get_settings
    from src.services.rate_limit import RateLimitExceeded, check_before_call

    s = get_settings()
    monkeypatch.setattr(s, "llm_rate_limit_per_minute", 2)

    cid = uuid4()
    await check_before_call(candidate_id=cid, model="openai/gpt-4o-mini")
    await check_before_call(candidate_id=cid, model="openai/gpt-4o-mini")
    with pytest.raises(RateLimitExceeded, match="per-minute"):
        await check_before_call(candidate_id=cid, model="openai/gpt-4o-mini")


@pytest.mark.asyncio
async def test_daily_call_limit_trips(fake_redis, monkeypatch):
    from src.config import get_settings
    from src.services.rate_limit import RateLimitExceeded, check_before_call

    s = get_settings()
    monkeypatch.setattr(s, "llm_rate_limit_per_minute", 1000)
    monkeypatch.setattr(s, "llm_daily_call_limit", 3)

    cid = uuid4()
    for _ in range(3):
        await check_before_call(candidate_id=cid, model="openai/gpt-4o-mini")
    with pytest.raises(RateLimitExceeded, match="daily LLM call cap"):
        await check_before_call(candidate_id=cid, model="openai/gpt-4o-mini")


@pytest.mark.asyncio
async def test_per_candidate_monthly_cap(fake_redis, monkeypatch):
    from src.config import get_settings
    from src.services.rate_limit import RateLimitExceeded, check_before_call

    s = get_settings()
    monkeypatch.setattr(s, "llm_rate_limit_per_minute", 1000)
    monkeypatch.setattr(s, "llm_daily_call_limit", 10000)
    monkeypatch.setattr(s, "llm_per_candidate_monthly_limit", 2)

    cid = uuid4()
    other = uuid4()
    await check_before_call(candidate_id=cid, model="openai/gpt-4o-mini")
    await check_before_call(candidate_id=cid, model="openai/gpt-4o-mini")
    with pytest.raises(RateLimitExceeded, match="per-candidate"):
        await check_before_call(candidate_id=cid, model="openai/gpt-4o-mini")

    # Another candidate is independent -- should still pass.
    await check_before_call(candidate_id=other, model="openai/gpt-4o-mini")


@pytest.mark.asyncio
async def test_record_after_call_updates_counters(fake_redis):
    from src.services.rate_limit import record_after_call, snapshot

    await record_after_call(model="openai/gpt-4o-mini", input_tokens=1000, output_tokens=500)
    await record_after_call(model="openai/gpt-4o", input_tokens=500, output_tokens=200)
    snap = await snapshot()
    assert snap.day_tokens == 1700
    # gpt-4o-mini: 1000 * 0.00015 + 500 * 0.0006 = 0.00015 + 0.0003 = 0.00045 USD
    # gpt-4o:      500 * 0.0025 + 200 * 0.01 = 0.00125 + 0.002 = 0.00325 USD
    # sum ~= 0.0037 USD. Rounding via int cents may yield 0 cents -- expect < 0.01 USD.
    assert snap.day_usd_spent < 0.01


@pytest.mark.asyncio
async def test_redis_failure_fails_open(monkeypatch):
    """If Redis throws, rate limit shouldn't hard-stop the LLM call."""
    from src.services import rate_limit
    from src.services.rate_limit import check_before_call

    class _Broken:
        async def incr(self, *a, **kw):
            raise RuntimeError("redis down")
        async def get(self, *a, **kw):
            raise RuntimeError("redis down")
        async def expire(self, *a, **kw):
            raise RuntimeError("redis down")
        async def incrby(self, *a, **kw):
            raise RuntimeError("redis down")

    monkeypatch.setattr(rate_limit, "get_redis", lambda: _Broken())
    # Should not raise.
    await check_before_call(candidate_id=None, model="openai/gpt-4o-mini")
