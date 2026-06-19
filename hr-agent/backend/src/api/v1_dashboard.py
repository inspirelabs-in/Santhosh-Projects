"""V1 dashboard endpoints: stage counts + candidate list + candidate detail.

Thin read-side API matched to the V1 pipeline schema. Uses the dashboard-key
auth gate so the same Retool/Next.js session flows through.
"""

from __future__ import annotations

from datetime import datetime, timedelta, UTC
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from src.api.auth import require_recruiter, require_viewer
from src.config import get_settings
from src.db.base import Application, AuditLog, Candidate, CandidateProfileRow, Interview, MeetingSession, Role, VoiceCall
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.v1_application import set_stage
from src.models.v1 import PipelineStage
from src.services.file_storage import presigned_get_url

_v1_settings = get_settings()

router = APIRouter(prefix="/dashboard/v1", tags=["v1"])


class StageCounts(BaseModel):
    counts: dict[str, int]
    total: int
    needs_hr_review: int


@router.get("/stages", response_model=StageCounts)
async def stage_counts(_: Annotated[str, Depends(require_viewer)]) -> StageCounts:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Application.current_stage, func.count())
                .group_by(Application.current_stage)
            )
        ).all()
        counts = {stage: int(c) for stage, c in rows}
        return StageCounts(
            counts=counts,
            total=sum(counts.values()),
            needs_hr_review=counts.get(PipelineStage.NEEDS_HR_REVIEW.value, 0),
        )


class AnalyticsOverview(BaseModel):
    total_candidates: int
    total_roles: int
    active_roles: int
    stage_counts: dict[str, int]
    recent_per_day: dict[str, int]
    source_counts: dict[str, int]


@router.get("/analytics/overview", response_model=AnalyticsOverview)
async def analytics_overview(
    _: Annotated[str, Depends(require_viewer)],
) -> AnalyticsOverview:
    cutoff = datetime.now(tz=UTC) - timedelta(days=30)

    async with session_scope() as session:
        total_candidates = int(
            await session.scalar(select(func.count(Candidate.id))) or 0
        )
        total_roles = int(
            await session.scalar(select(func.count(Role.id))) or 0
        )
        active_roles = int(
            await session.scalar(
                select(func.count(Role.id)).where(Role.status == "open")
            ) or 0
        )

        stage_rows = (
            await session.execute(
                select(Application.current_stage, func.count())
                .group_by(Application.current_stage)
            )
        ).all()

        _STAGE_GROUP = {
            "applied": "intake",
            "screening_sent": "screening",
            "screening_submitted": "screening",
            "screening_evaluated": "screening",
            "needs_hr_review": "screening",
            "voice_screen_scheduled": "voice_screen",
            "voice_screen_in_progress": "voice_screen",
            "voice_screen_callback_requested": "voice_screen",
            "voice_screen_completed": "voice_screen",
            "voice_screen_evaluated": "voice_screen",
            "assignment_sent": "assignment",
            "assignment_submitted": "assignment",
            "report_ready": "assignment",
            "assessment_invited": "assignment",
            "assessment_sent": "assignment",
            "assessment_completed": "assignment",
            "assessment_pending_review": "assignment",
            "assessment_evaluated": "assignment",
            "technical_meeting_scheduled": "tech_interview",
            "technical_meeting_in_progress": "tech_interview",
            "technical_meeting_completed": "tech_interview",
            "technical_evaluated": "tech_interview",
            "technical_pending_approval": "tech_interview",
            "ceo_meeting_scheduled": "ceo_interview",
            "ceo_meeting_in_progress": "ceo_interview",
            "ceo_meeting_completed": "ceo_interview",
            "ceo_pending_approval": "ceo_interview",
            "hr_meeting_scheduled": "hr_review",
            "hr_meeting_in_progress": "hr_review",
            "hr_meeting_completed": "hr_review",
            "hr_evaluated": "hr_review",
            "hired": "hired",
            "rejected": "rejected",
        }
        stage_counts: dict[str, int] = {}
        for stage, c in stage_rows:
            group = _STAGE_GROUP.get(stage, stage)
            stage_counts[group] = stage_counts.get(group, 0) + int(c)

        daily_rows = (
            await session.execute(
                select(
                    func.date(Application.created_at).label("day"),
                    func.count().label("cnt"),
                )
                .where(Application.created_at >= cutoff)
                .group_by("day")
                .order_by("day")
            )
        ).all()
        recent_per_day = {str(day): int(cnt) for day, cnt in daily_rows}

        source_rows = (
            await session.execute(
                select(
                    func.coalesce(Candidate.source_channel, "unknown"),
                    func.count(Application.id),
                )
                .join(Candidate, Candidate.id == Application.candidate_id)
                .group_by(Candidate.source_channel)
            )
        ).all()
        source_counts = {str(src): int(cnt) for src, cnt in source_rows}

    return AnalyticsOverview(
        total_candidates=total_candidates,
        total_roles=total_roles,
        active_roles=active_roles,
        stage_counts=stage_counts,
        recent_per_day=recent_per_day,
        source_counts=source_counts,
    )


class CandidateListItem(BaseModel):
    application_id: UUID
    candidate_id: UUID
    name: str | None
    email: str | None
    role_title: str | None
    current_stage: str
    screening_score: int | None
    created_at: datetime
    updated_at: datetime


class CandidateListPage(BaseModel):
    items: list[CandidateListItem]
    total: int
    limit: int
    offset: int


