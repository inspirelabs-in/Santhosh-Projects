"""Per-source health: emission rate, qualified rate, meeting rate, $/qualified-lead.

Joins:
  - signals.source → unique brands_emitted in window
  - dossiers → brands_scored, brands_qualified (tier in hot/warm)
  - grabon_feedback → meetings + wins
  - agent_traces → cost attributable to that source's brands (best-effort)
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def source_health(session: AsyncSession, *, since_days: int = 30) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                """
                WITH src_brands AS (
                  SELECT s.source, s.brand_id
                  FROM signals s
                  WHERE s.brand_id IS NOT NULL
                    AND s.ingested_at > NOW() - make_interval(days => :d)
                  GROUP BY s.source, s.brand_id
                ),
                latest_dossier AS (
                  SELECT DISTINCT ON (brand_id) brand_id,
                         (data->'score'->>'total')::int AS score_total,
                         (data->'score'->>'tier') AS tier,
                         cost_cents
                  FROM dossiers ORDER BY brand_id, version DESC
                ),
                joined AS (
                  SELECT sb.source, sb.brand_id, ld.score_total, ld.tier, ld.cost_cents,
                         f.label
                  FROM src_brands sb
                  LEFT JOIN latest_dossier ld ON ld.brand_id = sb.brand_id
                  LEFT JOIN LATERAL (
                    SELECT label FROM grabon_feedback
                    WHERE brand_id = sb.brand_id
                    ORDER BY created_at DESC LIMIT 1
                  ) f ON true
                )
                SELECT source,
                       COUNT(DISTINCT brand_id) AS brands_emitted,
                       COUNT(DISTINCT brand_id) FILTER (WHERE score_total IS NOT NULL) AS brands_scored,
                       COUNT(DISTINCT brand_id) FILTER (WHERE tier IN ('hot','warm')) AS brands_qualified,
                       COUNT(DISTINCT brand_id) FILTER (WHERE label = 'meeting_booked') AS meetings,
                       COUNT(DISTINCT brand_id) FILTER (WHERE label = 'closed_won') AS wins,
                       COALESCE(SUM(cost_cents), 0) AS total_cost_cents
                FROM joined GROUP BY source ORDER BY brands_qualified DESC
                """
            ),
            {"d": since_days},
        )
    ).mappings().all()

    out: list[dict[str, Any]] = []
    for r in rows:
        emitted = int(r["brands_emitted"]) or 0
        qualified = int(r["brands_qualified"]) or 0
        cost = int(r["total_cost_cents"]) or 0
        out.append(
            {
                **{k: int(v) if isinstance(v, (int, float)) else v for k, v in r.items()},
                "qualified_rate": (qualified / emitted) if emitted else 0.0,
                "cpql_cents": (cost // qualified) if qualified else None,
            }
        )
    return out
