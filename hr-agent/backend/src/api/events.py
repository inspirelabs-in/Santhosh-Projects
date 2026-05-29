"""Server-sent events stream for one application's lifecycle.

Frontend opens an EventSource against
``GET /dashboard/v1/applications/{id}/events?key=<dashboard_key>`` and stays
connected; the backend pushes a JSON message every time a webhook publishes
to the application's Redis channel.

The SSE auth uses a query-param fallback because EventSource does not let
the browser set custom headers. Recruiters with the dashboard key can read.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from src.services.events import subscribe_events

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard/v1/applications", tags=["events"])


def _resolve_role_from_query_key(key: str) -> str:
    raw = os.getenv("DASHBOARD_KEYS", "")
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Auth not configured")
    import json

    try:
        keys = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid auth config")
    role = keys.get(key)
    if role not in ("admin", "recruiter", "viewer"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid key")
    return role


async def _sse_iterator(application_id: UUID) -> AsyncIterator[bytes]:
    # Initial comment so the browser knows the connection is live.
    yield b": connected\n\n"
    try:
        async for raw in subscribe_events(application_id):
            yield f"data: {raw}\n\n".encode("utf-8")
    except asyncio.CancelledError:
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning("sse stream error: %s", exc)
        yield b"event: error\ndata: stream-failed\n\n"


@router.get("/{application_id}/events")
async def stream_events(
    application_id: UUID,
    key: str = Query(default="", description="Dashboard key (EventSource query-arg auth)"),
) -> StreamingResponse:
    if not key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing key query param")
    _resolve_role_from_query_key(key)
    return StreamingResponse(
        _sse_iterator(application_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
