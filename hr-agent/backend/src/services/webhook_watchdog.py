"""Webhook watchdog — detects applications waiting for callbacks that never arrive.

Runs every 30 minutes. Catches:
  - Voice calls in VOICE_SCREEN_SCHEDULED/IN_PROGRESS with no webhook for >2h
  - Meeting sessions with bot_status=pending for >1h past scheduled_at
  - Assessment results in status=invited with no completion for >7 days

When a timeout is detected, the application is parked at NEEDS_HR_REVIEW
with full audit trail explaining what happened.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select

from src.constants.external import ELEVENLABS_API_BASE
from src.constants.statuses import TERMINAL_APPLICATION_STATUSES
from src.constants.timers import (
    WEBHOOK_WATCHDOG_INITIAL_DELAY_SECONDS,
    WEBHOOK_WATCHDOG_LOOP_INTERVAL_SECONDS,
)
from src.db.base import (
    Application,
    AssessmentResult,
    MeetingSession,
    VoiceCall,
)
from src.db.connection import session_scope
from src.services.fallback_manager import webhook_timeout_fallback

logger = logging.getLogger(__name__)


VOICE_CALL_TIMEOUT_HOURS = 2.0
MEETING_BOT_TIMEOUT_HOURS = 1.5
ASSESSMENT_TIMEOUT_DAYS = 7


async def _try_recover_from_elevenlabs(call: VoiceCall) -> bool:
    """Try to fetch a completed conversation from ElevenLabs and process it.

    Returns True if the conversation was successfully recovered.
    """
    import httpx

    from src.config import get_settings

    settings = get_settings()
    if not settings.elevenlabs_api_key or not call.provider_call_id:
        return False

    url = f"{ELEVENLABS_API_BASE}/convai/conversations/{call.provider_call_id}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                url, headers={"xi-api-key": settings.elevenlabs_api_key}
            )
            if resp.status_code == 404:
                return False
            resp.raise_for_status()
            conv_data = resp.json()
    except Exception:
        logger.warning("elevenlabs API fetch failed for %s", call.provider_call_id)
        return False

    el_status = conv_data.get("status", "unknown")
    if el_status not in ("done", "ended"):
        return False

    # Conversation completed on ElevenLabs — trigger recovery endpoint
    from src.api.webhooks_voice import (
        ElData,
        ElMetadata,
        ElPayload,
        ElTranscriptTurn,
        _fetch_and_store_recording,
        _store_transcript,
        _transcript_text,
        _zip_answers,
    )
    from src.db.repositories.audit import log_audit
    from src.db.repositories.v1_application import set_stage
    from src.db.repositories.voice_call import save_call_completion
    from src.db.repositories.voice_call import claim_processing, mark_processing_done, mark_processing_failed
    from src.models.v1 import PipelineStage
    from src.services.events import publish_event
    from src.services.queue import enqueue

    raw_transcript = conv_data.get("transcript") or []
    turns = [
        ElTranscriptTurn(
            role=t.get("role", "user"),
            message=t.get("message") or t.get("text") or "",
            time_in_call_secs=t.get("time_in_call_secs"),
        )
        for t in raw_transcript
        if isinstance(t, dict)
    ]

    el_metadata = conv_data.get("metadata") or {}
    duration_sec = el_metadata.get("call_duration_secs")

    transcript_text = _transcript_text(turns)
    transcript_key = await _store_transcript(call.application_id, call.id, transcript_text)
    answers = _zip_answers(call.questions, turns)
    recording_key = await _fetch_and_store_recording(
        call.application_id, call.id, call.provider_call_id
    )

    call_kind = getattr(call, "call_kind", "screening")
    is_informational = call_kind in ("status_update", "joining_details", "general_query", "meeting_schedule")

    # Idempotency guard (V-C3 + V-C1): claim the call so the live webhook or the
    # /recover endpoint cannot double-run the evaluator for the same conversation.
    async with session_scope() as session:
        claimed = await claim_processing(session, call.id)
    if not claimed:
        logger.info(
            "watchdog: voice_call %s already processed/claimed, skipping", call.id
        )
        return False

    try:
        async with session_scope() as session:
            await save_call_completion(
                session,
                call.id,
                answers=answers,
                transcript_r2_key=transcript_key,
                recording_r2_key=recording_key,
                duration_sec=duration_sec,
                ended_at=datetime.now(UTC),
            )
            if not is_informational:
                await set_stage(
                    session, call.application_id, PipelineStage.VOICE_SCREEN_COMPLETED, force=True
                )
            await log_audit(
                session,
                application_id=call.application_id,
                action="voice_call_auto_recovered",
                actor="system",
                details={
                    "voice_call_id": str(call.id),
                    "conversation_id": call.provider_call_id,
                    "duration_sec": duration_sec,
                    "answer_count": len(answers),
                },
            )

        if not is_informational and call_kind != "confirmation":
            from src.activities.v1_evaluate_voice_call import evaluate_voice_call

            await enqueue("evaluate_voice_call", str(call.id))

        async with session_scope() as session:
            await mark_processing_done(session, call.id)
    except Exception:
        async with session_scope() as session:
            await mark_processing_failed(session, call.id)
        raise

    await publish_event(
        call.application_id,
        event="voice_call_completed",
        data={
            "voice_call_id": str(call.id),
            "duration_sec": duration_sec,
            "answer_count": len(answers),
            "auto_recovered": True,
        },
    )
    logger.info(
        "watchdog: auto-recovered voice_call=%s conversation=%s from elevenlabs",
        call.id, call.provider_call_id,
    )
    return True


async def _check_stuck_voice_calls() -> int:
    """Find voice calls that were dispatched but never got a webhook callback.

    First tries to recover the conversation from ElevenLabs API directly.
    Only parks for HR review if the conversation can't be recovered.
    """
    recovered = 0
    now = datetime.now(tz=UTC)
    cutoff = now - timedelta(hours=VOICE_CALL_TIMEOUT_HOURS)

    async with session_scope() as session:
        stuck_calls = (
            await session.execute(
                select(VoiceCall, Application)
                .join(Application, Application.id == VoiceCall.application_id)
                .where(
                    and_(
                        VoiceCall.status.in_(("pending", "dialing", "in_progress")),
                        VoiceCall.created_at < cutoff,
                        Application.current_stage.in_((
                            "voice_screen_scheduled",
                            "voice_screen_in_progress",
                        )),
                        Application.status.notin_(TERMINAL_APPLICATION_STATUSES),
                        or_(
                            Application.current_stage_key.is_(None),
                            Application.current_stage_key.notin_(("rejected", "hired")),
                        ),
                    )
                )
            )
        ).all()

    for call, app in stuck_calls:
        # Try to recover from ElevenLabs API first
        try:
            if await _try_recover_from_elevenlabs(call):
                recovered += 1
                continue
        except Exception:
            logger.exception("elevenlabs recovery failed for voice_call=%s", call.id)

        # Recovery failed — mark as timeout and park for HR
        hours = (now - call.created_at).total_seconds() / 3600
        async with session_scope() as session:
            row = await session.get(VoiceCall, call.id)
            if row is not None:
                row.status = "timeout"
                row.error = f"No webhook callback received after {hours:.1f}h"
        try:
            await webhook_timeout_fallback(
                app.id,
                expected_event="voice_call_completion",
                waited_hours=hours,
            )
            recovered += 1
        except Exception:
            logger.exception("Failed to recover stuck voice call %s", call.id)

    return recovered


async def _check_stuck_meetings() -> int:
    """Find meeting bots that were dispatched but never reported back."""
    recovered = 0
    now = datetime.now(tz=UTC)

    detached: list[tuple] = []
    async with session_scope() as session:
        stuck = (
            await session.execute(
                select(MeetingSession, Application)
                .join(Application, Application.id == MeetingSession.application_id)
                .where(
                    and_(
                        MeetingSession.bot_status.in_(("pending", "joining")),
                        or_(
                            and_(
                                MeetingSession.scheduled_at.isnot(None),
                                MeetingSession.scheduled_at < now - timedelta(
                                    hours=MEETING_BOT_TIMEOUT_HOURS
                                ),
                            ),
                            and_(
                                MeetingSession.scheduled_at.is_(None),
                                MeetingSession.created_at < now - timedelta(hours=3),
                            ),
                        ),
                        Application.status.notin_(TERMINAL_APPLICATION_STATUSES),
                        # V2 terminal cursor: a rejected/hired candidate may not have
                        # its legacy `status` flipped, so exclude by cursor too.
                        or_(
                            Application.current_stage_key.is_(None),
                            Application.current_stage_key.notin_(("rejected", "hired")),
                        ),
                    )
                )
            )
        ).all()
        for meeting, app in stuck:
            detached.append((
                meeting.id, meeting.scheduled_at, meeting.created_at,
                meeting.round, app.id,
            ))

    for mid, scheduled_at, created_at, round_name, app_id in detached:
        ref_time = scheduled_at or created_at
        hours = (now - ref_time).total_seconds() / 3600
        # Mark the session timed-out FIRST so a subsequent watchdog pass can't
        # re-detect the same stuck meeting and re-park/re-notify forever. This is
        # the loop-breaker: without it, every run re-alerts the same meeting.
        async with session_scope() as session:
            row = await session.get(MeetingSession, mid)
            if row is not None:
                row.bot_status = "timeout"
                row.error = f"Bot never reported back after {hours:.1f}h"
        try:
            await webhook_timeout_fallback(
                app_id,
                expected_event=f"meeting_bot_{round_name}_completion",
                waited_hours=hours,
            )
            recovered += 1
        except Exception:
            logger.exception("Failed to recover stuck meeting %s", mid)

    return recovered


async def _check_stuck_assessments() -> int:
    """Find assessments invited but never completed."""
    recovered = 0
    now = datetime.now(tz=UTC)
    cutoff = now - timedelta(days=ASSESSMENT_TIMEOUT_DAYS)

    detached: list[tuple] = []
    async with session_scope() as session:
        stuck = (
            await session.execute(
                select(AssessmentResult, Application)
                .join(Application, Application.id == AssessmentResult.application_id)
                .where(
                    and_(
                        AssessmentResult.status == "invited",
                        AssessmentResult.created_at < cutoff,
                        Application.status.notin_(TERMINAL_APPLICATION_STATUSES),
                        or_(
                            Application.current_stage_key.is_(None),
                            Application.current_stage_key.notin_(("rejected", "hired")),
                        ),
                    )
                )
            )
        ).all()
        for assessment, app in stuck:
            detached.append((
                assessment.id, assessment.created_at, assessment.provider, app.id,
            ))

    for aid, created_at, provider, app_id in detached:
        hours = (now - created_at).total_seconds() / 3600
        try:
            await webhook_timeout_fallback(
                app_id,
                expected_event=f"assessment_{provider}_completion",
                waited_hours=hours,
            )
            recovered += 1
        except Exception:
            logger.exception("Failed to recover stuck assessment %s", aid)

    return recovered


async def run_webhook_watchdog() -> None:
    """Background loop. Runs every 30 minutes."""
    from src.db.repositories.voice_call import sweep_stale_processing

    await asyncio.sleep(WEBHOOK_WATCHDOG_INITIAL_DELAY_SECONDS)  # initial delay
    while True:
        try:
            try:
                async with session_scope() as session:
                    await sweep_stale_processing(session, older_than_minutes=15)
            except Exception:
                logger.warning(
                    "webhook watchdog: stale-claim sweep failed", exc_info=True
                )
            v = await _check_stuck_voice_calls()
            m = await _check_stuck_meetings()
            a = await _check_stuck_assessments()
            if v or m or a:
                logger.info(
                    "webhook watchdog: recovered %d voice, %d meeting, %d assessment",
                    v, m, a,
                )
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("webhook watchdog crashed")
        await asyncio.sleep(WEBHOOK_WATCHDOG_LOOP_INTERVAL_SECONDS)  # 30 minutes
