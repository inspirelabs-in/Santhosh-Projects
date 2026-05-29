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


async def _check_stuck_voice_calls() -> int:
    """Find voice calls that were dispatched but never got a webhook callback."""
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
                        VoiceCall.status.in_(("pending", "dialing")),
                        VoiceCall.created_at < cutoff,
                        Application.current_stage.in_((
                            "voice_screen_scheduled",
                            "voice_screen_in_progress",
                        )),
                        Application.status.notin_(("rejected", "hired", "withdrawn")),
                    )
                )
            )
        ).all()

        for call, app in stuck_calls:
            hours = (now - call.created_at).total_seconds() / 3600
            call.status = "timeout"
            call.error = f"No webhook callback received after {hours:.1f}h"

    # Park applications outside the session to avoid long transactions
    for call, app in stuck_calls:
        hours = (now - call.created_at).total_seconds() / 3600
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
                        Application.status.notin_(("rejected", "hired", "withdrawn")),
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
                        Application.status.notin_(("rejected", "hired", "withdrawn")),
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
    await asyncio.sleep(600)  # initial delay
    while True:
        try:
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
        await asyncio.sleep(1800)  # 30 minutes
