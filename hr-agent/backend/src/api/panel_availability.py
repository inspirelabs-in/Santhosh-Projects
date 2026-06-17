"""Public endpoints for panel availability and candidate slot selection.

Panel flow:
  GET  /panel-availability/{token}          — context for the scheduling page
  POST /panel-availability/{token}/slots    — panel submits 3 time slots

Candidate flow:
  GET  /panel-availability/candidate/{token}  — context + available slots
  POST /panel-availability/candidate/{token}  — candidate picks slot or custom
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from src.db.base import Application, Candidate, MeetingSession, Role
from src.db.connection import session_scope
from src.services.panel_availability import (
    REQUIRED_PANEL_SLOTS,
    InvalidCandidateToken,
    InvalidPanelToken,
    record_candidate_selection,
    record_panel_slots,
    verify_candidate_token,
    verify_panel_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/panel-availability", tags=["panel-availability"])


# ─── Models ───────────────────────────────────────────────────────


class PanelAvailabilityContext(BaseModel):
    meeting_session_id: str
    panel_email: str
    round: str
    candidate_name: str | None
    role_title: str | None
    already_responded: bool
    status: str
    duration_minutes: int = 45
    panel_timezone: str = "Asia/Kolkata"
    required_slots: int = REQUIRED_PANEL_SLOTS


class PanelSlotSubmission(BaseModel):
    slots: list[dict[str, str]]


class CandidateSlotContext(BaseModel):
    meeting_session_id: str
    round: str
    candidate_name: str | None
    role_title: str | None
    slots: list[dict[str, Any]]
    status: str
    duration_minutes: int = 45


class CandidateSlotSelection(BaseModel):
    selected_index: int | None = None
    custom_datetime: str | None = None


# ─── Panel endpoints ──────────────────────────────────────────────


@router.get("/{token}")
async def get_panel_availability(token: str) -> PanelAvailabilityContext:
    try:
        claims = verify_panel_token(token)
    except InvalidPanelToken as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid or expired link: {e}",
        ) from e

    async with session_scope() as session:
        ms = await session.get(MeetingSession, claims.meeting_session_id)
        if ms is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Interview session not found",
            )

        neg = ms.negotiation_state or {}
        current_status = neg.get("status", "unknown")
        already_responded = current_status != "awaiting_panel"

        app = await session.get(Application, ms.application_id)
        candidate_name = None
        role_title = None
        if app:
            cand = await session.get(Candidate, app.candidate_id)
            candidate_name = cand.name if cand else None
            if app.role_id:
                role = await session.get(Role, app.role_id)
                role_title = role.title if role else None

    return PanelAvailabilityContext(
        meeting_session_id=str(claims.meeting_session_id),
        panel_email=claims.panel_email,
        round=claims.round,
        candidate_name=candidate_name,
        role_title=role_title,
        already_responded=already_responded,
        status=current_status,
        duration_minutes=neg.get("duration_minutes", 45),
        panel_timezone=neg.get("panel_timezone", "Asia/Kolkata"),
        required_slots=neg.get("required_slots", REQUIRED_PANEL_SLOTS),
    )


@router.post("/{token}/slots")
async def submit_panel_slots(
    token: str,
    body: PanelSlotSubmission,
) -> dict[str, Any]:
    """Panel member submits their chosen time slots."""
    try:
        claims = verify_panel_token(token)
    except InvalidPanelToken as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid or expired link: {e}",
        ) from e

    if len(body.slots) != REQUIRED_PANEL_SLOTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Exactly {REQUIRED_PANEL_SLOTS} time slots required",
        )

    for i, s in enumerate(body.slots):
        if "start" not in s or "end" not in s:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Slot {i+1} missing start or end",
            )

    try:
        result = await record_panel_slots(
            meeting_session_id=claims.meeting_session_id,
            panel_email=claims.panel_email,
            slots=body.slots,
        )
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        ) from e
    except Exception as exc:
        logger.exception("panel slots submission failed session=%s", claims.meeting_session_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save slots: {str(exc)[:200]}",
        ) from exc


# ─── Candidate endpoints ─────────────────────────────────────────


@router.get("/candidate/{token}")
async def get_candidate_slots(token: str) -> CandidateSlotContext:
    """Candidate opens their selection link — returns available slots."""
    try:
        claims = verify_candidate_token(token)
    except InvalidCandidateToken as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid or expired link: {e}",
        ) from e

    async with session_scope() as session:
        ms = await session.get(MeetingSession, claims.meeting_session_id)
        if ms is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Interview session not found",
            )

        neg = ms.negotiation_state or {}
        current_status = neg.get("status", "unknown")
        panel_slots = neg.get("panel_slots", [])

        app = await session.get(Application, ms.application_id)
        candidate_name = None
        role_title = None
        if app:
            cand = await session.get(Candidate, app.candidate_id)
            candidate_name = cand.name if cand else None
            if app.role_id:
                role = await session.get(Role, app.role_id)
                role_title = role.title if role else None

    return CandidateSlotContext(
        meeting_session_id=str(claims.meeting_session_id),
        round=claims.round,
        candidate_name=candidate_name,
        role_title=role_title,
        slots=panel_slots,
        status=current_status,
        duration_minutes=neg.get("duration_minutes", 45),
    )


@router.post("/candidate/{token}")
async def submit_candidate_selection(
    token: str,
    body: CandidateSlotSelection,
) -> dict[str, Any]:
    """Candidate picks a slot or proposes custom datetime."""
    try:
        claims = verify_candidate_token(token)
    except InvalidCandidateToken as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid or expired link: {e}",
        ) from e

    if body.selected_index is None and not body.custom_datetime:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Select a time slot or propose a custom time",
        )

    try:
        result = await record_candidate_selection(
            meeting_session_id=claims.meeting_session_id,
            application_id=claims.application_id,
            selected_index=body.selected_index,
            custom_datetime=body.custom_datetime,
        )
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        ) from e
    except Exception as exc:
        logger.exception("candidate selection failed session=%s", claims.meeting_session_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to book meeting: {str(exc)[:200]}",
        ) from exc
