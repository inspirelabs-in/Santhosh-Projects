"""Candidate-facing meeting reschedule request (token-signed, no auth).

Flow:
  1. The candidate gets a meeting invite email with a "request a different
     slot" link carrying a signed token (see ``services/chat_meeting``).
  2. They open the link (GET) → see the current time + a few suggested slots.
  3. They submit (POST) an optional preferred time + reason.
  4. We record the request on the meeting session, drop an in-app notification,
     and nudge the recruiter in Pulse chat so they reschedule via the agent.

We never move the meeting here — a human recruiter confirms the new time in
chat (``reschedule_meeting`` tool). This keeps a person in the loop.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from src.db.base import Application, Candidate, MeetingSession, Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.services.chat_meeting import (
    InvalidRescheduleToken,
    _ROUND_LABEL,
    verify_reschedule_token,
)
from src.services.events import publish_event
from src.services.slot_suggest import format_slot, suggest_slots

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/meeting/reschedule", tags=["meeting-reschedule"])


class RescheduleRequestBody(BaseModel):
    requested_at: datetime | None = None  # candidate's preferred new time (optional)
    reason: str | None = None


def _suggested_slots() -> list[dict[str, str]]:
    return [
        {"scheduled_at": s.isoformat(), "label": format_slot(s)}
        for s in suggest_slots(business_days=5, limit=6)
    ]


@router.get("/{token}")
async def get_reschedule_context(token: str) -> dict[str, Any]:
    """Return context for the candidate reschedule page."""
    try:
        claims = verify_reschedule_token(token)
    except InvalidRescheduleToken as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid or expired link: {e}")

    async with session_scope() as session:
        ms = await session.get(MeetingSession, claims.meeting_session_id)
        if ms is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "meeting not found")
        app = await session.get(Application, claims.application_id)
        candidate = await session.get(Candidate, app.candidate_id) if app else None
        role = await session.get(Role, app.role_id) if app and app.role_id else None
        round_v = ms.round
        scheduled_at = ms.scheduled_at

    return {
        "candidate_name": (candidate.name if candidate else None) or "there",
        "role_title": (role.title if role else None) or "the role",
        "round": round_v,
        "round_label": _ROUND_LABEL.get(round_v, round_v.title()),
        "current_scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
        "current_scheduled_label": format_slot(scheduled_at) if scheduled_at else None,
        "suggested_slots": _suggested_slots(),
    }


@router.post("/{token}", status_code=status.HTTP_202_ACCEPTED)
async def submit_reschedule_request(token: str, body: RescheduleRequestBody) -> dict[str, Any]:
    """Record a candidate's reschedule request and notify the recruiter."""
    try:
        claims = verify_reschedule_token(token)
    except InvalidRescheduleToken as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid or expired link: {e}")

    requested_iso = body.requested_at.isoformat() if body.requested_at else None
    reason = (body.reason or "").strip()[:1000]

    async with session_scope() as session:
        ms = await session.get(MeetingSession, claims.meeting_session_id)
        if ms is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "meeting not found")
        app = await session.get(Application, claims.application_id)
        candidate = await session.get(Candidate, app.candidate_id) if app else None
        round_v = ms.round

        st = dict(ms.negotiation_state or {})
        st["reschedule_requested"] = {
            "requested_at": requested_iso,
            "reason": reason or None,
            "ts": datetime.now(tz=UTC).isoformat(),
        }
        ms.negotiation_state = st

        await log_audit(
            session,
            application_id=claims.application_id,
            candidate_id=candidate.id if candidate else None,
            action="meeting_reschedule_requested",
            actor="candidate",
            details={
                "round": round_v,
                "meeting_session_id": str(claims.meeting_session_id),
                "requested_at": requested_iso,
                "reason": reason or None,
            },
        )

    # Nudge the recruiter in Pulse chat (best-effort).
    try:
        await publish_event(
            claims.application_id,
            event="meeting_reschedule_requested",
            data={
                "meeting_session_id": str(claims.meeting_session_id),
                "round": round_v,
                "requested_at": requested_iso,
                "reason": reason or None,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("reschedule nudge publish failed (best-effort): %s", exc)

    return {
        "ok": True,
        "message": (
            "Thanks — your request has been sent to the hiring team. "
            "They'll confirm a new time and email you an updated invite shortly."
        ),
    }
