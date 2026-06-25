"""HR dashboard REST endpoints (consumed by Retool).

All endpoints require a `X-Dashboard-Key` header. Write operations require
at least the `recruiter` role; admin-only ops are annotated explicitly.

View shape follows the skill's dashboard requirements:
  1. /dashboard/mail-inbox     -- email-sourced applications (all providers)
  2. /dashboard/shortlist      -- scored applications by tier, with override ctl
  3. /dashboard/scheduling     -- interviews confirmed/pending
  4. /dashboard/cold-pool      -- non-responders, for weekly rescue
  5. /dashboard/audit          -- searchable append-only log
  6. /dashboard/metrics/*      -- funnel, override rate, channel delivery
  7. /dashboard/candidate/{id} -- full candidate view (consolidated)

Post-shortlist manual actions (when HR wants to push the pipeline forward
for a candidate whose workflow isn't parked at the right wait point):
  - POST /dashboard/actions/send-screening/{application_id}
  - POST /dashboard/actions/propose-slots/{application_id}
  - POST /dashboard/actions/reengage/{application_id}

HR overrides are implemented as Temporal signals; the workflow id is
derived from application_id (`candidate-journey-{application_id}`).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, Request, UploadFile, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import and_, desc, func, or_, select

from src.api.auth import require_recruiter, require_viewer
from src.config import get_settings

_settings = get_settings()
from src.db.base import (
    Application,
    AuditLog,
    Candidate,
    CandidateProfileRow,
    Interview,
    PipelineAlert,
    Role,
    ScreeningResponseRow,
)
from src.activities.schedule import ProposeSlotsInput, run_propose_slots
from src.activities.screening import ScreeningSendInput, run_send_screening
from src.channels.email import send_email
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.models.candidate import ApplicationStatus, CandidateStatus, FitTier
from src.services import metrics
from src.services.file_storage import presigned_get_url, upload_resume
from src.services.request_rate_limit import enforce_rate_limit
from src.services.temporal_client import get_temporal_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# ---------------------------------------------------------------------------
# Read models
# ---------------------------------------------------------------------------


class MailInboxItem(BaseModel):
    application_id: UUID
    candidate_id: UUID
    candidate_name: str | None
    candidate_email: str | None
    role_title: str | None
    status: str
    mail_source: str | None
    subject: str | None
    received_at: datetime | None
    created_at: datetime


class ShortlistItem(BaseModel):
    application_id: UUID
    candidate_id: UUID
    candidate_name: str | None
    role_title: str | None
    fit_score: int | None
    fit_tier: str | None
    screening_score: int | None
    status: str
    created_at: datetime


class SchedulingItem(BaseModel):
    interview_id: UUID
    application_id: UUID
    candidate_name: str | None
    role_title: str | None
    scheduled_at: datetime | None
    meeting_link: str | None
    status: str


class AuditItem(BaseModel):
    id: int
    candidate_id: UUID | None
    application_id: UUID | None
    action: str
    actor: str
    details: dict[str, Any] | None
    model_version: str | None
    prompt_version: str | None
    langfuse_trace_id: str | None
    created_at: datetime


class AuditPage(BaseModel):
    items: list["AuditItem"]
    total: int
    limit: int
    offset: int


class ScreeningAnswerItem(BaseModel):
    question_id: str
    question_text: str | None = None
    question_type: str | None = None
    answer: Any = None
    score: float | None = None
    max_score: float | None = None
    rationale: str | None = None


class ScreeningResponseView(BaseModel):
    application_id: UUID
    role_title: str | None
    submitted_at: datetime | None
    composite_score: int | None
    knock_out_triggered: bool
    knock_out_reason: str | None
    items: list[ScreeningAnswerItem]


class FitFactorItem(BaseModel):
    factor: str
    score: float | None = None
    weight: float | None = None
    rationale: str | None = None
    evidence: list[str] = Field(default_factory=list)


class FitReportExtras(BaseModel):
    overall_score: int | None = None
    summary: str | None = None
    red_flags: list[str] = Field(default_factory=list)
    green_flags: list[str] = Field(default_factory=list)
    deterministic_tier: str | None = None
    llm_tier: str | None = None
    tier_agreement: bool | None = None


class CandidateView(BaseModel):
    candidate_id: UUID
    name: str | None
    email: str | None
    phone: str | None
    status: str
    source_channel: str | None
    applications: list[ShortlistItem]
    latest_profile: dict[str, Any] | None
    recent_audit: list[AuditItem]
    screening_responses: list[ScreeningResponseView] = Field(default_factory=list)
    fit_factors: list[FitFactorItem] = Field(default_factory=list)
    fit_report: FitReportExtras | None = None
    resume_download_url: str | None = None
    resume_filename: str | None = None


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


@router.get("/mail-inbox", response_model=list[MailInboxItem])
async def mail_inbox(
    _: Annotated[str, Depends(require_viewer)],
    mail_source: Literal["all", "gmail", "outlook", "imap_gmail", "imap_outlook", "generic"] = "all",
    limit: int = Query(200, le=1000),
) -> list[MailInboxItem]:
    """Applications that arrived via email (Gmail, Outlook, or any relay).

    `mail_source` filters by the provider tag recorded during intake. Use
    "all" to see every email-sourced application regardless of provider.
    """
    ms_subq = (
        select(AuditLog.details["mail_source"].astext)
        .where(AuditLog.application_id == Application.id)
        .where(AuditLog.action == "intake_completed")
        .order_by(desc(AuditLog.created_at))
        .limit(1)
        .scalar_subquery()
    )
    subj_subq = (
        select(AuditLog.details["subject"].astext)
        .where(AuditLog.application_id == Application.id)
        .where(AuditLog.action == "intake_completed")
        .order_by(desc(AuditLog.created_at))
        .limit(1)
        .scalar_subquery()
    )
    recv_subq = (
        select(AuditLog.details["received_at"].astext)
        .where(AuditLog.application_id == Application.id)
        .where(AuditLog.action == "intake_completed")
        .order_by(desc(AuditLog.created_at))
        .limit(1)
        .scalar_subquery()
    )

    filters = [Candidate.source_channel == "email"]
    if mail_source != "all":
        filters.append(ms_subq == mail_source)

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(
                    Application,
                    Candidate,
                    Role,
                    ms_subq.label("mail_source"),
                    subj_subq.label("subject"),
                    recv_subq.label("received_at"),
                )
                .join(Candidate, Candidate.id == Application.candidate_id)
                .join(Role, Role.id == Application.role_id, isouter=True)
                .where(and_(*filters))
                .order_by(desc(Application.created_at))
                .limit(limit)
            )
        ).all()
        out: list[MailInboxItem] = []
        for app, cand, role, src, subj, recv in rows:
            parsed_recv: datetime | None = None
            if recv:
                try:
                    parsed_recv = datetime.fromisoformat(recv)
                except ValueError:
                    parsed_recv = None
            out.append(
                MailInboxItem(
                    application_id=app.id,
                    candidate_id=cand.id,
                    candidate_name=cand.name,
                    candidate_email=cand.email,
                    role_title=role.title if role else None,
                    status=app.status,
                    mail_source=src,
                    subject=subj,
                    received_at=parsed_recv,
                    created_at=app.created_at,
                )
            )
        return out


@router.get("/shortlist", response_model=list[ShortlistItem])
async def shortlist(
    _: Annotated[str, Depends(require_viewer)],
    tier: Literal["green", "amber", "red", "all"] = "all",
    role_id: UUID | None = None,
    limit: int = Query(200, le=1000),
) -> list[ShortlistItem]:
    # Include rows that either have a fit_score OR were force-shortlisted by HR
    # (those may have status='shortlisted' with no fit_score yet).
    filters = [
        or_(
            Application.fit_score.isnot(None),
            Application.status == ApplicationStatus.SHORTLISTED.value,
        )
    ]
    if tier != "all":
        filters.append(Application.fit_tier == tier)
    if role_id is not None:
        filters.append(Application.role_id == role_id)

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Application, Candidate, Role)
                .join(Candidate, Candidate.id == Application.candidate_id)
                .join(Role, Role.id == Application.role_id, isouter=True)
                .where(and_(*filters))
                .order_by(desc(Application.fit_score).nullslast(), desc(Application.created_at))
                .limit(limit)
            )
        ).all()
        return [
            ShortlistItem(
                application_id=app.id,
                candidate_id=cand.id,
                candidate_name=cand.name,
                role_title=role.title if role else None,
                fit_score=app.fit_score,
                fit_tier=app.fit_tier,
                screening_score=app.screening_score,
                status=app.status,
                created_at=app.created_at,
            )
            for app, cand, role in rows
        ]


@router.get("/scheduling", response_model=list[SchedulingItem])
async def scheduling(
    _: Annotated[str, Depends(require_viewer)],
    limit: int = Query(100, le=500),
) -> list[SchedulingItem]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Interview, Application, Candidate, Role)
                .join(Application, Application.id == Interview.application_id)
                .join(Candidate, Candidate.id == Application.candidate_id)
                .join(Role, Role.id == Application.role_id, isouter=True)
                .where(Interview.status.in_(["proposed", "confirmed", "rescheduled"]))
                .order_by(Interview.scheduled_at.asc().nullslast())
                .limit(limit)
            )
        ).all()
        return [
            SchedulingItem(
                interview_id=iv.id,
                application_id=app.id,
                candidate_name=cand.name,
                role_title=role.title if role else None,
                scheduled_at=iv.scheduled_at,
                meeting_link=iv.meeting_link,
                status=iv.status,
            )
            for iv, app, cand, role in rows
        ]


@router.get("/cold-pool", response_model=list[ShortlistItem])
async def cold_pool(
    _: Annotated[str, Depends(require_viewer)],
    limit: int = Query(200, le=1000),
) -> list[ShortlistItem]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Application, Candidate, Role)
                .join(Candidate, Candidate.id == Application.candidate_id)
                .join(Role, Role.id == Application.role_id, isouter=True)
                .where(Application.status == ApplicationStatus.COLD.value)
                .order_by(desc(Application.updated_at))
                .limit(limit)
            )
        ).all()
        return [
            ShortlistItem(
                application_id=app.id,
                candidate_id=cand.id,
                candidate_name=cand.name,
                role_title=role.title if role else None,
                fit_score=app.fit_score,
                fit_tier=app.fit_tier,
                screening_score=app.screening_score,
                status=app.status,
                created_at=app.created_at,
            )
            for app, cand, role in rows
        ]


@router.get("/audit", response_model=AuditPage)
async def audit(
    _: Annotated[str, Depends(require_viewer)],
    candidate_id: UUID | None = None,
    application_id: UUID | None = None,
    action: str | None = None,
    search: str | None = Query(None, description="free text in action or actor", max_length=200),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> AuditPage:
    filters = []
    if candidate_id is not None:
        filters.append(AuditLog.candidate_id == candidate_id)
    if application_id is not None:
        filters.append(AuditLog.application_id == application_id)
    if action:
        filters.append(AuditLog.action == action)
    if search:
        like = f"%{search}%"
        filters.append(or_(AuditLog.action.ilike(like), AuditLog.actor.ilike(like)))

    async with session_scope() as session:
        count_stmt = select(func.count()).select_from(AuditLog)
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = int((await session.execute(count_stmt)).scalar() or 0)

        stmt = (
            select(AuditLog)
            .order_by(desc(AuditLog.id))
            .offset(offset)
            .limit(limit)
        )
        if filters:
            stmt = stmt.where(and_(*filters))
        rows = (await session.scalars(stmt)).all()
        items = [
            AuditItem(
                id=r.id,
                candidate_id=r.candidate_id,
                application_id=r.application_id,
                action=r.action,
                actor=r.actor,
                details=r.details,
                model_version=r.model_version,
                prompt_version=r.prompt_version,
                langfuse_trace_id=r.langfuse_trace_id,
                created_at=r.created_at,
            )
            for r in rows
        ]
        return AuditPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/candidate/{candidate_id}", response_model=CandidateView)
async def candidate_view(
    candidate_id: UUID,
    _: Annotated[str, Depends(require_viewer)],
) -> CandidateView:
    async with session_scope() as session:
        cand = await session.get(Candidate, candidate_id)
        if cand is None:
            raise HTTPException(status_code=404, detail="Candidate not found")

        apps = (
            await session.execute(
                select(Application, Role)
                .join(Role, Role.id == Application.role_id, isouter=True)
                .where(Application.candidate_id == candidate_id)
                .order_by(desc(Application.created_at))
            )
        ).all()
        profile = await session.scalar(
            select(CandidateProfileRow)
            .where(CandidateProfileRow.candidate_id == candidate_id)
            .order_by(desc(CandidateProfileRow.created_at))
            .limit(1)
        )
        recent = (
            await session.scalars(
                select(AuditLog)
                .where(AuditLog.candidate_id == candidate_id)
                .order_by(desc(AuditLog.id))
                .limit(50)
            )
        ).all()

        app_ids = [a.id for a, _ in apps]
        screening_rows: list[tuple[ScreeningResponseRow, Role | None]] = []
        if app_ids:
            screening_rows = (
                await session.execute(
                    select(ScreeningResponseRow, Role)
                    .join(Application, Application.id == ScreeningResponseRow.application_id)
                    .join(Role, Role.id == Application.role_id, isouter=True)
                    .where(ScreeningResponseRow.application_id.in_(app_ids))
                    .order_by(desc(ScreeningResponseRow.submitted_at).nullslast())
                )
            ).all()

        # Build question_id -> question_meta index per role from role.screening_questions JSON
        role_questions: dict[UUID, dict[str, dict]] = {}
        for _row, role in screening_rows:
            if role and role.id not in role_questions:
                q_index: dict[str, dict] = {}
                for q in role.screening_questions or []:
                    if isinstance(q, dict) and q.get("id"):
                        q_index[str(q["id"])] = q
                role_questions[role.id] = q_index

        screening_views: list[ScreeningResponseView] = []
        for sr, role in screening_rows:
            q_idx = role_questions.get(role.id, {}) if role else {}
            items: list[ScreeningAnswerItem] = []
            for r in sr.responses or []:
                if not isinstance(r, dict):
                    continue
                qid = str(r.get("question_id", ""))
                qmeta = q_idx.get(qid, {})
                items.append(
                    ScreeningAnswerItem(
                        question_id=qid,
                        question_text=qmeta.get("text") or qmeta.get("prompt"),
                        question_type=qmeta.get("type"),
                        answer=r.get("answer"),
                        score=r.get("score"),
                        max_score=qmeta.get("max_score") or qmeta.get("weight"),
                        rationale=r.get("rationale"),
                    )
                )
            screening_views.append(
                ScreeningResponseView(
                    application_id=sr.application_id,
                    role_title=role.title if role else None,
                    submitted_at=sr.submitted_at,
                    composite_score=sr.composite_score,
                    knock_out_triggered=bool(sr.knock_out_triggered),
                    knock_out_reason=sr.knock_out_reason,
                    items=items,
                )
            )

        # Fit factor breakdown from most-recent "fit_scored" audit entry.
        # criteria_scores (the role's own dynamic evaluation_spec dimensions) is
        # the canonical source now -- dimensions is only present on rows scored
        # before the dynamic-spec rewrite and kept here for historical display.
        fit_factors: list[FitFactorItem] = []
        fit_report: FitReportExtras | None = None
        for r in recent:
            if r.action == "fit_scored" and isinstance(r.details, dict):
                details = r.details
                criteria = details.get("criteria_scores") or []
                if isinstance(criteria, list) and criteria:
                    for c in criteria:
                        if not isinstance(c, dict):
                            continue
                        fit_factors.append(
                            FitFactorItem(
                                factor=str(c.get("label") or c.get("key") or "").replace("_", " "),
                                score=c.get("score"),
                                weight=c.get("weight"),
                                rationale=c.get("rationale"),
                                evidence=list(c.get("evidence") or []),
                            )
                        )
                else:
                    dims = details.get("dimensions") or {}
                    weights = details.get("weights_used") or {}
                    if isinstance(dims, dict):
                        for name, body in dims.items():
                            if not isinstance(body, dict):
                                continue
                            fit_factors.append(
                                FitFactorItem(
                                    factor=str(name).replace("_", " "),
                                    score=body.get("score"),
                                    weight=weights.get(name) if isinstance(weights, dict) else None,
                                    rationale=body.get("rationale"),
                                    evidence=list(body.get("evidence") or []),
                                )
                            )
                fit_report = FitReportExtras(
                    overall_score=details.get("overall_score"),
                    summary=details.get("summary"),
                    red_flags=list(details.get("red_flags") or []),
                    green_flags=list(details.get("green_flags") or []),
                    deterministic_tier=details.get("deterministic_tier"),
                    llm_tier=details.get("llm_tier"),
                    tier_agreement=details.get("tier_agreement"),
                )
                break

        resume_download_url: str | None = None
        resume_filename: str | None = None
        if profile and profile.raw_resume_r2_key:
            try:
                resume_download_url = await presigned_get_url(
                    _settings.r2_bucket_resumes, profile.raw_resume_r2_key, ttl_seconds=900
                )
                resume_filename = profile.raw_resume_r2_key.split("/")[-1]
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to presign resume URL: %s", e)

        return CandidateView(
            candidate_id=cand.id,
            name=cand.name,
            email=cand.email,
            phone=cand.phone,
            status=cand.status,
            source_channel=cand.source_channel,
            applications=[
                ShortlistItem(
                    application_id=a.id,
                    candidate_id=cand.id,
                    candidate_name=cand.name,
                    role_title=r.title if r else None,
                    fit_score=a.fit_score,
                    fit_tier=a.fit_tier,
                    screening_score=a.screening_score,
                    status=a.status,
                    created_at=a.created_at,
                )
                for a, r in apps
            ],
            latest_profile=profile.parsed_data if profile else None,
            recent_audit=[
                AuditItem(
                    id=r.id,
                    candidate_id=r.candidate_id,
                    application_id=r.application_id,
                    action=r.action,
                    actor=r.actor,
                    details=r.details,
                    model_version=r.model_version,
                    prompt_version=r.prompt_version,
                    langfuse_trace_id=r.langfuse_trace_id,
                    created_at=r.created_at,
                )
                for r in recent
            ],
            screening_responses=screening_views,
            fit_factors=fit_factors,
            fit_report=fit_report,
            resume_download_url=resume_download_url,
            resume_filename=resume_filename,
        )


# ---------------------------------------------------------------------------
# Overrides (write ops)
# ---------------------------------------------------------------------------


OverrideDecision = Literal["approve", "reject", "force_shortlist", "change_role", "withdraw"]


class OverridePayload(BaseModel):
    decision: OverrideDecision
    reason: str = Field(..., min_length=3, description="Why HR is overriding")
    hr_email: str = Field(..., description="HR user making the decision")
    new_role_id: UUID | None = None
    new_fit_tier: FitTier | None = None


async def _signal_workflow(application_id: UUID, decision: str) -> bool:
    try:
        client = await get_temporal_client()
        handle = client.get_workflow_handle(f"candidate-journey-{application_id}")
        await handle.signal("hr_override", decision)
        return True
    except Exception:
        logger.exception(
            "Failed to signal workflow for application %s (override=%s)",
            application_id,
            decision,
        )
        return False


@router.post("/override/{application_id}", status_code=status.HTTP_202_ACCEPTED)
async def override_application(
    request: Request,
    application_id: UUID = Path(...),
    payload: OverridePayload = ...,
    _: Annotated[str, Depends(require_recruiter)] = None,
) -> dict[str, Any]:
    await enforce_rate_limit(request, "dashboard_write", limit=30, window_seconds=60)
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise HTTPException(status_code=404, detail="Application not found")

        previous_status = app.status
        changes: dict[str, Any] = {}

        if payload.decision == "approve":
            app.status = ApplicationStatus.SHORTLISTED.value
            changes["status"] = app.status
        elif payload.decision == "reject":
            app.status = ApplicationStatus.REJECTED.value
            changes["status"] = app.status
        elif payload.decision == "force_shortlist":
            app.status = ApplicationStatus.SHORTLISTED.value
            if payload.new_fit_tier is not None:
                app.fit_tier = payload.new_fit_tier.value
                changes["fit_tier"] = app.fit_tier
            changes["status"] = app.status
        elif payload.decision == "change_role":
            if payload.new_role_id is None:
                raise HTTPException(
                    status_code=400, detail="new_role_id required for change_role"
                )
            app.role_id = payload.new_role_id
            app.status = ApplicationStatus.CLASSIFIED.value
            changes["role_id"] = str(app.role_id)
            changes["status"] = app.status
        elif payload.decision == "withdraw":
            app.status = ApplicationStatus.WITHDRAWN.value
            changes["status"] = app.status

        await log_audit(
            session,
            action="hr_override",
            actor=payload.hr_email,
            candidate_id=app.candidate_id,
            application_id=app.id,
            details={
                "decision": payload.decision,
                "reason": payload.reason,
                "previous_status": previous_status,
                "changes": changes,
            },
        )
        candidate_id = app.candidate_id

    signalled = await _signal_workflow(application_id, payload.decision)

    # Post-shortlist automation: if HR shortlists a candidate who has NOT yet
    # been through screening, kick off the screening invite. Do NOT re-send
    # screening if the candidate already submitted a response — that would
    # reset application.status back to SCREENING_IN_PROGRESS and unwind the
    # HR decision. In the "already-screened, HR approving post-screening"
    # case the Temporal signal above unblocks the workflow's wait_condition
    # and the journey proceeds to propose interview slots on its own.
    screening_sent = False
    if payload.decision in ("approve", "force_shortlist"):
        already_screened = False
        async with session_scope() as session:
            already_screened = await session.scalar(
                select(ScreeningResponseRow.id)
                .where(ScreeningResponseRow.application_id == application_id)
                .limit(1)
            ) is not None
        if not already_screened:
            try:
                result = await run_send_screening(
                    ScreeningSendInput(
                        candidate_id=candidate_id,
                        application_id=application_id,
                        kind="invite",
                    )
                )
                screening_sent = result.sent_email or result.sent_whatsapp
            except Exception:
                logger.exception(
                    "Failed to auto-send screening invite after shortlist for %s",
                    application_id,
                )

    return {
        "ok": True,
        "application_id": str(application_id),
        "candidate_id": str(candidate_id),
        "decision": payload.decision,
        "workflow_signalled": signalled,
        "screening_sent": screening_sent,
    }


# ---------------------------------------------------------------------------
# Manual actions (push the pipeline forward when workflow isn't parked)
# ---------------------------------------------------------------------------


actions_router = APIRouter(prefix="/dashboard/actions", tags=["dashboard-actions"])


async def _load_app_candidate(session, application_id: UUID) -> Application:
    app = await session.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return app


@actions_router.post("/send-screening/{application_id}", status_code=status.HTTP_202_ACCEPTED)
async def send_screening_now(
    application_id: UUID = Path(...),
    _: Annotated[str, Depends(require_recruiter)] = None,
) -> dict[str, Any]:
    """Send (or re-send) the screening invite to the candidate now."""
    async with session_scope() as session:
        app = await _load_app_candidate(session, application_id)
        candidate_id = app.candidate_id

    result = await run_send_screening(
        ScreeningSendInput(
            candidate_id=candidate_id,
            application_id=application_id,
            kind="invite",
        )
    )
    return {
        "ok": result.sent_email or result.sent_whatsapp,
        "email": result.sent_email,
        "whatsapp": result.sent_whatsapp,
        "failures": result.failures,
        "form_url": result.form_url,
    }


@actions_router.post("/propose-slots/{application_id}", status_code=status.HTTP_202_ACCEPTED)
async def propose_slots_now(
    application_id: UUID = Path(...),
    _: Annotated[str, Depends(require_recruiter)] = None,
) -> dict[str, Any]:
    """Find interviewer availability and send 3 slot proposals to the candidate."""
    async with session_scope() as session:
        await _load_app_candidate(session, application_id)

    result = await run_propose_slots(
        ProposeSlotsInput(
            candidate_id=(await _candidate_id_for(application_id)),
            application_id=application_id,
        )
    )
    return {
        "ok": not result.stalled,
        "stalled": result.stalled,
        "slots_proposed": result.slot_count,
    }


@actions_router.post("/reengage/{application_id}", status_code=status.HTTP_202_ACCEPTED)
async def reengage_cold(
    application_id: UUID = Path(...),
    hr_email: str = Query(..., description="HR user making the call"),
    _: Annotated[str, Depends(require_recruiter)] = None,
) -> dict[str, Any]:
    """Pull a candidate out of the cold pool and re-send the screening invite."""
    async with session_scope() as session:
        app = await _load_app_candidate(session, application_id)
        previous_status = app.status
        app.status = ApplicationStatus.SCREENING_IN_PROGRESS.value
        candidate_id = app.candidate_id
        await log_audit(
            session,
            action="cold_pool_reengage",
            actor=hr_email,
            candidate_id=candidate_id,
            application_id=application_id,
            details={"previous_status": previous_status},
        )

    result = await run_send_screening(
        ScreeningSendInput(
            candidate_id=candidate_id,
            application_id=application_id,
            kind="invite",
        )
    )
    return {
        "ok": result.sent_email or result.sent_whatsapp,
        "email": result.sent_email,
        "whatsapp": result.sent_whatsapp,
        "failures": result.failures,
    }


async def _candidate_id_for(application_id: UUID) -> UUID:
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise HTTPException(status_code=404, detail="Application not found")
        return app.candidate_id


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Pipeline alerts (from stall detector)
# ---------------------------------------------------------------------------


class AlertItem(BaseModel):
    id: UUID
    application_id: UUID
    alert_type: str
    stage: str | None
    hours_stuck: float | None
    details: dict[str, Any] | None
    created_at: datetime
    resolved_at: datetime | None


@router.get("/alerts", response_model=list[AlertItem])
async def pipeline_alerts(
    _: Annotated[str, Depends(require_viewer)],
    resolved: bool = Query(False, description="Include resolved alerts"),
    limit: int = Query(100, le=500),
) -> list[AlertItem]:
    """Active pipeline stall alerts from the stall detector."""
    async with session_scope() as session:
        q = select(PipelineAlert).order_by(desc(PipelineAlert.created_at)).limit(limit)
        if not resolved:
            q = q.where(PipelineAlert.resolved_at.is_(None))
        rows = (await session.scalars(q)).all()
        return [
            AlertItem(
                id=r.id,
                application_id=r.application_id,
                alert_type=r.alert_type,
                stage=r.stage,
                hours_stuck=r.hours_stuck,
                details=r.details,
                created_at=r.created_at,
                resolved_at=r.resolved_at,
            )
            for r in rows
        ]


@router.post("/alerts/{alert_id}/resolve", status_code=status.HTTP_200_OK)
async def resolve_alert(
    alert_id: UUID = Path(...),
    _: Annotated[str, Depends(require_recruiter)] = None,
) -> dict[str, Any]:
    """Manually resolve a pipeline alert."""
    async with session_scope() as session:
        alert = await session.get(PipelineAlert, alert_id)
        if alert is None:
            raise HTTPException(404, "Alert not found")
        alert.resolved_at = datetime.now(tz=UTC)
    return {"ok": True, "alert_id": str(alert_id)}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@router.get("/metrics/funnel")
async def funnel(
    _: Annotated[str, Depends(require_viewer)],
    since_days: int = 30,
    role_id: UUID | None = None,
) -> dict[str, Any]:
    async with session_scope() as session:
        counts = await metrics.funnel_counts(session, since_days=since_days, role_id=role_id)
    return {"since_days": since_days, "role_id": str(role_id) if role_id else None, "counts": counts}


@router.get("/metrics/overrides")
async def overrides(
    _: Annotated[str, Depends(require_viewer)],
    since_days: int = 30,
) -> dict[str, Any]:
    async with session_scope() as session:
        return await metrics.override_rate(session, since_days=since_days)


@router.get("/metrics/channels")
async def channels(
    _: Annotated[str, Depends(require_viewer)],
    since_days: int = 30,
) -> dict[str, Any]:
    async with session_scope() as session:
        return await metrics.channel_delivery_stats(session, since_days=since_days)


@router.get("/metrics/snapshot")
async def snapshot(
    _: Annotated[str, Depends(require_viewer)],
) -> dict[str, Any]:
    async with session_scope() as session:
        return await metrics.pipeline_snapshot(session)


# ---------------------------------------------------------------------------
# HR productivity endpoints (Tier 1): nudge, feedback, bulk-actions, resume re-upload
# ---------------------------------------------------------------------------


class NudgePayload(BaseModel):
    hr_email: EmailStr
    template: Literal["nudge", "screening_reminder", "interview_confirmation", "acknowledgement"] = (
        "nudge"
    )
    extra_note: str | None = Field(default=None, max_length=500)


@router.post("/applications/{application_id}/nudge", status_code=status.HTTP_202_ACCEPTED)
async def nudge_candidate(
    application_id: UUID = Path(...),
    payload: NudgePayload = ...,
    _: Annotated[str, Depends(require_recruiter)] = None,
) -> dict[str, Any]:
    """Send a templated reminder email to the candidate — one click from the dashboard."""
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise HTTPException(status_code=404, detail="Application not found")
        candidate = await session.get(Candidate, app.candidate_id)
        if candidate is None or not candidate.email:
            raise HTTPException(status_code=400, detail="Candidate has no email on file")
        role = await session.get(Role, app.role_id) if app.role_id else None
        role_title = role.title if role else "the role you applied for"

    result = await send_email(
        to=candidate.email,
        template=payload.template,
        variables={
            "candidate_name": candidate.name or "there",
            "role_title": role_title,
            "reference_id": str(application_id),
            "extra_note": payload.extra_note,
            "received_at": datetime.now(tz=UTC).strftime("%d %b %Y, %H:%M UTC"),
        },
        tags={"stage": "nudge", "actor": payload.hr_email},
    )

    async with session_scope() as session:
        await log_audit(
            session,
            action="nudge_sent" if result.success else "nudge_failed",
            actor=payload.hr_email,
            candidate_id=candidate.id,
            application_id=application_id,
            details={
                "template": payload.template,
                "provider": result.provider,
                "status_code": result.status_code,
                "error": result.error,
                "extra_note": payload.extra_note,
            },
        )

    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Email provider rejected the nudge: {result.error or 'unknown'}",
        )
    return {"ok": True, "application_id": str(application_id), "provider": result.provider}


class FeedbackPayload(BaseModel):
    hr_email: EmailStr
    recommendation: Literal["hire", "no_hire", "maybe", "next_round"]
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    overall_score: int | None = Field(default=None, ge=0, le=100)
    notes: str | None = Field(default=None, max_length=4000)


@router.post("/interviews/{interview_id}/feedback", status_code=status.HTTP_200_OK)
async def record_interview_feedback(
    interview_id: UUID = Path(...),
    payload: FeedbackPayload = ...,
    _: Annotated[str, Depends(require_recruiter)] = None,
) -> dict[str, Any]:
    """Capture post-interview feedback. Written to `interviews.feedback` +
    surfaces on the candidate timeline via audit_log."""
    async with session_scope() as session:
        interview = await session.get(Interview, interview_id)
        if interview is None:
            raise HTTPException(status_code=404, detail="Interview not found")

        feedback_blob = {
            "recommendation": payload.recommendation,
            "strengths": payload.strengths,
            "weaknesses": payload.weaknesses,
            "overall_score": payload.overall_score,
            "notes": payload.notes,
            "hr_email": payload.hr_email,
            "recorded_at": datetime.now(tz=UTC).isoformat(),
        }
        interview.feedback = feedback_blob
        if payload.recommendation in ("hire", "no_hire"):
            interview.status = "completed"

        await log_audit(
            session,
            action="interview_feedback_recorded",
            actor=payload.hr_email,
            application_id=interview.application_id,
            details=feedback_blob,
        )
    # Emit supervisor event for feedback received
    try:
        from src.services.typed_event_bus import EventType
        from src.services.typed_event_bus import publish_event as publish_supervisor_event
        async with session_scope() as sess:
            await publish_supervisor_event(
                sess,
                event_type=EventType.INTERVIEW_FEEDBACK_RECEIVED,
                application_id=interview.application_id,
                payload={
                    "interview_id": str(interview_id),
                    "recommendation": payload.recommendation,
                    "overall_score": payload.overall_score,
                    "reviewer": payload.hr_email,
                },
            )
    except Exception:
        logger.warning("failed to emit feedback event", exc_info=True)

    # Cross-reference feedback against evidence records
    try:
        from src.services.interview_intelligence import cross_reference_feedback
        discrepancies = await cross_reference_feedback(interview_id, feedback_blob)
        if discrepancies:
            logger.info(
                "feedback cross-ref found %d discrepancies for interview %s",
                len(discrepancies), interview_id,
            )
    except Exception:
        logger.warning("feedback cross-reference failed", exc_info=True)

    return {"ok": True, "interview_id": str(interview_id), "recommendation": payload.recommendation}


class BulkActionPayload(BaseModel):
    hr_email: EmailStr
    action: Literal["reject", "move_cold", "withdraw", "shortlist"]
    reason: str = Field(..., min_length=3, max_length=500)
    application_ids: list[UUID] = Field(..., min_length=1, max_length=200)


@router.post("/applications/bulk", status_code=status.HTTP_200_OK)
async def bulk_action(
    request: Request,
    payload: BulkActionPayload,
    _: Annotated[str, Depends(require_recruiter)] = None,
) -> dict[str, Any]:
    """Apply the same status change to up to 200 applications at once."""
    await enforce_rate_limit(request, "dashboard_write", limit=30, window_seconds=60)
    target_status = {
        "reject": ApplicationStatus.REJECTED.value,
        "move_cold": ApplicationStatus.COLD.value,
        "withdraw": ApplicationStatus.WITHDRAWN.value,
        "shortlist": ApplicationStatus.SHORTLISTED.value,
    }[payload.action]

    updated = 0
    failed: list[str] = []
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Application).where(Application.id.in_(payload.application_ids))
            )
        ).scalars().all()
        found_ids = {app.id for app in rows}
        for missing in set(payload.application_ids) - found_ids:
            failed.append(str(missing))

        for app in rows:
            previous = app.status
            app.status = target_status
            await log_audit(
                session,
                action=f"bulk_{payload.action}",
                actor=payload.hr_email,
                candidate_id=app.candidate_id,
                application_id=app.id,
                details={"reason": payload.reason, "previous_status": previous},
            )
            updated += 1

    # Best-effort signal the underlying workflows (not fatal if they're already done).
    if payload.action in ("reject", "withdraw"):
        signal_name = "reject" if payload.action == "reject" else "withdraw"
        for app_id in found_ids:
            await _signal_workflow(app_id, signal_name)

    return {
        "ok": True,
        "updated": updated,
        "failed_ids": failed,
        "action": payload.action,
        "new_status": target_status,
    }


@router.post("/candidates/{candidate_id}/resume", status_code=status.HTTP_202_ACCEPTED)
async def reupload_resume(
    candidate_id: UUID = Path(...),
    hr_email: Annotated[EmailStr, Form()] = ...,
    application_id: Annotated[UUID | None, Form()] = None,
    file: Annotated[UploadFile, File()] = ...,
    _: Annotated[str, Depends(require_recruiter)] = None,
) -> dict[str, Any]:
    """Upload / replace a candidate's resume and nudge the workflow forward.

    Use when Stage 3 parked in `awaiting_resume` because the original email
    had no attachment. Signals the CandidateJourneyWorkflow with `hr_override`
    = 'resume_uploaded' so it can retry parse + fit-score.
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    async with session_scope() as session:
        candidate = await session.get(Candidate, candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="Candidate not found")

        # Resolve which application to pivot. If caller didn't specify, pick the
        # most recent one that's still awaiting a resume.
        if application_id is None:
            row = (
                await session.execute(
                    select(Application)
                    .where(Application.candidate_id == candidate_id)
                    .order_by(desc(Application.created_at))
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail="No application for this candidate")
            application_id = row.id

    stored = await upload_resume(
        candidate_id=candidate_id,
        filename=file.filename or "resume.bin",
        content=content,
        content_type=file.content_type,
    )

    async with session_scope() as session:
        await log_audit(
            session,
            action="resume_reuploaded",
            actor=hr_email,
            candidate_id=candidate_id,
            application_id=application_id,
            details={
                "filename": file.filename,
                "r2_key": stored.key,
                "size_bytes": stored.size_bytes,
                "content_type": stored.content_type,
            },
        )

    signalled = await _signal_workflow(application_id, "resume_uploaded")
    return {
        "ok": True,
        "candidate_id": str(candidate_id),
        "application_id": str(application_id),
        "r2_key": stored.key,
        "workflow_signalled": signalled,
    }
