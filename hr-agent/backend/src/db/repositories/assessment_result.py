"""Persistence helpers for the assessment_results table."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import AssessmentResult


async def create_invite(
    session: AsyncSession,
    *,
    application_id: UUID,
    provider: str,
    assessment_kind: str,
    external_assessment_id: str,
    invite_url: str,
) -> AssessmentResult:
    row = AssessmentResult(
        application_id=application_id,
        provider=provider,
        assessment_kind=assessment_kind,
        external_assessment_id=external_assessment_id,
        invite_url=invite_url,
        invite_sent_at=datetime.now(UTC),
        status="invited",
    )
    session.add(row)
    await session.flush()
    return row


async def get_by_external_id(
    session: AsyncSession, *, provider: str, external_assessment_id: str
) -> AssessmentResult | None:
    stmt = select(AssessmentResult).where(
        AssessmentResult.provider == provider,
        AssessmentResult.external_assessment_id == external_assessment_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_for_application(
    session: AsyncSession, application_id: UUID
) -> list[AssessmentResult]:
    stmt = (
        select(AssessmentResult)
        .where(AssessmentResult.application_id == application_id)
        .order_by(AssessmentResult.created_at.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def save_completion(
    session: AsyncSession,
    assessment_id: UUID,
    *,
    raw_result: dict[str, Any],
    normalized: dict[str, Any],
    normalized_score: float | None,
    percentile: float | None,
    fit_band: str | None,
    completed_at: datetime,
) -> None:
    row = await session.get(AssessmentResult, assessment_id)
    if row is None:
        raise ValueError(f"assessment_result {assessment_id} not found")
    row.raw_result = raw_result
    row.normalized = normalized
    row.normalized_score = normalized_score
    row.percentile = percentile
    row.fit_band = fit_band
    row.completed_at = completed_at
    row.status = "completed"


async def mark_started(session: AsyncSession, assessment_id: UUID) -> None:
    row = await session.get(AssessmentResult, assessment_id)
    if row is None:
        return
    row.status = "in_progress"
    if row.started_at is None:
        row.started_at = datetime.now(UTC)


async def mark_failed(
    session: AsyncSession, assessment_id: UUID, *, status: str = "failed"
) -> None:
    row = await session.get(AssessmentResult, assessment_id)
    if row is None:
        return
    row.status = status
