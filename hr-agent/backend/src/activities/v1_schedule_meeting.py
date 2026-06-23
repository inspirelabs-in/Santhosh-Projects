"""AI-driven meeting scheduling for technical / CEO / HR rounds.

End-to-end flow when HR triggers ``schedule_meeting(application_id, round)``:

  1. Read ``role.scoring_rubric.scheduling`` -- panel + windows + duration.
  2. ``find_candidate_slot()`` picks the first free slot that respects
     panel windows + existing meeting load + (if available) Graph free/busy.
  3. ``create_online_meeting()`` mints a Teams join link via Graph.
  4. Persist a ``meeting_sessions`` row (provider=manual since the bot
     dispatch comes later; the row holds ``teams_join_url`` so the bot can
     pick it up).
  5. Email the panel *and* the candidate with the proposed slot + Teams link.
  6. Place a confirmation **call** to the candidate via the same ElevenLabs
     agent (``mode=confirmation``). The voice webhook captures
     ``CONFIRM=yes/no/reschedule`` markers. On accept we move the stage to
     ``*_meeting_scheduled``; on reject + alternative we recurse with the
     candidate's proposed time as ``avoid_starts`` to skip the rejected slot
     and pick the next one.

Stages used:
  technical -> TECHNICAL_MEETING_SCHEDULED
  ceo       -> CEO_MEETING_SCHEDULED
  hr        -> NEEDS_HR_REVIEW (HR round is final-step; we still send
              invites + book the meeting, but stage stays in HR queue)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select

from src.channels.email import send_email
from src.config import get_settings
from src.db.base import Application, Candidate, MeetingSession, Role, VoiceCall
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.meeting_session import create_session, attach_bot
from src.db.repositories.v1_application import set_stage, claim_stage_processing, record_stage_verdict, mark_stage_failed
from src.db.repositories.voice_call import create_voice_call, mark_dispatched
from src.models.scheduling import RoleScheduling
from src.models.v1 import MeetingRound, PipelineStage, VoiceCallStatus
from src.services.scheduling import find_candidate_slot, ProposedSlot
from src.services.online_meeting import (
    create_online_meeting,
    get_organiser_email,
)
from src.services.voice_provider import VoiceCallSpec, get_voice_provider

logger = logging.getLogger(__name__)


_ROUND_TO_STAGE = {
    "technical": PipelineStage.TECHNICAL_MEETING_SCHEDULED,
    "ceo": PipelineStage.CEO_MEETING_SCHEDULED,
    "hr": PipelineStage.HR_MEETING_SCHEDULED,
}


def _confirmation_prompt(
    *,
    company: str,
    role_title: str,
    round_name: str,
    candidate_name: str,
    slot_human: str,
    teams_link: str,
) -> str:
    return (
        f"You are the {company} hiring assistant calling {candidate_name} to confirm "
        f"their {round_name} interview for the {role_title} role.\n\n"
        f"Proposed time: {slot_human}\n"
        f"Teams link (already emailed): {teams_link}\n\n"
        "Conduct:\n"
        "1. Greet, confirm you have a minute, then state the proposed time.\n"
        "2. Ask: 'Does that time work for you?'\n"
        "3. If yes -- thank them, remind them to check their email for the Teams link, "
        "and end the call. In your reply state literally 'CONFIRM=yes' on its own line.\n"
        "4. If they want a different time -- ask for a specific date + time, repeat it "
        "back to confirm, end the call. State 'CONFIRM=reschedule; REQUESTED_AT=<ISO 8601>' on its own line.\n"
        "5. If they decline outright -- thank them, end the call. State 'CONFIRM=no'.\n\n"
        "Keep the entire call under 90 seconds. Never invent slot details. "
        "Never promise outcomes outside the slot question."
    )


def _slot_human(start: datetime, panel_tz: str) -> str:
    from zoneinfo import ZoneInfo

    local = start.astimezone(ZoneInfo(panel_tz))
    return local.strftime("%A, %d %B at %I:%M %p %Z")


async def schedule_meeting(
    *,
    application_id: UUID,
    round: str,
    avoid_starts: list[datetime] | None = None,
    attempt_no: int = 1,
) -> UUID:
    """Plan, book, and confirm one interview round.

    Returns the new ``meeting_sessions.id``. Raises if the role lacks a
    scheduling configuration for this round, or no slot can be found.
    """

    if round not in {"technical", "ceo", "hr"}:
        raise ValueError(f"unknown round {round!r}")

    settings = get_settings()
    if not settings.enable_meeting_analysis and round != "hr":
        raise RuntimeError("meeting scheduling disabled (ENABLE_MEETING_ANALYSIS=false)")

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise ValueError(f"application {application_id} not found")
        candidate = await session.get(Candidate, app.candidate_id)
        if candidate is None or not candidate.email or not candidate.phone:
            raise ValueError("candidate missing email or phone -- cannot schedule")
        role = await session.get(Role, app.role_id) if app.role_id else None
        if role is None:
            raise ValueError("role missing")

        # Idempotency: skip if a non-terminal meeting already exists for this round.
        existing = (
            await session.execute(
                select(MeetingSession)
                .where(
                    MeetingSession.application_id == application_id,
                    MeetingSession.round == round,
                    MeetingSession.bot_status.in_(("pending", "scheduled", "in_call")),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            logger.info(
                "schedule_meeting skipped: active %s meeting already exists (id=%s)",
                round,
                existing.id,
            )
            return existing.id

        cfg = RoleScheduling.from_role_rubric(role.scoring_rubric)
        if not cfg.enabled:
            raise RuntimeError(
                f"role {role.id} has scheduling disabled -- enable it in role config first"
            )
        round_cfg = cfg.rounds.get(round)  # type: ignore[arg-type]
        if round_cfg is None:
            raise RuntimeError(
                f"role {role.id} round={round} has no scheduling block configured"
            )

        # Fall back to the workspace PanelMember directory when the role
        # didn't override panel emails. Lets HR maintain a single source of
        # truth at /settings/panels instead of repeating emails per role.
        if not round_cfg.panel_emails:
            from src.db.base import PanelMember

            members = (
                await session.execute(
                    select(PanelMember).where(
                        PanelMember.role_type == round,
                        PanelMember.is_active.is_(True),
                    )
                )
            ).scalars().all()
            if members:
                round_cfg = round_cfg.model_copy(
                    update={"panel_emails": [m.email for m in members]}
                )

        # Default availability windows when role didn't specify -- weekday
        # 11 AM - 6 PM in panel timezone is a sane workspace-wide default.
        if not round_cfg.windows:
            from src.models.scheduling import AvailabilityWindow

            round_cfg = round_cfg.model_copy(
                update={
                    "windows": [
                        AvailabilityWindow(
                            days=[0, 1, 2, 3, 4],
                            start_hhmm="11:00",
                            end_hhmm="18:00",
                        )
                    ]
                }
            )

        if not round_cfg.panel_emails:
            raise RuntimeError(
                f"role {role.id} round={round} has no panel configured AND "
                "no workspace panel members are registered for this round type. "
                "Add members at /settings/panels."
            )

        slot = await find_candidate_slot(
            session,
            round_cfg=round_cfg,
            panel_tz=cfg.panel_timezone,
            horizon_days=cfg.horizon_business_days,
            min_lead_hours=cfg.min_lead_hours,
            avoid_starts=avoid_starts,
        )
        if slot is None:
            # No slot in panel free-busy + windows. Fall back to emailing the
            # panel for manual options. HR resolves via /agentic/meeting/manual-schedule.
            cand_name_for_email = candidate.name or "the candidate"
            cand_phone_for_email = candidate.phone or ""
            await log_audit(
                session,
                application_id=application_id,
                action="meeting_no_slot_fell_back_to_email",
                actor="agent",
                details={"round": round, "attempt_no": attempt_no},
            )
            from src.services.typed_event_bus import EventType, publish_event
            await publish_event(
                session, EventType.PANEL_UNAVAILABLE,
                application_id=application_id,
                candidate_id=app.candidate_id,
                payload={"round": round, "attempt_no": attempt_no, "panel_emails": list(round_cfg.panel_emails)},
                dedup_extra=f"panel_unavail:{application_id}:{round}",
            )
            await set_stage(
                session,
                application_id,
                PipelineStage.NEEDS_HR_REVIEW,
                force=True,
            )
            # Send the panel a request for slots. Outside the session.
            panel_emails_snapshot = list(round_cfg.panel_emails)
            role_title_snapshot = role.title
            cfg_horizon = cfg.horizon_business_days
            await _request_panel_slots(
                application_id=application_id,
                round=round,
                role_title=role_title_snapshot,
                candidate_name=cand_name_for_email,
                candidate_phone=cand_phone_for_email,
                panel_emails=panel_emails_snapshot,
                horizon_days=cfg_horizon,
            )
            raise RuntimeError(
                f"no available slot in the next {cfg.horizon_business_days} business days; "
                "panel was emailed for manual options, candidate parked for HR review"
            )

        candidate_email = candidate.email
        candidate_phone = candidate.phone
        candidate_name = candidate.name or "Candidate"
        candidate_id = candidate.id
        role_title = role.title
        organiser_email = (
            get_organiser_email()
            or round_cfg.panel_emails[0]
        )

    # Mint Teams meeting outside the DB session.
    subject = f"{settings.voice_agent_company_name} -- {round.title()} interview · {candidate_name} ({role_title})"
    join_url, ms_meeting_id = await create_online_meeting(
        organiser_email=organiser_email,
        subject=subject,
        start_utc=slot.start,
        end_utc=slot.end,
        attendee_emails=[*slot.panel_emails, candidate_email],
    )

    # Persist meeting session.
    async with session_scope() as session:
        meeting_row = await create_session(
            session,
            application_id=application_id,
            interview_id=None,
            round=round,
            teams_join_url=join_url,
            scheduled_at=slot.start,
            bot_provider="recall",
        )
        meeting_session_id = meeting_row.id
        meeting_row.bot_id = ms_meeting_id  # store Graph meeting id alongside the bot id slot
        if round in _ROUND_TO_STAGE:
            # Claim processing BEFORE committing the stage advance (P-1).
            stage_key = {"technical": "technical", "ceo": "ceo", "hr": "hr"}.get(round, round)
            await claim_stage_processing(session, application_id, stage_key)
            await set_stage(session, application_id, _ROUND_TO_STAGE[round], force=True)
            from src.models.pipeline import StageStatus
            _app = await session.get(Application, application_id)
            if _app is not None:
                _app.current_stage_key = stage_key
                _app.stage_status = str(StageStatus.SCHEDULED)
        await log_audit(
            session,
            application_id=application_id,
            action="meeting_slot_proposed",
            actor="agent",
            details={
                "round": round,
                "scheduled_at": slot.start.isoformat(),
                "join_url": join_url,
                "panel_emails": slot.panel_emails,
                "attempt_no": attempt_no,
            },
        )

    # Email panel + candidate.
    slot_human = _slot_human(slot.start, cfg.panel_timezone)
    invite_vars = {
        "candidate_name": candidate_name,
        "role_title": role_title,
        "round_label": round.title(),
        "slot_human": slot_human,
        "teams_link": join_url,
        "company_name": settings.voice_agent_company_name,
        "duration_minutes": round_cfg.duration_minutes,
    }
    try:
        cand_result = await send_email(
            to=candidate_email,
            template="meeting_invite_candidate",
            variables=invite_vars,
            tags={"category": "meeting_invite", "round": round},
            idempotency_key=f"{application_id}:meeting_invite_candidate:{round}:{int(slot.start.timestamp())}",
            application_id=str(application_id),
            candidate_id=str(candidate_id),
        )
        if not cand_result.success:
            async with session_scope() as session:
                await log_audit(
                    session,
                    application_id=application_id,
                    action="meeting_invite_email_failed",
                    actor="agent",
                    details={
                        "round": round,
                        "to": candidate_email,
                        "error": (cand_result.error or "")[:300],
                        "provider": cand_result.provider,
                    },
                )
        for panel_email in slot.panel_emails:
            await send_email(
                to=panel_email,
                template="meeting_invite_panel",
                variables={**invite_vars, "candidate_email": candidate_email},
                tags={"category": "meeting_invite_panel", "round": round},
                idempotency_key=f"{application_id}:meeting_invite_panel:{round}:{panel_email}:{int(slot.start.timestamp())}",
                application_id=str(application_id),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("invite email failed: %s", exc)

    # Meeting booked + invites sent -> record on_going verdict (P-1).
    _sk = {"technical": "technical", "ceo": "ceo", "hr": "hr"}.get(round, round)
    try:
        async with session_scope() as session:
            await record_stage_verdict(
                session, application_id, _sk,
                verdict="on_going",
                result_ref={"meeting_session_id": str(meeting_session_id)},
            )
    except Exception:  # noqa: BLE001
        logger.debug("stage verdict write failed for %s (non-fatal)", _sk)

    # Confirmation call to the candidate (gated). V1 default: skip — candidate
    # already got the meeting invite email + Teams link, no need for a phone
    # call. Re-enable via ENABLE_VOICE_MEETING_CONFIRMATION=true once voice
    # budget allows.
    voice_call_id: UUID | None = None
    if not settings.enable_voice_meeting_confirmation:
        logger.info(
            "skipping voice confirmation call for %s (voice scope = screening only)",
            application_id,
        )
    else:
        try:
            async with session_scope() as session:
                voice_row = await create_voice_call(
                    session,
                    application_id=application_id,
                    candidate_phone=candidate_phone,
                    questions=[],
                    scheduled_at=datetime.now(timezone.utc),
                    attempt_no=attempt_no,
                    provider="elevenlabs",
                    call_kind="confirmation",
                )
                voice_call_id = voice_row.id
                voice_row.status = VoiceCallStatus.PENDING.value

            spec = VoiceCallSpec(
                application_id=application_id,
                voice_call_id=voice_call_id,
                candidate_name=candidate_name,
                candidate_phone=candidate_phone,
                role_title=role_title,
                company_name=settings.voice_agent_company_name,
                questions=[],
                system_prompt=_confirmation_prompt(
                    company=settings.voice_agent_company_name,
                    role_title=role_title,
                    round_name=round,
                    candidate_name=candidate_name,
                    slot_human=slot_human,
                    teams_link=join_url,
                ),
                webhook_url=f"{settings.app_base_url.rstrip('/')}/webhooks/voice/elevenlabs",
                max_seconds=180,
                mode="confirmation",
                extra_dynamic_variables={
                    "round": round,
                    "meeting_session_id": str(meeting_session_id),
                    "scheduled_at": slot.start.isoformat(),
                },
            )
            provider = get_voice_provider()
            handle = await provider.create_call(spec)
            async with session_scope() as session:
                await mark_dispatched(
                    session, voice_call_id, provider_call_id=handle.provider_call_id
                )
                await log_audit(
                    session,
                    candidate_id=candidate_id,
                    application_id=application_id,
                    action="meeting_confirmation_call_dispatched",
                    actor="agent",
                    details={
                        "voice_call_id": str(voice_call_id),
                        "meeting_session_id": str(meeting_session_id),
                        "round": round,
                    },
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("confirmation call failed: %s", exc)

    # Dispatch the meeting bot for ALL rounds (technical, CEO, HR).
    # Read.ai (default): no HTTP call -- joins via calendar OAuth; the
    # report lands on /webhooks/meeting/readai.
    # Recall.ai (legacy): bot joins ~2 min before scheduled_at, posts
    # to /webhooks/meeting/recall.
    if settings.enable_meeting_analysis:
        try:
            from datetime import timedelta as _td
            from src.db.repositories.meeting_session import attach_bot, mark_failed
            from src.services.meeting_bot import get_meeting_bot_provider

            if settings.meeting_bot_provider == "recall":
                join_at = (slot.start - _td(seconds=settings.recall_bot_join_lead_seconds)).isoformat()
                webhook_url = f"{settings.app_base_url.rstrip('/')}/webhooks/meeting/recall"
                display_name = settings.recall_bot_display_name
            else:
                join_at = slot.start.isoformat()
                webhook_url = f"{settings.app_base_url.rstrip('/')}/webhooks/meeting/readai"
                display_name = "GrabOn AI Notetaker"
            provider = get_meeting_bot_provider()
            handle = await provider.dispatch_bot(
                meeting_session_id=meeting_session_id,
                join_url=join_url,
                scheduled_at=join_at,
                webhook_url=webhook_url,
                display_name=display_name,
            )
            async with session_scope() as session:
                # Read.ai joins via calendar -- bot_id stays empty until the
                # first webhook arrives and time-window-matches this row.
                if settings.meeting_bot_provider != "readai":
                    await attach_bot(session, meeting_session_id, bot_id=handle.bot_id)
                await log_audit(
                    session,
                    application_id=application_id,
                    action="meeting_bot_dispatched",
                    actor="agent",
                    details={
                        "meeting_session_id": str(meeting_session_id),
                        "round": round,
                        "bot_id": handle.bot_id,
                        "provider": settings.meeting_bot_provider,
                    },
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("recall bot dispatch failed for %s: %s", round, exc)
            try:
                async with session_scope() as session:
                    await mark_failed(session, meeting_session_id, error=str(exc)[:500])
                    await log_audit(
                        session,
                        application_id=application_id,
                        action="meeting_bot_dispatch_failed",
                        actor="agent",
                        details={"round": round, "error": str(exc)[:500]},
                    )
            except Exception:  # noqa: BLE001
                pass

    return meeting_session_id



async def _request_panel_slots(
    *,
    application_id: UUID,
    round: str,
    role_title: str,
    candidate_name: str,
    candidate_phone: str,
    panel_emails: list[str],
    horizon_days: int,
) -> None:
    """Email the panel asking for 2-3 slot options when auto-scheduling fails.

    Sent as a single email to all panel members. HR resolves by collecting
    replies (Slack/email) and using the manual-schedule endpoint:
    POST /agentic/meeting/manual-schedule.
    """
    if not panel_emails:
        return
    settings = get_settings()
    front_origin = settings.frontend_base_url.rstrip("/")
    candidate_url = f"{front_origin}/candidates/{application_id}"
    body_text = (
        f"Hi team,\n\n"
        f"Auto-scheduling could not find a slot for the {round} interview "
        f"with {candidate_name} ({candidate_phone}) for the {role_title} role "
        f"in the next {horizon_days} business days.\n\n"
        f"Please reply with 2-3 options (date + start time IST) you can offer. "
        f"HR will book one and send the calendar invite.\n\n"
        f"Candidate page: {candidate_url}\n"
    )
    try:
        await send_email(
            to=", ".join(panel_emails),
            template="panel_slot_request",
            variables={
                "round": round,
                "role_title": role_title,
                "candidate_name": candidate_name,
                "candidate_phone": candidate_phone,
                "horizon_days": horizon_days,
                "candidate_url": candidate_url,
                "body_text": body_text,
            },
            tags={
                "category": "panel_slot_request",
                "application_id": str(application_id),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("panel slot request email failed: %s", exc)
