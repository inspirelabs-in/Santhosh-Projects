"""Chat-driven meeting scheduling (Google Meet) + candidate reschedule loop.

This is the single source of truth for scheduling interview meetings now that
the auto panel-availability / smart-scheduler flow is disabled. Recruiters
book and move meetings by talking to Pulse (see ``recruiter_agent/tools.py``);
candidates can request a new time via a signed link, which notifies the
recruiter in chat + the notifications bell.

Provider is resolved by ``services/online_meeting`` (gmeet | graph). For
Google Meet, create/reschedule emit native calendar invites automatically; we
also send a branded invite that carries the candidate reschedule link.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from jose import JWTError, jwt
from sqlalchemy import select

from src.channels.email import send_email
from src.config import get_settings
from src.db.base import Application, Candidate, MeetingSession, Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.meeting_session import create_session
from src.db.repositories.v1_application import set_stage
from src.models.v1 import MeetingRound, PipelineStage
from src.services.online_meeting import (
    create_online_meeting,
    get_organiser_email,
    reschedule_online_meeting,
)
from src.services.slot_suggest import format_slot

logger = logging.getLogger(__name__)

_ALGORITHM = "HS256"
RESCHEDULE_TTL_DAYS = 14
DEFAULT_DURATION_MINUTES = 45

_VALID_ROUNDS = {
    MeetingRound.TECHNICAL.value,
    MeetingRound.CEO.value,
    MeetingRound.HR.value,
}
_ROUND_TO_STAGE = {
    "technical": PipelineStage.TECHNICAL_MEETING_SCHEDULED,
    "ceo": PipelineStage.CEO_MEETING_SCHEDULED,
    "hr": PipelineStage.HR_MEETING_SCHEDULED,
}
_ROUND_LABEL = {"technical": "Technical", "ceo": "CEO", "hr": "HR"}


# ─── Reschedule token (signed link the candidate clicks) ──────────────────────


class InvalidRescheduleToken(Exception):
    pass


@dataclass
class RescheduleClaims:
    meeting_session_id: UUID
    application_id: UUID


def generate_reschedule_token(
    *,
    meeting_session_id: UUID,
    application_id: UUID,
    ttl_days: int = RESCHEDULE_TTL_DAYS,
) -> str:
    settings = get_settings()
    exp = datetime.now(tz=UTC) + timedelta(days=ttl_days)
    payload = {
        "ms_id": str(meeting_session_id),
        "app_id": str(application_id),
        "act": "meeting_reschedule",
        "iss": settings.jwt_issuer,
        "exp": int(exp.timestamp()),
        "iat": int(datetime.now(tz=UTC).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_signing_secret, algorithm=_ALGORITHM)


def verify_reschedule_token(token: str) -> RescheduleClaims:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_signing_secret,
            algorithms=[_ALGORITHM],
            issuer=settings.jwt_issuer,
        )
    except JWTError as e:
        raise InvalidRescheduleToken(str(e)) from e
    if payload.get("act") != "meeting_reschedule":
        raise InvalidRescheduleToken("wrong token action")
    try:
        return RescheduleClaims(
            meeting_session_id=UUID(payload["ms_id"]),
            application_id=UUID(payload["app_id"]),
        )
    except (KeyError, ValueError) as e:
        raise InvalidRescheduleToken(f"malformed claims: {e}") from e


def build_reschedule_link(token: str) -> str:
    base = get_settings().frontend_base_url.rstrip("/")
    return f"{base}/meeting/reschedule/{token}"


# ─── Internals ────────────────────────────────────────────────────────────────


def _normalize_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


async def _send_meeting_emails(
    *,
    application_id: UUID,
    candidate_id: UUID | None,
    candidate_name: str,
    candidate_email: str,
    role_title: str,
    round: str,
    join_url: str,
    start_utc: datetime,
    duration_minutes: int,
    panel_emails: list[str],
    reschedule_link: str,
    kind: str,
) -> None:
    """Send branded invite/update emails to the candidate (with the reschedule
    link) and each panel member. Best-effort: a failed send is logged, not raised.
    """
    company = get_settings().voice_agent_company_name
    round_label = _ROUND_LABEL.get(round, round.title())
    slot_human = format_slot(start_utc)
    stamp = int(start_utc.timestamp())

    try:
        await send_email(
            to=candidate_email,
            template="meeting_invite_candidate",
            variables={
                "candidate_name": candidate_name,
                "role_title": role_title,
                "round_label": round_label,
                "slot_human": slot_human,
                "teams_link": join_url,
                "company_name": company,
                "duration_minutes": duration_minutes,
                "reschedule_link": reschedule_link,
            },
            tags={"type": f"meeting_invite_{kind}", "round": round},
            idempotency_key=f"{application_id}:meeting:{round}:cand:{kind}:{stamp}",
            application_id=str(application_id),
            candidate_id=str(candidate_id) if candidate_id else None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("candidate meeting email failed (best-effort): %s", exc)

    for pe in panel_emails:
        try:
            await send_email(
                to=pe,
                template="meeting_invite_panel",
                variables={
                    "candidate_name": candidate_name,
                    "candidate_email": candidate_email,
                    "role_title": role_title,
                    "round_label": round_label,
                    "slot_human": slot_human,
                    "teams_link": join_url,
                    "company_name": company,
                    "duration_minutes": duration_minutes,
                    "reschedule_link": reschedule_link,
                },
                tags={"type": "meeting_invite_panel", "round": round},
                idempotency_key=f"{application_id}:meeting:{round}:panel:{pe}:{kind}:{stamp}",
                application_id=str(application_id),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("panel meeting email to %s failed (best-effort): %s", pe, exc)


# NOTE: we intentionally do NOT enqueue ``dispatch_meeting_bot`` here.
# Read.ai joins via its calendar-OAuth integration with the organiser's
# calendar (the Google Meet event we create IS the trigger), and the post-call
# webhook matches this meeting_session row by join URL / start-time. Calling
# dispatch_meeting_bot would call create_session() a SECOND time and produce a
# duplicate meeting row for the same meeting. (A Recall.ai setup would instead
# need an explicit bot scheduled against THIS existing row, not a new one.)


# ─── Public: book + reschedule ────────────────────────────────────────────────


async def book_meeting(
    *,
    application_id: UUID,
    round: str,
    scheduled_at: datetime,
    panel_emails: list[str],
    duration_minutes: int = DEFAULT_DURATION_MINUTES,
) -> dict:
    """Create an online meeting (Google Meet / Teams), persist the session,
    advance the stage, email everyone, and arm the bot. Returns a result dict
    with ``ok`` or ``error``.
    """
    round = (round or "").strip().lower()
    if round not in _VALID_ROUNDS:
        return {"error": f"invalid_round: {round!r}. Use technical | ceo | hr."}
    if not panel_emails:
        return {"error": "panel_emails_required"}
    start_utc = _normalize_utc(scheduled_at)
    if start_utc <= datetime.now(tz=UTC):
        return {"error": "scheduled_at_must_be_in_the_future"}
    end_utc = start_utc + timedelta(minutes=duration_minutes)

    cfg = get_settings()
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            return {"error": "application_not_found"}
        candidate = await session.get(Candidate, app.candidate_id)
        if candidate is None or not candidate.email:
            return {"error": "candidate_email_missing"}
        role = await session.get(Role, app.role_id) if app.role_id else None
        role_title = role.title if role else "the role"
        candidate_name = candidate.name or "Candidate"
        candidate_email = candidate.email
        candidate_id = candidate.id

    organiser = get_organiser_email() or panel_emails[0]
    subject = (
        f"{cfg.voice_agent_company_name} -- "
        f"{_ROUND_LABEL.get(round, round.title())} interview · "
        f"{candidate_name} ({role_title})"
    )
    try:
        join_url, provider_meeting_id = await create_online_meeting(
            organiser_email=organiser,
            subject=subject,
            start_utc=start_utc,
            end_utc=end_utc,
            attendee_emails=[*panel_emails, candidate_email],
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("create_online_meeting failed for app=%s", application_id)
        return {"error": f"meeting_creation_failed: {exc}"}

    async with session_scope() as session:
        # Reuse an existing non-terminal meeting for this round (prevents
        # duplicate bookings when the agent re-proposes). Earlier duplicate
        # attempts can leave MORE than one such row, so take the most recent
        # and supersede the rest — never assume exactly one (that would raise
        # MultipleResultsFound).
        existing_rows = (
            await session.execute(
                select(MeetingSession)
                .where(
                    MeetingSession.application_id == application_id,
                    MeetingSession.round == round,
                    MeetingSession.bot_status.in_(
                        ("pending", "scheduled", "in_call")
                    ),
                )
                .order_by(MeetingSession.scheduled_at.desc())
            )
        ).scalars().all()
        existing = existing_rows[0] if existing_rows else None
        for stale in existing_rows[1:]:
            stale.bot_status = "cancelled"

        if existing is not None:
            existing.scheduled_at = start_utc
            existing.teams_join_url = join_url
            existing.bot_id = provider_meeting_id
            existing.bot_status = "pending"
            meeting_row = existing
        else:
            meeting_row = await create_session(
                session,
                application_id=application_id,
                interview_id=None,
                round=round,
                teams_join_url=join_url,
                scheduled_at=start_utc,
                bot_provider=cfg.meeting_bot_provider,
            )
            meeting_row.bot_id = provider_meeting_id

        meeting_row.negotiation_state = {
            "panel_emails": panel_emails,
            "duration_minutes": duration_minutes,
            "organiser": organiser,
            "provider": cfg.online_meeting_provider,
            "scheduled_via": "chat",
        }
        meeting_session_id = meeting_row.id
        await set_stage(session, application_id, _ROUND_TO_STAGE[round], force=True)
        await log_audit(
            session,
            application_id=application_id,
            candidate_id=candidate_id,
            action="meeting_scheduled_via_chat",
            actor="agent_via_chat",
            details={
                "round": round,
                "scheduled_at": start_utc.isoformat(),
                "join_url": join_url,
                "panel_emails": panel_emails,
                "meeting_session_id": str(meeting_session_id),
            },
        )

    token = generate_reschedule_token(
        meeting_session_id=meeting_session_id, application_id=application_id
    )
    reschedule_link = build_reschedule_link(token)
    await _send_meeting_emails(
        application_id=application_id,
        candidate_id=candidate_id,
        candidate_name=candidate_name,
        candidate_email=candidate_email,
        role_title=role_title,
        round=round,
        join_url=join_url,
        start_utc=start_utc,
        duration_minutes=duration_minutes,
        panel_emails=panel_emails,
        reschedule_link=reschedule_link,
        kind="candidate",
    )

    return {
        "ok": True,
        "meeting_session_id": str(meeting_session_id),
        "join_url": join_url,
        "scheduled_at": start_utc.isoformat(),
        "round": round,
        "panel_emails": panel_emails,
        "message": (
            f"{_ROUND_LABEL.get(round, round.title())} interview booked for "
            f"{format_slot(start_utc)}. Invite sent to {candidate_name} and "
            f"{len(panel_emails)} panel member(s)."
        ),
    }


async def reschedule_meeting(
    *,
    new_scheduled_at: datetime,
    meeting_session_id: UUID | None = None,
    application_id: UUID | None = None,
    round: str | None = None,
    duration_minutes: int | None = None,
    panel_emails: list[str] | None = None,
) -> dict:
    """Move an existing meeting to a new time. Identify the meeting by
    ``meeting_session_id``, or by ``application_id`` (+ optional ``round``,
    else the most recently scheduled meeting for that application).
    Re-emits invites and re-arms the bot. Returns ``ok`` or ``error``.
    """
    new_start = _normalize_utc(new_scheduled_at)
    if new_start <= datetime.now(tz=UTC):
        return {"error": "new_scheduled_at_must_be_in_the_future"}
    if not meeting_session_id and not application_id:
        return {"error": "meeting_session_id_or_application_id_required"}

    async with session_scope() as session:
        ms: MeetingSession | None = None
        if meeting_session_id:
            ms = await session.get(MeetingSession, meeting_session_id)
        else:
            stmt = select(MeetingSession).where(
                MeetingSession.application_id == application_id
            )
            if round:
                stmt = stmt.where(MeetingSession.round == round.strip().lower())
            stmt = stmt.order_by(MeetingSession.scheduled_at.desc()).limit(1)
            ms = (await session.execute(stmt)).scalar_one_or_none()
        if ms is None:
            return {"error": "meeting_not_found"}

        ms_id = ms.id
        app_id = ms.application_id
        round_v = ms.round
        provider_meeting_id = ms.bot_id
        nstate = dict(ms.negotiation_state or {})
        eff_panel = panel_emails or nstate.get("panel_emails") or []
        eff_duration = (
            duration_minutes
            or nstate.get("duration_minutes")
            or DEFAULT_DURATION_MINUTES
        )
        organiser = nstate.get("organiser") or get_organiser_email()

        app = await session.get(Application, app_id)
        candidate = await session.get(Candidate, app.candidate_id) if app else None
        role = await session.get(Role, app.role_id) if app and app.role_id else None
        candidate_name = (candidate.name if candidate else None) or "Candidate"
        candidate_email = candidate.email if candidate else None
        candidate_id = candidate.id if candidate else None
        role_title = role.title if role else "the role"

    if not candidate_email:
        return {"error": "candidate_email_missing"}
    if not provider_meeting_id:
        return {"error": "meeting_has_no_provider_event_id"}

    end_utc = new_start + timedelta(minutes=eff_duration)
    organiser = organiser or (eff_panel[0] if eff_panel else candidate_email)
    subject = (
        f"{get_settings().voice_agent_company_name} -- "
        f"{_ROUND_LABEL.get(round_v, round_v.title())} interview · "
        f"{candidate_name} ({role_title})"
    )
    try:
        join_url, new_provider_id = await reschedule_online_meeting(
            organiser_email=organiser,
            meeting_id=provider_meeting_id,
            subject=subject,
            start_utc=new_start,
            end_utc=end_utc,
            attendee_emails=[*eff_panel, candidate_email],
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("reschedule_online_meeting failed for ms=%s", ms_id)
        return {"error": f"reschedule_failed: {exc}"}

    async with session_scope() as session:
        row = await session.get(MeetingSession, ms_id)
        if row is not None:
            row.scheduled_at = new_start
            row.teams_join_url = join_url
            row.bot_id = new_provider_id
            row.bot_status = "pending"  # re-arm so the bot rejoins the new time
            st = dict(row.negotiation_state or {})
            st.pop("reschedule_requested", None)  # clear any pending request
            st["panel_emails"] = eff_panel
            st["duration_minutes"] = eff_duration
            st["organiser"] = organiser
            row.negotiation_state = st
            await log_audit(
                session,
                application_id=app_id,
                candidate_id=candidate_id,
                action="meeting_rescheduled_via_chat",
                actor="agent_via_chat",
                details={
                    "round": round_v,
                    "new_scheduled_at": new_start.isoformat(),
                    "join_url": join_url,
                    "meeting_session_id": str(ms_id),
                },
            )

    token = generate_reschedule_token(
        meeting_session_id=ms_id, application_id=app_id
    )
    reschedule_link = build_reschedule_link(token)
    await _send_meeting_emails(
        application_id=app_id,
        candidate_id=candidate_id,
        candidate_name=candidate_name,
        candidate_email=candidate_email,
        role_title=role_title,
        round=round_v,
        join_url=join_url,
        start_utc=new_start,
        duration_minutes=eff_duration,
        panel_emails=eff_panel,
        reschedule_link=reschedule_link,
        kind="reschedule",
    )

    return {
        "ok": True,
        "meeting_session_id": str(ms_id),
        "join_url": join_url,
        "scheduled_at": new_start.isoformat(),
        "round": round_v,
        "message": (
            f"{_ROUND_LABEL.get(round_v, round_v.title())} interview moved to "
            f"{format_slot(new_start)}. Updated invite sent to {candidate_name} "
            f"and {len(eff_panel)} panel member(s)."
        ),
    }
