"""CEO dashboard: read-only journey view for candidates in the CEO round.

Surfaces:
  * GET /dashboard/ceo/applications        -- list candidates queued for / in CEO round
  * GET /dashboard/ceo/applications/{id}   -- full journey: scores, transcripts, brief
  * POST /dashboard/ceo/applications/{id}/regenerate-brief -- on-demand re-aggregate

Auth: ``require_ceo`` (admin or ceo role).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from src.activities.v1_ceo_brief import generate_ceo_brief
from src.api.auth import require_ceo
from src.services.queue import enqueue
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
from src.models.v1 import PipelineStage
from src.services.file_storage import presigned_get_url

router = APIRouter(prefix="/dashboard/ceo", tags=["ceo-dashboard"])

_CEO_RELEVANT_STAGES = {
    PipelineStage.TECHNICAL_EVALUATED.value,
    PipelineStage.TECHNICAL_PENDING_APPROVAL.value,
    PipelineStage.CEO_MEETING_SCHEDULED.value,
    PipelineStage.CEO_MEETING_IN_PROGRESS.value,
    PipelineStage.CEO_MEETING_COMPLETED.value,
    PipelineStage.CEO_PENDING_APPROVAL.value,
}


class CEOListItem(BaseModel):
    application_id: UUID
    candidate_id: UUID
    candidate_name: str | None
    role_title: str | None
    current_stage: str
    fit_score: int | None
    has_brief: bool
    updated_at: datetime


@router.get("/applications", response_model=list[CEOListItem])
async def list_applications(
    _: Annotated[str, Depends(require_ceo)],
) -> list[CEOListItem]:
    async with session_scope() as session:
        stmt = (
            select(Application, Candidate, Role)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .outerjoin(Role, Role.id == Application.role_id)
            .where(Application.current_stage.in_(_CEO_RELEVANT_STAGES))
            .order_by(Application.updated_at.desc())
        )
        rows = (await session.execute(stmt)).all()
        return [
            CEOListItem(
                application_id=app.id,
                candidate_id=cand.id,
                candidate_name=cand.name,
                role_title=role.title if role is not None else None,
                current_stage=app.current_stage,
                fit_score=app.fit_score,
                has_brief=bool(app.journey_report),
                updated_at=app.updated_at,
            )
            for app, cand, role in rows
        ]


class VoiceSummary(BaseModel):
    voice_call_id: UUID
    status: str
    overall_score: int | None
    verdict: str | None
    duration_sec: float | None
    recording_url: str | None
    transcript_url: str | None
    transcript_text: str | None
    answers: list[dict[str, Any]] | None
    scheduled_at: datetime | None
    callback_at: datetime | None
    callback_reason: str | None
    attempt_no: int
    error: str | None
    candidate_response: str | None
    next_action: str | None


class AssessmentSummary(BaseModel):
    assessment_id: UUID
    provider: str
    kind: str | None
    status: str
    fit_band: str | None
    normalized_score: float | None
    percentile: float | None


class MeetingSummary(BaseModel):
    meeting_session_id: UUID
    round: str
    bot_status: str
    overall_score: int | None
    technical_score: int | None
    communication_score: int | None
    confidence_score: int | None
    verdict: str | None
    transcript_url: str | None
    recording_url: str | None
    summary: str | None
    emotion_timeline: list[dict[str, Any]] | None


class CEODetail(BaseModel):
    application_id: UUID
    candidate_name: str | None
    candidate_email: str | None
    role_title: str | None
    current_stage: str
    fit_score: int | None
    fit_tier: str | None
    screening_evaluation: dict[str, Any] | None
    voice_calls: list[VoiceSummary]
    assessments: list[AssessmentSummary]
    meetings: list[MeetingSummary]
    journey_report_markdown: str | None


def _compute_next_action(v: VoiceCall) -> str | None:
    """Human-readable next action for the phone-screen card."""
    status = (v.status or "").lower()
    tz = ZoneInfo(get_settings().voice_call_window_tz or "Asia/Kolkata")
    if status == "callback_requested" and v.callback_at:
        when = v.callback_at.astimezone(tz).strftime("%a %d %b, %I:%M %p %Z")
        return f"Auto re-dial scheduled for {when} (attempt {v.attempt_no + 1})"
    if status == "pending":
        if v.scheduled_at:
            when = v.scheduled_at.astimezone(tz).strftime("%a %d %b, %I:%M %p %Z")
            return f"Scheduled to dial at {when}"
        return "Queued -- waiting for dispatcher"
    if status == "dialing":
        return "Dialing candidate now"
    if status == "in_progress":
        return "Call in progress"
    if status == "no_answer":
        if v.callback_at:
            when = v.callback_at.astimezone(tz).strftime("%a %d %b, %I:%M %p %Z")
            return f"No answer — retry #{v.attempt_no + 1} at {when}"
        return f"No answer on attempt {v.attempt_no}; retry queued"
    if status == "failed":
        if v.callback_at:
            when = v.callback_at.astimezone(tz).strftime("%a %d %b, %I:%M %p %Z")
            return f"Call dropped — retry #{v.attempt_no + 1} at {when}"
        return v.error or "Call failed"
    if status == "completed":
        if v.verdict:
            return f"Evaluated -- verdict: {v.verdict}"
        return "Awaiting LLM evaluation"
    if status == "declined":
        return "Candidate declined the screen"
    return None


async def _maybe_signed(key: str | None) -> str | None:
    if not key:
        return None
    bucket = get_settings().r2_bucket_resumes
    return await presigned_get_url(bucket, key, ttl_seconds=3600)


async def _maybe_text(key: str | None, *, max_bytes: int = 40_000) -> str | None:
    """Fetch a text blob from R2 and return its decoded contents.

    Used to inline voice-call transcripts into the candidate page so HR
    doesn't need to hop to a presigned URL.
    """
    if not key:
        return None
    try:
        from src.services.file_storage import download

        bucket = get_settings().r2_bucket_resumes
        raw = await download(bucket, key)
        if len(raw) > max_bytes:
            raw = raw[:max_bytes] + b"\n\n[truncated]"
        return raw.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None


@router.get("/applications/{application_id}", response_model=CEODetail)
async def get_application(
    application_id: UUID,
    _: Annotated[str, Depends(require_ceo)],
) -> CEODetail:
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise HTTPException(404, "application not found")
        candidate = await session.get(Candidate, app.candidate_id)
        role = await session.get(Role, app.role_id) if app.role_id else None

        voice_rows = (
            await session.execute(
                select(VoiceCall)
                .where(VoiceCall.application_id == application_id)
                .order_by(VoiceCall.created_at.desc())
            )
        ).scalars().all()
        assessment_rows = (
            await session.execute(
                select(AssessmentResult)
                .where(AssessmentResult.application_id == application_id)
                .order_by(AssessmentResult.created_at.asc())
            )
        ).scalars().all()
        meeting_rows = (
            await session.execute(
                select(MeetingSession)
                .where(MeetingSession.application_id == application_id)
                .order_by(MeetingSession.created_at.asc())
            )
        ).scalars().all()

        voice_summaries: list[VoiceSummary] = []
        for v in voice_rows:
            candidate_response: str | None = None
            if isinstance(v.answers, list) and v.answers:
                last_ans = v.answers[-1]
                if isinstance(last_ans, dict):
                    txt = last_ans.get("answer_transcript")
                    if isinstance(txt, str) and txt.strip():
                        candidate_response = txt.strip()[:400]
            if candidate_response is None and v.callback_reason:
                candidate_response = v.callback_reason

            next_action = _compute_next_action(v)

            voice_summaries.append(
                VoiceSummary(
                    voice_call_id=v.id,
                    status=v.status,
                    overall_score=v.overall_score,
                    verdict=v.verdict,
                    duration_sec=v.duration_sec,
                    recording_url=await _maybe_signed(v.recording_r2_key),
                    transcript_url=await _maybe_signed(v.transcript_r2_key),
                    transcript_text=await _maybe_text(v.transcript_r2_key),
                    answers=v.answers if isinstance(v.answers, list) else None,
                    scheduled_at=v.scheduled_at,
                    callback_at=v.callback_at,
                    callback_reason=v.callback_reason,
                    attempt_no=v.attempt_no,
                    error=v.error,
                    candidate_response=candidate_response,
                    next_action=next_action,
                )
            )

        assessment_summaries = [
            AssessmentSummary(
                assessment_id=a.id,
                provider=a.provider,
                kind=a.assessment_kind,
                status=a.status,
                fit_band=a.fit_band,
                normalized_score=float(a.normalized_score)
                if a.normalized_score is not None
                else None,
                percentile=float(a.percentile) if a.percentile is not None else None,
            )
            for a in assessment_rows
        ]

        meeting_summaries: list[MeetingSummary] = []
        for m in meeting_rows:
            meeting_summaries.append(
                MeetingSummary(
                    meeting_session_id=m.id,
                    round=m.round,
                    bot_status=m.bot_status,
                    overall_score=m.overall_score,
                    technical_score=m.technical_score,
                    communication_score=m.communication_score,
                    confidence_score=m.confidence_score,
                    verdict=m.verdict,
                    transcript_url=await _maybe_signed(m.transcript_r2_key),
                    recording_url=await _maybe_signed(m.recording_r2_key),
                    summary=m.llm_report,
                    emotion_timeline=(
                        m.candidate_emotion_timeline
                        if isinstance(m.candidate_emotion_timeline, list)
                        else None
                    ),
                )
            )

        return CEODetail(
            application_id=app.id,
            candidate_name=candidate.name if candidate else None,
            candidate_email=candidate.email if candidate else None,
            role_title=role.title if role else None,
            current_stage=app.current_stage,
            fit_score=app.fit_score,
            fit_tier=app.fit_tier,
            screening_evaluation=app.screening_evaluation,
            voice_calls=voice_summaries,
            assessments=assessment_summaries,
            meetings=meeting_summaries,
            journey_report_markdown=app.journey_report,
        )


@router.post("/applications/{application_id}/regenerate-brief")
async def regenerate_brief(
    application_id: UUID,
    background: BackgroundTasks,
    _: Annotated[str, Depends(require_ceo)],
) -> dict[str, Any]:
    queued = await enqueue("generate_ceo_brief", str(application_id))
    if not queued:
        background.add_task(generate_ceo_brief, application_id=application_id)
    return {"ok": True}
