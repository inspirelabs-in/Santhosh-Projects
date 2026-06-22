"""[SCRAPE/RETIRED] Typed, durable event bus backed by supervisor_events table.

RETIRED with the supervisor: publish_event already no-ops when
enable_supervisor=False (the default). Superseded by src/db/events.py.

Original docs:


Events are published (written to Postgres) and consumed by the supervisor
engine via claim-based polling. Dedup keys prevent duplicate events from
concurrent publishers (e.g., two webhooks for the same voice call).

Usage:
    await publish_event("stage_changed", application_id=..., payload={...})
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import SupervisorEvent

logger = logging.getLogger(__name__)


class EventType(StrEnum):
    # Pipeline
    STAGE_CHANGED = "stage_changed"
    EVIDENCE_ADDED = "evidence_added"
    STALL_DETECTED = "stall_detected"
    CONTRADICTION_DETECTED = "contradiction_detected"

    # Voice
    VOICE_CALL_COMPLETED = "voice_call_completed"
    VOICE_CALLBACK_REQUESTED = "voice_callback_requested"
    VOICEMAIL_DETECTED = "voicemail_detected"
    VOICE_CALL_FAILED = "voice_call_failed"

    # Candidate
    CANDIDATE_MESSAGE_RECEIVED = "candidate_message_received"
    CANDIDATE_EMAIL_RECEIVED = "candidate_email_received"
    CANDIDATE_WITHDRAWAL = "candidate_withdrawal"

    # Meeting
    MEETING_COMPLETED = "meeting_completed"
    MEETING_NO_SHOW = "meeting_no_show"
    PANEL_UNAVAILABLE = "panel_unavailable"
    INTERVIEW_FEEDBACK_RECEIVED = "interview_feedback_received"

    # Assignment
    ASSIGNMENT_SUBMITTED = "assignment_submitted"
    ASSIGNMENT_OVERDUE = "assignment_overdue"

    # Admin / System
    ROLE_FROZEN = "role_frozen"
    BUDGET_CHANGED = "budget_changed"
    CONFIG_UPDATED = "config_updated"
    HR_DECISION_MADE = "hr_decision_made"
    ALERT_UNRESOLVED = "alert_unresolved"

    # Screening
    SCREENING_SUBMITTED = "screening_submitted"
    SCREENING_EVALUATED = "screening_evaluated"


def _make_dedup_key(
    event_type: str,
    application_id: UUID | None,
    extra: str = "",
) -> str:
    raw = f"{event_type}:{application_id or 'none'}:{extra}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


async def publish_event(
    session: AsyncSession,
    event_type: str | EventType,
    *,
    application_id: UUID | None = None,
    candidate_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
    dedup_extra: str = "",
) -> SupervisorEvent | None:
    """Publish an event. Returns the event row, or None if dedup suppressed it."""
    from src.config import get_settings
    settings = get_settings()
    if not settings.enable_supervisor:
        return None

    dedup_key = _make_dedup_key(str(event_type), application_id, dedup_extra)

    stmt = (
        pg_insert(SupervisorEvent)
        .values(
            event_type=str(event_type),
            application_id=application_id,
            candidate_id=candidate_id,
            payload=payload or {},
            dedup_key=dedup_key,
            status="pending",
        )
        .on_conflict_do_nothing(index_elements=["dedup_key"])
        .returning(SupervisorEvent.id)
    )
    result = await session.execute(stmt)
    row_id = result.scalar_one_or_none()

    if row_id is None:
        logger.debug("event deduped: %s app=%s", event_type, application_id)
        return None

    await session.flush()
    event = await session.get(SupervisorEvent, row_id)
    logger.info("event published: %s id=%s app=%s", event_type, row_id, application_id)
    return event


async def claim_pending_events(
    session: AsyncSession,
    worker_id: str,
    batch_size: int = 10,
) -> list[SupervisorEvent]:
    """Claim up to batch_size pending events for processing.

    Uses UPDATE ... WHERE status='pending' RETURNING for atomic claim.
    """
    subq = (
        select(SupervisorEvent.id)
        .where(SupervisorEvent.status == "pending")
        .order_by(SupervisorEvent.created_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    ids = (await session.execute(subq)).scalars().all()
    if not ids:
        return []

    now = datetime.now(UTC)
    await session.execute(
        update(SupervisorEvent)
        .where(SupervisorEvent.id.in_(ids))
        .values(status="claimed", claimed_by=worker_id, claimed_at=now)
    )
    await session.flush()

    result = await session.execute(
        select(SupervisorEvent).where(SupervisorEvent.id.in_(ids))
    )
    return list(result.scalars().all())


async def mark_processed(
    session: AsyncSession,
    event_id: UUID,
    error: str | None = None,
) -> None:
    status = "failed" if error else "processed"
    await session.execute(
        update(SupervisorEvent)
        .where(SupervisorEvent.id == event_id)
        .values(
            status=status,
            processed_at=datetime.now(UTC),
            error=error,
        )
    )
    await session.flush()
