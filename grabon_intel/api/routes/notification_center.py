"""Notification center — in-app notification management."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import text

from ...db import session as session_ctx

router = APIRouter(prefix="/notification-center", tags=["notifications"])


@router.get("")
async def list_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    where = "WHERE read = false" if unread_only else ""
    sql = text(
        f"SELECT n.*, b.name as brand_name "
        f"FROM notifications n "
        f"LEFT JOIN brands b ON b.id = n.brand_id "
        f"{where} "
        f"ORDER BY n.created_at DESC "
        f"LIMIT :limit OFFSET :offset"
    )
    async with session_ctx() as s:
        rows = (await s.execute(sql, {"limit": limit, "offset": offset})).mappings().all()
        count_row = (await s.execute(text("SELECT count(*) FROM notifications WHERE read = false"))).scalar()
    return {"items": [dict(r) for r in rows], "unread_count": count_row or 0}


@router.post("/read/{notification_id}")
async def mark_read(notification_id: int) -> dict:
    async with session_ctx() as s:
        await s.execute(
            text("UPDATE notifications SET read = true WHERE id = :id"),
            {"id": notification_id},
        )
    return {"id": notification_id, "read": True}


@router.post("/read-all")
async def mark_all_read() -> dict:
    async with session_ctx() as s:
        result = await s.execute(text("UPDATE notifications SET read = true WHERE read = false RETURNING id"))
        count = len(result.fetchall())
    return {"marked_read": count}


class NotificationCreate(BaseModel):
    type: str
    title: str
    message: str
    brand_id: int | None = None
    entity_type: str | None = None
    entity_id: int | None = None


@router.post("")
async def create_notification(body: NotificationCreate) -> dict:
    async with session_ctx() as s:
        row = (
            await s.execute(
                text(
                    "INSERT INTO notifications (type, title, message, brand_id, entity_type, entity_id) "
                    "VALUES (:type, :title, :message, :brand_id, :entity_type, :entity_id) "
                    "RETURNING id, type, title, created_at"
                ),
                body.model_dump(),
            )
        ).mappings().first()
    return dict(row)
