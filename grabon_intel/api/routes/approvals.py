"""HITL approvals queue."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from ...db import session as session_ctx

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalDecision(BaseModel):
    status: str  # approved | rejected | edited
    reviewer: str
    notes: str | None = None
    diff: dict[str, Any] | None = None


@router.get("")
async def list_pending(
    status_eq: str = Query(default="pending", alias="status"),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    async with session_ctx() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT id, entity_type, entity_id, brand_id, status, reviewer, "
                    "notes, created_at, decided_at FROM approvals WHERE status = :st "
                    "ORDER BY created_at ASC LIMIT :l"
                ),
                {"st": status_eq, "l": limit},
            )
        ).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.post("/{approval_id}/decide")
async def decide(approval_id: int, body: ApprovalDecision) -> dict:
    if body.status not in {"approved", "rejected", "edited"}:
        raise HTTPException(422, "invalid status")
    async with session_ctx() as s:
        row = (
            await s.execute(
                text(
                    "UPDATE approvals SET status = :st, reviewer = :rv, notes = :nt, "
                    "diff = CAST(:df AS JSONB), decided_at = NOW() "
                    "WHERE id = :id AND status = 'pending' "
                    "RETURNING id, status, decided_at"
                ),
                {
                    "id": approval_id,
                    "st": body.status,
                    "rv": body.reviewer,
                    "nt": body.notes,
                    "df": _json(body.diff) if body.diff is not None else None,
                },
            )
        ).mappings().first()
    if not row:
        raise HTTPException(409, "approval not pending or not found")
    return dict(row)


def _json(obj: Any) -> str:
    import orjson

    return orjson.dumps(obj).decode("utf-8")
