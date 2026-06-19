"""Action-overlay repository: the mutable human layer over *derived* inbox items.

The inbox itself is derived (a query over domain_events + application state), so
this table only holds what derivation can't: snooze / assign / dismiss, keyed by
a stable ``action_key`` (e.g. 'app:{id}:assessment_review' or 'event:{id}').
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import ActionOverlay


async def get(session: AsyncSession, action_key: str) -> ActionOverlay | None:
    return await session.scalar(
        select(ActionOverlay).where(ActionOverlay.action_key == action_key)
    )


async def get_or_create(
    session: AsyncSession, *, action_key: str, org_id: UUID | None = None
) -> ActionOverlay:
    row = await get(session, action_key)
    if row is None:
        row = ActionOverlay(action_key=action_key, org_id=org_id)
        session.add(row)
        await session.flush()
    return row


async def snooze(
    session: AsyncSession, *, action_key: str, until: datetime, org_id: UUID | None = None
) -> ActionOverlay:
    row = await get_or_create(session, action_key=action_key, org_id=org_id)
    row.snoozed_until = until
    return row


async def dismiss(
    session: AsyncSession, *, action_key: str, when: datetime, org_id: UUID | None = None
) -> ActionOverlay:
    row = await get_or_create(session, action_key=action_key, org_id=org_id)
    row.dismissed_at = when
    return row


async def assign(
    session: AsyncSession,
    *,
    action_key: str,
    user_id: UUID | None,
    org_id: UUID | None = None,
) -> ActionOverlay:
    row = await get_or_create(session, action_key=action_key, org_id=org_id)
    row.assigned_to = user_id
    return row