@router.get("/candidates", response_model=CandidateListPage)
async def list_candidates(
    _: Annotated[str, Depends(require_viewer)],
    stage: str | None = Query(None),
    role_id: UUID | None = Query(None),
    q: str | None = Query(None, description="fuzzy name/email search"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> CandidateListPage:
    filters = []
    if stage:
        filters.append(Application.current_stage == stage)
    if role_id:
        filters.append(Application.role_id == role_id)
    if q:
        pattern = f"%{q.lower()}%"
        filters.append(
            func.lower(Candidate.name).like(pattern)
            | func.lower(Candidate.email).like(pattern)
        )

    async with session_scope() as session:
        count_stmt = (
            select(func.count())
            .select_from(Application)
            .join(Candidate, Candidate.id == Application.candidate_id)
        )
        for f in filters:
            count_stmt = count_stmt.where(f)
        total = int((await session.execute(count_stmt)).scalar() or 0)

        stmt = (
            select(Application, Candidate, Role)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .join(Role, Role.id == Application.role_id, isouter=True)
            .order_by(desc(Application.updated_at))
            .offset(offset)
            .limit(limit)
        )
        for f in filters:
            stmt = stmt.where(f)
        items: list[CandidateListItem] = []
        for app, cand, role in (await session.execute(stmt)).all():
            items.append(
                CandidateListItem(
                    application_id=app.id,
                    candidate_id=cand.id,
                    name=cand.name,
                    email=cand.email,
                    role_title=role.title if role else None,
                    current_stage=app.current_stage,
                    screening_score=app.screening_score,
                    created_at=app.created_at,
                    updated_at=app.updated_at,
                )
            )
        return CandidateListPage(items=items, total=total, limit=limit, offset=offset)


class CandidateDetail(BaseModel):
    application_id: UUID
    candidate: dict[str, Any]
    role: dict[str, Any] | None
    current_stage: str
    screening_questions: Any
    screening_evaluation: Any
    assignment_submission: Any
    journey_report: str | None
    profile: dict[str, Any] | None
    audit: list[dict[str, Any]]
    resume_download_url: str | None = None
    resume_filename: str | None = None
    application_mail: dict[str, Any] | None = None
    admin_review: dict[str, Any] | None = None
    meeting_reports: dict[str, Any] | None = None
    fit_score: int | None = None
    fit_tier: str | None = None
    fit_breakdown: dict[str, Any] | None = None
    voice_evaluation: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


@router.get("/candidates/{application_id}", response_model=CandidateDetail)
async def candidate_detail(
    application_id: UUID,
    _: Annotated[str, Depends(require_viewer)],
) -> CandidateDetail:
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise HTTPException(status_code=404, detail="application_not_found")
        cand = await session.get(Candidate, app.candidate_id)
        role = await session.get(Role, app.role_id) if app.role_id else None
        profile_row = (
            await session.scalars(
                select(CandidateProfileRow)
                .where(CandidateProfileRow.candidate_id == app.candidate_id)
                .order_by(desc(CandidateProfileRow.created_at))
                .limit(1)
            )
        ).first()
        audit_rows = (
            await session.scalars(
                select(AuditLog)
                .where(AuditLog.application_id == application_id)
                .order_by(AuditLog.created_at.asc())
                .limit(200)
            )
        ).all()

        resume_url: str | None = None
        resume_filename: str | None = None
        if profile_row and profile_row.raw_resume_r2_key:
            try:
                resume_url = await presigned_get_url(
                    _v1_settings.r2_bucket_resumes,
                    profile_row.raw_resume_r2_key,
                    ttl_seconds=3600,
                )
                raw_name = profile_row.raw_resume_r2_key.split("/")[-1]
                import re as _re
                resume_filename = _re.sub(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_", "", raw_name)
            except Exception:  # noqa: BLE001
                pass

        # Pull intake-mail metadata from the intake_completed audit row.
        application_mail: dict[str, Any] | None = None
        for a in audit_rows:
            if a.action == "intake_completed" and a.details:
                d = a.details
                if d.get("source") == "email" or d.get("body_preview"):
                    application_mail = {
                        "subject": d.get("subject"),
                        "body_preview": d.get("body_preview"),
                        "body_links": d.get("body_links") or [],
                        "forwarder_email": d.get("forwarder_email"),
                        "forwarder_name": d.get("forwarder_name"),
                        "received_at": d.get("received_at"),
                        "source": d.get("source"),
                    }
                    break

        meeting_rows = (
            await session.scalars(
                select(MeetingSession)
                .where(MeetingSession.application_id == application_id)
                .order_by(MeetingSession.created_at.desc())
            )
        ).all()
        meeting_reports: dict[str, Any] = {}
        for m in meeting_rows:
            if m.round and m.report and m.round not in meeting_reports:
                meeting_reports[m.round] = m.report

        # Fit-score breakdown comes from the most recent fit_scored audit row.
        fit_breakdown: dict[str, Any] | None = None
        for a in reversed(audit_rows):
            if a.action == "fit_scored" and isinstance(a.details, dict):
                d = a.details
                fit_breakdown = {
                    "overall_score": d.get("overall_score"),
                    "dimensions": d.get("dimensions"),
                    "red_flags": d.get("red_flags"),
                    "green_flags": d.get("green_flags"),
                    "summary": d.get("summary"),
                    "knock_outs": d.get("knock_outs"),
                    "deterministic_tier": d.get("deterministic_tier"),
                    "weights_used": d.get("weights_used"),
                    "pending_verification": d.get("pending_verification", []),
                    "scoring_pass": d.get("scoring_pass", "resume_only"),
                    "scored_at": a.created_at.isoformat(),
                }
                break

        # Voice call evaluation + transcript
        voice_row = (
            await session.scalars(
                select(VoiceCall)
                .where(
                    VoiceCall.application_id == application_id,
                    VoiceCall.evaluation.isnot(None),
                )
                .order_by(desc(VoiceCall.ended_at))
                .limit(1)
            )
        ).first()
        voice_evaluation: dict[str, Any] | None = None
        if voice_row and voice_row.evaluation:
            ev = voice_row.evaluation
            bucket = get_settings().r2_bucket_resumes
            voice_evaluation = {
                "verdict": ev.get("verdict") or voice_row.verdict,
                "overall_score": ev.get("overall_score") or voice_row.overall_score,
                "verdict_rationale": ev.get("verdict_rationale"),
                "red_flags": ev.get("red_flags", []),
                "strengths": ev.get("strengths", []),
                "per_question": ev.get("per_question", []),
                "extracted_facts": ev.get("extracted_facts"),
                "transcript": voice_row.answers,
                "questions": voice_row.questions,
                "duration_sec": voice_row.duration_sec,
                "started_at": voice_row.started_at.isoformat() if voice_row.started_at else None,
                "ended_at": voice_row.ended_at.isoformat() if voice_row.ended_at else None,
                "recording_url": await presigned_get_url(bucket, voice_row.recording_r2_key, ttl_seconds=3600) if voice_row.recording_r2_key else None,
                "transcript_url": await presigned_get_url(bucket, voice_row.transcript_r2_key, ttl_seconds=3600) if voice_row.transcript_r2_key else None,
            }

        return CandidateDetail(
            application_id=application_id,
            candidate={
                "id": str(cand.id),
                "name": cand.name,
                "email": cand.email,
                "phone": cand.phone,
                "source_channel": cand.source_channel,
            },
            role=(
                {
                    "id": str(role.id),
                    "title": role.title,
                    "assignment_brief": role.assignment_brief,
                    "assignment_deadline_days": role.assignment_deadline_days,
                }
                if role
                else None
            ),
            current_stage=app.current_stage,
            screening_questions=app.screening_questions,
            screening_evaluation=app.screening_evaluation,
            assignment_submission=app.assignment_submission,
            journey_report=app.journey_report,
            profile=profile_row.parsed_data if profile_row else None,
            audit=[
                {
                    "id": a.id,
                    "action": a.action,
                    "actor": a.actor,
                    "details": a.details,
                    "created_at": a.created_at.isoformat(),
                }
                for a in audit_rows
            ],
            resume_download_url=resume_url,
            resume_filename=resume_filename,
            application_mail=application_mail,
            admin_review=app.admin_review,
            meeting_reports=meeting_reports or None,
            fit_score=app.fit_score,
            fit_tier=app.fit_tier,
            fit_breakdown=fit_breakdown,
            voice_evaluation=voice_evaluation,
            created_at=app.created_at,
            updated_at=app.updated_at,
        )


class MentionCandidate(BaseModel):
    application_id: UUID
    name: str | None = None
    email: str | None = None


@router.get("/candidates-mention", response_model=list[MentionCandidate])
async def candidates_for_mention(
    _: Annotated[str, Depends(require_viewer)],
    q: str | None = Query(None, description="name/email substring to match"),
    limit: int = Query(8, ge=1, le=25),
) -> list[MentionCandidate]:
    """Lightweight candidate lookup powering the @-mention autocomplete in
    Pulse chat. Returns the most recently active applications with an email.
    """
    async with session_scope() as session:
        stmt = select(Application.id, Candidate.name, Candidate.email).join(
            Candidate, Candidate.id == Application.candidate_id
        )
        if q:
            pattern = f"%{q.lower()}%"
            stmt = stmt.where(
                func.lower(Candidate.name).like(pattern)
                | func.lower(Candidate.email).like(pattern)
            )
        stmt = (
            stmt.where(Candidate.email.isnot(None))
            .order_by(desc(Application.updated_at))
            .limit(limit)
        )
        rows = (await session.execute(stmt)).all()
    return [
        MentionCandidate(application_id=app_id, name=name, email=email)
        for app_id, name, email in rows
    ]


class NotificationItem(BaseModel):
    id: int
    action: str
    actor: str
    application_id: UUID | None
    candidate_id: UUID | None
    candidate_name: str | None = None
    role_title: str | None = None
    details: dict[str, Any] | None = None
    created_at: datetime


NOTIFICATION_ACTIONS = {
    "intake_completed": "New application",
    "screening_sent": "Screening sent",
    "screening_submitted": "Screening submitted",
    "screening_evaluated": "Screening evaluated",
    "assignment_sent": "Assignment sent",
    "assignment_submitted": "Assignment submitted",
    "assignment_parsed": "Assignment reviewed",
    "journey_report_generated": "Journey report ready",
    "parked_no_role": "Parked — no role matched",
    "hr_stage_override": "HR override",
    "pipeline_error": "Pipeline error",
    "meeting_reschedule_requested": "Reschedule requested",
    "meeting_scheduled_via_chat": "Meeting scheduled",
    "meeting_rescheduled_via_chat": "Meeting rescheduled",
    "meeting_technical_awaiting_chat_scheduling": "Ready to schedule (technical)",
    "meeting_ceo_awaiting_chat_scheduling": "Ready to schedule (CEO)",
    "meeting_hr_awaiting_chat_scheduling": "Ready to schedule (HR)",
}


@router.get("/notifications", response_model=list[NotificationItem])
async def notifications(
    _: Annotated[str, Depends(require_viewer)],
    limit: int = Query(30, le=100),
) -> list[NotificationItem]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(AuditLog, Candidate, Role)
                .outerjoin(Candidate, Candidate.id == AuditLog.candidate_id)
                .outerjoin(
                    Application, Application.id == AuditLog.application_id
                )
                .outerjoin(Role, Role.id == Application.role_id)
                .where(AuditLog.action.in_(list(NOTIFICATION_ACTIONS.keys())))
                .order_by(desc(AuditLog.created_at))
                .limit(limit)
            )
        ).all()
        out: list[NotificationItem] = []
        for audit, cand, role in rows:
            out.append(
                NotificationItem(
                    id=audit.id,
                    action=audit.action,
                    actor=audit.actor,
                    application_id=audit.application_id,
                    candidate_id=audit.candidate_id,
                    candidate_name=cand.name if cand else None,
                    role_title=role.title if role else None,
                    details=audit.details,
                    created_at=audit.created_at,
                )
            )
        return out


@router.post("/dev/reset", summary="DEV-ONLY: wipe all candidates, applications, roles, audit")
async def dev_reset(actor: Annotated[str, Depends(require_recruiter)]) -> dict:
    from src.config import get_settings as _gs
    s = _gs()
    if s.app_env == "production":
        raise HTTPException(status_code=403, detail="not_allowed_in_production")
    from sqlalchemy import text
    async with session_scope() as session:
        # Order matters for FK cascade; TRUNCATE ... CASCADE handles children.
        await session.execute(
            text(
                "TRUNCATE TABLE "
                "consent_artifacts, candidate_profiles, screening_responses, "
                "screening_answers, interviews, processed_messages, "
                "voice_calls, voice_campaigns, assessment_results, "
                "meeting_sessions, email_sends, assignments, "
                "conversations, messages, "
                "recruiter_messages, recruiter_conversations, recruiter_memory, "
                "supervisor_events, supervisor_actions, "
                "evidence_records, decision_records, pipeline_alerts, "
                "applications, candidates, roles, webhook_events "
                "RESTART IDENTITY CASCADE"
            )
        )
        # audit_log is append-only by trigger; bypass with DELETE via superuser fn.
        try:
            await session.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_immutable"))
            await session.execute(text("TRUNCATE TABLE audit_log RESTART IDENTITY"))
            await session.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_immutable"))
        except Exception:  # noqa: BLE001
            pass
    # Flush recruiter-chat Redis keys (inbox, cancel, pubsub)
    try:
        from src.db.connection import get_redis
        r = get_redis()
        keys = await r.keys("recruiter-chat:*")
        if keys:
            await r.delete(*keys)
    except Exception:  # noqa: BLE001
        pass
    return {"status": "ok", "wiped_by": actor}


class ReassignRoleBody(BaseModel):
    role_id: UUID
    note: str | None = None


@router.post("/candidates/{application_id}/role")
async def reassign_role(
    application_id: UUID,
    body: ReassignRoleBody,
    actor: Annotated[str, Depends(require_recruiter)],
) -> dict:
    """Reassign an application to a different role and restart the pipeline.

    Resets current_stage to `applied`, clears screening + assignment state,
    re-kicks `run_apply_to_screening` with the existing parsed resume.
    """
    from src.pipeline.v1 import run_apply_to_screening

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise HTTPException(status_code=404, detail="application_not_found")
        target_role = await session.get(Role, body.role_id)
        if target_role is None or target_role.status != "open":
            raise HTTPException(status_code=400, detail="role_not_open")

        old_role_id = app.role_id
        candidate_id = app.candidate_id

        # Reset pipeline-specific JSONB state for a clean re-run.
        app.role_id = body.role_id
        app.current_stage = PipelineStage.APPLIED.value
        app.screening_questions = None
        app.screening_evaluation = None
        app.screening_score = None
        app.assignment_submission = None
        app.journey_report = None

        # Find the latest resume key to reuse.
        profile_row = (
            await session.scalars(
                select(CandidateProfileRow)
                .where(CandidateProfileRow.candidate_id == candidate_id)
                .order_by(CandidateProfileRow.created_at.desc())
                .limit(1)
            )
        ).first()
        resume_key = profile_row.raw_resume_r2_key if profile_row else None
        resume_filename = None
        if resume_key:
            resume_filename = resume_key.split("/")[-1]

        await log_audit(
            session,
            action="role_reassigned",
            actor=actor,
            candidate_id=candidate_id,
            application_id=application_id,
            details={
                "old_role_id": str(old_role_id) if old_role_id else None,
                "new_role_id": str(body.role_id),
                "note": body.note,
            },
        )

    # Kick fresh pipeline in background.
    import asyncio as _a
    _a.create_task(
        run_apply_to_screening(
            application_id=application_id,
            candidate_id=candidate_id,
            role_id=body.role_id,
            resume_r2_key=resume_key,
            resume_filename=resume_filename,
        )
    )
    return {"status": "ok", "new_role_id": str(body.role_id)}


@router.post("/candidates/{application_id}/regenerate-report")
async def regenerate_report(
    application_id: UUID,
    actor: Annotated[str, Depends(require_recruiter)],
) -> dict:
    """Re-run generate_journey_report on an existing application.

    Uses whatever profile / screening / assignment state is already saved.
    Overwrites applications.journey_report.
    """
    import asyncio as _a
    from src.activities.v1_journey_report import generate_journey_report

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise HTTPException(status_code=404, detail="application_not_found")
        await log_audit(
            session,
            application_id=application_id,
            candidate_id=app.candidate_id,
            action="journey_report_requeued",
            actor=actor,
        )

    _a.create_task(generate_journey_report(application_id=application_id))
    return {"status": "queued"}


@router.post("/candidates/{application_id}/reevaluate-screening")
async def reevaluate_screening(
    application_id: UUID,
    actor: Annotated[str, Depends(require_recruiter)],
) -> dict:
    """Re-run screening evaluation against the stored submission answers.

    Useful after prompt edits or when the evaluator hallucinated on the first
    pass. Does NOT move the stage.
    """
    import asyncio as _a
    from src.models.v1 import ScreeningSubmission
    from src.pipeline.v1 import run_screening_evaluation

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise HTTPException(status_code=404, detail="application_not_found")
        sq = app.screening_questions or {}
        sub = sq.get("submission") if isinstance(sq, dict) else None
        if not sub:
            raise HTTPException(
                status_code=409, detail="no_screening_submission_to_evaluate"
            )
        submission = ScreeningSubmission.model_validate(sub)
        await log_audit(
            session,
            application_id=application_id,
            candidate_id=app.candidate_id,
            action="screening_eval_requeued",
            actor=actor,
        )

    _a.create_task(
        run_screening_evaluation(
            application_id=application_id, submission=submission
        )
    )
    return {"status": "queued"}


class RejectBody(BaseModel):
    category: str
    reason_template: str | None = None
    preview_only: bool = False


@router.post("/candidates/{application_id}/reject")
async def reject_candidate(
    application_id: UUID,
    body: RejectBody,
    actor: Annotated[str, Depends(require_recruiter)],
) -> dict:
    """Generate a rejection email via REJECTION_MESSAGE_V1, preview or send.

    If preview_only=true, returns the drafted body without sending or
    transitioning. Otherwise sends the email and moves stage to `rejected`.
    """
    from src.llm.client import get_llm_client
    from src.llm.prompts import REJECTION_MESSAGE_V1, REJECTION_MESSAGE_VERSION
    from src.channels.email import send_email
    from pydantic import BaseModel as _BM

    class _RejectOut(_BM):
        body: str
        category_used: str

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise HTTPException(status_code=404, detail="application_not_found")
        cand = await session.get(Candidate, app.candidate_id)
        role = await session.get(Role, app.role_id) if app.role_id else None

        prompt = REJECTION_MESSAGE_V1.format(
            name=(cand.name if cand else "") or "there",
            role_title=role.title if role else "the role",
            rejection_category=body.category,
            reason_template=body.reason_template or body.category,
        )
        client = get_llm_client()
        result = await client.complete(
            prompt=prompt,
            response_model=_RejectOut,
            trace_name="rejection_message",
            prompt_version=REJECTION_MESSAGE_VERSION,
            candidate_id=app.candidate_id,
            application_id=application_id,
            system='Return strict JSON {"body": "...", "category_used": "..."}.',
            max_tokens=500,
        )
        draft = result.parsed.body

        if body.preview_only:
            return {"status": "preview", "body": draft, "category": body.category}

        if cand and cand.email:
            role_title = role.title if role else "the role"
            body_html = "".join(
                f"<p>{line}</p>" for line in draft.split("\n\n") if line.strip()
            )
            await send_email(
                to=cand.email,
                template="rejection",
                variables={
                    "candidate_name": cand.name,
                    "role_title": role_title,
                    "body_html": body_html,
                    "application_id": str(application_id),
                },
                tags={"type": "rejection", "application_id": str(application_id)},
            )
        # Capture which stage the candidate was rejected from so the UI can
        # show "rejected at screening call" instead of pretending the
        # pipeline completed. Stash on admin_review JSONB to avoid a
        # migration; existing keys are preserved.
        prior_stage = app.current_stage
        merged_admin = dict(app.admin_review or {})
        merged_admin.update(
            {
                "rejected_from_stage": prior_stage,
                "rejected_category": body.category,
                "rejected_at": datetime.now(UTC).isoformat(),
                "rejected_by": actor,
            }
        )
        app.admin_review = merged_admin

        await set_stage(session, application_id, PipelineStage.REJECTED, force=True)
        await log_audit(
            session,
            application_id=application_id,
            candidate_id=app.candidate_id,
            action="candidate_rejected",
            actor=actor,
            details={
                "category": body.category,
                "email_sent": bool(cand and cand.email),
                "rejected_from_stage": prior_stage,
            },
        )

    return {"status": "rejected", "body": draft}


class BulkStageBody(BaseModel):
    application_ids: list[UUID]
    stage: str
    note: str | None = None


@router.post("/candidates-bulk/stage")
async def bulk_set_stage(
    body: BulkStageBody,
    background: BackgroundTasks,
    actor: Annotated[str, Depends(require_recruiter)],
) -> dict:
    """Force-set stage for many applications. Skips missing rows."""
    try:
        target = PipelineStage(body.stage)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_stage")
    ok: list[str] = []
    failed: list[dict[str, str]] = []
    async with session_scope() as session:
        for app_id in body.application_ids:
            app = await session.get(Application, app_id)
            if app is None:
                failed.append({"id": str(app_id), "error": "not_found"})
                continue
            try:
                await set_stage(session, app_id, target, force=True)
                await log_audit(
                    session,
                    application_id=app_id,
                    candidate_id=app.candidate_id,
                    action="hr_stage_override",
                    actor=actor,
                    details={
                        "new_stage": target.value,
                        "note": body.note,
                        "bulk": True,
                    },
                )
                ok.append(str(app_id))
            except Exception as e:  # noqa: BLE001
                failed.append({"id": str(app_id), "error": str(e)[:200]})

    for app_id_str in ok:
        _dispatch_stage_side_effects(background, UUID(app_id_str), target)
    return {"status": "done", "updated": ok, "failed": failed}


class BulkRejectBody(BaseModel):
    application_ids: list[UUID]
    category: str
    reason_template: str | None = None
    send_email: bool = True


@router.post("/candidates-bulk/reject")
async def bulk_reject(
    body: BulkRejectBody,
    actor: Annotated[str, Depends(require_recruiter)],
) -> dict:
    """Reject many candidates. LLM drafts a body per candidate (personalized)
    unless send_email=False (then just transition + audit)."""
    from src.llm.client import get_llm_client
    from src.llm.prompts import REJECTION_MESSAGE_V1, REJECTION_MESSAGE_VERSION
    from src.channels.email import send_email as _send
    from pydantic import BaseModel as _BM

    class _RejectOut(_BM):
        body: str
        category_used: str

    client = get_llm_client()
    ok: list[str] = []
    failed: list[dict[str, str]] = []

    for app_id in body.application_ids:
        try:
            async with session_scope() as session:
                app = await session.get(Application, app_id)
                if app is None:
                    failed.append({"id": str(app_id), "error": "not_found"})
                    continue
                cand = await session.get(Candidate, app.candidate_id)
                role = await session.get(Role, app.role_id) if app.role_id else None
                draft: str | None = None

                if body.send_email and cand and cand.email:
                    prompt = REJECTION_MESSAGE_V1.format(
                        name=(cand.name or "there"),
                        role_title=role.title if role else "the role",
                        rejection_category=body.category,
                        reason_template=body.reason_template or body.category,
                    )
                    result = await client.complete(
                        prompt=prompt,
                        response_model=_RejectOut,
                        trace_name="rejection_message",
                        prompt_version=REJECTION_MESSAGE_VERSION,
                        candidate_id=app.candidate_id,
                        application_id=app_id,
                        system='Return strict JSON {"body": "...", "category_used": "..."}.',
                        max_tokens=500,
                    )
                    draft = result.parsed.body
                    body_html = "".join(
                        f"<p>{line}</p>" for line in draft.split("\n\n") if line.strip()
                    )
                    await _send(
                        to=cand.email,
                        template="rejection",
                        variables={
                            "candidate_name": cand.name,
                            "role_title": role.title if role else "the role",
                            "body_html": body_html,
                            "application_id": str(app_id),
                        },
                        tags={
                            "type": "rejection",
                            "application_id": str(app_id),
                            "bulk": "true",
                        },
                    )

                await set_stage(session, app_id, PipelineStage.REJECTED, force=True)
                await log_audit(
                    session,
                    application_id=app_id,
                    candidate_id=app.candidate_id,
                    action="candidate_rejected",
                    actor=actor,
                    details={
                        "category": body.category,
                        "email_sent": bool(draft),
                        "bulk": True,
                    },
                )
                ok.append(str(app_id))
        except Exception as e:  # noqa: BLE001
            failed.append({"id": str(app_id), "error": str(e)[:200]})

    return {"status": "done", "rejected": ok, "failed": failed}


class CompareItem(BaseModel):
    application_id: UUID
    candidate: dict[str, Any]
    role_title: str | None
    current_stage: str
    screening_score: int | None
    verdict: str | None
    verdict_rationale: str | None
    logistics_values: dict[str, Any] | None
    logistics_check: dict[str, Any] | None
    strengths: list[str]
    red_flags: list[str]
    profile: dict[str, Any] | None
    assignment_summary: str | None
    assignment_highlights: list[str]
    assignment_concerns: list[str]
    per_question: list[dict[str, Any]]


@router.get("/candidates-compare", response_model=list[CompareItem])
async def compare_candidates(
    ids: Annotated[str, Query(description="Comma-separated application UUIDs, 2-4")],
    _: Annotated[str, Depends(require_viewer)],
) -> list[CompareItem]:
    try:
        uuids = [UUID(x.strip()) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_uuid_list")
    if not 2 <= len(uuids) <= 4:
        raise HTTPException(status_code=400, detail="pick_2_to_4_candidates")

    out: list[CompareItem] = []
    async with session_scope() as session:
        for app_id in uuids:
            app = await session.get(Application, app_id)
            if app is None:
                continue
            cand = await session.get(Candidate, app.candidate_id)
            role = await session.get(Role, app.role_id) if app.role_id else None
            profile_row = (
                await session.scalars(
                    select(CandidateProfileRow)
                    .where(CandidateProfileRow.candidate_id == app.candidate_id)
                    .order_by(desc(CandidateProfileRow.created_at))
                    .limit(1)
                )
            ).first()
            se = app.screening_evaluation or {}
            asub = app.assignment_submission or {}
            pr = (asub or {}).get("parse_result") or {}
            out.append(
                CompareItem(
                    application_id=app_id,
                    candidate={
                        "id": str(cand.id) if cand else None,
                        "name": cand.name if cand else None,
                        "email": cand.email if cand else None,
                    },
                    role_title=role.title if role else None,
                    current_stage=app.current_stage,
                    screening_score=app.screening_score,
                    verdict=se.get("verdict"),
                    verdict_rationale=se.get("verdict_rationale"),
                    logistics_values=se.get("logistics_values"),
                    logistics_check=se.get("logistics_check"),
                    strengths=se.get("strengths") or [],
                    red_flags=se.get("red_flags") or [],
                    profile=profile_row.parsed_data if profile_row else None,
                    assignment_summary=pr.get("summary"),
                    assignment_highlights=pr.get("highlights") or [],
                    assignment_concerns=pr.get("concerns") or [],
                    per_question=se.get("per_question") or [],
                )
            )
    return out


@router.get("/candidates-export.csv")
async def export_candidates_csv(
    _: Annotated[str, Depends(require_viewer)],
    stage: str | None = Query(None),
    role_id: UUID | None = Query(None),
) -> Any:
    """Export the filtered candidate list as CSV. Non-PII columns only beyond
    name/email (which HR already has access to)."""
    import csv
    import io

    from fastapi import Response

    async with session_scope() as session:
        stmt = (
            select(Application, Candidate, Role)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .join(Role, Role.id == Application.role_id, isouter=True)
            .order_by(desc(Application.updated_at))
        )
        if stage:
            stmt = stmt.where(Application.current_stage == stage)
        if role_id:
            stmt = stmt.where(Application.role_id == role_id)
        rows = (await session.execute(stmt)).all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "application_id", "name", "email", "phone", "role",
        "stage", "screening_score", "verdict",
        "current_ctc_lpa", "expected_ctc_lpa", "notice_days",
        "location", "created_at", "updated_at",
    ])
    for app, cand, role in rows:
        se = app.screening_evaluation or {}
        lv = (se.get("logistics_values") or {})
        w.writerow([
            str(app.id),
            cand.name or "",
            cand.email or "",
            cand.phone or "",
            role.title if role else "",
            app.current_stage,
            app.screening_score if app.screening_score is not None else "",
            se.get("verdict") or "",
            lv.get("current_ctc_lpa") or "",
            lv.get("expected_ctc_lpa") or "",
            lv.get("notice_period_days") or "",
            lv.get("current_location") or "",
            app.created_at.isoformat() if app.created_at else "",
            app.updated_at.isoformat() if app.updated_at else "",
        ])

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="candidates.csv"',
        },
    )


