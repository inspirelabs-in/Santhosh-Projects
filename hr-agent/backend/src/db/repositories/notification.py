"""Notification repository: the FYI feed (the 🔔 bell).

Informational counterpart to action items: read/unread, no resolution. Created
from informational domain events; surfaced in the notifications bell.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import Notification


async def create(
    session: AsyncSession,
    *,
    type: str,
    title: str,
    org_id: UUID | None = None,
    user_id: UUID | None = None,
    body: str | None = None,
    link: str | None = None,
) -> Notification:
    n = Notification(
        type=str(type),
        title=title,
        org_id=org_id,
        user_id=user_id,
        body=body,
        link=link,
    )
    session.add(n)
    await session.flush()
    return n


async def list_for_org(
    session: AsyncSession, org_id: UUID, *, unread_only: bool = False, limit: int = 50
) -> list[Notification]:
    stmt = select(Notification).where(Notification.org_id == org_id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
    return list(await session.scalars(stmt))


async def mark_read(session: AsyncSession, notification_id: UUID) -> Notification | None:
    n = await session.get(Notification, notification_id)
    if n is not None and n.read_at is None:
        n.read_at = func.now()
    return n


async def unread_count(session: AsyncSession, org_id: UUID) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.org_id == org_id, Notification.read_at.is_(None))
    )
    return int(count or 0)
