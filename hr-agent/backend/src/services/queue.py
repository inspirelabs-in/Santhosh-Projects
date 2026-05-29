"""Arq job-queue helper.

Provides a single ``enqueue()`` entrypoint used from API handlers to schedule
durable background work. Falls back to running the coroutine inline (via
FastAPI BackgroundTasks-style ``add_task``) when arq is disabled, so the
codebase keeps working with or without the worker container.

Usage from a webhook:

    from src.services.queue import enqueue
    await enqueue("evaluate_voice_call", voice_call_id=str(vc_id))

Job names must match the keys registered in
``src/workers/main.py``'s ``WorkerSettings.functions``.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from src.config import get_settings

logger = logging.getLogger(__name__)


_pool: ArqRedis | None = None
_pool_lock = asyncio.Lock()


def _redis_settings() -> RedisSettings:
    settings = get_settings()
    return RedisSettings.from_dsn(settings.redis_url)


async def get_arq_pool() -> ArqRedis:
    """Return a process-wide arq pool, creating it on first use."""

    global _pool
    if _pool is not None:
        return _pool
    async with _pool_lock:
        if _pool is None:
            _pool = await create_pool(_redis_settings())
        return _pool


async def close_arq_pool() -> None:
    global _pool
    if _pool is None:
        return
    with suppress(Exception):
        await _pool.aclose()
    _pool = None


async def enqueue(
    job_name: str,
    *args: Any,
    _defer_for: timedelta | None = None,
    _defer_until: datetime | None = None,
    _job_id: str | None = None,
    **kwargs: Any,
) -> bool:
    """Enqueue a job onto the worker queue.

    Returns True if enqueued, False if arq is disabled (caller should run the
    work inline via BackgroundTasks). The function never raises -- if Redis
    or arq is unreachable we log and return False so the API stays up.
    """

    settings = get_settings()
    if not settings.arq_enabled:
        return False
    try:
        pool = await get_arq_pool()
        job = await pool.enqueue_job(
            job_name,
            *args,
            _defer_until=_defer_until,
            _defer_by=_defer_for,
            _job_id=_job_id,
            _queue_name=settings.arq_queue_name,
            **kwargs,
        )
        if job is None:
            logger.warning("arq enqueue returned None for %s (likely duplicate id)", job_name)
            return False
        return True
    except Exception as exc:  # noqa: BLE001 -- never break the request path
        logger.exception("arq enqueue failed for %s: %s", job_name, exc)
        return False
