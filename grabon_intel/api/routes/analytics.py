"""Analytics endpoints — source health, CPQL, TAM."""
from __future__ import annotations

from fastapi import APIRouter, Query

from ...analytics import cost_per_qualified_lead, source_health, tam_coverage
from ...db import session as session_ctx

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/source-health")
async def src(since_days: int = Query(default=30, ge=1, le=365)) -> dict:
    async with session_ctx() as s:
        items = await source_health(s, since_days=since_days)
    return {"window_days": since_days, "items": items}


@router.get("/cpql")
async def cpql(since_days: int = Query(default=30, ge=1, le=365)) -> dict:
    async with session_ctx() as s:
        return await cost_per_qualified_lead(s, since_days=since_days)


@router.get("/tam")
async def tam() -> dict:
    async with session_ctx() as s:
        items = await tam_coverage(s)
    return {"items": items}


@router.get("/budget")
async def budget() -> dict:
    from sqlalchemy import text as sa_text

    from ...config import get_settings

    settings = get_settings()
    async with session_ctx() as s:
        row = (
            await s.execute(
                sa_text("SELECT spent_cents, cap_cents FROM budget WHERE day = CURRENT_DATE")
            )
        ).first()
    if row:
        return {"spent_cents": row[0], "cap_cents": row[1]}
    return {"spent_cents": 0, "cap_cents": settings.daily_cost_cap_cents}
