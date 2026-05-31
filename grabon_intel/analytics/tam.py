"""TAM coverage — % of stored brands touched / scored / qualified per category."""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def tam_coverage(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                """
                WITH dossier_latest AS (
                  SELECT DISTINCT ON (brand_id) brand_id,
                         (data->'research'->'positioning'->>'category') AS category,
                         (data->'score'->>'tier') AS tier
                  FROM dossiers ORDER BY brand_id, version DESC
                )
                SELECT COALESCE(NULLIF(category,''),'unknown') AS category,
                       COUNT(*) AS scored,
                       COUNT(*) FILTER (WHERE tier IN ('hot','warm')) AS qualified,
                       COUNT(*) FILTER (WHERE tier = 'hot') AS hot
                FROM dossier_latest
                GROUP BY 1
                ORDER BY qualified DESC, scored DESC
                """
            )
        )
    ).mappings().all()
    return [dict(r) for r in rows]
