"""Recent signals stream + per-brand timeline."""
from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import text

from ...db import session as session_ctx

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("")
async def recent(
    type_eq: str | None = Query(default=None, alias="type"),
    source_eq: str | None = Query(default=None, alias="source"),
    brand_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict:
    where = []
    params: dict = {"limit": limit, "offset": offset}
    if type_eq:
        where.append("type = :type")
        params["type"] = type_eq
    if source_eq:
        where.append("source = :source")
        params["source"] = source_eq
    if brand_id is not None:
        where.append("brand_id = :bid")
        params["bid"] = brand_id
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    async with session_ctx() as s:
        rows = (
            await s.execute(
                text(
                    f"SELECT id, brand_id, brand_hint, type, value_num, value_text, "
                    f"source, payload, observed_at, ingested_at FROM signals {where_sql} "
                    "ORDER BY ingested_at DESC LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        ).mappings().all()
    return {"items": [dict(r) for r in rows], "limit": limit, "offset": offset}