import logging as _logging

_stage_log = _logging.getLogger(__name__)


async def _send_rejection_email(
    cand: Candidate | None,
    role: Role | None,
    application_id: UUID,
    category: str,
    note: str | None,
) -> None:
    """Best-effort rejection email. Shared by tech/ceo/hr decision endpoints."""
    if not cand or not cand.email:
        return
    try:
        from src.channels.email import send_email

        role_title = role.title if role else "the role"
        body_html = (
            f"<p>Thank you for your interest in the <strong>{role_title}</strong> "
            f"position at GrabOn and for the time you invested in our process.</p>"
            f"<p>After careful consideration, we've decided not to move forward "
            f"with your application at this stage.</p>"
            f"<p>We encourage you to apply for future openings that match your "
            f"profile. We wish you the very best in your career.</p>"
        )
        await send_email(
            to=cand.email,
            template="rejection",
            variables={
                "candidate_name": cand.name,
                "role_title": role_title,
                "body_html": body_html,
                "application_id": str(application_id),
            },
            tags={"type": "rejection", "application_id": str(application_id)},
        )
    except Exception:  # noqa: BLE001
        _stage_log.exception(
            "rejection email failed for application %s", application_id
        )


_STAGES_WITH_SIDE_EFFECTS = {
    PipelineStage.SCREENING_EVALUATED,
    PipelineStage.VOICE_SCREEN_EVALUATED,
    PipelineStage.ASSESSMENT_EVALUATED,
    PipelineStage.TECHNICAL_EVALUATED,
    PipelineStage.CEO_MEETING_COMPLETED,
    PipelineStage.ASSIGNMENT_SENT,
}


