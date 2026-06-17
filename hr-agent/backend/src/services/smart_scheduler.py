"""Smart agentic scheduler: zero-touch interview scheduling.

Flow:
  1. auto_progress hits meeting step -> calls initiate_smart_schedule()
  2. Resolves panel from workspace PanelMember directory (by role_type)
  3. Checks panel free/busy via Graph API + window expansion
  4. Picks top 3 available slots
  5. Dispatches voice call to candidate: "We have slots at X, Y, Z -- which works?"
  6. Post-call webhook extracts candidate choice -> handle_candidate_response()
  7a. If candidate picks a slot -> book_confirmed_meeting()
  7b. If candidate proposes alternative -> check_and_negotiate()
  7c. If no agreement after max_negotiation_attempts -> escalate_to_hr()

State is tracked on MeetingSession with a negotiation_state JSONB field.
Requires the ``negotiation_state`` column on ``meeting_sessions`` (JSONB, nullable).
Add via Alembic: ``op.add_column('meeting_sessions', sa.Column('negotiation_state', JSONB))``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.channels.email import send_email
from src.config import get_settings
from src.db.base import Application, Candidate, MeetingSession, PanelMember, Role, VoiceCall
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.meeting_session import attach_bot, create_session
from src.db.repositories.v1_application import set_stage
from src.models.scheduling import AvailabilityWindow, RoleScheduling, RoundScheduling
from src.models.v1 import CallKind, MeetingRound, PipelineStage
from src.services.events import publish_event
from src.services.online_meeting import create_online_meeting, get_organiser_email
from src.services.scheduling import (
    ProposedSlot,
    _expand_windows,
    _graph_busy_intervals,
    _slot_collides,
)
from src.services.typed_event_bus import EventType
from src.services.typed_event_bus import publish_event as publish_supervisor_event

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
MAX_NEGOTIATION_ATTEMPTS = 3
DEFAULT_HORIZON_DAYS = 14
DEFAULT_MIN_LEAD_HOURS = 18

# Maps meeting round -> pipeline stage for "scheduled"
_ROUND_TO_SCHEDULED_STAGE: dict[str, PipelineStage] = {
    MeetingRound.TECHNICAL: PipelineStage.TECHNICAL_MEETING_SCHEDULED,
    MeetingRound.CEO: PipelineStage.CEO_MEETING_SCHEDULED,
    MeetingRound.HR: PipelineStage.HR_MEETING_SCHEDULED,
}

# Default weekday availability when no windows are configured
_DEFAULT_WINDOWS = [
    AvailabilityWindow(days=[0, 1, 2, 3, 4], start_hhmm="11:00", end_hhmm="18:00"),
]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def initiate_smart_schedule(
    *,
    application_id: UUID,
    round: str,
) -> UUID:
    """Kick off agentic scheduling for a meeting round.

    Called from ``auto_progress._fire_meeting`` when the pipeline reaches a
    meeting step. Returns the created ``MeetingSession.id``.
    """
    settings = get_settings()

    async with session_scope() as session:
        # 1. Load entities
        app = await session.get(Application, application_id)
        if app is None:
            raise ValueError(f"application {application_id} not found")
        if app.role_id is None:
            raise ValueError("application has no role_id")

        candidate = await session.get(Candidate, app.candidate_id)
        if candidate is None:
            raise ValueError("candidate not found")

        role = await session.get(Role, app.role_id)
        if role is None:
            raise ValueError("role not found")

        # 2. Resolve panel
        round_cfg = await _resolve_panel_for_round(session, role, round)

        # 3. Check scheduling is viable
        role_sched = RoleScheduling.from_role_rubric(role.scoring_rubric)
        if not role_sched.enabled:
            logger.info(
                "smart_schedule: scheduling not enabled for role %s, escalating",
                role.id,
            )
            # Create a session just to track the escalation
            ms = await create_session(
                session,
                application_id=application_id,
                interview_id=None,
                round=round,
                teams_join_url="",
                scheduled_at=None,
            )
            ms.negotiation_state = {
                "status": "escalated",
                "reason": "scheduling_not_enabled",
            }
            await set_stage(session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True)
            await log_audit(
                session,
                application_id=application_id,
                candidate_id=candidate.id,
                action="smart_schedule_escalated",
                actor="agent",
                details={"reason": "scheduling_not_enabled", "round": round},
            )
            return ms.id

        panel_tz = role_sched.panel_timezone or "Asia/Kolkata"
        horizon = role_sched.horizon_business_days or DEFAULT_HORIZON_DAYS

        # 4. Find top 3 slots
        slots = await _find_top_slots(
            session,
            round_cfg=round_cfg,
            panel_tz=panel_tz,
            horizon=horizon,
            count=3,
        )

        # 5. If no slots, escalate
        if not slots:
            ms = await create_session(
                session,
                application_id=application_id,
                interview_id=None,
                round=round,
                teams_join_url="",
                scheduled_at=None,
            )
            ms.negotiation_state = {
                "status": "escalated",
                "reason": "no_available_slots",
                "attempt": 1,
            }
            await set_stage(session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True)
            await log_audit(
                session,
                application_id=application_id,
                candidate_id=candidate.id,
                action="smart_schedule_no_slots",
                actor="agent",
                details={"round": round, "horizon_days": horizon},
            )
            await publish_supervisor_event(
                session,
                EventType.PANEL_UNAVAILABLE,
                application_id=application_id,
                candidate_id=candidate.id,
                payload={"round": round, "reason": "no_available_slots"},
            )
            return ms.id

        # 6. Create MeetingSession
        proposed_data = [
            {
                "index": i + 1,
                "start": s.start.isoformat(),
                "end": s.end.isoformat(),
                "panel_emails": s.panel_emails,
            }
            for i, s in enumerate(slots)
        ]
        ms = await create_session(
            session,
            application_id=application_id,
            interview_id=None,
            round=round,
            teams_join_url="",
            scheduled_at=None,
        )
        ms.bot_status = "negotiating"
        ms.negotiation_state = {
            "status": "proposing",
            "proposed_slots": proposed_data,
            "attempt": 1,
            "max_attempts": MAX_NEGOTIATION_ATTEMPTS,
        }
        meeting_session_id = ms.id

        await log_audit(
            session,
            application_id=application_id,
            candidate_id=candidate.id,
            action="smart_schedule_initiated",
            actor="agent",
            details={
                "meeting_session_id": str(meeting_session_id),
                "round": round,
                "slot_count": len(slots),
                "proposed_slots": proposed_data,
            },
        )

        # 7. Dispatch voice call to candidate
        candidate_name = candidate.name or "Candidate"
        candidate_phone = candidate.phone
        role_title = role.title

    # Build prompt and dispatch outside the DB session to avoid long holds
    ctx = {
        "candidate_name": candidate_name,
        "role_title": role_title,
        "round": round,
        "company_name": settings.voice_agent_company_name,
    }
    system_prompt, first_message = _build_slot_proposal_prompt(
        context=ctx,
        slots=slots,
        round_name=round,
        attempt=1,
    )

    if candidate_phone:
        try:
            from src.activities.v1_dispatch_voice_call import dispatch_voice_call

            voice_call_id = await dispatch_voice_call(
                application_id=application_id,
                call_kind=CallKind.MEETING_SCHEDULE,
                attempt_no=1,
            )
            logger.info(
                "smart_schedule: dispatched voice call %s for session %s",
                voice_call_id,
                meeting_session_id,
            )
        except Exception:
            logger.exception(
                "smart_schedule: voice dispatch failed for session %s, "
                "will fall back to email",
                meeting_session_id,
            )
            # Fall back to email proposal
            await _send_slot_proposal_email(
                application_id=application_id,
                meeting_session_id=meeting_session_id,
                candidate_email=candidate.email,
                candidate_name=candidate_name,
                slots=slots,
                round_name=round,
                role_title=role_title,
            )
    else:
        # No phone -- email only
        await _send_slot_proposal_email(
            application_id=application_id,
            meeting_session_id=meeting_session_id,
            candidate_email=candidate.email,
            candidate_name=candidate_name,
            slots=slots,
            round_name=round,
            role_title=role_title,
        )

    # 8. Set stage to NEEDS_HR_REVIEW temporarily (scheduling in progress)
    async with session_scope() as session:
        await set_stage(session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True)

    await publish_event(
        application_id,
        event="smart_schedule_initiated",
        data={"round": round, "meeting_session_id": str(meeting_session_id)},
    )

    return meeting_session_id


# ---------------------------------------------------------------------------
# Panel resolution
# ---------------------------------------------------------------------------


async def _resolve_panel_for_round(
    session: AsyncSession,
    role: Role,
    round: str,
) -> RoundScheduling:
    """Resolve panel emails and windows for a given interview round.

    Priority:
      1. Explicit config in ``role.scoring_rubric.scheduling.rounds.<round>``
      2. PanelMember directory lookup by ``role_type`` matching the round
      3. Defaults: weekday 11:00-18:00 IST, 45-min duration
    """
    role_sched = RoleScheduling.from_role_rubric(role.scoring_rubric)
    round_cfg = role_sched.rounds.get(round, RoundScheduling())

    # If panel_emails already populated, we may still need to fill windows
    if round_cfg.panel_emails and round_cfg.windows:
        return round_cfg

    # Try smart panel matcher first, then fall back to all active members
    if not round_cfg.panel_emails:
        # Map round name to role_type for lookup
        role_type_map = {
            MeetingRound.TECHNICAL: "technical",
            MeetingRound.CEO: "ceo",
            MeetingRound.HR: "hr",
        }
        role_type = role_type_map.get(round, round)

        # Smart matcher: score members by expertise, seniority, department, load
        from src.services.panel_matcher import match_panel_for_role

        matched = await match_panel_for_role(
            session, role=role, round=round, count=5,
        )

        if matched:
            round_cfg = round_cfg.model_copy(
                update={"panel_emails": [p.email for p in matched]}
            )
            logger.info(
                "smart_schedule: matched %d panel members for round=%s via smart matcher",
                len(matched),
                round,
            )
        else:
            # Fall back to ALL active members of that role_type
            stmt = (
                select(PanelMember)
                .where(PanelMember.role_type == role_type)
                .where(PanelMember.is_active.is_(True))
            )
            panel_rows = (await session.execute(stmt)).scalars().all()

            if panel_rows:
                round_cfg = round_cfg.model_copy(
                    update={"panel_emails": [p.email for p in panel_rows]}
                )
                logger.info(
                    "smart_schedule: resolved %d panel members for round=%s from directory (fallback)",
                    len(panel_rows),
                    round,
                )
            else:
                logger.warning(
                    "smart_schedule: no panel members found for round=%s", round
                )

    # Fill default windows if not configured
    if not round_cfg.windows:
        round_cfg = round_cfg.model_copy(update={"windows": _DEFAULT_WINDOWS})
        logger.info("smart_schedule: using default availability windows for round=%s", round)

    return round_cfg


# ---------------------------------------------------------------------------
# Slot finder (multi-slot variant)
# ---------------------------------------------------------------------------


async def _find_top_slots(
    session: AsyncSession,
    *,
    round_cfg: RoundScheduling,
    panel_tz: str,
    horizon: int,
    count: int = 3,
) -> list[ProposedSlot]:
    """Find ``count`` non-overlapping available slots.

    Reuses ``_expand_windows`` and ``_graph_busy_intervals`` from
    ``src.services.scheduling`` but iterates to collect multiple slots
    rather than returning only the first one.
    """
    if not round_cfg.panel_emails:
        return []

    min_lead = DEFAULT_MIN_LEAD_HOURS

    expanded = _expand_windows(
        round_cfg=round_cfg,
        panel_tz=panel_tz,
        horizon_days=horizon,
        min_lead_hours=min_lead,
    )
    if not expanded:
        return []

    horizon_end = max(end for _, end in expanded)

    # Gather blocking intervals from both DB and Graph
    from src.services.scheduling import _existing_meetings_for_panel

    db_blocks = await _existing_meetings_for_panel(
        session, round_cfg.panel_emails, horizon_end
    )

    organiser = get_organiser_email() or (
        round_cfg.panel_emails[0] if round_cfg.panel_emails else ""
    )
    graph_blocks = await _graph_busy_intervals(
        panel_emails=round_cfg.panel_emails,
        start_utc=expanded[0][0],
        end_utc=horizon_end,
        organiser_email=organiser,
    )
    blocks = db_blocks + (graph_blocks or [])

    duration = timedelta(minutes=round_cfg.duration_minutes)
    found: list[ProposedSlot] = []

    for window_start, window_end in expanded:
        cursor = window_start
        # Align to half-hour boundary
        if cursor.minute % 30 != 0:
            extra = 30 - (cursor.minute % 30)
            cursor = cursor + timedelta(minutes=extra)

        while cursor + duration <= window_end:
            slot_start = cursor
            slot_end = cursor + duration

            # Check against all blocks + already-found slots
            all_blocks = blocks + [(s.start, s.end) for s in found]
            if not _slot_collides(slot_start, slot_end, all_blocks):
                found.append(
                    ProposedSlot(
                        start=slot_start,
                        end=slot_end,
                        panel_emails=round_cfg.panel_emails,
                    )
                )
                if len(found) >= count:
                    return found

            cursor += timedelta(minutes=30)

    return found


# ---------------------------------------------------------------------------
# Candidate response handler
# ---------------------------------------------------------------------------


async def handle_candidate_response(
    *,
    meeting_session_id: UUID,
    decision: str,
    preferred_at: datetime | None = None,
) -> str:
    """Process candidate's scheduling decision after a voice call.

    Called from ``webhooks_voice.py`` after a ``meeting_schedule`` call completes.

    Args:
        meeting_session_id: The MeetingSession tracking this negotiation.
        decision: One of ``"yes"``, ``"reschedule"``, ``"no"``.
        preferred_at: The candidate's chosen or proposed datetime (UTC).

    Returns:
        A string describing the action taken.
    """
    async with session_scope() as session:
        ms = await session.get(MeetingSession, meeting_session_id)
        if ms is None:
            raise ValueError(f"meeting_session {meeting_session_id} not found")

        neg = ms.negotiation_state or {}
        proposed_slots = neg.get("proposed_slots", [])

        app = await session.get(Application, ms.application_id)
        candidate = await session.get(Candidate, app.candidate_id) if app else None

        if decision == "yes" and preferred_at is not None:
            # Check if preferred_at matches one of the proposed slots
            matched_slot = _match_proposed_slot(proposed_slots, preferred_at)
            if matched_slot:
                await book_confirmed_meeting(
                    meeting_session_id=meeting_session_id,
                    slot=matched_slot,
                )
                return "meeting_booked"
            else:
                # Candidate said yes but to a time not in our proposals --
                # treat as reschedule to negotiate
                return await _check_and_negotiate(
                    meeting_session_id=meeting_session_id,
                    candidate_preferred=preferred_at,
                )

        elif decision == "reschedule" and preferred_at is not None:
            return await _check_and_negotiate(
                meeting_session_id=meeting_session_id,
                candidate_preferred=preferred_at,
            )

        elif decision == "no":
            await escalate_to_hr(
                meeting_session_id=meeting_session_id,
                reason="candidate_declined",
            )
            return "escalated_candidate_declined"

        else:
            # Ambiguous response -- escalate
            await escalate_to_hr(
                meeting_session_id=meeting_session_id,
                reason=f"ambiguous_response: decision={decision}",
            )
            return "escalated_ambiguous_response"


# ---------------------------------------------------------------------------
# Negotiation
# ---------------------------------------------------------------------------


async def _check_and_negotiate(
    *,
    meeting_session_id: UUID,
    candidate_preferred: datetime,
) -> str:
    """Cross-reference candidate's preferred time with panel availability.

    If the time works, books immediately. Otherwise proposes 3 new slots
    near the candidate's preference and dispatches another voice call.
    Escalates after ``MAX_NEGOTIATION_ATTEMPTS``.
    """
    async with session_scope() as session:
        ms = await session.get(MeetingSession, meeting_session_id)
        if ms is None:
            raise ValueError(f"meeting_session {meeting_session_id} not found")

        neg = ms.negotiation_state or {}
        attempt = neg.get("attempt", 1)
        max_attempts = neg.get("max_attempts", MAX_NEGOTIATION_ATTEMPTS)

        if attempt >= max_attempts:
            await escalate_to_hr(
                meeting_session_id=meeting_session_id,
                reason=f"max_negotiation_attempts_reached ({max_attempts})",
            )
            return "escalated_max_attempts"

        app = await session.get(Application, ms.application_id)
        if app is None or app.role_id is None:
            await escalate_to_hr(
                meeting_session_id=meeting_session_id,
                reason="application_or_role_missing",
            )
            return "escalated_data_error"

        role = await session.get(Role, app.role_id)
        if role is None:
            await escalate_to_hr(
                meeting_session_id=meeting_session_id,
                reason="role_not_found",
            )
            return "escalated_data_error"

        candidate = await session.get(Candidate, app.candidate_id)
        round_cfg = await _resolve_panel_for_round(session, role, ms.round)
        role_sched = RoleScheduling.from_role_rubric(role.scoring_rubric)
        panel_tz = role_sched.panel_timezone or "Asia/Kolkata"

        # Check if candidate's preferred time is actually available
        candidate_slot = ProposedSlot(
            start=candidate_preferred.astimezone(timezone.utc),
            end=(candidate_preferred + timedelta(minutes=round_cfg.duration_minutes)).astimezone(
                timezone.utc
            ),
            panel_emails=round_cfg.panel_emails,
        )

        # Quick availability check for this specific slot
        is_available = await _check_slot_available(session, candidate_slot, round_cfg)

        if is_available:
            # Candidate's time works -- book it
            await session.commit()  # release lock before booking

            await book_confirmed_meeting(
                meeting_session_id=meeting_session_id,
                slot=candidate_slot,
            )
            return "meeting_booked_candidate_preference"

        # Candidate's time doesn't work -- find 3 new slots near their preference
        new_slots = await _find_slots_near_preference(
            session,
            round_cfg=round_cfg,
            panel_tz=panel_tz,
            preferred=candidate_preferred,
            count=3,
        )

        if not new_slots:
            await escalate_to_hr(
                meeting_session_id=meeting_session_id,
                reason="no_alternative_slots_near_candidate_preference",
            )
            return "escalated_no_alternatives"

        # Update negotiation state
        new_attempt = attempt + 1
        proposed_data = [
            {
                "index": i + 1,
                "start": s.start.isoformat(),
                "end": s.end.isoformat(),
                "panel_emails": s.panel_emails,
            }
            for i, s in enumerate(new_slots)
        ]
        ms.negotiation_state = {
            "status": "proposing",
            "proposed_slots": proposed_data,
            "attempt": new_attempt,
            "max_attempts": max_attempts,
            "candidate_preferred_at": candidate_preferred.isoformat(),
        }

        await log_audit(
            session,
            application_id=ms.application_id,
            candidate_id=candidate.id if candidate else None,
            action="smart_schedule_renegotiate",
            actor="agent",
            details={
                "meeting_session_id": str(meeting_session_id),
                "attempt": new_attempt,
                "candidate_preferred": candidate_preferred.isoformat(),
                "new_slot_count": len(new_slots),
            },
        )

        candidate_name = candidate.name if candidate else "Candidate"
        candidate_phone = candidate.phone if candidate else None
        role_title = role.title

    # Dispatch another voice call with the new slots
    settings = get_settings()
    ctx = {
        "candidate_name": candidate_name,
        "role_title": role_title,
        "round": ms.round,
        "company_name": settings.voice_agent_company_name,
    }
    _sys, _first = _build_slot_proposal_prompt(
        context=ctx,
        slots=new_slots,
        round_name=ms.round,
        attempt=new_attempt,
    )

    if candidate_phone:
        try:
            from src.activities.v1_dispatch_voice_call import dispatch_voice_call

            await dispatch_voice_call(
                application_id=ms.application_id,
                call_kind=CallKind.MEETING_SCHEDULE,
                attempt_no=new_attempt,
            )
        except Exception:
            logger.exception(
                "smart_schedule: re-negotiation voice dispatch failed session=%s",
                meeting_session_id,
            )
            await escalate_to_hr(
                meeting_session_id=meeting_session_id,
                reason="voice_dispatch_failed_on_renegotiation",
            )
            return "escalated_voice_failure"

    return "renegotiating"


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------


async def book_confirmed_meeting(
    *,
    meeting_session_id: UUID,
    slot: ProposedSlot,
) -> UUID:
    """Book a confirmed meeting: create online meeting, send invites, update state.

    Returns the ``MeetingSession.id``.
    """
    settings = get_settings()

    async with session_scope() as session:
        ms = await session.get(MeetingSession, meeting_session_id)
        if ms is None:
            raise ValueError(f"meeting_session {meeting_session_id} not found")

        app = await session.get(Application, ms.application_id)
        if app is None:
            raise ValueError(f"application {ms.application_id} not found")

        candidate = await session.get(Candidate, app.candidate_id)
        if candidate is None:
            raise ValueError("candidate not found")

        role = await session.get(Role, app.role_id) if app.role_id else None
        role_title = role.title if role else "Interview"
        round_label = ms.round.capitalize()

        # 1. Create online meeting
        organiser = get_organiser_email() or (
            slot.panel_emails[0] if slot.panel_emails else ""
        )
        candidate_email = candidate.email or ""
        all_attendees = list(set(slot.panel_emails + [candidate_email]))

        subject = f"{round_label} Interview - {candidate.name or 'Candidate'} - {role_title}"

        try:
            join_url, provider_meeting_id = await create_online_meeting(
                organiser_email=organiser,
                subject=subject,
                start_utc=slot.start,
                end_utc=slot.end,
                attendee_emails=all_attendees,
            )
        except Exception as exc:
            logger.exception(
                "smart_schedule: online meeting creation failed session=%s",
                meeting_session_id,
            )
            ms.negotiation_state = {
                **(ms.negotiation_state or {}),
                "status": "escalated",
                "reason": f"meeting_creation_failed: {exc!s:.200}",
            }
            await set_stage(session, ms.application_id, PipelineStage.NEEDS_HR_REVIEW, force=True)
            await log_audit(
                session,
                application_id=ms.application_id,
                candidate_id=candidate.id,
                action="smart_schedule_meeting_creation_failed",
                actor="agent",
                details={"error": str(exc)[:500]},
            )
            return meeting_session_id

        # 2. Update MeetingSession
        ms.teams_join_url = join_url
        ms.scheduled_at = slot.start
        ms.bot_status = "pending"
        ms.negotiation_state = {
            **(ms.negotiation_state or {}),
            "status": "confirmed",
            "confirmed_slot": {
                "start": slot.start.isoformat(),
                "end": slot.end.isoformat(),
            },
            "provider_meeting_id": provider_meeting_id,
        }

        # 3. Set pipeline stage
        scheduled_stage = _ROUND_TO_SCHEDULED_STAGE.get(
            ms.round, PipelineStage.NEEDS_HR_REVIEW
        )
        await set_stage(session, ms.application_id, scheduled_stage, force=True)

        # 4. Audit + events
        await log_audit(
            session,
            application_id=ms.application_id,
            candidate_id=candidate.id,
            action="smart_schedule_meeting_booked",
            actor="agent",
            details={
                "meeting_session_id": str(meeting_session_id),
                "round": ms.round,
                "scheduled_at": slot.start.isoformat(),
                "join_url": join_url,
                "panel_emails": slot.panel_emails,
            },
        )

        await publish_supervisor_event(
            session,
            EventType.STAGE_CHANGED,
            application_id=ms.application_id,
            candidate_id=candidate.id,
            payload={
                "new_stage": scheduled_stage.value,
                "round": ms.round,
                "scheduled_at": slot.start.isoformat(),
            },
        )

        # Capture values for email dispatch outside session
        candidate_name = candidate.name or "Candidate"
        candidate_email_addr = candidate.email
        panel_emails = slot.panel_emails
        app_id = ms.application_id
        cand_id = candidate.id

    # 5. Send confirmation emails to candidate + panel
    slot_ist = slot.start.astimezone(IST)
    slot_end_ist = slot.end.astimezone(IST)
    time_str = slot_ist.strftime("%A, %B %d at %I:%M %p IST")
    end_time_str = slot_end_ist.strftime("%I:%M %p IST")

    # Email candidate
    if candidate_email_addr:
        try:
            await send_email(
                to=candidate_email_addr,
                template="meeting_confirmed_candidate",
                variables={
                    "candidate_name": candidate_name,
                    "role_title": role_title,
                    "round": round_label,
                    "meeting_time": time_str,
                    "meeting_end_time": end_time_str,
                    "meeting_link": join_url,
                    "company_name": settings.voice_agent_company_name,
                },
                idempotency_key=f"{meeting_session_id}:candidate_confirm",
                application_id=str(app_id),
                candidate_id=str(cand_id),
            )
        except Exception:
            logger.exception(
                "smart_schedule: failed to email candidate for session=%s",
                meeting_session_id,
            )

    # Email panel members
    for panel_email in panel_emails:
        try:
            await send_email(
                to=panel_email,
                template="meeting_confirmed_panel",
                variables={
                    "candidate_name": candidate_name,
                    "role_title": role_title,
                    "round": round_label,
                    "meeting_time": time_str,
                    "meeting_end_time": end_time_str,
                    "meeting_link": join_url,
                    "company_name": settings.voice_agent_company_name,
                },
                idempotency_key=f"{meeting_session_id}:panel:{panel_email}",
                application_id=str(app_id),
            )
        except Exception:
            logger.exception(
                "smart_schedule: failed to email panel member %s for session=%s",
                panel_email,
                meeting_session_id,
            )

    # 6. Dispatch meeting bot if configured
    if settings.recall_api_key or settings.read_ai_api_key:
        try:
            from src.services.queue import enqueue

            await enqueue(
                "dispatch_meeting_bot",
                str(meeting_session_id),
            )
        except Exception:
            logger.exception(
                "smart_schedule: meeting bot dispatch enqueue failed session=%s",
                meeting_session_id,
            )

    # 7. Publish real-time event
    await publish_event(
        app_id,
        event="meeting_booked",
        data={
            "meeting_session_id": str(meeting_session_id),
            "round": round_label.lower(),
            "scheduled_at": slot.start.isoformat(),
            "join_url": join_url,
        },
    )

    return meeting_session_id


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------


async def escalate_to_hr(
    *,
    meeting_session_id: UUID,
    reason: str,
) -> None:
    """Escalate scheduling to HR for manual intervention.

    Updates state, notifies panel, logs audit trail.
    """
    settings = get_settings()

    async with session_scope() as session:
        ms = await session.get(MeetingSession, meeting_session_id)
        if ms is None:
            logger.error("escalate_to_hr: meeting_session %s not found", meeting_session_id)
            return

        app = await session.get(Application, ms.application_id)
        candidate = await session.get(Candidate, app.candidate_id) if app else None
        role = await session.get(Role, app.role_id) if app and app.role_id else None

        # 1. Update negotiation state
        ms.bot_status = "escalated"
        ms.negotiation_state = {
            **(ms.negotiation_state or {}),
            "status": "escalated",
            "reason": reason,
            "escalated_at": datetime.now(UTC).isoformat(),
        }

        # 2. Set stage
        await set_stage(session, ms.application_id, PipelineStage.NEEDS_HR_REVIEW, force=True)

        # 3. Audit
        await log_audit(
            session,
            application_id=ms.application_id,
            candidate_id=candidate.id if candidate else None,
            action="smart_schedule_escalated",
            actor="agent",
            details={
                "meeting_session_id": str(meeting_session_id),
                "round": ms.round,
                "reason": reason,
            },
        )

        # 4. Publish supervisor event
        await publish_supervisor_event(
            session,
            EventType.PANEL_UNAVAILABLE,
            application_id=ms.application_id,
            candidate_id=candidate.id if candidate else None,
            payload={
                "round": ms.round,
                "reason": reason,
                "meeting_session_id": str(meeting_session_id),
            },
        )

        # Capture for email outside session
        panel_emails = (ms.negotiation_state or {}).get("proposed_slots", [{}])
        panel_addresses: list[str] = []
        if panel_emails and isinstance(panel_emails[0], dict):
            for slot_data in panel_emails:
                panel_addresses.extend(slot_data.get("panel_emails", []))
        panel_addresses = list(set(panel_addresses))

        # Fallback: resolve from role config
        if not panel_addresses and role:
            round_cfg = await _resolve_panel_for_round(session, role, ms.round)
            panel_addresses = round_cfg.panel_emails

        candidate_name = candidate.name if candidate else "Unknown"
        role_title = role.title if role else "N/A"
        round_name = ms.round
        app_id = ms.application_id

    # 5. Email panel requesting manual slots
    for email_addr in panel_addresses:
        try:
            await send_email(
                to=email_addr,
                template="meeting_escalation_panel",
                variables={
                    "candidate_name": candidate_name,
                    "role_title": role_title,
                    "round": round_name.capitalize(),
                    "reason": reason,
                    "company_name": settings.voice_agent_company_name,
                },
                idempotency_key=f"{meeting_session_id}:escalation:{email_addr}",
                application_id=str(app_id),
            )
        except Exception:
            logger.exception(
                "smart_schedule: failed to send escalation email to %s", email_addr
            )

    await publish_event(
        app_id,
        event="smart_schedule_escalated",
        data={
            "meeting_session_id": str(meeting_session_id),
            "round": round_name,
            "reason": reason,
        },
    )


# ---------------------------------------------------------------------------
# Voice prompt builder
# ---------------------------------------------------------------------------


def _build_slot_proposal_prompt(
    context: dict[str, Any],
    slots: list[ProposedSlot],
    round_name: str,
    attempt: int,
) -> tuple[str, str]:
    """Build system prompt + first message for the scheduling voice call.

    The voice agent will propose specific time slots to the candidate and
    extract their preference using structured markers.

    Returns:
        (system_prompt, first_message)
    """
    candidate_name = context.get("candidate_name", "there")
    role_title = context.get("role_title", "the position")
    company_name = context.get("company_name", "our company")
    round_label = round_name.capitalize()

    # Format slots in IST for human readability
    slot_lines = []
    for i, slot in enumerate(slots, 1):
        ist_start = slot.start.astimezone(IST)
        ist_end = slot.end.astimezone(IST)
        slot_lines.append(
            f"  Option {i}: {ist_start.strftime('%A, %B %d at %I:%M %p')} "
            f"to {ist_end.strftime('%I:%M %p')} IST"
        )
    slot_text = "\n".join(slot_lines)

    attempt_note = ""
    if attempt > 1:
        attempt_note = (
            "\n\nThis is a follow-up call because the candidate's previously "
            "preferred time was not available. Be empathetic and acknowledge "
            "the inconvenience."
        )

    system_prompt = f"""You are a professional scheduling assistant for {company_name}.
