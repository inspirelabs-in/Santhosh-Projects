"""Brand listing + detail."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from ...db import session as session_ctx

router = APIRouter(prefix="/brands", tags=["brands"])


@router.get("")
async def list_brands(
    status_eq: str | None = Query(default=None, alias="status"),
    tier: str | None = Query(default=None, description="filter by dossier tier: hot/warm/watchlist/park"),
    q: str | None = Query(default=None, description="substring match on name or domain"),
    has_domain: bool = Query(default=False, description="only brands with a real domain"),
    has_dossier: bool = Query(default=False, description="only brands with at least one dossier"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    where: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if status_eq:
        where.append("b.status = :status")
        params["status"] = status_eq
    if has_domain:
        where.append("b.domain IS NOT NULL AND b.domain != ''")
    if has_dossier:
        where.append("EXISTS (SELECT 1 FROM dossiers WHERE brand_id = b.id)")
    if q:
        where.append("(b.name ILIKE :q OR b.domain ILIKE :q)")
        params["q"] = f"%{q}%"
    if tier:
        where.append("d.data->'score'->>'tier' = :tier")
        params["tier"] = tier
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = text(
        "SELECT b.id, b.name, b.domain, b.status, b.created_at, "
        "  d.data->'score'->>'tier' AS tier, "
        "  (d.data->'score'->>'total')::int AS score, "
        "  d.data->'score'->'why'->>0 AS score_headline, "
        "  d.data->'opportunity'->>'diagnosis' AS diagnosis, "
        "  ("
        "    SELECT string_agg(k, ', ') FROM jsonb_object_keys(d.data->'competitor'->'gap_map') AS k"
        "  ) AS gap_summary "
        "FROM brands b "
        "LEFT JOIN LATERAL ("
        "  SELECT data FROM dossiers WHERE brand_id = b.id ORDER BY version DESC LIMIT 1"
        ") d ON true "
        f"{where_sql} "
        "ORDER BY (d.data->'score'->>'total')::int DESC NULLS LAST, b.id DESC "
        "LIMIT :limit OFFSET :offset"
    )
    async with session_ctx() as s:
        rows = (await s.execute(sql, params)).mappings().all()
    return {"items": [dict(r) for r in rows], "limit": limit, "offset": offset}


@router.get("/{brand_id}")
async def get_brand(brand_id: int) -> dict:
    async with session_ctx() as s:
        row = (
            await s.execute(
                text("SELECT * FROM brands WHERE id = :id"),
                {"id": brand_id},
            )
        ).mappings().first()
        if not row:
            raise HTTPException(404, "brand not found")
        latest = (
            await s.execute(
                text(
                    "SELECT id, version, generated_at, cost_cents, data "
                    "FROM dossiers WHERE brand_id = :id ORDER BY version DESC LIMIT 1"
                ),
                {"id": brand_id},
            )
        ).mappings().first()
    return {"brand": dict(row), "latest_dossier": dict(latest) if latest else None}


@router.get("/{brand_id}/signals")
async def get_brand_signals(brand_id: int) -> dict:
    """Return latest signals for a brand, grouped by source (same shape as dossier signal_evidence)."""
    async with session_ctx() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT DISTINCT ON (source, type) "
                    "source, type, value_num, value_text, payload, observed_at "
                    "FROM signals WHERE brand_id = :bid "
                    "ORDER BY source, type, observed_at DESC"
                ),
                {"bid": brand_id},
            )
        ).mappings().all()
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        entry: dict = {
            "type": r["type"],
            "value_num": float(r["value_num"]) if r["value_num"] is not None else None,
            "value_text": r["value_text"],
            "observed_at": r["observed_at"].isoformat() if r["observed_at"] else None,
        }
        if r["payload"]:
            entry["payload"] = r["payload"]
        grouped.setdefault(r["source"], []).append(entry)
    return grouped


class StatusUpdate(BaseModel):
    status: str


@router.patch("/{brand_id}/status")
async def update_brand_status(brand_id: int, body: StatusUpdate) -> dict:
    valid = {"new", "qualified", "contacted", "responded", "meeting", "proposal", "won", "lost", "parked"}
    if body.status not in valid:
        raise HTTPException(400, f"invalid status. must be one of: {', '.join(sorted(valid))}")
    async with session_ctx() as s:
        result = await s.execute(
            text("UPDATE brands SET status = :st WHERE id = :id RETURNING id"),
            {"st": body.status, "id": brand_id},
        )
        if not result.first():
            raise HTTPException(404, "brand not found")
    return {"id": brand_id, "status": body.status}


@router.get("/export/csv")
async def export_csv(
    tier: str | None = Query(default=None),
    status: str | None = Query(default=None),
    score_min: int | None = Query(default=None),
    score_max: int | None = Query(default=None),
    category: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
) -> Any:
    """Export brands as CSV with dossier data."""
    from fastapi.responses import StreamingResponse
    import csv
    import io

    where: list[str] = []
    params: dict[str, Any] = {"limit": limit}
    if tier:
        where.append("d.data->'score'->>'tier' = :tier")
        params["tier"] = tier
    if status:
        where.append("b.status = :status")
        params["status"] = status
    if score_min is not None:
        where.append("(d.data->'score'->>'total')::int >= :score_min")
        params["score_min"] = score_min
    if score_max is not None:
        where.append("(d.data->'score'->>'total')::int <= :score_max")
        params["score_max"] = score_max
    if category:
        where.append("d.data->'research'->'positioning'->>'category' ILIKE :category")
        params["category"] = f"%{category}%"
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    sql = text(
        "SELECT b.id, b.name, b.domain, b.status, "
        "  d.data->'score'->>'tier' AS tier, "
        "  (d.data->'score'->>'total')::int AS score, "
        "  d.data->'score'->>'confidence' AS confidence, "
        "  d.data->'research'->'company'->>'hq' AS hq, "
        "  d.data->'research'->'company'->>'revenue_band' AS revenue_band, "
        "  d.data->'research'->'company'->>'employees_est' AS employees, "
        "  d.data->'research'->'company'->>'funding_stage' AS funding_stage, "
        "  d.data->'research'->'company'->>'founded_year' AS founded_year, "
        "  d.data->'research'->'positioning'->>'category' AS category, "
        "  d.data->'opportunity'->>'diagnosis' AS diagnosis, "
        "  (d.data->'score'->>'estimated_deal_value_inr')::text AS deal_value_inr, "
        "  (SELECT string_agg(k, '; ') FROM jsonb_object_keys(d.data->'competitor'->'gap_map') AS k) AS service_gaps, "
        "  (SELECT string_agg(p.name || ' <' || coalesce(p.email, '') || '>', '; ') "
        "   FROM persons p WHERE p.brand_id = b.id LIMIT 3) AS contacts "
        "FROM brands b "
        "LEFT JOIN LATERAL ("
        "  SELECT data FROM dossiers WHERE brand_id = b.id ORDER BY version DESC LIMIT 1"
        ") d ON true "
        f"{where_sql} "
        "ORDER BY (d.data->'score'->>'total')::int DESC NULLS LAST "
        "LIMIT :limit"
    )
    async with session_ctx() as s:
        rows = (await s.execute(sql, params)).mappings().all()

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["id", "name", "domain", "status", "tier", "score", "confidence",
                     "hq", "revenue_band", "employees", "funding_stage", "founded_year",
                     "category", "diagnosis", "deal_value_inr", "service_gaps", "contacts"],
    )
    writer.writeheader()
    for r in rows:
        writer.writerow(dict(r))

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads_export.csv"},
    )


class BulkStatusUpdate(BaseModel):
    brand_ids: list[int]
    status: str
    actor: str | None = None


@router.patch("/bulk/status")
async def bulk_update_status(body: BulkStatusUpdate) -> dict:
    valid = {"new", "qualified", "contacted", "responded", "meeting", "proposal", "won", "lost", "parked"}
    if body.status not in valid:
        raise HTTPException(400, f"invalid status: {body.status}")
    if not body.brand_ids or len(body.brand_ids) > 100:
        raise HTTPException(400, "provide 1-100 brand_ids")

    async with session_ctx() as s:
        result = await s.execute(
            text("UPDATE brands SET status = :st WHERE id = ANY(:ids) RETURNING id"),
            {"st": body.status, "ids": body.brand_ids},
        )
        updated = [r[0] for r in result.fetchall()]

        for bid in updated:
            await s.execute(
                text(
                    "INSERT INTO lead_events (brand_id, event_type, to_status, actor) "
                    "VALUES (:bid, 'bulk_status_change', :status, :actor)"
                ),
                {"bid": bid, "status": body.status, "actor": body.actor},
            )

    return {"updated": len(updated), "brand_ids": updated}


class BulkEnrichRequest(BaseModel):
    brand_ids: list[int]


@router.post("/bulk/enrich")
async def bulk_enrich(body: BulkEnrichRequest) -> dict:
    """Trigger dossier generation for multiple brands."""
    if not body.brand_ids or len(body.brand_ids) > 20:
        raise HTTPException(400, "provide 1-20 brand_ids")

    from temporalio.client import Client as TemporalClient
    from ..._temporal import get_temporal_client
    from ...workflows.dossier import DossierWF, DossierWFInput

    results = []
    client = await get_temporal_client()
    for bid in body.brand_ids:
        try:
            wf_id = f"bulk-dossier-{bid}-{int(__import__('time').time())}"
            await client.start_workflow(
                DossierWF.run,
                DossierWFInput(brand_id=bid, reason="bulk_enrich"),
                id=wf_id,
                task_queue="grabon-intel",
            )
            results.append({"brand_id": bid, "workflow_id": wf_id, "status": "started"})
        except Exception as e:
            results.append({"brand_id": bid, "status": "failed", "error": str(e)[:200]})

    return {"results": results}