def _dispatch_stage_side_effects(
    background: BackgroundTasks,
    application_id: UUID,
    target: PipelineStage,
) -> None:
    """Fire pipeline side effects when HR manually sets a stage.

    For stages that are intermediate decision points (e.g. voice_screen_evaluated),
    auto_progress will pick the right next action (dispatch assessment, schedule
    meeting, etc.).

    For ASSIGNMENT_SENT specifically, we directly dispatch the assignment email
    since auto_progress expects to arrive at that stage itself.
    """
    from src.services.auto_progress import auto_progress

    if target == PipelineStage.ASSIGNMENT_SENT:
        async def _send_assignment():
            from src.activities.v1_dispatch_assessment import dispatch_assessment
            try:
                await dispatch_assessment(application_id=application_id)
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "side-effect dispatch_assessment failed for %s", application_id
                )
        background.add_task(_send_assignment)
    elif target in _STAGES_WITH_SIDE_EFFECTS:
        background.add_task(auto_progress, application_id=application_id)


class StageActionBody(BaseModel):
    stage: str
    note: str | None = None


@router.post("/candidates/{application_id}/stage")
async def set_stage_action(
    application_id: UUID,
    body: StageActionBody,
    background: BackgroundTasks,
    actor: Annotated[str, Depends(require_recruiter)],
) -> dict:
    try:
        target = PipelineStage(body.stage)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_stage")
    async with session_scope() as session:
        await set_stage(session, application_id, target, force=True)
        await log_audit(
            session,
            application_id=application_id,
            action="hr_stage_override",
            actor=actor,
            details={"new_stage": target.value, "note": body.note},
        )

    _dispatch_stage_side_effects(background, application_id, target)
    return {"status": "ok", "stage": target.value}


