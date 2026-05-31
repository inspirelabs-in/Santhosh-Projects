"""Cost-per-qualified-lead (CPQL) view.

Total cost in window / brands at hot|warm tier in window. Materialised
view path is offered, but a regular query is fine at MVP volumes.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def cost_per_qualified_lead(session: AsyncSession, *, since_days: int = 30) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                """
                WITH spend AS (
                  SELECT COALESCE(SUM(total_cost_cents),0) AS c
                  FROM agent_traces
                  WHERE created_at > NOW() - make_interval(days => :d)
                ),
                qualified AS (
                  SELECT COUNT(DISTINCT brand_id) AS n
                  FROM dossiers
                  WHERE (data->'score'->>'tier') IN ('hot','warm')
                    AND generated_at > NOW() - make_interval(days => :d)
                ),
                hot AS (
                  SELECT COUNT(DISTINCT brand_id) AS n
                  FROM dossiers
                  WHERE (data->'score'->>'tier') = 'hot'
                    AND generated_at > NOW() - make_interval(days => :d)
                )
                SELECT spend.c AS total_cost_cents,
                       qualified.n AS qualified,
                       hot.n AS hot
                FROM spend, qualified, hot
                """
            ),
            {"d": since_days},
        )
    ).first()
    if not row:
        return {"window_days": since_days, "total_cost_cents": 0, "qualified": 0, "hot": 0, "cpql_cents": None, "cphl_cents": None}
    cost, qualified, hot = int(row[0]), int(row[1]), int(row[2])
    return {
        "window_days": since_days,
        "total_cost_cents": cost,
        "qualified": qualified,
        "hot": hot,
        "cpql_cents": (cost // qualified) if qualified else None,
        "cphl_cents": (cost // hot) if hot else None,
    }
