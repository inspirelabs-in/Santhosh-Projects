"""V1 pipeline orchestrator (runs under FastAPI BackgroundTasks, no Temporal).

Each public coroutine is self-contained, idempotent where possible, and logs
its own audit rows. Stage transitions go through
`src.db.repositories.v1_application.set_stage` which enforces the DAG from
`src.models.v1.ALLOWED_TRANSITIONS`.

Failures are caught per stage and leave the application in its prior stage +
an audit_log row tagged with `action='pipeline_error'`. HR can re-trigger
manually from the dashboard.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select

from src.activities.fit_score import FitScoreInput, run_fit_score
from src.activities.parse_resume import ParseResumeInput, run_parse_resume
from src.activities.v1_evaluate_screening import evaluate_screening
from src.activities.v1_generate_screening import generate_screening_questions
from src.models.candidate import ApplicationStatus, FitTier
from src.activities.v1_journey_report import generate_journey_report
from src.activities.v1_parse_assignment import parse_assignment
from src.activities.v1_send_assignment import send_assignment_email
from src.channels.email import send_email
from src.config import get_settings
from src.db.base import Application, Candidate, CandidateProfileRow, Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.v1_application import set_stage
from src.services.auto_progress import auto_progress
from src.models.candidate import CandidateProfile
from src.models.v1 import (
    AssignmentSubmission,
    GeneratedScreeningSet,
    PipelineStage,
    ScreeningSubmission,
)
from src.services.screening_url import generate_apply_token
from src.services.fallback_manager import llm_fallback
from src.services.stage_notifier import notify_candidate_stage_change

logger = logging.getLogger(__name__)
_settings = get_settings()


async def _load_latest_profile(candidate_id: UUID) -> CandidateProfile | None:
    async with session_scope() as session:
        row = (
            await session.scalars(
                select(CandidateProfileRow)
                .where(CandidateProfileRow.candidate_id == candidate_id)
                .order_by(CandidateProfileRow.created_at.desc())
                .limit(1)
            )
        ).first()
        if row is None or not row.parsed_data:
            return None
        return CandidateProfile.model_validate(row.parsed_data)


async def _log_error(application_id: UUID, candidate_id: UUID | None, stage: str, err: Exception) -> None:
    async with session_scope() as session:
        await log_audit(
            session,
            application_id=application_id,
            candidate_id=candidate_id,
            action="pipeline_error",
            actor="agent",
            details={"stage": stage, "error": str(err)[:500]},
        )


# ---------------------------------------------------------------------------
# Stage: applied -> screening_sent
# Called as BackgroundTask right after POST /webhooks/careers-form.
# ---------------------------------------------------------------------------


async def run_apply_to_screening(
    *,
    application_id: UUID,
    candidate_id: UUID,
    role_id: UUID | None,
    resume_r2_key: str | None,
    resume_filename: str | None,
) -> None:
    """Parse resume -> generate screening questions -> email link. All-or-nothing.

    If any step fails the app stays in APPLIED and an error audit row is written.
    """
    if role_id is None:
        logger.warning("apply %s has no role_id; parking as needs_hr_review", application_id)
        try:
            async with session_scope() as session:
                await set_stage(session, application_id, PipelineStage.NEEDS_HR_REVIEW)
                await log_audit(
                    session,
                    application_id=application_id,
                    candidate_id=candidate_id,
                    action="parked_no_role",
                    actor="agent",
                )
        except Exception as e:  # noqa: BLE001
            await _log_error(application_id, candidate_id, "parked_no_role", e)
        return

    # 1. Parse resume (if uploaded). Capture surviving candidate id in case
    # the parser merged this placeholder into a prior candidate row.
    profile: CandidateProfile | None = None
    if resume_r2_key:
        try:
            parse_result = await run_parse_resume(
                ParseResumeInput(
                    candidate_id=candidate_id,
                    application_id=application_id,
                    r2_key=resume_r2_key,
                    filename=resume_filename or "resume.pdf",
                )
            )
            if parse_result and parse_result.candidate_id != candidate_id:
                logger.info(
                    "candidate %s merged into %s during parse_resume",
                    candidate_id, parse_result.candidate_id,
                )
                candidate_id = parse_result.candidate_id
            parse_succeeded = True
        except Exception as e:  # noqa: BLE001
            logger.exception("parse_resume failed for %s", application_id)
            await _log_error(application_id, candidate_id, "parse_resume", e)
            parse_succeeded = False
    else:
        # No resume attached. Body-only application. Treat as parse-success
        # so the downstream gate only fires on actual parse errors.
        parse_succeeded = True

    # 1a. Dedup active applications on (candidate_id, role_id). If the parser
    # merged this row into an existing candidate that already has an ACTIVE
    # application for the same role, withdraw the duplicate so we don't dial
    # twice / send two assignment links.
    if role_id is not None:
        try:
            async with session_scope() as session:
                from sqlalchemy import select as _select
                from src.db.base import Application as _AppRow

                rows = (
                    await session.execute(
                        _select(_AppRow)
                        .where(_AppRow.candidate_id == candidate_id)
                        .where(_AppRow.role_id == role_id)
                        .where(_AppRow.id != application_id)
                        .where(_AppRow.status == ApplicationStatus.ACTIVE.value)
                    )
                ).scalars().all()
                if rows:
                    # Keep the OLDEST active app; reject the new one as a
                    # duplicate so we don't dial twice / send two assignments.
                    oldest = min(rows, key=lambda r: r.created_at)
                    keep_id = oldest.id
                    await set_stage(
                        session,
                        application_id,
                        PipelineStage.REJECTED,
                        force=True,
                    )
                    await log_audit(
                        session,
                        application_id=application_id,
                        candidate_id=candidate_id,
                        action="dedup_duplicate_application_withdrawn",
                        actor="agent",
                        details={
                            "kept_application_id": str(keep_id),
                            "role_id": str(role_id),
                            "reason": "candidate already has active application for this role",
                        },
                    )
            if rows:
                return
        except Exception as e:  # noqa: BLE001
            await _log_error(application_id, candidate_id, "dedup_active_app", e)

    profile = await _load_latest_profile(candidate_id)
    # Parse-fail vs no-resume distinction: only park for HR when we actually
    # tried and failed. No-resume mails (body-only applications) get a minimal
    # profile and continue.
    if profile is None and resume_r2_key and not parse_succeeded:
        try:
            async with session_scope() as session:
                await set_stage(
                    session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True
                )
                await log_audit(
                    session,
                    application_id=application_id,
                    candidate_id=candidate_id,
                    action="parked_parse_failed",
                    actor="agent",
                    details={"reason": "resume parse failed; HR review required"},
                )
        except Exception as e:  # noqa: BLE001
            await _log_error(application_id, candidate_id, "park_parse_failed", e)
        return
    if profile is None:
        # No resume parsed; use a minimal profile so screening questions are
        # generic-but-still-tailored to the role.
        profile = CandidateProfile()

    # 1b. Resume vs JD fit-score + auto-shortlist branching.
    # If the role enables agentic auto-shortlist, run fit-score now so the
    # downstream branch (voice-screen vs written screening vs auto-reject)
    # has a tier to act on.
    fit_tier: FitTier | None = None
    voice_screen_enabled = False
    auto_shortlist = False
    screening_modality = "chat"
    try:
        async with session_scope() as session:
            role = await session.get(Role, role_id)
            rubric = role.scoring_rubric if role else None
            agentic = (
                rubric.get("agentic", {})
                if isinstance(rubric, dict)
                else {}
            )
            screening_modality = (role.screening_modality if role else "chat") or "chat"
            # Voice path only when role.screening_modality == 'voice' AND
            # voice provider configured. Chat-first roles never enter the
            # voice branch even if the global flag is on.
            voice_provider_on = bool(
                agentic.get("voice_screening_enabled")
            ) or _settings.enable_voice_screening
            voice_screen_enabled = screening_modality == "voice" and voice_provider_on
            auto_shortlist = bool(agentic.get("auto_shortlist", True))
        # fit_score is the resume-vs-JD validation gate. ALWAYS run it as the
        # first check so unqualified candidates never reach the voice agent.
        # RED auto-rejects, GREEN/AMBER continues. Skips only when the role
        # explicitly disables auto_shortlist (HR wants every applicant to be
        # screened manually).
        if auto_shortlist:
            fit_out = await run_fit_score(
                FitScoreInput(
                    candidate_id=candidate_id,
                    application_id=application_id,
                )
            )
            fit_tier = fit_out.tier
    except Exception as e:  # noqa: BLE001
        logger.exception("fit_score failed for %s", application_id)
        await _log_error(application_id, candidate_id, "fit_score", e)
        await llm_fallback(application_id, candidate_id, "fit_score", str(e))
        return  # parked for HR review; don't proceed with broken state

    # Auto-reject reds.
    if fit_tier == FitTier.RED:
        try:
            async with session_scope() as session:
                await set_stage(session, application_id, PipelineStage.REJECTED, force=True)
                await log_audit(
                    session,
                    application_id=application_id,
                    candidate_id=candidate_id,
                    action="auto_rejected_fit",
                    actor="agent",
                    details={"reason": "fit_tier=red"},
                )
        except Exception as e:  # noqa: BLE001
            await _log_error(application_id, candidate_id, "auto_reject_fit", e)
        return

    # Voice-screen path: green/amber + provider enabled -> agent calls candidate.
    # auto_progress on APPLIED stage will dispatch the voice screening activity,
    # which itself sets VOICE_SCREEN_SCHEDULED.
    if fit_tier in {FitTier.GREEN, FitTier.AMBER} and voice_screen_enabled:
        try:
            async with session_scope() as session:
                await log_audit(
                    session,
                    application_id=application_id,
                    candidate_id=candidate_id,
                    action="auto_shortlisted_voice",
                    actor="agent",
                    details={"fit_tier": fit_tier.value},
                )
            await auto_progress(application_id=application_id)
        except Exception as e:  # noqa: BLE001
            await _log_error(application_id, candidate_id, "auto_shortlist_voice", e)
        return

    # Voice screening is the configured screening modality. If we land here it
    # means voice screening is disabled OR fit_score failed/unknown. Park for
    # HR rather than fall back to written email-screening (per product flow:
    # screening always happens via agent phone call).
    if voice_screen_enabled:
        try:
            async with session_scope() as session:
                await log_audit(
                    session,
                    application_id=application_id,
                    candidate_id=candidate_id,
                    action="parked_voice_screen_unavailable",
                    actor="agent",
                    details={
                        "reason": "fit_tier_missing_or_not_eligible",
                        "fit_tier": fit_tier.value if fit_tier else None,
                    },
                )
                await set_stage(
                    session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True
                )
        except Exception as e:  # noqa: BLE001
            await _log_error(application_id, candidate_id, "park_no_voice", e)
        return

    # V2 chat-first screening path. Used when voice screening is disabled.
    # Pre-warms the agent (tailored questions + assignment skeleton) and
    # emails the candidate a single chat link.
    try:
        from src.pipeline.chat_invite import run_apply_to_chat

        await run_apply_to_chat(
            application_id=application_id,
            candidate_id=candidate_id,
            role_id=role_id,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("chat invite failed for %s", application_id)
        await _log_error(application_id, candidate_id, "send_chat_invite", e)


# ---------------------------------------------------------------------------
# Stage: screening_submitted -> screening_evaluated -> (assignment_sent | needs_hr_review)
# Called as BackgroundTask after POST /apply/{token}/screening.
# ---------------------------------------------------------------------------


async def run_screening_evaluation(
    *,
    application_id: UUID,
    submission: ScreeningSubmission,
) -> None:
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            return
        candidate_id = app.candidate_id
        role_id = app.role_id
        questions_json = app.screening_questions or {}
        questions_list = questions_json.get("questions", []) if isinstance(questions_json, dict) else []

    profile = await _load_latest_profile(candidate_id) or CandidateProfile()

    try:
        evaluation = await evaluate_screening(
            application_id=application_id,
            candidate_id=candidate_id,
            role_id=role_id,  # type: ignore[arg-type]
            profile=profile,
            questions=questions_list,
            submission=submission,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("evaluate_screening failed for %s", application_id)
        await _log_error(application_id, candidate_id, "evaluate_screening", e)
        await llm_fallback(application_id, candidate_id, "evaluate_screening", str(e))
        return

    # Self-consistency guard: even if LLM returned clear_pass, downgrade to
    # needs_hr_review if the per-question signal doesn't support that verdict.
    # Protects against a hallucinated pass (e.g. all-empty submissions that
    # somehow slipped through the answer-validation layer in apply.py).
    low_relevance_count = sum(
        1 for pq in evaluation.per_question if pq.relevance == "low"
    )
    zero_score_count = sum(1 for pq in evaluation.per_question if pq.score == 0)
    forced_review = (
        evaluation.verdict == "clear_pass"
        and (
            low_relevance_count >= 2
            or zero_score_count >= 1
            or len(evaluation.red_flags) > 0
            or not (
                evaluation.logistics_check.ctc_in_range
                and evaluation.logistics_check.notice_acceptable
                and evaluation.logistics_check.location_workable
            )
        )
    )
    if forced_review:
        logger.info(
            "downgrading clear_pass to needs_hr_review for %s "
            "(low_rel=%d zero_score=%d red_flags=%d)",
            application_id, low_relevance_count, zero_score_count,
            len(evaluation.red_flags),
        )
        async with session_scope() as session:
            await log_audit(
                session,
                application_id=application_id,
                candidate_id=candidate_id,
                action="verdict_downgraded",
                actor="agent",
                details={
                    "original_verdict": evaluation.verdict,
                    "new_verdict": "needs_hr_review",
                    "low_relevance_count": low_relevance_count,
                    "zero_score_count": zero_score_count,
                    "red_flag_count": len(evaluation.red_flags),
                },
            )

    # V1 routing: clear_pass -> auto-progress engine first.
    # If the role has voice_screening_enabled, the agent calls the candidate
    # instead of sending the take-home assignment. Otherwise the V1 flow
    # ships the assignment as before.
    if evaluation.verdict == "clear_pass" and not forced_review:
        async with session_scope() as session:
            await set_stage(session, application_id, PipelineStage.SCREENING_EVALUATED)
        progressed = await auto_progress(application_id=application_id)
        if not progressed.startswith("noop"):
            return  # agent took over (fired/fallback/skipped); skip the V1 assignment branch
        try:
            await send_assignment_email(
                application_id=application_id,
                candidate_id=candidate_id,
                role_id=role_id,  # type: ignore[arg-type]
            )
            async with session_scope() as session:
                await set_stage(session, application_id, PipelineStage.ASSIGNMENT_SENT)
        except Exception as e:  # noqa: BLE001
            logger.exception("send_assignment failed for %s", application_id)
            await _log_error(application_id, candidate_id, "send_assignment", e)
            async with session_scope() as session:
                await set_stage(session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True)
    else:
        async with session_scope() as session:
            await set_stage(session, application_id, PipelineStage.SCREENING_EVALUATED)
            await set_stage(session, application_id, PipelineStage.NEEDS_HR_REVIEW)
            await log_audit(
                session,
                application_id=application_id,
                candidate_id=candidate_id,
                action="screening_routed_to_hr",
                actor="agent",
                details={
                    "verdict": evaluation.verdict,
                    "overall_score": evaluation.overall_score,
                    "reason": "did not clear_pass" if not forced_review else "forced_review",
                    "red_flags": [f.description for f in evaluation.red_flags] if evaluation.red_flags else [],
                },
            )


# ---------------------------------------------------------------------------
# Stage: assignment_submitted -> report_ready
# ---------------------------------------------------------------------------


async def run_assignment_processing(
    *,
    application_id: UUID,
    submission: AssignmentSubmission,
) -> None:
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            return
        candidate_id = app.candidate_id
        role_id = app.role_id

    try:
        await parse_assignment(
            application_id=application_id,
            candidate_id=candidate_id,
            role_id=role_id,  # type: ignore[arg-type]
            submission=submission,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("parse_assignment failed for %s", application_id)
        await _log_error(application_id, candidate_id, "parse_assignment", e)
        return

    try:
        await generate_journey_report(application_id=application_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("journey_report failed for %s", application_id)
        await _log_error(application_id, candidate_id, "journey_report", e)

    async with session_scope() as session:
        await set_stage(session, application_id, PipelineStage.REPORT_READY, force=True)

    # Notify candidate their application is under final review
    try:
        await notify_candidate_stage_change(application_id, "report_ready")
    except Exception:  # noqa: BLE001
        logger.warning("stage notification failed for %s", application_id)

    try:
        await _request_tech_panel_review(application_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("tech_panel_review request failed for %s", application_id)
        await _log_error(application_id, candidate_id, "tech_panel_review_request", e)


async def _request_tech_panel_review(application_id: UUID) -> None:
    """After report_ready, email the technical panel and move to TECHNICAL_PENDING_APPROVAL."""
    from src.db.base import PanelMember

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            return
        cand = await session.get(Candidate, app.candidate_id)
        role = await session.get(Role, app.role_id) if app.role_id else None

        rows = (
            await session.execute(
                select(PanelMember).where(
                    PanelMember.role_type == "technical",
                    PanelMember.is_active.is_(True),
                )
            )
        ).scalars().all()

        review_url = f"{_settings.frontend_base_url.rstrip('/')}/candidates/{application_id}"

        recipients_sent: list[str] = []
        for member in rows:
            try:
                await send_email(
                    to=member.email,
                    template="tech_review_request",
                    variables={
                        "candidate_name": cand.name if cand else None,
                        "candidate_email": cand.email if cand else None,
                        "role_title": role.title if role else None,
                        "fit_score": app.fit_score,
                        "fit_tier": app.fit_tier,
                        "application_id": str(application_id),
                        "review_url": review_url,
                        "panel_member_name": member.name,
                    },
                    tags={
                        "type": "tech_review_request",
                        "application_id": str(application_id),
                    },
                )
                recipients_sent.append(member.email)
            except Exception:  # noqa: BLE001
                logger.exception("tech_review_request email failed for %s", member.email)

        await log_audit(
            session,
            application_id=application_id,
            candidate_id=app.candidate_id,
            action="tech_review_requested",
            actor="agent",
            details={"recipients": recipients_sent, "count": len(recipients_sent)},
        )

        await set_stage(
            session,
            application_id,
            PipelineStage.TECHNICAL_PENDING_APPROVAL,
            force=True,
        )