class TriggerPanelAvailabilityBody(BaseModel):
    round: str  # "technical" | "ceo" | "hr"


@router.post("/candidates/{application_id}/trigger-panel-availability")
async def trigger_panel_availability(
    application_id: UUID,
    body: TriggerPanelAvailabilityBody,
    actor: Annotated[str, Depends(require_recruiter)],
) -> dict:
    """Manually trigger panel availability emails for a given interview round.

    Sends confirmation-link emails to all panel members for the specified round.
    Used to test mail delivery or re-send when the auto flow didn't fire.
    """
    if not body.round or not body.round.strip():
        raise HTTPException(status_code=400, detail="round is required")

    from src.services.panel_availability import initiate_panel_availability

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise HTTPException(status_code=404, detail="application_not_found")
        await log_audit(
            session,
            application_id=application_id,
            action="panel_availability_manual_trigger",
            actor=actor,
            details={"round": body.round},
        )

    try:
        meeting_session_id = await initiate_panel_availability(
            application_id=application_id,
            round=body.round,
        )
        return {
            "status": "ok",
            "meeting_session_id": str(meeting_session_id),
            "round": body.round,
            "message": f"Panel availability emails sent for {body.round} round",
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send panel emails: {exc!s:.300}",
        ) from exc


class TechDecisionBody(BaseModel):
    action: str  # "advance_to_ceo" | "reject"
    note: str | None = None


