"""Durable event backbone — ``emit_event`` / ``resolve_event``.

This is the ONE place application code writes a domain event. It persists the
event (Postgres = source of truth) and best-effort pushes a live update to Redis
so the UI updates in realtime (``services.events.publish_event``). A Redis
failure is non-fatal — the durable row is what matters, which is exactly what
fixes the "alerts lost on reload / buried in the profile" bug class.

The derived inbox reads unresolved actionable events; ``resolve_event`` closes
them when a human acts or when the application advances past the gate.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import DomainEvent
from src.db.repositories import domain_event as event_repo
from src.services.events import publish_event

logger = logging.getLogger(__name__)


async def emit_event(
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
    """Append a domain event (durable) and push a live update (best-effort)."""
    ev = await event_repo.add(
        session,
        type=type,
        org_id=org_id,
        application_id=application_id,
        role_id=role_id,
        payload=payload,
        actor=actor,
        requires_action=requires_action,
    )
    # Durable row is written; the Redis push is only for realtime UI and must
    # never block or fail the caller.
    if application_id is not None:
        try:
            await publish_event(application_id, event=str(type), data=payload or {})
        except Exception as exc:  # noqa: BLE001
            logger.debug("emit_event live push failed (non-fatal): %s", exc)
    return ev


async def resolve_event(
    session: AsyncSession, event_id: int, *, by: str
) -> DomainEvent | None:
    """Mark an actionable event resolved (it leaves the inbox)."""
    return await event_repo.resolve(session, event_id, by=by)
