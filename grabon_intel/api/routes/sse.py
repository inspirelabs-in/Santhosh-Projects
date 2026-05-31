"""SSE stream of agent_traces — powers the live "reasoning panel" in UI.

Polls the DB every `poll_secs` and pushes new traces. Simple LISTEN/NOTIFY
upgrade later removes the poll. Keep poll cheap (indexed scan on
`created_at` > cursor) to avoid load.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from typing import AsyncIterator

import orjson
from fastapi import APIRouter, Query
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import text

from ...db import session as session_ctx

router = APIRouter(prefix="/stream", tags=["stream"])


@router.get("/traces")
async def stream_traces(
    poll_secs: float = Query(default=2.0, ge=0.5, le=10.0),
    brand_id: int | None = None,
) -> EventSourceResponse:
    """Server-Sent Events of new agent_traces rows."""
    cursor = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=10)

    async def gen() -> AsyncIterator[dict]:
        nonlocal cursor
        while True:
            params: dict = {"c": cursor}
            where = "created_at > :c"
            if brand_id is not None:
                where += " AND brand_id = :b"
                params["b"] = brand_id
            async with session_ctx() as s:
                rows = (
                    await s.execute(
                        text(
                            f"SELECT id, workflow_id, brand_id, agent, total_cost_cents, "
                            f"duration_ms, status, created_at FROM agent_traces "
                            f"WHERE {where} ORDER BY created_at ASC LIMIT 50"
                        ),
                        params,
                    )
                ).mappings().all()
            for r in rows:
                cursor = max(cursor, r["created_at"])
                yield {
                    "event": "trace",
                    "data": orjson.dumps(dict(r), default=str).decode("utf-8"),
                }
            await asyncio.sleep(poll_secs)

    return EventSourceResponse(gen())