@router.post("/candidates/{application_id}/tech-decision")
async def tech_decision(
    application_id: UUID,
    body: TechDecisionBody,
    actor: Annotated[str, Depends(require_recruiter)],
) -> dict:
    """Technical panel decision after report_ready. Advances to CEO review or rejects."""
    from src.channels.email import send_email
    from src.db.base import PanelMember

    if body.action not in ("advance_to_ceo", "reject"):
        raise HTTPException(status_code=400, detail="invalid_action")

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise HTTPException(status_code=404, detail="application_not_found")
        cand = await session.get(Candidate, app.candidate_id)
        role = await session.get(Role, app.role_id) if app.role_id else None

        if body.action == "reject":
            prior_stage = app.current_stage
            merged_admin = dict(app.admin_review or {})
            merged_admin.update(
                {
                    "rejected_from_stage": prior_stage,
                    "rejected_category": "tech_panel_reject",
                    "rejected_at": datetime.now(UTC).isoformat(),
                    "rejected_by": actor,
                    "rejected_note": body.note,
                }
            )
            app.admin_review = merged_admin
            await set_stage(
                session, application_id, PipelineStage.REJECTED, force=True
            )
            await log_audit(
                session,
                application_id=application_id,
                candidate_id=app.candidate_id,
                action="tech_decision_reject",
                actor=actor,
                details={"note": body.note, "rejected_from_stage": prior_stage},
            )

            await _send_rejection_email(
                cand, role, application_id, "tech_panel_reject", body.note,
            )
            return {"status": "rejected"}

        # advance_to_ceo
        ceo_members = (
            await session.execute(
                select(PanelMember).where(
                    PanelMember.role_type.in_(["ceo"]),
                    PanelMember.is_active.is_(True),
                )
            )
        ).scalars().all()

        review_url = (
            f"{_v1_settings.frontend_base_url.rstrip('/')}/ceo/{application_id}"
        )
        recipients_sent: list[str] = []
        for member in ceo_members:
            try:
                await send_email(
                    to=member.email,
                    template="ceo_handoff",
                    variables={
                        "candidate_name": cand.name if cand else None,
                        "candidate_email": cand.email if cand else None,
                        "role_title": role.title if role else None,
                        "fit_score": app.fit_score,
                        "fit_tier": app.fit_tier,
                        "application_id": str(application_id),
                        "review_url": review_url,
                        "tech_decision_actor": actor,
                        "tech_decision_note": body.note,
                    },
                    tags={
                        "type": "ceo_handoff",
                        "application_id": str(application_id),
                    },
                )
                recipients_sent.append(member.email)
            except Exception:  # noqa: BLE001
                pass

        await set_stage(
            session,
            application_id,
            PipelineStage.CEO_PENDING_APPROVAL,
            force=True,
        )
        await log_audit(
            session,
            application_id=application_id,
            candidate_id=app.candidate_id,
            action="tech_decision_advance",
            actor=actor,
            details={
                "note": body.note,
                "ceo_recipients": recipients_sent,
            },
        )

    try:
        from src.api.meetings import _kick_schedule_meeting
        await _kick_schedule_meeting(application_id, "ceo")
    except Exception:  # noqa: BLE001
        _stage_log.exception("auto-schedule CEO meeting failed for %s", application_id)

    return {"status": "advanced", "ceo_recipients": recipients_sent}


