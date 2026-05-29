"""Lightweight circuit breaker for external services.

Tracks failures per service. When failure count exceeds threshold within
the window, the circuit opens and calls fail fast without hitting the
external service. After a cooldown, the circuit enters half-open state
and allows one probe call.

Redis-backed for cross-worker consistency.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from src.db.connection import get_redis

logger = logging.getLogger(__name__)

# Service names used as circuit keys
ELEVENLABS = "elevenlabs"
RESEND = "resend"
OPENAI = "openai"
ANTHROPIC = "anthropic"
GRAPH_API = "microsoft_graph"
READ_AI = "read_ai"


@dataclass
class CircuitState:
    service: str
    failures: int
    is_open: bool
    last_failure_at: float | None
    opens_at: float | None


# Defaults: 3 failures in 5 minutes = open for 10 minutes
THRESHOLDS: dict[str, dict[str, Any]] = {
    ELEVENLABS: {"max_failures": 2, "window_secs": 300, "cooldown_secs": 600},
    RESEND: {"max_failures": 5, "window_secs": 300, "cooldown_secs": 300},
    OPENAI: {"max_failures": 3, "window_secs": 300, "cooldown_secs": 300},
    ANTHROPIC: {"max_failures": 3, "window_secs": 300, "cooldown_secs": 300},
    GRAPH_API: {"max_failures": 3, "window_secs": 300, "cooldown_secs": 300},
    READ_AI: {"max_failures": 3, "window_secs": 300, "cooldown_secs": 600},
}
DEFAULT_THRESHOLD = {"max_failures": 3, "window_secs": 300, "cooldown_secs": 300}


class CircuitOpenError(Exception):
    """Raised when circuit is open and call should not proceed."""
    def __init__(self, service: str, retry_after_secs: int):
        self.service = service
        self.retry_after_secs = retry_after_secs
        super().__init__(
            f"Circuit open for {service}. Retry after {retry_after_secs}s."
        )


async def record_failure(service: str, error_msg: str = "") -> CircuitState:
    """Record a failure for the service. Returns current state."""
    cfg = THRESHOLDS.get(service, DEFAULT_THRESHOLD)
    redis = get_redis()
    key = f"circuit:{service}"
    now = time.time()

    pipe = redis.pipeline()
    pipe.zadd(key, {f"{now}:{error_msg[:100]}": now})
    pipe.zremrangebyscore(key, 0, now - cfg["window_secs"])
    pipe.zcard(key)
    pipe.expire(key, cfg["window_secs"] + cfg["cooldown_secs"])
    results = await pipe.execute()
    failure_count = int(results[2])

    is_open = failure_count >= cfg["max_failures"]
    if is_open:
        open_key = f"circuit:{service}:open"
        await redis.set(open_key, str(now), ex=cfg["cooldown_secs"])
        logger.warning(
            "Circuit OPEN for %s: %d failures in %ds window. Cooldown %ds.",
            service, failure_count, cfg["window_secs"], cfg["cooldown_secs"],
        )

    return CircuitState(
        service=service,
        failures=failure_count,
        is_open=is_open,
        last_failure_at=now,
        opens_at=now if is_open else None,
    )


async def check_circuit(service: str) -> None:
    """Check if the circuit is open. Raises CircuitOpenError if so."""
    redis = get_redis()
    open_key = f"circuit:{service}:open"
    open_at = await redis.get(open_key)
    if open_at is not None:
        cfg = THRESHOLDS.get(service, DEFAULT_THRESHOLD)
        elapsed = time.time() - float(open_at)
        remaining = max(1, int(cfg["cooldown_secs"] - elapsed))
        raise CircuitOpenError(service, remaining)


async def record_success(service: str) -> None:
    """Record a successful call. Resets failure window."""
    redis = get_redis()
    pipe = redis.pipeline()
    pipe.delete(f"circuit:{service}")
    pipe.delete(f"circuit:{service}:open")
    await pipe.execute()


async def get_all_states() -> list[CircuitState]:
    """Get state of all tracked circuits (for diagnostics endpoint)."""
    redis = get_redis()
    states: list[CircuitState] = []
    now = time.time()
    for service in THRESHOLDS:
        cfg = THRESHOLDS[service]
        count = await redis.zcount(
            f"circuit:{service}", now - cfg["window_secs"], now
        )
        is_open = await redis.exists(f"circuit:{service}:open")
        states.append(CircuitState(
            service=service,
            failures=int(count),
            is_open=bool(is_open),
            last_failure_at=None,
            opens_at=None,
        ))
    return states
