"""HTTP request rate limiter (Redis-backed).

Used to throttle public-facing endpoints (apply form, screening submit,
assignment submit). Sliding-window counter per (route_bucket, client_ip).
Fails CLOSED if Redis is unreachable — rejects requests rather than
allowing unbounded traffic when the limiter backend is down.

Usage in a FastAPI route:

    from src.services.request_rate_limit import enforce_rate_limit

    @router.post("/apply/{token}/screening")
    async def submit(request: Request, token: str, ...):
        await enforce_rate_limit(request, "apply_screening", limit=5, window_seconds=60)
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import HTTPException, Request, status

from src.db.connection import get_redis

logger = logging.getLogger(__name__)


def _client_key(request: Request, suffix: str) -> str:
    fwd = (
        request.headers.get("x-forwarded-for")
        or request.headers.get("x-real-ip")
        or (request.client.host if request.client else "anon")
    )
    ip = (fwd.split(",")[0] or "anon").strip()
    return f"httprl:{suffix}:{ip}"


async def is_allowed(
    request: Request,
    bucket: str,
    *,
    limit: int,
    window_seconds: int,
) -> bool:
    key = _client_key(request, bucket)
    try:
        redis = get_redis()
        now = int(time.time())
        window_key = f"{key}:{now // window_seconds}"
        async with redis.pipeline(transaction=False) as pipe:
            pipe.incr(window_key)
            pipe.expire(window_key, window_seconds * 2)
            count, _ = await pipe.execute()
        return int(count) <= limit
    except Exception as exc:  # noqa: BLE001
        logger.error("http rate limit check failed (blocking request): %s", exc)
        return False


async def enforce_rate_limit(
    request: Request,
    bucket: str,
    *,
    limit: int,
    window_seconds: int = 60,
    error_message: Optional[str] = None,
) -> None:
    if not await is_allowed(
        request, bucket, limit=limit, window_seconds=window_seconds
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=error_message or f"rate limit exceeded ({limit}/{window_seconds}s)",
        )
