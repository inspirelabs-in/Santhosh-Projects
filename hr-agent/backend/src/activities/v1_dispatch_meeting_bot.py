"""Dispatch a meeting-bot session for a scheduled Teams interview.

Behaviour depends on ``settings.meeting_bot_provider``:

* ``readai`` (default) -- creates the ``meeting_sessions`` row only. Read.ai
  joins the meeting via its calendar OAuth integration with the configured
  organiser mailbox; no HTTP call is made here. Completion is signalled
  through the ``/webhooks/meeting/readai`` endpoint.
* ``recall`` (legacy) -- additionally schedules a Recall.ai bot to join a
  configurable lead-time before the meeting starts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID

from src.config import get_settings
from src.db.base import Application
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.meeting_session import attach_bot, create_session, mark_failed
from src.db.repositories.v1_application import set_stage
from src.models.v1 import MeetingRound, PipelineStage
from src.services.meeting_bot import get_meeting_bot_provider

logger = logging.getLogger(__name__)


_ROUND_TO_STAGE = {
    MeetingRound.TECHNICAL.value: PipelineStage.TECHNICAL_MEETING_SCHEDULED,
    MeetingRound.CEO.value: PipelineStage.CEO_MEETING_SCHEDULED,
    MeetingRound.HR.value: PipelineStage.HR_MEETING_SCHEDULED,
}


async def dispatch_meeting_bot(
    *,
    application_id: UUID,
    round: str,
    teams_join_url: str,
    scheduled_at: datetime,
    interview_id: UUID | None = None,
) -> UUID:
    settings = get_settings()
    if not settings.enable_meeting_analysis:
        raise RuntimeError("meeting analysis disabled (ENABLE_MEETING_ANALYSIS=false)")
    if round not in _ROUND_TO_STAGE:
        raise ValueError(f"invalid round {round!r}")

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise ValueError(f"application {application_id} not found")

        row = await create_session(
            session,
            application_id=application_id,
            interview_id=interview_id,
            round=round,
            teams_join_url=teams_join_url,
            scheduled_at=scheduled_at,
            bot_provider=settings.meeting_bot_provider,
        )
        meeting_session_id = row.id
        await set_stage(
            session, application_id, _ROUND_TO_STAGE[round], force=True
        )
        await log_audit(
            session,
            application_id=application_id,
            action="meeting_session_created",
            actor="hr",
            details={
                "meeting_session_id": str(meeting_session_id),
                "round": round,
                "scheduled_at": scheduled_at.isoformat(),
            },
        )

    provider_name = settings.meeting_bot_provider
    if provider_name == "recall":
        lead = timedelta(seconds=settings.recall_bot_join_lead_seconds)
        join_at = (scheduled_at - lead).isoformat()
        webhook_url = f"{settings.app_base_url.rstrip('/')}/webhooks/meeting/recall"
        display_name = settings.recall_bot_display_name
    else:  # readai (calendar-trigger) or future providers
        join_at = scheduled_at.isoformat()
        webhook_url = f"{settings.app_base_url.rstrip('/')}/webhooks/meeting/readai"
        display_name = "GrabOn AI Notetaker"

    provider = get_meeting_bot_provider()
    try:
        handle = await provider.dispatch_bot(
            meeting_session_id=meeting_session_id,
            join_url=teams_join_url,
            scheduled_at=join_at,
            webhook_url=webhook_url,
            display_name=display_name,
        )
    except Exception as exc:  # noqa: BLE001
        async with session_scope() as session:
            await mark_failed(session, meeting_session_id, error=str(exc))
            await log_audit(
                session,
                application_id=application_id,
                action="meeting_bot_dispatch_failed",
                actor="agent",
                details={"error": str(exc)[:500], "provider": provider_name},
            )
        raise

    # For Read.ai (calendar-trigger), the synthetic bot_id is just the
    # meeting_session_id placeholder -- skip attach so the real Read.ai
    # session_id can be stored when the first webhook fires (via
    # webhooks_meeting.py time-window matcher).
    if provider_name != "readai":
        async with session_scope() as session:
            await attach_bot(session, meeting_session_id, bot_id=handle.bot_id)
    return meeting_session_id
