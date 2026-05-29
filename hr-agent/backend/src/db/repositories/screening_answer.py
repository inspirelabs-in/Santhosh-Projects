"""V2 screening_answers repository.

One row per application. Upsert semantics so the agent can patch fields as
the conversation reveals them (e.g. CTC discovered in turn 3, notice in
turn 5) without orchestrating insert vs update.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import ScreeningAnswerRow


# Fields that can be patched incrementally by the agent.
_PATCHABLE_FIELDS = {
    "tailored_q1",
    "tailored_a1",
    "tailored_q2",
    "tailored_a2",
    "current_ctc_lpa",
    "expected_ctc_lpa",
    "notice_period_days",
    "willing_to_relocate",
    "evaluation",
    "composite_score",
    "knock_out_triggered",
    "knock_out_reason",
}


async def get(
    session: AsyncSession, application_id: UUID
) -> ScreeningAnswerRow | None:
    result = await session.execute(
        select(ScreeningAnswerRow).where(
            ScreeningAnswerRow.application_id == application_id
        )
    )
    return result.scalar_one_or_none()


async def upsert(
    session: AsyncSession,
    application_id: UUID,
    **fields: Any,
) -> ScreeningAnswerRow:
    """Insert-or-patch. Only known fields are written."""
    clean = {k: v for k, v in fields.items() if k in _PATCHABLE_FIELDS and v is not None}

    stmt = (
        pg_insert(ScreeningAnswerRow)
        .values(application_id=application_id, **clean)
        .on_conflict_do_update(
            index_elements=["application_id"],
            set_={**clean, "updated_at": datetime.now(tz=UTC)},
        )
    )
    await session.execute(stmt)
    row = await get(session, application_id)
    assert row is not None
    return row


async def mark_submitted(
    session: AsyncSession,
    application_id: UUID,
) -> None:
    row = await get(session, application_id)
    if row is None:
        raise ValueError(f"screening_answers missing for {application_id}")
    row.submitted_at = datetime.now(tz=UTC)


def is_complete(row: ScreeningAnswerRow | None) -> bool:
    """True when the 6 required fields are filled."""
    if row is None:
        return False
    required = (
        row.tailored_a1,
        row.tailored_a2,
        row.current_ctc_lpa,
        row.expected_ctc_lpa,
        row.notice_period_days,
        row.willing_to_relocate,
    )
    return all(v is not None for v in required)