class CEODecisionBody(BaseModel):
    action: str  # "advance_to_hr" | "reject"
    note: str | None = None


@router.post("/candidates/{application_id}/ceo-decision")
async def ceo_decision(
    application_id: UUID,
    body: CEODecisionBody,
    actor: Annotated[str, Depends(require_recruiter)],
) -> dict:
    """CEO decision after ceo_pending_approval. Advances to HR meeting or rejects."""
    if body.action not in ("advance_to_hr", "reject"):
        raise HTTPException(status_code=400, detail="invalid_action")

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise HTTPException(status_code=404, detail="application_not_found")

        if body.action == "reject":
            prior_stage = app.current_stage
            merged_admin = dict(app.admin_review or {})
            merged_admin.update(
                {
                    "rejected_from_stage": prior_stage,
                    "rejected_category": "ceo_reject",
                    "rejected_at": datetime.now(UTC).isoformat(),
                    "rejected_by": actor,
                    "rejected_note": body.note,
                }
            )
            app.admin_review = merged_admin
            cand = await session.get(Candidate, app.candidate_id)
            role = await session.get(Role, app.role_id) if app.role_id else None
            await set_stage(
                session, application_id, PipelineStage.REJECTED, force=True
            )
            await log_audit(
                session,
                application_id=application_id,
                candidate_id=app.candidate_id,
                action="ceo_decision_reject",
                actor=actor,
                details={"note": body.note, "rejected_from_stage": prior_stage},
            )

            await _send_rejection_email(
                cand, role, application_id, "ceo_reject", body.note,
            )
            return {"status": "rejected"}

        # advance_to_hr
        from src.channels.email import send_email
        from src.db.base import PanelMember

        cand = await session.get(Candidate, app.candidate_id)
        role = await session.get(Role, app.role_id) if app.role_id else None

        hr_members = (
            await session.execute(
                select(PanelMember).where(
                    PanelMember.role_type == "hr",
                    PanelMember.is_active.is_(True),
                )
            )
        ).scalars().all()

        review_url = (
            f"{_v1_settings.frontend_base_url.rstrip('/')}/hr/{application_id}"
        )
        hr_recipients: list[str] = []
        for member in hr_members:
            try:
                await send_email(
                    to=member.email,
                    template="hr_review_request",
                    variables={
                        "candidate_name": cand.name if cand else None,
                        "candidate_email": cand.email if cand else None,
                        "role_title": role.title if role else None,
                        "fit_score": app.fit_score,
                        "fit_tier": app.fit_tier,
                        "application_id": str(application_id),
                        "review_url": review_url,
                        "ceo_decision_actor": actor,
                        "ceo_decision_note": body.note,
                    },
                    tags={
                        "type": "hr_review_request",
                        "application_id": str(application_id),
                    },
                )
                hr_recipients.append(member.email)
            except Exception:  # noqa: BLE001
                pass

        if cand and cand.email:
            try:
                await send_email(
                    to=cand.email,
                    template="candidate_progress_to_hr",
                    variables={
                        "candidate_name": cand.name,
                        "role_title": role.title if role else None,
                        "application_id": str(application_id),
                    },
                    tags={
                        "type": "candidate_progress_to_hr",
                        "application_id": str(application_id),
                    },
                )
            except Exception:  # noqa: BLE001
                pass

        await set_stage(
            session,
            application_id,
            PipelineStage.HR_MEETING_SCHEDULED,
            force=True,
        )
        await log_audit(
            session,
            application_id=application_id,
            candidate_id=app.candidate_id,
            action="ceo_decision_advance",
            actor=actor,
            details={"note": body.note, "hr_recipients": hr_recipients},
        )

    try:
        from src.api.meetings import _kick_schedule_meeting
        await _kick_schedule_meeting(application_id, "hr")
    except Exception:  # noqa: BLE001
        _stage_log.exception("auto-schedule HR meeting failed for %s", application_id)

    return {"status": "advanced_to_hr", "hr_recipients": hr_recipients}


class HRDecisionBody(BaseModel):
    action: str  # "extend_offer" | "reject"
    note: str | None = None


@router.post("/candidates/{application_id}/hr-decision")
async def hr_decision(
    application_id: UUID,
    body: HRDecisionBody,
    actor: Annotated[str, Depends(require_recruiter)],
) -> dict:
    """HR final decision after HR round. Extends offer or rejects."""
    from src.channels.email import send_email

    if body.action not in ("extend_offer", "reject"):
        raise HTTPException(status_code=400, detail="invalid_action")

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise HTTPException(status_code=404, detail="application_not_found")
        cand = await session.get(Candidate, app.candidate_id)
        role = await session.get(Role, app.role_id) if app.role_id else None

        if body.action == "reject":
            prior_stage = app.current_stage
            merged_admin = dict(app.admin_review or {})
            merged_admin.update(
                {
                    "rejected_from_stage": prior_stage,
                    "rejected_category": "hr_reject",
                    "rejected_at": datetime.now(UTC).isoformat(),
                    "rejected_by": actor,
                    "rejected_note": body.note,
                }
            )
            app.admin_review = merged_admin
            await set_stage(
                session, application_id, PipelineStage.REJECTED, force=True
            )
            await log_audit(
                session,
                application_id=application_id,
                candidate_id=app.candidate_id,
                action="hr_decision_reject",
                actor=actor,
                details={"note": body.note, "rejected_from_stage": prior_stage},
            )

            await _send_rejection_email(
                cand, role, application_id, "hr_reject", body.note,
            )
            return {"status": "rejected"}

        # extend_offer
        offer_sent = False
        if cand and cand.email:
            try:
                await send_email(
                    to=cand.email,
                    template="offer_extended",
                    variables={
                        "candidate_name": cand.name,
                        "role_title": role.title if role else None,
                        "application_id": str(application_id),
                        "offer_note": body.note,
                    },
                    tags={
                        "type": "offer_extended",
                        "application_id": str(application_id),
                    },
                )
                offer_sent = True
            except Exception:  # noqa: BLE001
                pass

        await set_stage(
            session, application_id, PipelineStage.HIRED, force=True
        )
        await log_audit(
            session,
            application_id=application_id,
            candidate_id=app.candidate_id,
            action="hr_decision_extend_offer",
            actor=actor,
            details={"note": body.note, "offer_email_sent": offer_sent},
        )
    return {"status": "offer_extended", "offer_email_sent": offer_sent}


# ---------------------------------------------------------------------------
# Evidence chain (Phase 2)
# ---------------------------------------------------------------------------


