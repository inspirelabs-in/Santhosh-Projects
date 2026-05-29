"""Interview row repository."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import Interview
from src.models.scheduling import InterviewStatus


async def create_interview(
    session: AsyncSession,
    *,
    application_id: UUID,
    scheduled_at: datetime | None,
    calendar_event_id: str | None,
    meeting_link: str | None,
    status: InterviewStatus = InterviewStatus.PROPOSED,
) -> Interview:
    row = Interview(
        application_id=application_id,
        scheduled_at=scheduled_at,
        calendar_event_id=calendar_event_id,
        meeting_link=meeting_link,
        status=status.value,
    )
    session.add(row)
    await session.flush()
    return row


async def get_interview(session: AsyncSession, interview_id: UUID) -> Interview | None:
    return await session.get(Interview, interview_id)


async def get_latest_for_application(
    session: AsyncSession, application_id: UUID
) -> Interview | None:
    return await session.scalar(
        select(Interview)
        .where(Interview.application_id == application_id)
        .order_by(Interview.created_at.desc())
        .limit(1)
    )
