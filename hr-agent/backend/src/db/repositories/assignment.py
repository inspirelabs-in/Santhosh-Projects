"""V2 assignments repository.

One row per application. Lifecycle: agent generates brief -> candidate sees
brief in chat -> candidate submits (text + URL + uploaded files) -> agent
evaluates.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import AssignmentRow


async def get(
    session: AsyncSession, application_id: UUID
) -> AssignmentRow | None:
    result = await session.execute(
        select(AssignmentRow).where(AssignmentRow.application_id == application_id)
    )
    return result.scalar_one_or_none()


async def save_generated(
    session: AsyncSession,
    application_id: UUID,
    *,
    brief_md: str,
    problems: list[dict[str, Any]],
    submission_format: dict[str, Any],
    evaluation_rubric: dict[str, Any] | None = None,
    deadline_days: int | None = None,
) -> AssignmentRow:
    payload: dict[str, Any] = {
        "brief_md": brief_md,
        "problems": problems,
        "submission_format": submission_format,
        "evaluation_rubric": evaluation_rubric,
        "generated_at": datetime.now(tz=UTC),
    }
    if deadline_days:
        from datetime import timedelta
        payload["deadline_at"] = datetime.now(tz=UTC) + timedelta(days=deadline_days)
    stmt = (
        pg_insert(AssignmentRow)
        .values(application_id=application_id, **payload)
        .on_conflict_do_update(
            index_elements=["application_id"],
            set_={**payload, "updated_at": datetime.now(tz=UTC)},
        )
    )
    await session.execute(stmt)
    row = await get(session, application_id)
    assert row is not None
    return row


async def attach_submission(
    session: AsyncSession,
    application_id: UUID,
    *,
    submission_url: str | None = None,
    submission_text: str | None = None,
    submission_r2_keys: list[str] | None = None,
) -> AssignmentRow:
    row = await get(session, application_id)
    if row is None:
        raise ValueError(f"assignment missing for {application_id}")
    if submission_url is not None:
        row.submission_url = submission_url
    if submission_text is not None:
        row.submission_text = submission_text
    if submission_r2_keys is not None:
        existing = list(row.submission_r2_keys or [])
        new_keys = [k for k in submission_r2_keys if k not in existing]
        existing.extend(new_keys)
        row.submission_r2_keys = existing
        await session.flush()
    if row.submitted_at is None:
        row.submitted_at = datetime.now(tz=UTC)
    return row


async def save_evaluation(
    session: AsyncSession,
    application_id: UUID,
    *,
    evaluation: dict[str, Any],
    score: int | None,
) -> None:
    row = await get(session, application_id)
    if row is None:
        raise ValueError(f"assignment missing for {application_id}")
    row.evaluation = evaluation
    row.score = score
