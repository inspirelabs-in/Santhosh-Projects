"""Lead lifecycle — status transitions + timeline events."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from ...db import session as session_ctx

router = APIRouter(prefix="/lifecycle", tags=["lifecycle"])

VALID_STATUSES = ["new", "qualified", "contacted", "responded", "meeting", "proposal", "won", "lost", "parked"]

VALID_TRANSITIONS = {
    "new": ["qualified", "parked", "lost"],
    "qualified": ["contacted", "parked", "lost"],
    "contacted": ["responded", "meeting", "parked", "lost"],
    "responded": ["meeting", "contacted", "parked", "lost"],
    "meeting": ["proposal", "won", "lost", "contacted"],
    "proposal": ["won", "lost", "meeting"],
    "won": [],
    "lost": ["new", "qualified"],
    "parked": ["new", "qualified"],
}


class StatusTransition(BaseModel):
    to_status: str
    note: str | None = None
    actor: str | None = None
    force: bool = False


@router.post("/{brand_id}/transition")
async def transition_status(brand_id: int, body: StatusTransition) -> dict:
    async with session_ctx() as s:
        current = (
            await s.execute(
                text("SELECT status FROM brands WHERE id = :id"),
                {"id": brand_id},
            )
        ).scalar()
        if current is None:
            raise HTTPException(404, "brand not found")

        current_status = current or "new"
        if body.to_status not in VALID_STATUSES:
            raise HTTPException(400, f"invalid status: {body.to_status}")

        allowed = VALID_TRANSITIONS.get(current_status, [])
        if body.to_status not in allowed and not body.force:
            raise HTTPException(
                400,
                f"cannot transition from '{current_status}' to '{body.to_status}'. "
                f"Allowed: {allowed}. Use force=true to override.",
            )

        await s.execute(
            text("UPDATE brands SET status = :st WHERE id = :id"),
            {"st": body.to_status, "id": brand_id},
        )

        await s.execute(
            text(
                "INSERT INTO lead_events (brand_id, event_type, from_status, to_status, note, actor) "
                "VALUES (:brand_id, 'status_change', :from_st, :to_st, :note, :actor)"
            ),
            {
                "brand_id": brand_id,
                "from_st": current_status,
                "to_st": body.to_status,
                "note": body.note,
                "actor": body.actor,
            },
        )

        # Create notification for important transitions
        if body.to_status in ("meeting", "won", "lost"):
            await s.execute(
                text(
                    "INSERT INTO notifications (type, title, message, brand_id) "
                    "VALUES (:type, :title, :message, :brand_id)"
                ),
                {
                    "type": f"lifecycle.{body.to_status}",
                    "title": f"Lead moved to {body.to_status}",
                    "message": f"Brand #{brand_id} transitioned from {current_status} to {body.to_status}",
                    "brand_id": brand_id,
                },
            )

    return {"brand_id": brand_id, "from": current_status, "to": body.to_status}


@router.get("/{brand_id}/timeline")
async def get_timeline(brand_id: int, limit: int = Query(default=50, ge=1, le=200)) -> dict:
    async with session_ctx() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT * FROM lead_events "
                    "WHERE brand_id = :brand_id "
                    "ORDER BY created_at DESC LIMIT :limit"
                ),
                {"brand_id": brand_id, "limit": limit},
            )
        ).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.get("/pipeline-stages")
async def pipeline_stages() -> dict:
    """Get count of brands in each pipeline stage + total estimated deal value."""
    async with session_ctx() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT b.status, count(*) as count, "
                    "  coalesce(sum((d.data->'score'->>'estimated_deal_value_inr')::bigint), 0) as total_value "
                    "FROM brands b "
                    "LEFT JOIN LATERAL ("
                    "  SELECT data FROM dossiers WHERE brand_id = b.id ORDER BY version DESC LIMIT 1"
                    ") d ON true "
                    "WHERE b.status IN ('new','qualified','contacted','responded','meeting','proposal','won','lost','parked') "
                    "GROUP BY b.status"
                )
            )
        ).mappings().all()
    stages = {s: {"count": 0, "total_value": 0} for s in VALID_STATUSES}
    for r in rows:
        stages[r["status"]] = {"count": r["count"], "total_value": int(r["total_value"])}
    return {"stages": stages, "valid_transitions": VALID_TRANSITIONS}
