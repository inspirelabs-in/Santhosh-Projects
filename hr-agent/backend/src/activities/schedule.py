"""Stage 7: Scheduling.

Three activities:
  - `propose_slots_activity`: query interviewer availability, generate 3 slot
    tokens (stored in Redis keyed by application_id), send proposal to
    candidate via email + WhatsApp.
  - `book_interview_activity`: given a slot_token the candidate picked,
    create the Calendar event with Meet link, persist Interview row, send
    confirmation email + WhatsApp to the candidate and panel.
  - `mark_scheduling_stalled_activity`: park the application for HR review
    when no slot works.

Slot state is kept in Redis (short TTL) rather than Postgres -- proposals
are ephemeral; only the booked slot needs durable storage.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo
from uuid import UUID

from temporalio import activity

from src.channels.email import send_email
from src.channels.whatsapp import send_template as send_whatsapp_template
from src.config import get_settings
from src.db.connection import get_redis, session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.candidate import get_application, get_candidate, update_application_status
from src.db.repositories.interview import create_interview, get_latest_for_application
from src.db.repositories.role import get_role
from src.models.candidate import ApplicationStatus, CandidateStatus
from src.models.scheduling import InterviewStatus, TimeSlot
from src.services.calendar_sync import cancel_event, create_event, find_free_slots
from src.services.dedup import is_valid_indian_mobile

logger = logging.getLogger(__name__)
_settings = get_settings()

_SLOT_PROPOSAL_TTL = 3 * 24 * 3600  # 3 days


def _slot_key(application_id: UUID) -> str:
    return f"schedule:slots:{application_id}"


def _format_slot(slot: TimeSlot) -> str:
    return slot.start.strftime("%a %d %b, %I:%M %p") + " – " + slot.end.strftime("%I:%M %p IST")


# ---------------------------------------------------------------------------
# Propose slots
# ---------------------------------------------------------------------------


@dataclass
class ProposeSlotsInput:
    candidate_id: UUID
    application_id: UUID
    date_range_days: int = 7
    duration_minutes: int = 60
    max_slots: int = 3


@dataclass
class ProposeSlotsResult:
    slot_count: int
    sent_email: bool
    sent_whatsapp: bool
    stalled: bool  # True if no free slots were found


async def run_propose_slots(payload: ProposeSlotsInput) -> ProposeSlotsResult:
    async with session_scope() as session:
        app = await get_application(session, payload.application_id)
        if app is None or app.role_id is None:
            raise ValueError("application missing or has no role")
        candidate = await get_candidate(session, payload.candidate_id)
        role = await get_role(session, app.role_id)
        if candidate is None or role is None:
            raise ValueError("candidate or role not found")

        panel_emails = [
            m.get("calendar_email") for m in (role.interviewer_panel or []) if m.get("calendar_email")
        ]
        snapshot = {
            "candidate_email": candidate.email,
            "candidate_name": candidate.name,
            "candidate_phone": candidate.phone,
            "role_title": role.title,
            "panel_emails": panel_emails,
        }

    slots = await find_free_slots(
        interviewer_emails=snapshot["panel_emails"],
        date_range_days=payload.date_range_days,
        slot_duration_minutes=payload.duration_minutes,
        max_slots=payload.max_slots,
    )

    # No slots (or calendar unavailable) → mark stalled, return.
    if not slots:
        async with session_scope() as session:
            await update_application_status(
                session, payload.application_id, ApplicationStatus.NEEDS_HR_REVIEW
            )
            await log_audit(
                session,
                action="scheduling_stalled",
                actor="agent",
                candidate_id=payload.candidate_id,
                application_id=payload.application_id,
                details={"reason": "no_free_slots"},
            )
        return ProposeSlotsResult(slot_count=0, sent_email=False, sent_whatsapp=False, stalled=True)

    # Persist slot-token → (start, end, interviewers) in Redis so the
    # booking webhook can look them up without re-querying the calendar.
    redis = get_redis()
    await redis.setex(
        _slot_key(payload.application_id),
        _SLOT_PROPOSAL_TTL,
        json.dumps(
            [
                {
                    "slot_token": s.slot_token,
                    "start": s.start.isoformat(),
                    "end": s.end.isoformat(),
                    "interviewer_emails": s.interviewer_emails,
                }
                for s in slots
            ]
        ),
    )

    booking_base_url = f"{_settings.app_base_url.rstrip('/')}/schedule/{payload.application_id}"
    slots_for_email = [
        {"slot_token": s.slot_token, "display": _format_slot(s)} for s in slots
    ]

    sent_email = False
    if snapshot["candidate_email"]:
        er = await send_email(
            to=snapshot["candidate_email"],
            template="interview_slot_proposal",
            variables={
                "candidate_name": snapshot["candidate_name"],
                "role_title": snapshot["role_title"],
                "slots": slots_for_email,
                "booking_base_url": booking_base_url,
                "duration_minutes": payload.duration_minutes,
            },
            tags={"stage": "scheduling", "kind": "slot_proposal"},
        )
        sent_email = er.success

    sent_whatsapp = False
    if snapshot["candidate_phone"] and is_valid_indian_mobile(snapshot["candidate_phone"]):
        # Single "pick a slot" template. The booking page lists all options.
        wr = await send_whatsapp_template(
            to_phone=snapshot["candidate_phone"],
            template_name="interview_slot_proposal_v1",
            body_params=[snapshot["candidate_name"] or "there", snapshot["role_title"]],
            button_url_param=str(payload.application_id),
        )
        sent_whatsapp = wr.success

    async with session_scope() as session:
        await update_application_status(
            session, payload.application_id, ApplicationStatus.SCHEDULED
        )
        cand = await get_candidate(session, payload.candidate_id)
        if cand is not None:
            cand.status = CandidateStatus.SCHEDULING.value
        await log_audit(
            session,
            action="slots_proposed",
            actor="agent",
            candidate_id=payload.candidate_id,
            application_id=payload.application_id,
            details={
                "slot_count": len(slots),
                "email_sent": sent_email,
                "whatsapp_sent": sent_whatsapp,
                "duration_minutes": payload.duration_minutes,
            },
        )

    return ProposeSlotsResult(
        slot_count=len(slots),
        sent_email=sent_email,
        sent_whatsapp=sent_whatsapp,
        stalled=False,
    )


@activity.defn(name="propose_slots")
async def propose_slots_activity(payload: ProposeSlotsInput) -> ProposeSlotsResult:
    return await run_propose_slots(payload)


# ---------------------------------------------------------------------------
# Book interview (called from webhook handler when candidate picks a slot)
# ---------------------------------------------------------------------------


@dataclass
class BookInterviewInput:
    candidate_id: UUID
    application_id: UUID
    slot_token: str


@dataclass
class BookInterviewResult:
    interview_id: UUID
    scheduled_at: str
    meeting_link: str | None


# [SCRAPE] dead: run_book_interview/book_interview_activity (Temporal-era). KEEP run_propose_slots (live).
async def run_book_interview(payload: BookInterviewInput) -> BookInterviewResult:
    redis = get_redis()
    raw = await redis.get(_slot_key(payload.application_id))
    if raw is None:
        raise ValueError("slot proposal expired or not found")

    slots = json.loads(raw)
    slot = next((s for s in slots if s["slot_token"] == payload.slot_token), None)
    if slot is None:
        raise ValueError("slot_token does not match any proposed slot")

    start = datetime.fromisoformat(slot["start"])
    end = datetime.fromisoformat(slot["end"])

    async with session_scope() as session:
        app = await get_application(session, payload.application_id)
        if app is None or app.role_id is None:
            raise ValueError("application missing or has no role")
        candidate = await get_candidate(session, payload.candidate_id)
        role = await get_role(session, app.role_id)
        if candidate is None or role is None:
            raise ValueError("candidate or role not found")
        snapshot = {
            "candidate_email": candidate.email,
            "candidate_name": candidate.name,
            "candidate_phone": candidate.phone,
            "role_title": role.title,
            "jd_text": role.jd_text,
            "panel_emails": slot["interviewer_emails"],
        }

    attendees = list(snapshot["panel_emails"])
    if snapshot["candidate_email"]:
        attendees.append(snapshot["candidate_email"])

    event = await create_event(
        summary=f"Interview: {snapshot['candidate_name'] or 'Candidate'} -- {snapshot['role_title']}",
        description=(
            f"Interview with {snapshot['candidate_name'] or 'candidate'} for the "
            f"{snapshot['role_title']} role at GrabOn.\n\n"
            f"Application: {payload.application_id}"
        ),
        start=start,
        end=end,
        attendees=attendees,
    )
    calendar_event_id = event.event_id if event else None
    meeting_link = event.meeting_link if event else None

    async with session_scope() as session:
        interview = await create_interview(
            session,
            application_id=payload.application_id,
            scheduled_at=start,
            calendar_event_id=calendar_event_id,
            meeting_link=meeting_link,
            status=InterviewStatus.CONFIRMED,
        )
        await update_application_status(
            session, payload.application_id, ApplicationStatus.SCHEDULED
        )
        cand = await get_candidate(session, payload.candidate_id)
        if cand is not None:
            cand.status = CandidateStatus.SCHEDULED.value

        await log_audit(
            session,
            action="interview_booked",
            actor="candidate",
            candidate_id=payload.candidate_id,
            application_id=payload.application_id,
            details={
                "interview_id": str(interview.id),
                "scheduled_at": start.isoformat(),
                "meeting_link": meeting_link,
                "calendar_event_id": calendar_event_id,
                "panel": snapshot["panel_emails"],
            },
        )
        interview_id = interview.id

    # Burn the slot token so it can't be used twice.
    await redis.delete(_slot_key(payload.application_id))

    # Confirmation email to candidate.
    scheduled_display = start.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%a %d %b %Y, %I:%M %p IST")
    if snapshot["candidate_email"]:
        await send_email(
            to=snapshot["candidate_email"],
            template="interview_confirmation",
            variables={
                "candidate_name": snapshot["candidate_name"],
                "role_title": snapshot["role_title"],
                "scheduled_display": scheduled_display,
                "duration_minutes": int((end - start).total_seconds() // 60),
                "meeting_link": meeting_link,
            },
            tags={"stage": "scheduling", "kind": "confirmation"},
        )

    # WhatsApp confirmation (template: interview_confirmation_v1).
    if snapshot["candidate_phone"] and is_valid_indian_mobile(snapshot["candidate_phone"]):
        await send_whatsapp_template(
            to_phone=snapshot["candidate_phone"],
            template_name="interview_confirmation_v1",
            body_params=[
                snapshot["candidate_name"] or "there",
                snapshot["role_title"],
                scheduled_display,
                meeting_link or "Link in your email",
            ],
        )

    return BookInterviewResult(
        interview_id=interview_id,
        scheduled_at=start.isoformat(),
        meeting_link=meeting_link,
    )


@activity.defn(name="book_interview")
async def book_interview_activity(payload: BookInterviewInput) -> BookInterviewResult:
    return await run_book_interview(payload)


# ---------------------------------------------------------------------------
# Cancel / reschedule
# ---------------------------------------------------------------------------


@dataclass
class CancelInterviewInput:
    application_id: UUID
    reason: str = "candidate_request"


@activity.defn(name="cancel_interview")
async def cancel_interview_activity(payload: CancelInterviewInput) -> bool:
    async with session_scope() as session:
        interview = await get_latest_for_application(session, payload.application_id)
        if interview is None:
            return False
        event_id = interview.calendar_event_id
        interview.status = InterviewStatus.CANCELLED.value
        await log_audit(
            session,
            action="interview_cancelled",
            actor="agent",
            application_id=payload.application_id,
            details={"reason": payload.reason, "calendar_event_id": event_id},
        )
    if event_id:
        await cancel_event(event_id)
    return True
