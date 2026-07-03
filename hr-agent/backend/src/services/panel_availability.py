"""Panel availability → candidate slot selection → booking flow.

Flow:
  1. Recruiter triggers a round → email sent to the matching panel member.
  2. Panel member opens link, picks REQUIRED_PANEL_SLOTS (3) date/times.
  3. Slots stored in DB; candidate emailed with a selection link.
  4. Candidate picks one of the 3, OR proposes a custom datetime.
  5. Meeting booked via Google Meet, calendar invites sent to both.

Round→panel mapping is generic: round name maps to PanelMember.role_type.
Handles variable rounds (2 tech, no HR, etc.) without hardcoding.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.channels.email import send_email
from src.config import get_settings
from src.db.base import Application, Candidate, MeetingSession, PanelMember, Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.meeting_session import create_session
from src.db.repositories.v1_application import set_stage
from src.models.scheduling import AvailabilityWindow, RoleScheduling, RoundScheduling
from src.models.v1 import MeetingRound, PipelineStage
from src.services.events import publish_event

logger = logging.getLogger(__name__)

_ALGORITHM = "HS256"
IST = ZoneInfo("Asia/Kolkata")
DEFAULT_HORIZON_DAYS = 14
PANEL_CONFIRM_TTL_DAYS = 3
CANDIDATE_SELECT_TTL_DAYS = 5
REQUIRED_PANEL_SLOTS = 3
MIN_PANEL_CONFIRMATIONS = 1

_DEFAULT_WINDOWS = [
    AvailabilityWindow(days=[0, 1, 2, 3, 4], start_hhmm="11:00", end_hhmm="18:00"),
]

_KNOWN_ROUND_ROLE_MAP: dict[str, str] = {
    MeetingRound.TECHNICAL: "technical",
    MeetingRound.CEO: "ceo",
    MeetingRound.HR: "hr",
}


class InvalidPanelToken(Exception):
    pass


class InvalidCandidateToken(Exception):
    pass


# ─── Panel Token ──────────────────────────────────────────────────


@dataclass
class PanelTokenClaims:
    meeting_session_id: UUID
    panel_email: str
    round: str
    expires_at: datetime


def generate_panel_token(
    *,
    meeting_session_id: UUID,
    panel_email: str,
    round: str,
    ttl_days: int = PANEL_CONFIRM_TTL_DAYS,
) -> str:
    settings = get_settings()
    exp = datetime.now(tz=UTC) + timedelta(days=ttl_days)
    payload = {
        "ms_id": str(meeting_session_id),
        "email": panel_email,
        "round": round,
        "act": "panel_availability",
        "iss": settings.jwt_issuer,
        "exp": int(exp.timestamp()),
        "iat": int(datetime.now(tz=UTC).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_signing_secret, algorithm=_ALGORITHM)


def verify_panel_token(token: str) -> PanelTokenClaims:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_signing_secret,
            algorithms=[_ALGORITHM],
            issuer=settings.jwt_issuer,
        )
    except JWTError as e:
        raise InvalidPanelToken(str(e)) from e

    if payload.get("act") != "panel_availability":
        raise InvalidPanelToken("wrong token action")

    try:
        return PanelTokenClaims(
            meeting_session_id=UUID(payload["ms_id"]),
            panel_email=payload["email"],
            round=payload["round"],
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (KeyError, ValueError) as e:
        raise InvalidPanelToken(f"malformed claims: {e}") from e


# ─── Candidate Token ─────────────────────────────────────────────


@dataclass
class CandidateTokenClaims:
    meeting_session_id: UUID
    application_id: UUID
    round: str
    expires_at: datetime


def generate_candidate_token(
    *,
    meeting_session_id: UUID,
    application_id: UUID,
    round: str,
    ttl_days: int = CANDIDATE_SELECT_TTL_DAYS,
) -> str:
    settings = get_settings()
    exp = datetime.now(tz=UTC) + timedelta(days=ttl_days)
    payload = {
        "ms_id": str(meeting_session_id),
        "app_id": str(application_id),
        "round": round,
        "act": "candidate_slot_selection",
        "iss": settings.jwt_issuer,
        "exp": int(exp.timestamp()),
        "iat": int(datetime.now(tz=UTC).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_signing_secret, algorithm=_ALGORITHM)


def verify_candidate_token(token: str) -> CandidateTokenClaims:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_signing_secret,
            algorithms=[_ALGORITHM],
            issuer=settings.jwt_issuer,
        )
    except JWTError as e:
        raise InvalidCandidateToken(str(e)) from e

    if payload.get("act") != "candidate_slot_selection":
        raise InvalidCandidateToken("wrong token action")

    try:
        return CandidateTokenClaims(
            meeting_session_id=UUID(payload["ms_id"]),
            application_id=UUID(payload["app_id"]),
            round=payload["round"],
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (KeyError, ValueError) as e:
        raise InvalidCandidateToken(f"malformed claims: {e}") from e


# ─── Round → role_type mapping ────────────────────────────────────


def round_to_role_type(round_name: str) -> str:
    """Map round name to PanelMember.role_type. Generic — unknown rounds
    use the round name itself as role_type."""
    return _KNOWN_ROUND_ROLE_MAP.get(round_name, round_name)


# ─── Slot generation ──────────────────────────────────────────────


def _generate_proposed_slots(
    round_cfg: RoundScheduling,
    panel_tz: str,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    count: int = 8,
) -> list[dict[str, Any]]:
    tz = ZoneInfo(panel_tz)
    now = datetime.now(tz=UTC)
    min_lead = timedelta(hours=18)
    earliest = now + min_lead

    windows = round_cfg.windows or _DEFAULT_WINDOWS
    duration = timedelta(minutes=round_cfg.duration_minutes)

    slots: list[dict[str, Any]] = []

    for day_offset in range(horizon_days):
        day_date = (now + timedelta(days=day_offset)).astimezone(tz).date()

        for window in windows:
            if day_date.weekday() not in window.days:
                continue

            h_start, m_start = map(int, window.start_hhmm.split(":"))
            h_end, m_end = map(int, window.end_hhmm.split(":"))

            window_start = datetime(
                day_date.year, day_date.month, day_date.day,
                h_start, m_start, tzinfo=tz,
            ).astimezone(UTC)
            window_end = datetime(
                day_date.year, day_date.month, day_date.day,
                h_end, m_end, tzinfo=tz,
            ).astimezone(UTC)

            cursor = window_start
            if cursor.minute % 30 != 0:
                extra = 30 - (cursor.minute % 30)
                cursor += timedelta(minutes=extra)

            while cursor + duration <= window_end:
                if cursor >= earliest:
                    ist_start = cursor.astimezone(IST)
                    ist_end = (cursor + duration).astimezone(IST)
                    slots.append({
                        "index": len(slots) + 1,
                        "start_utc": cursor.isoformat(),
                        "end_utc": (cursor + duration).isoformat(),
                        "start_ist": ist_start.strftime("%A, %B %d at %I:%M %p IST"),
                        "end_ist": ist_end.strftime("%I:%M %p IST"),
                        "label": f"{ist_start.strftime('%a %b %d, %I:%M %p')} - {ist_end.strftime('%I:%M %p')} IST",
                    })
                    if len(slots) >= count:
                        return slots
                cursor += timedelta(minutes=60)

    return slots


# ─── Panel resolution (generic) ──────────────────────────────────


async def _resolve_panel(
    session: AsyncSession,
    role: Role,
    round: str,
) -> RoundScheduling:
    """Resolve panel for a round. Generic — unknown rounds look up
    PanelMember by role_type = round name."""
    role_sched = RoleScheduling.from_role_rubric(role.scoring_rubric)
    round_cfg = role_sched.rounds.get(round, RoundScheduling())

    if round_cfg.panel_emails and round_cfg.windows:
        return round_cfg

    if not round_cfg.panel_emails:
        role_type = round_to_role_type(round)

        try:
            from src.services.panel_matcher import match_panel_for_role
            matched = await match_panel_for_role(session, role=role, round=round, count=5)
            if matched:
                round_cfg = round_cfg.model_copy(
                    update={"panel_emails": [p.email for p in matched]}
                )
        except Exception:
            pass

        if not round_cfg.panel_emails:
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

    if not round_cfg.windows:
        round_cfg = round_cfg.model_copy(update={"windows": _DEFAULT_WINDOWS})

    return round_cfg


# ─── Initiate flow ────────────────────────────────────────────────


async def initiate_panel_availability(
    *,
    application_id: UUID,
    round: str,
) -> UUID:
    """Start the panel availability flow. Sends email to the panel member
    for this round so they can pick 3 time slots."""
    settings = get_settings()

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise ValueError(f"application {application_id} not found")

        candidate = await session.get(Candidate, app.candidate_id)
        if candidate is None:
            raise ValueError("candidate not found")

        role = await session.get(Role, app.role_id) if app.role_id else None
        if role is None:
            raise ValueError("role not found")

        round_cfg = await _resolve_panel(session, role, round)

        if not round_cfg.panel_emails:
            raise ValueError(f"no panel members found for round={round}")

        role_sched = RoleScheduling.from_role_rubric(role.scoring_rubric)
        panel_tz = role_sched.panel_timezone or "Asia/Kolkata"

        ms = await create_session(
            session,
            application_id=application_id,
            interview_id=None,
            round=round,
            teams_join_url="",
            scheduled_at=None,
        )
        ms.bot_status = "awaiting_panel"
        ms.negotiation_state = {
            "status": "awaiting_panel",
            "panel_emails": round_cfg.panel_emails,
            "panel_slots": [],
            "initiated_at": datetime.now(UTC).isoformat(),
            "duration_minutes": round_cfg.duration_minutes,
            "panel_timezone": panel_tz,
            "required_slots": REQUIRED_PANEL_SLOTS,
        }
        meeting_session_id = ms.id

        await log_audit(
            session,
            application_id=application_id,
            candidate_id=candidate.id,
            action="panel_availability_initiated",
            actor="agent",
            details={
                "meeting_session_id": str(meeting_session_id),
                "round": round,
                "panel_emails": round_cfg.panel_emails,
            },
        )

        candidate_name = candidate.name or "Candidate"
        role_title = role.title
        panel_emails = round_cfg.panel_emails

    front_url = settings.frontend_base_url.rstrip("/")

    for panel_email in panel_emails:
        token = generate_panel_token(
            meeting_session_id=meeting_session_id,
            panel_email=panel_email,
            round=round,
        )
        confirm_url = f"{front_url}/confirm-availability/{token}"

        try:
            await send_email(
                to=panel_email,
                template="panel_availability_request",
                variables={
                    "panel_email": panel_email,
                    "candidate_name": candidate_name,
                    "role_title": role_title,
                    "round": round.capitalize(),
                    "confirm_url": confirm_url,
                    "slot_count": REQUIRED_PANEL_SLOTS,
                    "company_name": settings.voice_agent_company_name,
                },
                idempotency_key=f"{meeting_session_id}:panel_avail:{panel_email}",
                application_id=str(application_id),
            )
        except Exception:
            logger.exception(
                "panel_availability: failed to email %s for session=%s",
                panel_email, meeting_session_id,
            )

    await set_stage_awaiting_panel(application_id, meeting_session_id, round)

    await publish_event(
        application_id,
        event="panel_availability_initiated",
        data={"round": round, "meeting_session_id": str(meeting_session_id)},
    )

    return meeting_session_id


async def set_stage_awaiting_panel(
    application_id: UUID,
    meeting_session_id: UUID,
    round: str,
) -> None:
    async with session_scope() as session:
        await set_stage(session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True)
        await log_audit(
            session,
            application_id=application_id,
            action="panel_availability_awaiting",
            actor="agent",
            details={
                "meeting_session_id": str(meeting_session_id),
                "round": round,
            },
        )


# ─── Panel submits 3 slots ───────────────────────────────────────


async def record_panel_slots(
    *,
    meeting_session_id: UUID,
    panel_email: str,
    slots: list[dict[str, str]],
) -> dict[str, Any]:
    """Panel member submits their N chosen time slots.
    Each slot: {start: ISO datetime, end: ISO datetime}.
    Stores them and emails candidate with selection link.
    """
    if len(slots) != REQUIRED_PANEL_SLOTS:
        raise ValueError(f"exactly {REQUIRED_PANEL_SLOTS} slots required, got {len(slots)}")

    panel_slots = []
    now = datetime.now(tz=UTC)
    for i, s in enumerate(slots):
        start = datetime.fromisoformat(s["start"])
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        start = start.astimezone(UTC)

        end = datetime.fromisoformat(s["end"])
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        end = end.astimezone(UTC)

        if start < now + timedelta(hours=1):
            raise ValueError(f"slot {i+1} must be at least 1 hour from now")
        if start > now + timedelta(days=15):
            raise ValueError(f"slot {i+1} must be within 15 days")

        ist_start = start.astimezone(IST)
        ist_end = end.astimezone(IST)

        panel_slots.append({
            "index": i + 1,
            "start_utc": start.isoformat(),
            "end_utc": end.isoformat(),
            "start_ist": ist_start.strftime("%A, %B %d at %I:%M %p IST"),
            "end_ist": ist_end.strftime("%I:%M %p IST"),
            "label": f"{ist_start.strftime('%a %b %d, %I:%M %p')} – {ist_end.strftime('%I:%M %p')} IST",
        })

    async with session_scope() as session:
        ms = await session.get(MeetingSession, meeting_session_id)
        if ms is None:
            raise ValueError("meeting session not found")

        neg = ms.negotiation_state or {}
        if neg.get("status") not in ("awaiting_panel",):
            return {"status": "already_resolved", "current_status": neg.get("status")}

        neg["panel_slots"] = panel_slots
        neg["panel_responded_at"] = datetime.now(UTC).isoformat()
        neg["panel_responded_by"] = panel_email
        neg["status"] = "awaiting_candidate"
        ms.negotiation_state = neg
        ms.bot_status = "awaiting_candidate"

        app = await session.get(Application, ms.application_id)
        candidate = await session.get(Candidate, app.candidate_id) if app else None
        role = await session.get(Role, app.role_id) if app and app.role_id else None

        await log_audit(
            session,
            application_id=ms.application_id,
            action="panel_slots_submitted",
            actor=panel_email,
            details={
                "meeting_session_id": str(meeting_session_id),
                "slots": panel_slots,
            },
        )

        application_id = ms.application_id
        round_name = ms.round
        candidate_name = candidate.name if candidate else "Candidate"
        candidate_email = candidate.email if candidate else None
        role_title = role.title if role else "Open Position"
        duration_minutes = neg.get("duration_minutes", 45)

    if not candidate_email:
        logger.error("No candidate email for session=%s, cannot send slot selection", meeting_session_id)
        return {"status": "slots_saved", "error": "no_candidate_email"}

    await _email_candidate_slots(
        meeting_session_id=meeting_session_id,
        application_id=application_id,
        candidate_email=candidate_email,
        candidate_name=candidate_name,
        role_title=role_title,
        round_name=round_name,
        panel_slots=panel_slots,
        duration_minutes=duration_minutes,
    )

    return {
        "status": "slots_saved_candidate_notified",
        "meeting_session_id": str(meeting_session_id),
        "slots_count": len(panel_slots),
    }


async def _email_candidate_slots(
    *,
    meeting_session_id: UUID,
    application_id: UUID,
    candidate_email: str,
    candidate_name: str,
    role_title: str,
    round_name: str,
    panel_slots: list[dict],
    duration_minutes: int,
) -> None:
    settings = get_settings()
    front_url = settings.frontend_base_url.rstrip("/")

    token = generate_candidate_token(
        meeting_session_id=meeting_session_id,
        application_id=application_id,
        round=round_name,
    )
    select_url = f"{front_url}/select-slot/{token}"

    try:
        await send_email(
            to=candidate_email,
            template="candidate_slot_selection",
            variables={
                "candidate_name": candidate_name,
                "role_title": role_title,
                "round": round_name.capitalize(),
                "slots": panel_slots,
                "select_url": select_url,
                "duration_minutes": duration_minutes,
                "company_name": settings.voice_agent_company_name,
            },
            idempotency_key=f"{meeting_session_id}:candidate_slots:{candidate_email}",
            application_id=str(application_id),
        )
    except Exception:
        logger.exception(
            "Failed to email candidate %s slot selection for session=%s",
            candidate_email, meeting_session_id,
        )


# ─── Candidate selects a slot (or proposes custom) ───────────────


async def record_candidate_selection(
    *,
    meeting_session_id: UUID,
    application_id: UUID,
    selected_index: int | None = None,
    custom_datetime: str | None = None,
) -> dict[str, Any]:
    """Candidate picks one of the panel's slots, or proposes a custom time.
    Books the meeting immediately.
    """
    from src.services.scheduling import ProposedSlot
    from src.services.smart_scheduler import book_confirmed_meeting

    async with session_scope() as session:
        ms = await session.get(MeetingSession, meeting_session_id)
        if ms is None:
            raise ValueError("meeting session not found")

        neg = ms.negotiation_state or {}
        if neg.get("status") not in ("awaiting_candidate",):
            return {"status": "already_resolved", "current_status": neg.get("status")}

        panel_slots = neg.get("panel_slots", [])
        panel_emails = neg.get("panel_emails", [])
        duration_minutes = neg.get("duration_minutes", 45)

        if selected_index is not None:
            slot_data = next(
                (s for s in panel_slots if s["index"] == selected_index),
                None,
            )
            if not slot_data:
                raise ValueError(f"invalid slot index {selected_index}")

            start = datetime.fromisoformat(slot_data["start_utc"])
            end = datetime.fromisoformat(slot_data["end_utc"])

            neg["status"] = "booking"
            neg["candidate_selected_index"] = selected_index
            neg["candidate_selected_at"] = datetime.now(UTC).isoformat()
            ms.negotiation_state = neg

        elif custom_datetime:
            start = datetime.fromisoformat(custom_datetime)
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
            start = start.astimezone(UTC)

            now = datetime.now(tz=UTC)
            if start < now + timedelta(hours=1):
                raise ValueError("custom time must be at least 1 hour from now")
            if start > now + timedelta(days=15):
                raise ValueError("custom time must be within 15 days")

            end = start + timedelta(minutes=duration_minutes)

            neg["status"] = "booking"
            neg["candidate_custom_datetime"] = start.isoformat()
            neg["candidate_selected_at"] = datetime.now(UTC).isoformat()
            ms.negotiation_state = neg

        else:
            raise ValueError("either selected_index or custom_datetime required")

        await log_audit(
            session,
            application_id=ms.application_id,
            action="candidate_slot_selected",
            actor="candidate",
            details={
                "meeting_session_id": str(meeting_session_id),
                "selected_index": selected_index,
                "custom_datetime": custom_datetime,
                "start": start.isoformat(),
            },
        )

    slot = ProposedSlot(
        start=start,
        end=end,
        panel_emails=panel_emails,
    )

    try:
        await book_confirmed_meeting(
            meeting_session_id=meeting_session_id,
            slot=slot,
        )
        return {
            "status": "booked",
            "meeting_session_id": str(meeting_session_id),
            "scheduled_at": start.isoformat(),
        }
    except Exception as exc:
        logger.exception(
            "candidate selection: booking failed session=%s", meeting_session_id,
        )
        async with session_scope() as session:
            ms = await session.get(MeetingSession, meeting_session_id)
            if ms:
                ms.negotiation_state = {
                    **(ms.negotiation_state or {}),
                    "status": "escalated",
                    "reason": f"booking_failed: {exc!s:.200}",
                }
            await set_stage(session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True)

        return {
            "status": "booking_failed",
            "error": str(exc)[:200],
            "meeting_session_id": str(meeting_session_id),
        }
