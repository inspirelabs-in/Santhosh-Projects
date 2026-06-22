"""Voice-call persistence helpers.

Activities should use these instead of touching VoiceCall directly so that
state transitions, score writes, and callback scheduling stay in one place.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from src.db.base import VoiceCall
from src.models.v1 import ProcessingStatus, VoiceCallStatus


_TERMINAL_STATUSES = {
    VoiceCallStatus.COMPLETED.value,
    VoiceCallStatus.FAILED.value,
    VoiceCallStatus.NO_ANSWER.value,
    VoiceCallStatus.DECLINED.value,
    VoiceCallStatus.CALLBACK_REQUESTED.value,
}


async def next_attempt_no(session: AsyncSession, application_id: UUID) -> int:
    """Return ``max(attempt_no) + 1`` for a given application, default 1."""
    stmt = select(func.coalesce(func.max(VoiceCall.attempt_no), 0)).where(
        VoiceCall.application_id == application_id
    )
    current = (await session.execute(stmt)).scalar_one()
    return int(current) + 1


async def cancel_in_flight(session: AsyncSession, application_id: UUID) -> int:
    """Mark any pending/dialing/in-progress rows as failed before re-dispatch.

    Prevents a list of stale "attempt 1 / dialing" cards piling up in the UI
    when the recruiter clicks Phone screen multiple times.
    """
    stmt = select(VoiceCall).where(
        VoiceCall.application_id == application_id,
        VoiceCall.status.in_(
            [
                VoiceCallStatus.PENDING.value,
                VoiceCallStatus.DIALING.value,
                VoiceCallStatus.IN_PROGRESS.value,
            ]
        ),
    )
    rows = (await session.execute(stmt)).scalars().all()
    for row in rows:
        row.status = VoiceCallStatus.FAILED.value
        row.error = "superseded by new dispatch"
    return len(rows)


async def create_voice_call(
    session: AsyncSession,
    *,
    application_id: UUID,
    candidate_phone: str | None,
    questions: list[dict[str, Any]],
    scheduled_at: datetime | None,
    attempt_no: int = 1,
    provider: str = "elevenlabs",
    call_kind: str = "screening",
) -> VoiceCall:
    row = VoiceCall(
        application_id=application_id,
        provider=provider,
        candidate_phone=candidate_phone,
        questions=questions,
        scheduled_at=scheduled_at,
        attempt_no=attempt_no,
        status=VoiceCallStatus.PENDING.value,
        call_kind=call_kind,
    )
    session.add(row)
    await session.flush()
    return row


async def get_voice_call(session: AsyncSession, voice_call_id: UUID) -> VoiceCall | None:
    return await session.get(VoiceCall, voice_call_id)


async def get_by_provider_id(
    session: AsyncSession, *, provider: str, provider_call_id: str
) -> VoiceCall | None:
    stmt = select(VoiceCall).where(
        VoiceCall.provider == provider,
        VoiceCall.provider_call_id == provider_call_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def mark_dispatched(
    session: AsyncSession, voice_call_id: UUID, *, provider_call_id: str
) -> None:
    row = await session.get(VoiceCall, voice_call_id)
    if row is None:
        raise ValueError(f"voice_call {voice_call_id} not found")
    row.provider_call_id = provider_call_id
    row.status = VoiceCallStatus.DIALING.value


async def mark_in_progress(session: AsyncSession, voice_call_id: UUID) -> None:
    row = await session.get(VoiceCall, voice_call_id)
    if row is None:
        return
    row.status = VoiceCallStatus.IN_PROGRESS.value
    if row.started_at is None:
        from datetime import UTC

        row.started_at = datetime.now(UTC)


async def claim_completion(
    session: AsyncSession, voice_call_id: UUID, sentinel_key: str
) -> bool:
    """[SCRAPE] superseded by claim_processing (processing_status CAS); no live
    caller. The old transcript_r2_key sentinel could orphan a row on crash
    (V-C1) -- the processing_status guard + watchdog sweep replace it.

    Atomically claim webhook completion for ``voice_call_id``.

    First caller wins. Returns True if this caller claimed it, False if another
    concurrent webhook already did. Used as the idempotency primitive for the
    voice webhook handler so duplicate ElevenLabs deliveries do not run the
    evaluator twice. The sentinel is later overwritten with the real R2 key.
    """
    from sqlalchemy import update

    stmt = (
        update(VoiceCall)
        .where(VoiceCall.id == voice_call_id)
        .where(
            (VoiceCall.transcript_r2_key.is_(None))
            | (VoiceCall.transcript_r2_key == "")
        )
        .values(transcript_r2_key=sentinel_key)
    )
    result = await session.execute(stmt)
    # rowcount == 1 -> we claimed it. 0 -> someone else already wrote a key.
    return (result.rowcount or 0) > 0


async def save_call_completion(
    session: AsyncSession,
    voice_call_id: UUID,
    *,
    answers: list[dict[str, Any]],
    transcript_r2_key: str | None,
    recording_r2_key: str | None,
    duration_sec: float | None,
    ended_at: datetime,
) -> None:
    row = await session.get(VoiceCall, voice_call_id)
    if row is None:
        raise ValueError(f"voice_call {voice_call_id} not found")
    row.answers = answers
    row.transcript_r2_key = transcript_r2_key
    row.recording_r2_key = recording_r2_key
    row.duration_sec = duration_sec
    row.ended_at = ended_at
    row.status = VoiceCallStatus.COMPLETED.value


async def save_callback_request(
    session: AsyncSession,
    voice_call_id: UUID,
    *,
    callback_at: datetime,
    reason: str | None,
) -> None:
    row = await session.get(VoiceCall, voice_call_id)
    if row is None:
        raise ValueError(f"voice_call {voice_call_id} not found")
    row.status = VoiceCallStatus.CALLBACK_REQUESTED.value
    row.callback_at = callback_at
    row.callback_reason = reason


async def save_evaluation(
    session: AsyncSession,
    voice_call_id: UUID,
    *,
    evaluation: dict[str, Any],
    emotion_features: dict[str, Any] | None,
) -> None:
    row = await session.get(VoiceCall, voice_call_id)
    if row is None:
        raise ValueError(f"voice_call {voice_call_id} not found")
    row.evaluation = evaluation
    row.emotion_features = emotion_features
    row.overall_score = int(evaluation.get("overall_score") or 0) or None
    row.verdict = evaluation.get("verdict")


async def mark_failed(
    session: AsyncSession, voice_call_id: UUID, *, error: str, status: VoiceCallStatus
) -> None:
    row = await session.get(VoiceCall, voice_call_id)
    if row is None:
        return
    row.status = status.value
    if len(error) > 2000:
        logger.warning("voice call %s error truncated from %d to 2000 chars", voice_call_id, len(error))
    row.error = error[:2000]


# ---------------------------------------------------------------------------
# Result-processing idempotency guard.
#
# Three paths can try to ingest+evaluate one call's result (the live webhook, the
# /recover endpoint, the watchdog) and the provider re-delivers webhooks. The
# guard makes ingestion run EXACTLY ONCE: a caller atomically claims the row
# (pending|failed -> processing); the loser skips. After ingest the winner marks
# it processed (or failed, which is retryable). A crashed worker that leaves a row
# stuck in ``processing`` is swept back to ``pending`` by the watchdog.
# ---------------------------------------------------------------------------


async def claim_processing(session: AsyncSession, voice_call_id: UUID) -> bool:
    """Atomically claim a call's result for processing.

    UPDATE ... WHERE processing_status IN (pending, failed) -> processing.
    Returns True iff THIS caller won the claim (rowcount == 1). A caller that
    gets False must NOT ingest/evaluate (another path already owns it)."""
    from sqlalchemy import update

    stmt = (
        update(VoiceCall)
        .where(VoiceCall.id == voice_call_id)
        .where(
            VoiceCall.processing_status.in_(
                [ProcessingStatus.PENDING.value, ProcessingStatus.FAILED.value]
            )
        )
        .values(processing_status=ProcessingStatus.PROCESSING.value)
    )
    result = await session.execute(stmt)
    return (result.rowcount or 0) > 0


async def mark_processing_done(session: AsyncSession, voice_call_id: UUID) -> None:
    """Mark the result fully ingested + evaluator dispatched."""
    row = await session.get(VoiceCall, voice_call_id)
    if row is not None:
        row.processing_status = ProcessingStatus.PROCESSED.value


async def mark_processing_failed(session: AsyncSession, voice_call_id: UUID) -> None:
    """Release the claim as failed so a later path may retry."""
    row = await session.get(VoiceCall, voice_call_id)
    if row is not None:
        row.processing_status = ProcessingStatus.FAILED.value


async def sweep_stale_processing(session: AsyncSession, *, older_than_minutes: int = 15) -> int:
    """Reset rows stuck in ``processing`` (crashed mid-ingest) back to ``pending``
    so recovery can re-claim them. Returns the number reset. Called by the
    watchdog. Uses ``updated_at`` as the staleness clock."""
    from datetime import UTC, datetime, timedelta
    from sqlalchemy import update

    cutoff = datetime.now(UTC) - timedelta(minutes=older_than_minutes)
    stmt = (
        update(VoiceCall)
        .where(VoiceCall.processing_status == ProcessingStatus.PROCESSING.value)
        .where(VoiceCall.updated_at < cutoff)
        .values(processing_status=ProcessingStatus.PENDING.value)
    )
    result = await session.execute(stmt)
    n = result.rowcount or 0
    if n:
        logger.warning("swept %d stale 'processing' voice_calls back to pending", n)
    return n
