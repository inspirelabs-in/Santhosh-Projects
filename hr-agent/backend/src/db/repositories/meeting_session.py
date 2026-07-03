"""Persistence helpers for the meeting_sessions table."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants.statuses import ACTIVE_MEETING_SESSION_BOT_STATUSES
from src.db.base import MeetingSession


async def create_session(
    session: AsyncSession,
    *,
    application_id: UUID,
    interview_id: UUID | None,
    round: str,
    teams_join_url: str,
    scheduled_at: datetime | None,
    bot_provider: str = "recall",
) -> MeetingSession:
    row = MeetingSession(
        application_id=application_id,
        interview_id=interview_id,
        round=round,
        teams_join_url=teams_join_url,
        scheduled_at=scheduled_at,
        bot_provider=bot_provider,
        bot_status="pending",
    )
    session.add(row)
    await session.flush()
    return row


async def get_session(
    session: AsyncSession, meeting_session_id: UUID
) -> MeetingSession | None:
    return await session.get(MeetingSession, meeting_session_id)


async def get_by_bot_id(
    session: AsyncSession, *, bot_id: str
) -> MeetingSession | None:
    stmt = select(MeetingSession).where(MeetingSession.bot_id == bot_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_by_join_url(
    session: AsyncSession,
    *,
    join_url: str,
    statuses: tuple[str, ...] = ACTIVE_MEETING_SESSION_BOT_STATUSES,
) -> MeetingSession | None:
    """Match a Read.ai report to its meeting_session by Teams join URL.
    Limited to non-terminal statuses to avoid re-triggering completed runs.
    """
    stmt = (
        select(MeetingSession)
        .where(MeetingSession.teams_join_url == join_url)
        .where(MeetingSession.bot_status.in_(statuses))
        .order_by(MeetingSession.scheduled_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_by_time_window(
    session: AsyncSession,
    *,
    start_time: datetime,
    window_minutes: int = 30,
    statuses: tuple[str, ...] = ACTIVE_MEETING_SESSION_BOT_STATUSES,
) -> MeetingSession | None:
    """Match a Read.ai webhook to its meeting_session by start_time +/- window.
    Used when the provider doesn't send the Teams join URL (Read.ai). Picks the
    most recently scheduled non-terminal meeting whose scheduled_at falls in
    the window. Returns None if no plausible candidate exists.
    """
    from datetime import timedelta as _td
    lo = start_time - _td(minutes=window_minutes)
    hi = start_time + _td(minutes=window_minutes)
    stmt = (
        select(MeetingSession)
        .where(MeetingSession.scheduled_at.between(lo, hi))
        .where(MeetingSession.bot_status.in_(statuses))
        .order_by(MeetingSession.scheduled_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def attach_bot(
    session: AsyncSession, meeting_session_id: UUID, *, bot_id: str
) -> None:
    row = await session.get(MeetingSession, meeting_session_id)
    if row is None:
        raise ValueError(f"meeting_session {meeting_session_id} not found")
    row.bot_id = bot_id
    row.bot_status = "scheduled"


async def mark_started(session: AsyncSession, meeting_session_id: UUID) -> None:
    row = await session.get(MeetingSession, meeting_session_id)
    if row is None:
        return
    row.bot_status = "in_call"
    if row.started_at is None:
        row.started_at = datetime.now(UTC)


async def save_artifacts(
    session: AsyncSession,
    meeting_session_id: UUID,
    *,
    transcript_r2_key: str | None,
    recording_r2_key: str | None,
    duration_sec: float | None,
    ended_at: datetime,
    participants: list[dict[str, Any]] | None,
) -> None:
    row = await session.get(MeetingSession, meeting_session_id)
    if row is None:
        raise ValueError(f"meeting_session {meeting_session_id} not found")
    row.transcript_r2_key = transcript_r2_key
    row.recording_r2_key = recording_r2_key
    row.duration_sec = duration_sec
    row.ended_at = ended_at
    row.participants = participants
    row.bot_status = "done"


async def save_analysis(
    session: AsyncSession,
    meeting_session_id: UUID,
    *,
    report: dict[str, Any],
    llm_report: str,
    technical_score: int | None,
    communication_score: int | None,
    confidence_score: int | None,
    overall_score: int | None,
    verdict: str | None,
    candidate_emotion_timeline: list[dict[str, Any]] | None,
) -> None:
    row = await session.get(MeetingSession, meeting_session_id)
    if row is None:
        raise ValueError(f"meeting_session {meeting_session_id} not found")
    row.report = report
    row.llm_report = llm_report
    row.technical_score = technical_score
    row.communication_score = communication_score
    row.confidence_score = confidence_score
    row.overall_score = overall_score
    row.verdict = verdict
    row.candidate_emotion_timeline = candidate_emotion_timeline


async def mark_failed(
    session: AsyncSession, meeting_session_id: UUID, *, error: str
) -> None:
    row = await session.get(MeetingSession, meeting_session_id)
    if row is None:
        return
    row.bot_status = "failed"
    row.error = error[:2000]
