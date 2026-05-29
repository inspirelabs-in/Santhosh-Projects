"""Live "what is the agent doing right now" endpoint.

Returns a snapshot of in-flight work across the whole pipeline:

  * Voice calls in dialing / in_progress.
  * Assessments invited / in_progress.
  * Meeting bots scheduled / in_call.
  * Recently completed actions (last 30 minutes) for context.

The frontend dashboard polls this every 5-10s, or subscribes to the
existing SSE channel for per-application updates.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import desc, select

from src.api.auth import require_viewer
from src.db.base import (
    Application,
    AssessmentResult,
    AuditLog,
    Candidate,
    MeetingSession,
    Role,
    VoiceCall,
)
from src.db.connection import session_scope

router = APIRouter(prefix="/dashboard/v1/agent", tags=["agent-status"])


class AgentJob(BaseModel):
    kind: str  # "voice_call" | "assessment" | "meeting"
    status: str
    application_id: UUID
    candidate_name: str | None
    role_title: str | None
    started_at: datetime | None
    detail: dict[str, Any] | None = None


class RecentActivity(BaseModel):
    application_id: UUID
    candidate_name: str | None
    action: str
    details: dict[str, Any] | None
    created_at: datetime


class AgentSnapshot(BaseModel):
    in_flight: list[AgentJob]
    recent: list[RecentActivity]
    counts: dict[str, int]


_ACTIVE_VOICE = ["pending", "dialing", "in_progress", "callback_requested"]
_ACTIVE_ASSESS = ["invited", "in_progress"]
_ACTIVE_MEET = ["pending", "scheduled", "in_call"]


@router.get("/snapshot", response_model=AgentSnapshot)
async def agent_snapshot(
    _: Annotated[str, Depends(require_viewer)],
) -> AgentSnapshot:
    in_flight: list[AgentJob] = []
    counts: dict[str, int] = {"voice": 0, "assessment": 0, "meeting": 0}

    async with session_scope() as session:
        # Voice
        stmt = (
            select(VoiceCall, Candidate, Role)
            .join(Application, Application.id == VoiceCall.application_id)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .outerjoin(Role, Role.id == Application.role_id)
            .where(VoiceCall.status.in_(_ACTIVE_VOICE))
            .order_by(desc(VoiceCall.created_at))
            .limit(40)
        )
        for v, cand, role in (await session.execute(stmt)).all():
            counts["voice"] += 1
            in_flight.append(
                AgentJob(
                    kind="voice_call",
                    status=v.status,
                    application_id=v.application_id,
                    candidate_name=cand.name,
                    role_title=role.title if role else None,
                    started_at=v.started_at or v.created_at,
                    detail={"attempt_no": v.attempt_no, "duration_sec": v.duration_sec},
                )
            )

        # Assessments
        stmt = (
            select(AssessmentResult, Candidate, Role)
            .join(Application, Application.id == AssessmentResult.application_id)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .outerjoin(Role, Role.id == Application.role_id)
            .where(AssessmentResult.status.in_(_ACTIVE_ASSESS))
            .order_by(desc(AssessmentResult.created_at))
            .limit(40)
        )
        for a, cand, role in (await session.execute(stmt)).all():
            counts["assessment"] += 1
            in_flight.append(
                AgentJob(
                    kind="assessment",
                    status=a.status,
                    application_id=a.application_id,
                    candidate_name=cand.name,
                    role_title=role.title if role else None,
                    started_at=a.invite_sent_at or a.created_at,
                    detail={"kind": a.assessment_kind, "provider": a.provider},
                )
            )

        # Meetings
        stmt = (
            select(MeetingSession, Candidate, Role)
            .join(Application, Application.id == MeetingSession.application_id)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .outerjoin(Role, Role.id == Application.role_id)
            .where(MeetingSession.bot_status.in_(_ACTIVE_MEET))
            .order_by(desc(MeetingSession.created_at))
            .limit(40)
        )
        for m, cand, role in (await session.execute(stmt)).all():
            counts["meeting"] += 1
            in_flight.append(
                AgentJob(
                    kind="meeting",
                    status=m.bot_status,
                    application_id=m.application_id,
                    candidate_name=cand.name,
                    role_title=role.title if role else None,
                    started_at=m.scheduled_at or m.created_at,
                    detail={"round": m.round},
                )
            )

        # Recent agent actions (last 30 min) for context.
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
        stmt = (
            select(AuditLog, Candidate)
            .join(Application, Application.id == AuditLog.application_id)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .where(AuditLog.created_at >= cutoff)
            .where(AuditLog.actor == "agent")
            .order_by(desc(AuditLog.created_at))
            .limit(20)
        )
        recent = [
            RecentActivity(
                application_id=row.AuditLog.application_id,
                candidate_name=row.Candidate.name,
                action=row.AuditLog.action,
                details=row.AuditLog.details,
                created_at=row.AuditLog.created_at,
            )
            for row in (await session.execute(stmt)).all()
            if row.AuditLog.application_id is not None
        ]

    return AgentSnapshot(in_flight=in_flight, recent=recent, counts=counts)
