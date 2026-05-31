"""Dossier read + version history."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from ...db import session as session_ctx

router = APIRouter(prefix="/dossiers", tags=["dossiers"])


@router.get("/by-brand/{brand_id}")
async def list_versions(brand_id: int, limit: int = Query(default=20, ge=1, le=100)) -> dict:
    async with session_ctx() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT id, version, generated_at, cost_cents, "
                    "data->'score' AS score "
                    "FROM dossiers WHERE brand_id = :b ORDER BY version DESC LIMIT :l"
                ),
                {"b": brand_id, "l": limit},
            )
        ).mappings().all()
    return {"brand_id": brand_id, "items": [dict(r) for r in rows]}


@router.get("/{dossier_id}")
async def get_dossier(dossier_id: int) -> dict:
    async with session_ctx() as s:
        row = (
            await s.execute(
                text(
                    "SELECT id, brand_id, version, data, markdown, "
                    "cost_cents, generated_at FROM dossiers WHERE id = :id"
                ),
                {"id": dossier_id},
            )
        ).mappings().first()
    if not row:
        raise HTTPException(404, "dossier not found")
    return dict(row)
