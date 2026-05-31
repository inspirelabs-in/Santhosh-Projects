"""Similarity + reverse-ICP endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ...db import session as session_ctx
from ...embeddings import reverse_icp, similar_to_brand, update_dossier_embedding

router = APIRouter(prefix="/similar", tags=["similar"])


@router.get("/brand/{brand_id}")
async def similar(brand_id: int, limit: int = Query(default=10, ge=1, le=100)) -> dict:
    async with session_ctx() as s:
        items = await similar_to_brand(s, brand_id, limit=limit)
    if not items:
        raise HTTPException(404, "no embedding yet for this brand")
    return {"brand_id": brand_id, "items": items}


@router.get("/reverse-icp")
async def reverse(limit: int = Query(default=50, ge=1, le=500)) -> dict:
    async with session_ctx() as s:
        items = await reverse_icp(s, limit=limit)
    return {"items": items, "n_closed_won_seed": sum(1 for _ in items if _.get("similarity", 0) > 0.99)}


@router.post("/embed-dossier/{dossier_id}")
async def embed_dossier(dossier_id: int) -> dict:
    async with session_ctx() as s:
        ok = await update_dossier_embedding(s, dossier_id)
    if not ok:
        raise HTTPException(404, "dossier not found")
    return {"dossier_id": dossier_id, "status": "embedded"}
