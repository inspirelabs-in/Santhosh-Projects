"""Domain-event repository: raw persistence for the durable work backbone.

Most application code should call ``src.db.events.emit_event`` /
``resolve_event`` (which also push a live update to Redis). These are the
underlying queries: append an event, read the actionable subset (the inbox feed),
read an application's full trace, and resolve events.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import DomainEvent


async def add(
    session: AsyncSession,
    *,
    type: str,
    org_id: UUID | None = None,
    application_id: UUID | None = None,
    role_id: UUID | None = None,
    payload: dict | None = None,
    actor: str = "system",
    requires_action: bool = False,
) -> DomainEvent:
    ev = DomainEvent(
        type=str(type),
        org_id=org_id,
        application_id=application_id,
        role_id=role_id,
        payload=payload or {},
        actor=actor,
        requires_action=requires_action,
    )
    session.add(ev)
    await session.flush()
    return ev


async def open_actionable(
    session: AsyncSession, org_id: UUID, *, limit: int = 200
) -> list[DomainEvent]:
    """Unresolved events that need a human — the raw inbox feed."""
    result = await session.scalars(
        select(DomainEvent)
        .where(
            DomainEvent.org_id == org_id,
            DomainEvent.requires_action.is_(True),
            DomainEvent.resolved_at.is_(None),
        )
        .order_by(DomainEvent.created_at.desc())
        .limit(limit)
    )
    return list(result)


async def for_application(
    session: AsyncSession, application_id: UUID, *, limit: int = 500
) -> list[DomainEvent]:
    """Full chronological trace for a candidate's application."""
    result = await session.scalars(
        select(DomainEvent)
        .where(DomainEvent.application_id == application_id)
        .order_by(DomainEvent.created_at.asc())
        .limit(limit)
    )
    return list(result)


async def resolve(session: AsyncSession, event_id: int, *, by: str) -> DomainEvent | None:
    ev = await session.get(DomainEvent, event_id)
    if ev is not None and ev.resolved_at is None:
        ev.resolved_at = func.now()
        ev.resolved_by = by
    return ev


async def resolve_open_for_application(
    session: AsyncSession, application_id: UUID, type: str, *, by: str
) -> int:
    """Close any open actionable events of a type for an application (e.g. when
    the app advances past the gate that created them). Returns count resolved."""
    rows = await session.scalars(
        select(DomainEvent).where(
            DomainEvent.application_id == application_id,
            DomainEvent.type == str(type),
            DomainEvent.requires_action.is_(True),
            DomainEvent.resolved_at.is_(None),
        )
    )
    n = 0
    for ev in rows:
        ev.resolved_at = func.now()
        ev.resolved_by = by
        n += 1
    return n


async def resolve_open_for_stage(
    session: AsyncSession, application_id: UUID, stage_key: str, *, by: str
) -> int:
    """Close open actionable events tied to a specific stage of an application
    (called when the candidate advances PAST that stage's gate). Matches on the
    event payload's ``stage_key``. Returns the count resolved."""
    rows = await session.scalars(
        select(DomainEvent).where(
            DomainEvent.application_id == application_id,
            DomainEvent.requires_action.is_(True),
            DomainEvent.resolved_at.is_(None),
            DomainEvent.payload["stage_key"].astext == str(stage_key),
        )
    )
    n = 0
    for ev in rows:
        ev.resolved_at = func.now()
        ev.resolved_by = by
        n += 1
    return n
