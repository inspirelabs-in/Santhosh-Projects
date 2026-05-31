"""Token-bucket rate limiter — burst and drain behavior."""
from __future__ import annotations

import asyncio
import time

import pytest

from grabon_intel.tools.base import RateLimiter


@pytest.mark.asyncio
async def test_burst_then_throttle() -> None:
    rl = RateLimiter(rate=10, period=1.0, capacity=5)
    t0 = time.monotonic()
    # Burst 5 should be ~instant.
    for _ in range(5):
        await rl.acquire()
    burst_elapsed = time.monotonic() - t0
    assert burst_elapsed < 0.1, f"burst too slow: {burst_elapsed}"

    # 6th acquire must wait at least ~1/10s = 100ms.
    t1 = time.monotonic()
    await rl.acquire()
    wait_elapsed = time.monotonic() - t1
    assert wait_elapsed >= 0.08, f"throttle did not kick in: {wait_elapsed}"


@pytest.mark.asyncio
async def test_does_not_deadlock_under_concurrency() -> None:
    rl = RateLimiter(rate=50, period=1.0, capacity=10)

    async def worker() -> None:
        for _ in range(5):
            await rl.acquire()

    await asyncio.wait_for(asyncio.gather(*[worker() for _ in range(8)]), timeout=5.0)