class EvidenceItem(BaseModel):
    id: str
    fact_key: str
    fact_value: Any
    source_stage: str
    source_type: str
    extraction_method: str
    confidence: float | None
    evidence_text: str | None
    created_at: datetime


class DecisionItem(BaseModel):
    id: str
    decision_type: str
    outcome: str
    outcome_value: dict
    evidence_ids: list[str]
    policy_rule_ids: list[str]
    created_at: datetime


class ContradictionItem(BaseModel):
    id: str
    fact_key: str | None
    old_value: Any | None
    new_value: Any | None
    old_source_stage: str | None
    new_source_stage: str | None
    created_at: datetime


class EvidenceChainResponse(BaseModel):
    application_id: str
    evidence: list[EvidenceItem]
    decisions: list[DecisionItem]
    contradictions: list[ContradictionItem]


@router.get(
    "/applications/{application_id}/evidence-chain",
    response_model=EvidenceChainResponse,
)
async def evidence_chain(
    application_id: UUID,
    _: Annotated[str, Depends(require_viewer)],
) -> EvidenceChainResponse:
    """Full provenance trail for an application: evidence, decisions, contradiction alerts."""
    from src.db.base import DecisionRecord, EvidenceRecord, PipelineAlert
    from src.db.repositories.evidence import (
        get_decisions_for_application,
        get_evidence_for_application,
    )

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise HTTPException(status_code=404, detail="application not found")

        evidence_rows = await get_evidence_for_application(session, application_id)
        decision_rows = await get_decisions_for_application(session, application_id)

        contradiction_rows = (
            await session.execute(
                select(PipelineAlert)
                .where(PipelineAlert.application_id == application_id)
                .where(PipelineAlert.alert_type == "fact_contradiction")
                .order_by(PipelineAlert.created_at)
            )
        ).scalars().all()

    return EvidenceChainResponse(
        application_id=str(application_id),
        evidence=[
            EvidenceItem(
                id=str(e.id),
                fact_key=e.fact_key,
                fact_value=e.fact_value,
                source_stage=e.source_stage,
                source_type=e.source_type,
                extraction_method=e.extraction_method,
                confidence=e.confidence,
                evidence_text=e.evidence_text,
                created_at=e.created_at,
            )
            for e in evidence_rows
        ],
        decisions=[
            DecisionItem(
                id=str(d.id),
                decision_type=d.decision_type,
                outcome=d.outcome,
                outcome_value=d.outcome_value or {},
                evidence_ids=d.evidence_ids or [],
                policy_rule_ids=d.policy_rule_ids or [],
                created_at=d.created_at,
            )
            for d in decision_rows
        ],
        contradictions=[
            ContradictionItem(
                id=str(c.id),
                fact_key=(c.details or {}).get("fact_key"),
                old_value=(c.details or {}).get("old_value"),
                new_value=(c.details or {}).get("new_value"),
                old_source_stage=(c.details or {}).get("old_source_stage"),
                new_source_stage=(c.details or {}).get("new_source_stage"),
                created_at=c.created_at,
            )
            for c in contradiction_rows
        ],
    )


# ---------------------------------------------------------------------------
# Phase 6: Pipeline confidence
# ---------------------------------------------------------------------------


class StageConfidenceResponse(BaseModel):
    stage: str
    evidence_count: int
    avg_confidence: float
    contradiction_count: int
    consistency_ratio: float


class PipelineConfidenceResponse(BaseModel):
    application_id: str
    overall: float
    recommendation: str
    supervisor_accuracy: float | None
    per_stage: list[StageConfidenceResponse]
    details: dict


@router.get(
    "/applications/{application_id}/confidence",
    response_model=PipelineConfidenceResponse,
)
async def pipeline_confidence(
    application_id: UUID,
    _: Annotated[str, Depends(require_viewer)],
) -> PipelineConfidenceResponse:
    """Pipeline confidence score for an application."""
    from src.services.confidence_analyzer import analyze_confidence

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise HTTPException(status_code=404, detail="application not found")

        result = await analyze_confidence(
            session, application_id, current_stage=app.current_stage,
        )

    return PipelineConfidenceResponse(
        application_id=str(application_id),
        overall=result.overall,
        recommendation=result.recommendation,
        supervisor_accuracy=result.supervisor_accuracy,
        per_stage=[
            StageConfidenceResponse(
                stage=sc.stage,
                evidence_count=sc.evidence_count,
                avg_confidence=sc.avg_confidence,
                contradiction_count=sc.contradiction_count,
                consistency_ratio=sc.consistency_ratio,
            )
            for sc in result.per_stage.values()
        ],
        details=result.details,
    )


class EngagementResponse(BaseModel):
    application_id: str
    overall: float
    response_speed: float
    completion_rate: float
    scheduling_flexibility: float
    signals: list[str]
    risk: str


@router.get(
    "/applications/{application_id}/engagement",
    response_model=EngagementResponse,
)
async def application_engagement(
    application_id: UUID,
    _: Annotated[str, Depends(require_viewer)],
) -> EngagementResponse:
    """Candidate engagement score for an application."""
    from src.services.engagement_scorer import compute_engagement

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise HTTPException(status_code=404, detail="application not found")
        result = await compute_engagement(session, application_id)

    return EngagementResponse(
        application_id=str(application_id),
        overall=result.overall,
        response_speed=result.response_speed,
        completion_rate=result.completion_rate,
        scheduling_flexibility=result.scheduling_flexibility,
        signals=result.signals,
        risk=result.risk,
    )


class FeedbackDiscrepancy(BaseModel):
    type: str
    description: str
    evidence_id: str | None = None
    round: str | None = None


class InterviewIntelligenceResponse(BaseModel):
    application_id: str
    interviews_with_feedback: int
    total_discrepancies: int
    discrepancies: list[FeedbackDiscrepancy]


@router.get(
    "/applications/{application_id}/interview-intelligence",
    response_model=InterviewIntelligenceResponse,
)
async def application_interview_intelligence(
    application_id: UUID,
    _: Annotated[str, Depends(require_viewer)],
) -> InterviewIntelligenceResponse:
    """Cross-reference report: interviewer feedback vs pipeline evidence."""
    from src.services.interview_intelligence import cross_reference_feedback

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise HTTPException(status_code=404, detail="application not found")

        interviews = (
            await session.execute(
                select(Interview)
                .where(Interview.application_id == application_id)
                .where(Interview.feedback.isnot(None))
            )
        ).scalars().all()

    all_discrepancies: list[dict] = []
    for interview in interviews:
        if interview.feedback:
            disc = await cross_reference_feedback(interview.id, interview.feedback)
            all_discrepancies.extend(disc)

    return InterviewIntelligenceResponse(
        application_id=str(application_id),
        interviews_with_feedback=len(interviews),
        total_discrepancies=len(all_discrepancies),
        discrepancies=[
            FeedbackDiscrepancy(
                type=d.get("type", "unknown"),
                description=d.get("description", ""),
                evidence_id=d.get("evidence_id"),
                round=d.get("round"),
            )
            for d in all_discrepancies[:20]
        ],
    )
