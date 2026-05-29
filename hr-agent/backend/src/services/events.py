"""Lightweight in-process + Redis pub/sub event bus.

Used by webhooks + activities to push live updates ("stage_changed",
"voice_call_completed", "assessment_completed", "meeting_analyzed") so the
frontend can subscribe via SSE instead of polling every 30s.

Channel naming: ``hiring-agent:application:{application_id}``.

Falls back to a no-op when Redis is not reachable so the request path is
never blocked by the event bus.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator
from uuid import UUID

import redis.asyncio as redis_async

from src.config import get_settings

logger = logging.getLogger(__name__)


_redis_client: redis_async.Redis | None = None


def _channel(application_id: UUID) -> str:
    return f"hiring-agent:application:{application_id}"


async def _client() -> redis_async.Redis:
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = redis_async.from_url(
            settings.redis_url, encoding="utf-8", decode_responses=True
        )
    return _redis_client


async def publish_event(
    application_id: UUID,
    *,
    event: str,
    data: dict[str, Any] | None = None,
) -> None:
    payload = json.dumps({"event": event, "data": data or {}})
    try:
        client = await _client()
        await client.publish(_channel(application_id), payload)
    except Exception as exc:  # noqa: BLE001
        logger.debug("event publish failed (non-fatal): %s", exc)


async def subscribe_events(application_id: UUID) -> AsyncIterator[str]:
    """Async iterator that yields raw JSON event strings until cancelled."""

    client = await _client()
    pubsub = client.pubsub()
    await pubsub.subscribe(_channel(application_id))
    try:
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            data = msg.get("data")
            if isinstance(data, str) and data:
                yield data
    finally:
        await pubsub.unsubscribe(_channel(application_id))
        await pubsub.aclose()
