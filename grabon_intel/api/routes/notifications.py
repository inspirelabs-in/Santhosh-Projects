"""In-app notifications via SSE.

Streams real-time notifications: new dossiers, score changes,
approval requests, sequence events. Browser connects via EventSource.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import orjson
from fastapi import APIRouter
from sqlalchemy import text
from sse_starlette.sse import EventSourceResponse

from ...db import session as session_ctx

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/stream")
async def notification_stream() -> EventSourceResponse:
    async def gen() -> AsyncIterator[dict]:
        last_approval_id = 0
        last_trace_id = ""

        while True:
            try:
                async with session_ctx() as s:
                    # New pending approvals
                    approvals = (
                        await s.execute(
                            text(
                                "SELECT id, entity_type, entity_id, brand_id, created_at "
                                "FROM approvals WHERE status = 'pending' AND id > :lid "
                                "ORDER BY id ASC LIMIT 10"
                            ),
                            {"lid": last_approval_id},
                        )
                    ).all()
                    for a in approvals:
                        last_approval_id = max(last_approval_id, a[0])
                        yield {
                            "event": "approval",
                            "data": orjson.dumps({
                                "id": a[0],
                                "entity_type": a[1],
                                "entity_id": a[2],
                                "brand_id": a[3],
                                "created_at": str(a[4]),
                                "message": f"New {a[1]} approval needed for brand #{a[3] or a[2]}",
                            }).decode(),
                        }

                    # Recent agent traces (dossier completions)
                    traces = (
                        await s.execute(
                            text(
                                "SELECT id::text, agent, brand_id, status, total_cost_cents, created_at "
                                "FROM agent_traces WHERE id::text > :lid "
                                "ORDER BY created_at ASC LIMIT 10"
                            ),
                            {"lid": last_trace_id},
                        )
                    ).all()
                    for t in traces:
                        last_trace_id = t[0] if t[0] > last_trace_id else last_trace_id
                        yield {
                            "event": "trace",
                            "data": orjson.dumps({
                                "id": t[0],
                                "agent": t[1],
                                "brand_id": t[2],
                                "status": t[3],
                                "cost_cents": t[4],
                                "created_at": str(t[5]),
                                "message": f"{t[1]} completed for brand #{t[2] or '?'} ({t[3]})",
                            }).decode(),
                        }

            except Exception:
                pass

            await asyncio.sleep(5)

    return EventSourceResponse(gen())
