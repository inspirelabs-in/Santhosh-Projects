"""HR-facing endpoints for the agentic interview rounds.

Two surfaces:

  * Dispatch endpoints (POST) -- start a voice screen / send assessment / send
    a meeting bot. Recruiter+ only.
  * List endpoints (GET) -- paginated read-only views over voice_calls,
    assessment_results, and meeting_sessions. Viewer+ allowed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import desc, select

from src.activities.v1_dispatch_assessment import dispatch_assessment
from src.activities.v1_dispatch_meeting_bot import dispatch_meeting_bot
from src.activities.v1_schedule_meeting import schedule_meeting
from src.activities.v1_voice_screening import dispatch_voice_screening
from src.api.auth import require_admin, require_recruiter, require_viewer
from src.config import get_settings
from src.db.base import (
    Application,
    AssessmentResult,
    Candidate,
    MeetingSession,
    Role,
    VoiceCall,
)
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.v1_application import save_admin_review, set_stage
from src.models.v1 import MeetingRound, PipelineStage
from src.services.file_storage import presigned_get_url
from src.services.queue import enqueue

router = APIRouter(prefix="/agentic", tags=["agentic"])


class StartVoiceScreenBody(BaseModel):
    application_id: UUID
    scheduled_at: datetime | None = None


class PromoteVoiceBody(BaseModel):
    application_id: UUID
    note: str | None = None


class ManualScheduleBody(BaseModel):
    application_id: UUID
    round: str  # technical | ceo | hr
    scheduled_at: datetime
    duration_minutes: int = 45
    panel_emails: list[str]


@router.post("/meeting/manual-schedule", status_code=status.HTTP_201_CREATED)
async def manual_schedule_meeting(
    body: ManualScheduleBody,
    _: Annotated[str, Depends(require_recruiter)],
) -> dict[str, Any]:
    """HR override: book a meeting at a specific datetime + panel.

    Bypasses the auto slot-finder. Mints the Teams link, persists the
    meeting_session row, advances stage. Use when free-busy fails or HR
    coordinated slots out-of-band (Slack, WhatsApp, email).
    """
    from src.config import get_settings as _get_settings
    from src.db.base import Application, Candidate, Role
    from src.db.repositories.audit import log_audit
    from src.db.repositories.meeting_session import create_session
    from src.db.repositories.v1_application import set_stage
    from src.models.v1 import MeetingRound, PipelineStage
    from src.services.online_meeting import create_online_meeting, get_organiser_email
    from datetime import timedelta as _td

    if body.round not in {MeetingRound.TECHNICAL.value, MeetingRound.CEO.value, MeetingRound.HR.value}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid round {body.round}")
    if not body.panel_emails:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "panel_emails required")

    cfg = _get_settings()
    end = body.scheduled_at + _td(minutes=body.duration_minutes)
    organiser = get_organiser_email() or body.panel_emails[0]

    async with session_scope() as session:
        app = await session.get(Application, body.application_id)
        if app is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "application not found")
        candidate = await session.get(Candidate, app.candidate_id)
        if candidate is None or not candidate.email:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "candidate email missing")
        role = await session.get(Role, app.role_id) if app.role_id else None
        role_title = role.title if role else "the role"
        candidate_name = candidate.name or "Candidate"
        candidate_email = candidate.email
        application_id = app.id

    subject = (
        f"{cfg.voice_agent_company_name} -- {body.round.title()} interview · "
        f"{candidate_name} ({role_title})"
    )
    join_url, ms_meeting_id = await create_online_meeting(
        organiser_email=organiser,
        subject=subject,
        start_utc=body.scheduled_at,
        end_utc=end,
        attendee_emails=[*body.panel_emails, candidate_email],
    )

    _ROUND_TO_STAGE = {
        "technical": PipelineStage.TECHNICAL_MEETING_SCHEDULED,
        "ceo": PipelineStage.CEO_MEETING_SCHEDULED,
        "hr": PipelineStage.HR_MEETING_SCHEDULED,
    }
    async with session_scope() as session:
        meeting_row = await create_session(
            session,
            application_id=application_id,
            interview_id=None,
            round=body.round,
            teams_join_url=join_url,
            scheduled_at=body.scheduled_at,
            bot_provider="recall",
        )
        meeting_row.bot_id = ms_meeting_id
        await set_stage(
            session, application_id, _ROUND_TO_STAGE[body.round], force=True
        )
        await log_audit(
            session,
            application_id=application_id,
            action="meeting_manually_scheduled",
            actor="agent",
            details={
                "round": body.round,
                "scheduled_at": body.scheduled_at.isoformat(),
                "join_url": join_url,
                "panel_emails": body.panel_emails,
            },
        )
    return {
        "ok": True,
        "meeting_session_id": str(meeting_row.id),
        "join_url": join_url,
        "scheduled_at": body.scheduled_at.isoformat(),
    }


@router.post("/voice-screen/promote", status_code=status.HTTP_202_ACCEPTED)
async def promote_voice_screen(
    body: PromoteVoiceBody,
    background: BackgroundTasks,
    actor: Annotated[str, Depends(require_recruiter)],
) -> dict[str, Any]:
    """HR override: push a needs_hr_review candidate into the assessment round.

    Bypasses the auto-pass threshold. Stage moves to VOICE_SCREEN_EVALUATED
    and the assessment dispatcher fires the next stage (assignment email).
    """
    from src.db.repositories.audit import log_audit
    from src.db.repositories.v1_application import set_stage
    from src.models.v1 import PipelineStage
    from src.services.auto_progress import auto_progress

    async with session_scope() as session:
        await set_stage(
            session,
            body.application_id,
            PipelineStage.VOICE_SCREEN_EVALUATED,
            force=True,
        )
        await log_audit(
            session,
            application_id=body.application_id,
            action="voice_screen_promoted_to_assessment",
            actor=actor,
            details={"note": body.note or ""},
        )
    background.add_task(auto_progress, application_id=body.application_id)
    return {"ok": True}


@router.post("/voice-screen/reconcile", status_code=status.HTTP_202_ACCEPTED)
async def reconcile_voice_screens(
    background: BackgroundTasks,
    _: Annotated[str, Depends(require_recruiter)],
) -> dict[str, Any]:
    """Force a one-shot reconcile pass against ElevenLabs.

    Useful when a webhook delivery was missed (ngrok restart, deploy, network
    blip) and a row sits stuck in dialing / in_progress.
    """
    from src.workers.jobs import reconcile_stuck_voice_calls

    queued = await enqueue("reconcile_stuck_voice_calls")
    if not queued:
        background.add_task(reconcile_stuck_voice_calls, ctx={})
    return {"ok": True}


@router.post("/voice-screen/dispatch", status_code=status.HTTP_202_ACCEPTED)
async def start_voice_screen(
    body: StartVoiceScreenBody,
    background: BackgroundTasks,
    _: Annotated[str, Depends(require_recruiter)],
) -> dict[str, Any]:
    # Validate prereqs synchronously so the recruiter sees a clear error
    # instead of a silent 202 + background-task crash. Without this, missing
    # phone / profile / role left the candidate stuck on "applied" with no
    # surfaced reason.
    from src.db.base import Application, Candidate, CandidateProfileRow, Role
    from src.db.connection import session_scope
    from sqlalchemy import select

    settings = get_settings()
    if not settings.enable_voice_screening:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "voice screening disabled")

    async with session_scope() as session:
        app = await session.get(Application, body.application_id)
        if app is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "application not found")
        if app.role_id is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "application has no role")
        candidate = await session.get(Candidate, app.candidate_id)
        if candidate is None or not candidate.phone:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "candidate phone missing -- cannot dial",
            )
        role = await session.get(Role, app.role_id)
        if role is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "role missing")
        profile_row = (
            await session.execute(
                select(CandidateProfileRow)
                .where(CandidateProfileRow.candidate_id == candidate.id)
                .order_by(CandidateProfileRow.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if profile_row is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "candidate profile not parsed yet -- upload + parse resume first",
            )

    queued = await enqueue(
        "dispatch_voice_screening",
        str(body.application_id),
        scheduled_at_iso=body.scheduled_at.isoformat() if body.scheduled_at else None,
    )
    if not queued:
        background.add_task(
            dispatch_voice_screening,
            application_id=body.application_id,
            scheduled_at=body.scheduled_at,
        )
    return {"ok": True, "application_id": str(body.application_id)}


class DispatchVoiceCallBody(BaseModel):
    application_id: UUID
    call_kind: str = "status_update"


@router.post("/voice-call/dispatch", status_code=status.HTTP_202_ACCEPTED)
async def dispatch_generic_voice_call(
    body: DispatchVoiceCallBody,
    background: BackgroundTasks,
    _: Annotated[str, Depends(require_recruiter)],
) -> dict[str, Any]:
    """Dispatch a non-screening voice call (confirmation, status update, etc.)."""
    from src.db.base import Application, Candidate
    from src.db.connection import session_scope
    from src.models.v1 import CallKind

    settings = get_settings()
    if not (settings.enable_voice_screening or settings.enable_voice_calls):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "voice calls disabled")

    try:
        kind = CallKind(body.call_kind)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid call_kind: {body.call_kind}")

    async with session_scope() as session:
        app = await session.get(Application, body.application_id)
        if app is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "application not found")
        candidate = await session.get(Candidate, app.candidate_id)
        if candidate is None or not candidate.phone:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "candidate phone missing")

    queued = await enqueue(
        "dispatch_voice_call",
        str(body.application_id),
        call_kind=kind.value,
    )
    if not queued:
        from src.activities.v1_dispatch_voice_call import dispatch_voice_call as _dvc
        background.add_task(
            _dvc,
            application_id=body.application_id,
            call_kind=kind,
        )
    return {"ok": True, "voice_call_id": str(body.application_id)}


class StartAssessmentBody(BaseModel):
    application_id: UUID


@router.post("/assessment/dispatch", status_code=status.HTTP_202_ACCEPTED)
async def start_assessment(
    body: StartAssessmentBody,
    background: BackgroundTasks,
    _: Annotated[str, Depends(require_recruiter)],
) -> dict[str, Any]:
    queued = await enqueue("dispatch_assessment", str(body.application_id))
    if not queued:
        background.add_task(dispatch_assessment, application_id=body.application_id)
    return {"ok": True, "application_id": str(body.application_id)}


class DispatchMeetingBody(BaseModel):
    application_id: UUID
    round: str  # "technical" | "ceo"
    teams_join_url: str
    scheduled_at: datetime
    interview_id: UUID | None = None


@router.post("/meeting/dispatch", status_code=status.HTTP_202_ACCEPTED)
async def dispatch_meeting(
    body: DispatchMeetingBody,
    _: Annotated[str, Depends(require_recruiter)],
) -> dict[str, Any]:
    if body.round not in {MeetingRound.TECHNICAL.value, MeetingRound.CEO.value, MeetingRound.HR.value}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid round {body.round}")
    meeting_session_id = await dispatch_meeting_bot(
        application_id=body.application_id,
        round=body.round,
        teams_join_url=body.teams_join_url,
        scheduled_at=body.scheduled_at,
        interview_id=body.interview_id,
    )
    return {"ok": True, "meeting_session_id": str(meeting_session_id)}


class AISchedulingBody(BaseModel):
    application_id: UUID
    round: str  # "technical" | "ceo" | "hr"


@router.post("/meeting/ai-schedule", status_code=status.HTTP_202_ACCEPTED)
async def ai_schedule_meeting(
    body: AISchedulingBody,
    background: BackgroundTasks,
    _: Annotated[str, Depends(require_recruiter)],
) -> dict[str, Any]:
    """Hand the scheduling job to the agent.

    Steps the agent runs:
      * picks an open slot from the role's panel windows + Graph free/busy,
      * mints a Teams meeting via Graph,
      * emails panel + candidate the link,
      * places a confirmation phone call to the candidate.

    Returns 202 immediately; the heavy lifting runs in the worker.
    """
    if body.round not in {"technical", "ceo", "hr"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid round {body.round}")
    queued = await enqueue(
        "schedule_meeting",
        str(body.application_id),
        round=body.round,
    )
    if not queued:
        background.add_task(
            schedule_meeting,
            application_id=body.application_id,
            round=body.round,
        )
    return {"ok": True, "application_id": str(body.application_id), "round": body.round}


# ---------------------------------------------------------------------------
# Admin-gate endpoints: explicit human approvals between rounds
# ---------------------------------------------------------------------------


_PI_PERSONAS = {
    "altruist", "captain", "collaborator", "controller", "craftsman",
    "guardian", "individualist", "maverick", "operator", "persuader",
    "promoter", "scholar", "specialist", "strategist", "venturer",
    "adapter", "analyzer",
}


class AssessmentReviewBody(BaseModel):
    application_id: UUID
    decision: str  # "approve" | "reject"
    pi_assessment_link: str | None = None
    pi_persona: str | None = None
    notes: str | None = None


class RoundApprovalBody(BaseModel):
    application_id: UUID
    decision: str  # "approve" | "reject"
    notes: str | None = None


class HRFinalizeBody(BaseModel):
    application_id: UUID
    decision: str  # "hired" | "rejected"
    notes: str | None = None


_GATE_STAGES = {
    "assessment": {
        PipelineStage.ASSESSMENT_COMPLETED.value,
        PipelineStage.ASSESSMENT_PENDING_REVIEW.value,
    },
    "technical": {
        PipelineStage.TECHNICAL_EVALUATED.value,
        PipelineStage.TECHNICAL_PENDING_APPROVAL.value,
    },
    "ceo": {
        PipelineStage.CEO_MEETING_COMPLETED.value,
        PipelineStage.CEO_PENDING_APPROVAL.value,
    },
    "hr": {
        PipelineStage.HR_MEETING_COMPLETED.value,
        PipelineStage.HR_EVALUATED.value,
    },
}


async def _guard_stage_for_round(application_id: UUID, round_key: str) -> Application:
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "application_not_found")
        if app.current_stage not in _GATE_STAGES[round_key]:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"application not at {round_key} review gate "
                f"(current_stage={app.current_stage})",
            )
        existing = (app.admin_review or {}).get(round_key)
        if existing and existing.get("decision"):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{round_key} round already decided ({existing.get('decision')})",
            )
        return app


async def _record_review(
    *,
    application_id: UUID,
    round_key: str,
    actor: str,
    payload: dict[str, Any],
    new_stage: PipelineStage | None,
) -> None:
    async with session_scope() as session:
        await save_admin_review(
            session,
            application_id,
            round_key=round_key,
            payload=payload,
        )
        if new_stage is not None:
            await set_stage(session, application_id, new_stage, force=True)
        await log_audit(
            session,
            application_id=application_id,
            action=f"admin_review_{round_key}",
            actor=actor,
            details={
                "new_stage": new_stage.value if new_stage else None,
                **payload,
            },
        )


async def _kick_schedule_meeting(application_id: UUID, round: str) -> None:
    from src.services.queue import enqueue
    from src.db.connection import session_scope
    from src.db.repositories.audit import log_audit
    queued = await enqueue("schedule_meeting", str(application_id), round=round)
    if queued:
        return
    try:
        await schedule_meeting(application_id=application_id, round=round)
    except Exception as exc:  # noqa: BLE001
        # Surface failure into audit log so HR can see why nothing got scheduled.
        async with session_scope() as session:
            await log_audit(
                session,
                application_id=application_id,
                action="schedule_meeting_failed",
                actor="agent",
                details={"round": round, "error": str(exc)[:500]},
            )


@router.post("/assessment/review", status_code=status.HTTP_200_OK)
async def review_assessment(
    body: AssessmentReviewBody,
    actor: Annotated[str, Depends(require_recruiter)],
) -> dict[str, Any]:
    if body.decision not in {"approve", "reject"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "decision must be approve|reject")
    await _guard_stage_for_round(body.application_id, "assessment")
    if body.decision == "approve":
        if not body.pi_assessment_link or not body.pi_persona:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "pi_assessment_link and pi_persona required to approve",
            )
        if body.pi_persona.lower() not in _PI_PERSONAS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"unknown pi_persona; expected one of {sorted(_PI_PERSONAS)}",
            )
    new_stage = (
        PipelineStage.ASSESSMENT_EVALUATED if body.decision == "approve"
        else PipelineStage.REJECTED
    )
    await _record_review(
        application_id=body.application_id,
        round_key="assessment",
        actor=actor,
        payload={
            "decision": body.decision,
            "pi_assessment_link": body.pi_assessment_link,
            "pi_persona": (body.pi_persona or "").lower() or None,
            "notes": body.notes,
            "reviewer": actor,
        },
        new_stage=new_stage,
    )
    if body.decision == "approve":
        # Kick the technical round scheduler now that admin signed off.
        await _kick_schedule_meeting(body.application_id, "technical")
    return {"ok": True, "stage": new_stage.value}


@router.post("/technical/approve", status_code=status.HTTP_200_OK)
async def approve_technical(
    body: RoundApprovalBody,
    actor: Annotated[str, Depends(require_recruiter)],
) -> dict[str, Any]:
    if body.decision not in {"approve", "reject"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "decision must be approve|reject")
    await _guard_stage_for_round(body.application_id, "technical")
    if body.decision == "reject":
        await _record_review(
            application_id=body.application_id,
            round_key="technical",
            actor=actor,
            payload={"decision": body.decision, "notes": body.notes, "reviewer": actor},
            new_stage=PipelineStage.REJECTED,
        )
        return {"ok": True, "stage": PipelineStage.REJECTED.value}
    await _record_review(
        application_id=body.application_id,
        round_key="technical",
        actor=actor,
        payload={"decision": body.decision, "notes": body.notes, "reviewer": actor},
        new_stage=None,
    )
    await _kick_schedule_meeting(body.application_id, "ceo")
    return {"ok": True, "stage": "ceo_scheduling"}


@router.post("/ceo/approve", status_code=status.HTTP_200_OK)
async def approve_ceo(
    body: RoundApprovalBody,
    actor: Annotated[str, Depends(require_recruiter)],
) -> dict[str, Any]:
    if body.decision not in {"approve", "reject"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "decision must be approve|reject")
    await _guard_stage_for_round(body.application_id, "ceo")
    if body.decision == "reject":
        await _record_review(
            application_id=body.application_id,
            round_key="ceo",
            actor=actor,
            payload={"decision": body.decision, "notes": body.notes, "reviewer": actor},
            new_stage=PipelineStage.REJECTED,
        )
        return {"ok": True, "stage": PipelineStage.REJECTED.value}
    await _record_review(
        application_id=body.application_id,
        round_key="ceo",
        actor=actor,
        payload={"decision": body.decision, "notes": body.notes, "reviewer": actor},
        new_stage=None,
    )
    await _kick_schedule_meeting(body.application_id, "hr")
    return {"ok": True, "stage": "hr_scheduling"}


@router.post("/hr/finalize", status_code=status.HTTP_200_OK)
async def finalize_hr(
    body: HRFinalizeBody,
    actor: Annotated[str, Depends(require_admin)],
) -> dict[str, Any]:
    if body.decision not in {"hired", "rejected"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "decision must be hired|rejected")
    await _guard_stage_for_round(body.application_id, "hr")
    new_stage = (
        PipelineStage.HIRED if body.decision == "hired" else PipelineStage.REJECTED
    )
    await _record_review(
        application_id=body.application_id,
        round_key="hr",
        actor=actor,
        payload={"decision": body.decision, "notes": body.notes, "reviewer": actor},
        new_stage=new_stage,
    )
    try:
        from src.services.outcome_email import dispatch_outcome_email
        await dispatch_outcome_email(
            application_id=body.application_id,
            outcome=body.decision,
            notes=body.notes,
        )
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "stage": new_stage.value}


# ---------------------------------------------------------------------------
# Read-side: paginated list endpoints for HR dashboards
# ---------------------------------------------------------------------------


async def _signed(key: str | None, ttl: int = 3600) -> str | None:
    if not key:
        return None
    bucket = get_settings().r2_bucket_resumes
    return await presigned_get_url(bucket, key, ttl_seconds=ttl)


class VoiceCallListItem(BaseModel):
    voice_call_id: UUID
    application_id: UUID
    candidate_name: str | None
    role_title: str | None
    candidate_phone: str | None
    status: str
    call_kind: str = "screening"
    overall_score: int | None
    verdict: str | None
    duration_sec: float | None
    scheduled_at: datetime | None
    callback_at: datetime | None
    callback_reason: str | None
    candidate_response: str | None
    next_action: str | None
    error: str | None
    attempt_no: int
    recording_url: str | None
    transcript_url: str | None
    created_at: datetime
    ended_at: datetime | None


@router.get("/voice-calls", response_model=list[VoiceCallListItem])
async def list_voice_calls(
    _: Annotated[str, Depends(require_viewer)],
    status_filter: str | None = Query(default=None, alias="status"),
    call_kind: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[VoiceCallListItem]:
    async with session_scope() as session:
        stmt = (
            select(VoiceCall, Candidate, Role)
            .join(Application, Application.id == VoiceCall.application_id)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .outerjoin(Role, Role.id == Application.role_id)
            .order_by(desc(VoiceCall.created_at))
            .limit(limit)
            .offset(offset)
        )
        if status_filter:
            stmt = stmt.where(VoiceCall.status == status_filter)
        if call_kind:
            stmt = stmt.where(VoiceCall.call_kind == call_kind)
        rows = (await session.execute(stmt)).all()

        from src.api.ceo_dashboard import _compute_next_action

        items: list[VoiceCallListItem] = []
        for v, cand, role in rows:
            candidate_response: str | None = None
            if isinstance(v.answers, list) and v.answers:
                last_ans = v.answers[-1]
                if isinstance(last_ans, dict):
                    txt = last_ans.get("answer_transcript")
                    if isinstance(txt, str) and txt.strip():
                        candidate_response = txt.strip()[:400]
            if candidate_response is None and v.callback_reason:
                candidate_response = v.callback_reason

            items.append(
                VoiceCallListItem(
                    voice_call_id=v.id,
                    application_id=v.application_id,
                    candidate_name=cand.name,
                    role_title=role.title if role is not None else None,
                    candidate_phone=v.candidate_phone,
                    status=v.status,
                    call_kind=getattr(v, "call_kind", "screening"),
                    overall_score=v.overall_score,
                    verdict=v.verdict,
                    duration_sec=v.duration_sec,
                    scheduled_at=v.scheduled_at,
                    callback_at=v.callback_at,
                    callback_reason=v.callback_reason,
                    candidate_response=candidate_response,
                    next_action=_compute_next_action(v),
                    error=v.error,
                    attempt_no=v.attempt_no,
                    recording_url=await _signed(v.recording_r2_key),
                    transcript_url=await _signed(v.transcript_r2_key),
                    created_at=v.created_at,
                    ended_at=v.ended_at,
                )
            )
        return items


class AssessmentListItem(BaseModel):
    assessment_id: UUID
    application_id: UUID
    candidate_name: str | None
    role_title: str | None
    provider: str
    kind: str | None
    status: str
    fit_band: str | None
    normalized_score: float | None
    percentile: float | None
    invite_sent_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


_ASSIGNMENT_INVITE_STAGES = {
    PipelineStage.ASSIGNMENT_SENT.value,
}
_ASSIGNMENT_IN_PROGRESS_STAGES = {
    PipelineStage.ASSIGNMENT_SUBMITTED.value,
}
_ASSIGNMENT_COMPLETED_STAGES = {
    PipelineStage.REPORT_READY.value,
    PipelineStage.TECHNICAL_PENDING_APPROVAL.value,
    PipelineStage.CEO_MEETING_SCHEDULED.value,
    PipelineStage.CEO_MEETING_IN_PROGRESS.value,
    PipelineStage.CEO_MEETING_COMPLETED.value,
    PipelineStage.CEO_PENDING_APPROVAL.value,
    PipelineStage.HR_MEETING_SCHEDULED.value,
    PipelineStage.HR_MEETING_IN_PROGRESS.value,
    PipelineStage.HR_MEETING_COMPLETED.value,
    PipelineStage.HR_EVALUATED.value,
    PipelineStage.HIRED.value,
}


def _assignment_row_for(app: Application, cand: Candidate, role: Role | None) -> AssessmentListItem:
    stage = app.current_stage
    has_submission = bool(app.assignment_submission)
    if stage in _ASSIGNMENT_COMPLETED_STAGES:
        status = "completed"
    elif stage in _ASSIGNMENT_IN_PROGRESS_STAGES:
        status = "in_progress"
    elif stage in _ASSIGNMENT_INVITE_STAGES:
        status = "invite_sent"
    elif stage == PipelineStage.REJECTED.value and has_submission:
        status = "completed"
    else:
        status = "queued"

    completed_at = app.updated_at if stage in _ASSIGNMENT_COMPLETED_STAGES else None
    return AssessmentListItem(
        assessment_id=app.id,
        application_id=app.id,
        candidate_name=cand.name,
        role_title=role.title if role is not None else None,
        provider="internal",
        kind="assignment",
        status=status,
        fit_band=app.fit_tier,
        normalized_score=float(app.fit_score) / 10.0 if app.fit_score is not None else None,
        percentile=float(app.fit_score) if app.fit_score is not None else None,
        invite_sent_at=app.updated_at if stage == PipelineStage.ASSIGNMENT_SENT.value else None,
        completed_at=completed_at,
        created_at=app.created_at,
    )


@router.get("/assessments", response_model=list[AssessmentListItem])
async def list_assessments(
    _: Annotated[str, Depends(require_viewer)],
    status_filter: str | None = Query(default=None, alias="status"),
    provider: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[AssessmentListItem]:
    async with session_scope() as session:
        stmt = (
            select(AssessmentResult, Candidate, Role)
            .join(Application, Application.id == AssessmentResult.application_id)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .outerjoin(Role, Role.id == Application.role_id)
            .order_by(desc(AssessmentResult.created_at))
            .limit(limit)
            .offset(offset)
        )
        if status_filter:
            stmt = stmt.where(AssessmentResult.status == status_filter)
        if provider:
            stmt = stmt.where(AssessmentResult.provider == provider)
        rows = (await session.execute(stmt)).all()
        pi_rows = [
            AssessmentListItem(
                assessment_id=a.id,
                application_id=a.application_id,
                candidate_name=cand.name,
                role_title=role.title if role is not None else None,
                provider=a.provider,
                kind=a.assessment_kind,
                status=a.status,
                fit_band=a.fit_band,
                normalized_score=float(a.normalized_score) if a.normalized_score is not None else None,
                percentile=float(a.percentile) if a.percentile is not None else None,
                invite_sent_at=a.invite_sent_at,
                completed_at=a.completed_at,
                created_at=a.created_at,
            )
            for a, cand, role in rows
        ]

        # Synthetic rows for V1 coding assignments — surfaces invite/submitted/completed
        # state on the assessments page so HR sees a unified view.
        assign_stmt = (
            select(Application, Candidate, Role)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .outerjoin(Role, Role.id == Application.role_id)
            .where(
                Application.current_stage.in_(
                    list(
                        _ASSIGNMENT_INVITE_STAGES
                        | _ASSIGNMENT_IN_PROGRESS_STAGES
                        | _ASSIGNMENT_COMPLETED_STAGES
                    )
                )
            )
            .order_by(desc(Application.updated_at))
            .limit(limit)
        )
        if provider and provider != "internal":
            assign_rows: list = []
        else:
            assign_rows = (await session.execute(assign_stmt)).all()

        assignment_items = [
            _assignment_row_for(app, cand, role)
            for app, cand, role in assign_rows
        ]
        if status_filter:
            assignment_items = [
                row for row in assignment_items if row.status == status_filter
            ]

        merged = pi_rows + assignment_items
        merged.sort(key=lambda r: r.created_at, reverse=True)
        return merged[: limit + offset][offset:]


class MeetingListItem(BaseModel):
    meeting_session_id: UUID
    application_id: UUID
    candidate_name: str | None
    role_title: str | None
    round: str
    bot_status: str
    overall_score: int | None
    verdict: str | None
    scheduled_at: datetime | None
    started_at: datetime | None
    duration_sec: float | None
    transcript_url: str | None
    created_at: datetime


@router.get("/meetings", response_model=list[MeetingListItem])
async def list_meetings(
    _: Annotated[str, Depends(require_viewer)],
    round_filter: str | None = Query(default=None, alias="round"),
    bot_status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[MeetingListItem]:
    async with session_scope() as session:
        stmt = (
            select(MeetingSession, Candidate, Role)
            .join(Application, Application.id == MeetingSession.application_id)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .outerjoin(Role, Role.id == Application.role_id)
            .order_by(desc(MeetingSession.created_at))
            .limit(limit)
            .offset(offset)
        )
        if round_filter:
            stmt = stmt.where(MeetingSession.round == round_filter)
        if bot_status:
            stmt = stmt.where(MeetingSession.bot_status == bot_status)
        rows = (await session.execute(stmt)).all()
        items: list[MeetingListItem] = []
        for m, cand, role in rows:
            items.append(
                MeetingListItem(
                    meeting_session_id=m.id,
                    application_id=m.application_id,
                    candidate_name=cand.name,
                    role_title=role.title if role is not None else None,
                    round=m.round,
                    bot_status=m.bot_status,
                    overall_score=m.overall_score,
                    verdict=m.verdict,
                    scheduled_at=m.scheduled_at,
                    started_at=m.started_at,
                    duration_sec=m.duration_sec,
                    transcript_url=await _signed(m.transcript_r2_key),
                    created_at=m.created_at,
                )
            )
        return items
