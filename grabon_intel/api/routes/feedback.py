"""Feedback capture: label + structured reasons.

Why a separate `feedback_reasons` table vs one column on `grabon_feedback`:
  - Multiple reasons per feedback row (e.g. "wrong industry" AND "no budget").
  - Aggregation across reasons feeds the weekly ICP-nudge job.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from ...db import session as session_ctx
from ...events import emit

router = APIRouter(prefix="/feedback", tags=["feedback"])


_LABELS = {"good_fit", "bad_fit", "meeting_booked", "closed_won", "closed_lost"}


class FeedbackBody(BaseModel):
    brand_id: int
    label: str
    reasons: list[str] = []
    note: str | None = None
    value_inr: int | None = None
    reviewer: str | None = None


@router.post("")
async def submit(body: FeedbackBody) -> dict:
    if body.label not in _LABELS:
        raise HTTPException(422, f"label must be one of {_LABELS}")
    async with session_ctx() as s:
        fid_row = (
            await s.execute(
                text(
                    "INSERT INTO grabon_feedback (brand_id, label, note, value_inr, reviewer) "
                    "VALUES (:b, :l, :n, :v, :r) RETURNING id"
                ),
                {"b": body.brand_id, "l": body.label, "n": body.note, "v": body.value_inr, "r": body.reviewer},
            )
        ).first()
        fid = int(fid_row[0])
        for category in body.reasons:
            await s.execute(
                text("INSERT INTO feedback_reasons (feedback_id, category) VALUES (:f, :c)"),
                {"f": fid, "c": category[:64]},
            )
        await emit(
            s,
            topic="feedback.captured",
            brand_id=body.brand_id,
            payload={"label": body.label, "reasons": body.reasons, "value_inr": body.value_inr},
        )
    return {"id": fid}


@router.get("/reasons/agg")
async def aggregate_reasons(
    label: str | None = Query(default=None),
    since_days: int = Query(default=90, ge=1, le=730),
) -> dict:
    where = "f.created_at > NOW() - make_interval(days => :d)"
    params: dict = {"d": since_days}
    if label:
        where += " AND f.label = :l"
        params["l"] = label
    async with session_ctx() as s:
        rows = (
            await s.execute(
                text(
                    f"SELECT r.category, COUNT(*) AS c, "
                    f"COUNT(*) FILTER (WHERE f.label = 'closed_lost') AS lost_c, "
                    f"COUNT(*) FILTER (WHERE f.label = 'closed_won') AS won_c "
                    f"FROM feedback_reasons r JOIN grabon_feedback f ON f.id = r.feedback_id "
                    f"WHERE {where} GROUP BY r.category ORDER BY c DESC LIMIT 50"
                ),
                params,
            )
        ).mappings().all()
    items = [dict(r) for r in rows]
    return {"window_days": since_days, "items": items, "icp_nudge": _suggest_nudge(items)}


def _suggest_nudge(rows: list[dict]) -> dict:
    """Cheap heuristic: reasons that show up disproportionately on closed_lost
    become weight-down candidates; on closed_won become weight-up."""
    out_down: list[str] = []
    out_up: list[str] = []
    for r in rows:
        c, lost, won = int(r["c"]), int(r["lost_c"]), int(r["won_c"])
        if c < 4:
            continue
        if lost / c >= 0.65 and lost - won >= 3:
            out_down.append(r["category"])
        elif won / c >= 0.55 and won - lost >= 3:
            out_up.append(r["category"])
    return {"weight_down": out_down[:8], "weight_up": out_up[:8]}
