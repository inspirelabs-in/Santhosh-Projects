"""Tool primitives.

Every tool is an async callable with:
- name (stable id used in trace + cost tracking)
- per-instance rate limit (in-process token bucket)
- structured ToolResult (or raise ToolError)
- graceful degradation when API key missing — returns empty payload
  flagged with `degraded=True`, never raises.

The in-process token bucket is sufficient for a single worker process.
Multi-process / multi-host rate limits move to Redis later — see
`RateLimiter.from_redis` placeholder.
"""
from __future__ import annotations

import abc
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ToolResult:
    name: str
    data: Any
    cost_cents: int = 0
    degraded: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


class ToolError(RuntimeError):
    pass


class RateLimiter:
    """Token-bucket rate limiter. `rate` tokens per `period` seconds, bursting up to `capacity`."""

    __slots__ = ("rate", "period", "capacity", "_tokens", "_last", "_lock")

    def __init__(self, rate: int, period: float = 1.0, capacity: int | None = None) -> None:
        self.rate = rate
        self.period = period
        self.capacity = capacity or rate
        self._tokens = float(self.capacity)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, cost: float = 1.0) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last
                self._tokens = min(self.capacity, self._tokens + elapsed * (self.rate / self.period))
                self._last = now
                if self._tokens >= cost:
                    self._tokens -= cost
                    return
                deficit = cost - self._tokens
                sleep_for = deficit / (self.rate / self.period)
            # Sleep outside the lock so other tasks can drain capacity in parallel.
            await asyncio.sleep(max(sleep_for, 0.001))

    async def __aenter__(self) -> "RateLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


# Quick sleep helper outside the lock (kept here for tests).
async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


class Tool(abc.ABC):
    """Stateful tool with rate-limit + key check."""

    name: str = "abstract"

    def __init__(self, *, rate: int = 5, period: float = 1.0) -> None:
        self._rl = RateLimiter(rate=rate, period=period)

    @abc.abstractmethod
    async def call(self, **kwargs: Any) -> ToolResult:
        raise NotImplementedError

    @property
    def available(self) -> bool:
        return True  # subclasses override based on key presence

    async def safe(self, **kwargs: Any) -> ToolResult:
        """Wrap `call` with graceful degradation. Never raises."""
        from ..logging import ErrorCategory, classify_error, get_logger, record_error

        log = get_logger(f"tool.{self.name}")
        if not self.available:
            record_error(
                category=ErrorCategory.CONFIG_ERROR,
                source=f"tool.{self.name}",
                message=f"{self.name} unavailable — no credentials configured",
            )
            return ToolResult(name=self.name, data=None, degraded=True, meta={"reason": "no_credentials"})
        await self._rl.acquire()
        try:
            return await self.call(**kwargs)
        except ToolError as exc:
            cat = classify_error(exc)
            log.warning("tool.error", tool=self.name, category=cat.value, error=str(exc))
            record_error(
                category=cat,
                source=f"tool.{self.name}",
                message=str(exc)[:300],
                detail=str(kwargs),
            )
            return ToolResult(name=self.name, data=None, degraded=True, meta={"error": str(exc), "category": cat.value})
        except Exception as exc:  # noqa: BLE001
            cat = classify_error(exc)
            log.warning("tool.error", tool=self.name, category=cat.value, error=str(exc))
            record_error(
                category=cat,
                source=f"tool.{self.name}",
                message=f"{type(exc).__name__}: {exc}"[:300],
                detail=str(kwargs),
            )
            return ToolResult(name=self.name, data=None, degraded=True, meta={"error": f"{type(exc).__name__}: {exc}", "category": cat.value})
