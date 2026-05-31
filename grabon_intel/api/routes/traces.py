"""Agent trace listing + status + error log for activity view."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import text

from ...db import session as session_ctx
from ...logging import get_error_summary, get_recent_errors

router = APIRouter(prefix="/traces", tags=["traces"])


@router.get("")
async def list_traces(
    limit: int = Query(default=30, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    brand_id: int | None = Query(default=None),
    agent: str | None = Query(default=None),
) -> dict:
    where: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if brand_id is not None:
        where.append("t.brand_id = :bid")
        params["bid"] = brand_id
    if agent:
        where.append("t.agent = :agent")
        params["agent"] = agent
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = text(
        "SELECT t.id, t.workflow_id, t.brand_id, t.agent, "
        "  t.total_cost_cents, t.duration_ms, t.status, t.error, "
        "  t.created_at, b.name AS brand_name "
        "FROM agent_traces t "
        "LEFT JOIN brands b ON b.id = t.brand_id "
        f"{where_sql} "
        "ORDER BY t.created_at DESC "
        "LIMIT :limit OFFSET :offset"
    )
    async with session_ctx() as s:
        rows = (await s.execute(sql, params)).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.get("/agent-status")
async def agent_status() -> dict:
    """Overview of agent health: last runs, collector stats, pending work."""
    async with session_ctx() as s:
        last_discovery = (
            await s.execute(
                text(
                    "SELECT workflow_id, created_at, duration_ms, status, error "
                    "FROM agent_traces WHERE agent = 'auto_discovery' "
                    "ORDER BY created_at DESC LIMIT 1"
                )
            )
        ).mappings().first()

        last_monitoring = (
            await s.execute(
                text(
                    "SELECT workflow_id, created_at, duration_ms, status, error "
                    "FROM agent_traces WHERE agent = 'monitoring' "
                    "ORDER BY created_at DESC LIMIT 1"
                )
            )
        ).mappings().first()

        collector_stats = (
            await s.execute(
                text(
                    "SELECT collector, "
                    "  COUNT(*) AS total_runs, "
                    "  SUM(signals_emitted) AS total_signals, "
                    "  MAX(finished_at) AS last_run, "
                    "  SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors "
                    "FROM collector_runs "
                    "GROUP BY collector ORDER BY MAX(finished_at) DESC NULLS LAST"
                )
            )
        ).mappings().all()

        brands_pending = (
            await s.execute(
                text(
                    "SELECT COUNT(*) FROM brands "
                    "WHERE domain IS NOT NULL AND domain != '' "
                    "AND id NOT IN (SELECT DISTINCT brand_id FROM dossiers)"
                )
            )
        ).scalar()

        recent_discoveries = (
            await s.execute(
                text(
                    "SELECT b.id, b.name, b.domain, b.created_at, "
                    "  d.data->'score'->>'tier' AS tier, "
                    "  (d.data->'score'->>'total')::int AS score "
                    "FROM brands b "
                    "LEFT JOIN LATERAL ("
                    "  SELECT data FROM dossiers WHERE brand_id = b.id ORDER BY version DESC LIMIT 1"
                    ") d ON true "
                    "WHERE b.domain IS NOT NULL AND b.domain != '' "
                    "ORDER BY b.created_at DESC LIMIT 10"
                )
            )
        ).mappings().all()

    return {
        "last_discovery": dict(last_discovery) if last_discovery else None,
        "last_monitoring": dict(last_monitoring) if last_monitoring else None,
        "collectors": [dict(c) for c in collector_stats],
        "brands_pending_research": brands_pending or 0,
        "recent_discoveries": [dict(r) for r in recent_discoveries],
        "error_summary": get_error_summary(),
    }


@router.get("/errors")
async def list_errors(
    limit: int = Query(default=50, ge=1, le=200),
    category: str | None = Query(default=None),
) -> dict:
    """Return recent categorised errors from the in-memory buffer."""
    return {
        "items": get_recent_errors(limit=limit, category=category),
        "summary": get_error_summary(),
    }


@router.get("/errors/summary")
async def error_summary() -> dict:
    """Lightweight health check — category counts + health status."""
    return get_error_summary()