Your task is to schedule a {round_label} interview for the {role_title} position.

You have {len(slots)} available time slots to propose to the candidate:
{slot_text}

RULES:
- Be warm, professional, and concise.
- Present all {len(slots)} options clearly.
- If the candidate picks one, confirm it and end the call.
- If none work, ask the candidate for their preferred date and time.
- Do NOT make up or promise times outside the given options.
- If the candidate wants to reschedule to a different time, note their preference.
- Keep the call under 2 minutes.{attempt_note}

RESPONSE EXTRACTION (include exactly one of these markers at the end of the conversation summary):
- If candidate selects an option: SELECTED_SLOT=<1|2|3>
- If candidate proposes a different time: PREFERRED_AT=<ISO8601>; CONFIRM=reschedule
- If candidate declines entirely: CONFIRM=decline"""

    first_message = (
        f"Hello {candidate_name}! I'm calling from {company_name} regarding your "
        f"application for the {role_title} role. We'd like to schedule your "
        f"{round_label} interview. I have {len(slots)} time slots available -- "
        f"let me share them with you."
    )

    return system_prompt, first_message


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _match_proposed_slot(
    proposed_slots: list[dict[str, Any]],
    preferred_at: datetime,
) -> ProposedSlot | None:
    """Check if ``preferred_at`` matches one of the proposed slots (within 5 min tolerance)."""
    preferred_utc = preferred_at.astimezone(timezone.utc)
    tolerance = timedelta(minutes=5)

    for slot_data in proposed_slots:
        try:
            slot_start = datetime.fromisoformat(slot_data["start"])
            slot_end = datetime.fromisoformat(slot_data["end"])
            panel_emails = slot_data.get("panel_emails", [])
        except (KeyError, ValueError):
            continue

        if abs(slot_start - preferred_utc) <= tolerance:
            return ProposedSlot(
                start=slot_start,
                end=slot_end,
                panel_emails=panel_emails,
            )
    return None


async def _check_slot_available(
    session: AsyncSession,
    slot: ProposedSlot,
    round_cfg: RoundScheduling,
) -> bool:
    """Check whether a single slot is free for the panel."""
    from src.services.scheduling import _existing_meetings_for_panel

    organiser = get_organiser_email() or (
        round_cfg.panel_emails[0] if round_cfg.panel_emails else ""
    )
    db_blocks = await _existing_meetings_for_panel(
        session, round_cfg.panel_emails, slot.end
    )
    graph_blocks = await _graph_busy_intervals(
        panel_emails=round_cfg.panel_emails,
        start_utc=slot.start,
        end_utc=slot.end,
        organiser_email=organiser,
    )
    blocks = db_blocks + (graph_blocks or [])
    return not _slot_collides(slot.start, slot.end, blocks)


async def _find_slots_near_preference(
    session: AsyncSession,
    *,
    round_cfg: RoundScheduling,
    panel_tz: str,
    preferred: datetime,
    count: int = 3,
) -> list[ProposedSlot]:
    """Find available slots near the candidate's preferred date/time.

    Searches a narrow window (preferred day +/- 2 days) first, then
    falls back to the full horizon if needed.
    """
    # Try narrow window first: +/- 2 business days around preference
    for search_days in (5, DEFAULT_HORIZON_DAYS):
        narrow_cfg = round_cfg.model_copy()
        slots = await _find_top_slots(
            session,
            round_cfg=narrow_cfg,
            panel_tz=panel_tz,
            horizon=search_days,
            count=count,
        )
        if slots:
            # Sort by proximity to candidate's preferred time
            preferred_utc = preferred.astimezone(timezone.utc)
            slots.sort(key=lambda s: abs((s.start - preferred_utc).total_seconds()))
            return slots[:count]

    return []


async def _send_slot_proposal_email(
    *,
    application_id: UUID,
    meeting_session_id: UUID,
    candidate_email: str | None,
    candidate_name: str,
    slots: list[ProposedSlot],
    round_name: str,
    role_title: str,
) -> None:
    """Fallback: email the candidate with proposed time slots."""
    if not candidate_email:
        logger.warning(
            "smart_schedule: no email for candidate, cannot send slot proposal"
        )
        return

    settings = get_settings()
    slot_lines = []
    for i, slot in enumerate(slots, 1):
        ist_start = slot.start.astimezone(IST)
        ist_end = slot.end.astimezone(IST)
        slot_lines.append(
            f"Option {i}: {ist_start.strftime('%A, %B %d at %I:%M %p')} "
            f"to {ist_end.strftime('%I:%M %p')} IST"
        )

    try:
        await send_email(
            to=candidate_email,
            template="meeting_slot_proposal",
            variables={
                "candidate_name": candidate_name,
                "role_title": role_title,
                "round": round_name.capitalize(),
                "slots": "\n".join(slot_lines),
                "company_name": settings.voice_agent_company_name,
            },
            idempotency_key=f"{meeting_session_id}:slot_proposal",
            application_id=str(application_id),
        )
    except Exception:
        logger.exception(
            "smart_schedule: slot proposal email failed for session=%s",
            meeting_session_id,
        )
