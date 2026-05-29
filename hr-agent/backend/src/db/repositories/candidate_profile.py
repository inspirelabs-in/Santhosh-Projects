"""CandidateProfile persistence (Stage 3 output)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import CandidateProfileRow
from src.models.candidate import CandidateProfile


async def upsert_candidate_profile(
    session: AsyncSession,
    *,
    candidate_id: UUID,
    profile: CandidateProfile,
    raw_resume_r2_key: str | None,
    embedding: list[float] | None = None,
) -> CandidateProfileRow:
    """Insert or replace the parsed profile for this candidate.

    We keep history by appending a new row rather than updating in place -- the
    latest row (by created_at) is the authoritative profile; older rows are
    audit evidence for re-parses.
    """
    row = CandidateProfileRow(
        candidate_id=candidate_id,
        raw_resume_r2_key=raw_resume_r2_key,
        parsed_data=profile.model_dump(mode="json"),
        extraction_confidence=profile.field_confidence.model_dump(mode="json"),
        embedding=embedding,
    )
    session.add(row)
    await session.flush()
    return row


async def get_latest_profile(
    session: AsyncSession, candidate_id: UUID
) -> CandidateProfileRow | None:
    return await session.scalar(
        select(CandidateProfileRow)
        .where(CandidateProfileRow.candidate_id == candidate_id)
        .order_by(CandidateProfileRow.created_at.desc())
        .limit(1)
    )
