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
        # Fetch current candidate identity so parse_resume can use it as
        # fallback when the LLM fails to extract name/email from the PDF.
        fallback_email: str | None = None
        fallback_name: str | None = None
        try:
            async with session_scope() as session:
                cand = await session.get(Candidate, candidate_id)
                if cand:
                    fallback_email = cand.email
                    fallback_name = cand.name
        except Exception:
            pass
        try:
            parse_result = await run_parse_resume(
                ParseResumeInput(
                    candidate_id=candidate_id,
                    application_id=application_id,
                    r2_key=resume_r2_key,
                    filename=resume_filename or "resume.pdf",
                    fallback_email=fallback_email,
                    fallback_name=fallback_name,
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
    try:
        async with session_scope() as session:
            role = await session.get(Role, role_id)
            rubric = role.scoring_rubric if role else None
            agentic = (
                rubric.get("agentic", {})
                if isinstance(rubric, dict)
                else {}
            )
            # Voice is the only screening channel.
            voice_provider_on = bool(
                agentic.get("voice_screening_enabled")
            ) or _settings.enable_voice_screening
            voice_screen_enabled = voice_provider_on
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

    # Record the inline stages (email_filter + fit) as per-candidate verdicts so
    # they show in the stage view, then drive progression through the generic
    # stage-runner. The runner walks the ROLE's configured pipeline -- it does NOT
    # assume voice is next; a role can have any stage (or none) after fit.
    from src.services.stage_runner import (
        advance_candidate,
        record_email_filter_passed,
        record_fit_verdict,
    )
    from src.models.pipeline import StageVerdict

    await record_email_filter_passed(application_id)

    # Fit routes through the shared verdict (the stage-runner owns advance/park/
    # reject): green = pass (advance to the role's next stage), amber = needs_review
    # (park for HR -- never auto-dropped), red = reject. Comp/logistics are flags in
    # the score, never a reason to drop a candidate here.
    if fit_tier is not None:
        _fit_verdict = {
            FitTier.GREEN: StageVerdict.PASS,
            FitTier.AMBER: StageVerdict.NEEDS_REVIEW,
            FitTier.RED: StageVerdict.FAIL,
        }[fit_tier]
        try:
            async with session_scope() as session:
                await log_audit(
                    session,
                    application_id=application_id,
                    candidate_id=candidate_id,
                    action="fit_routed",
                    actor="agent",
                    details={"fit_tier": fit_tier.value, "verdict": _fit_verdict.value},
                )
            await advance_candidate(
                application_id=application_id,
                completed_stage_key="fit",
                verdict=_fit_verdict,
                result_ref={"fit_tier": fit_tier.value},
            )
        except Exception as e:  # noqa: BLE001
            await _log_error(application_id, candidate_id, "fit_route", e)
        return

    # If we land here, fit tier was not GREEN/AMBER (shouldn't happen after
    # RED auto-reject above, but guard anyway). Park for HR review.
    try:
        async with session_scope() as session:
            await log_audit(
                session,
                application_id=application_id,
                candidate_id=candidate_id,
                action="parked_voice_screen_unavailable",
                actor="agent",
                details={
                    "reason": "fit_tier_not_eligible",
                    "fit_tier": fit_tier.value if fit_tier else None,
                    "voice_screen_enabled": voice_screen_enabled,
                },
            )
            await set_stage(
                session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True
            )
    except Exception as e:  # noqa: BLE001
        await _log_error(application_id, candidate_id, "park_no_voice", e)


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

    # Score-only routing: the shared asymmetric band (services/evaluation.route_score,
    # applied inside advance_candidate) decides pass / needs_review / reject from the
    # fair overall_score. No self-consistency guard and no confidence gate -- the
    # prompt scores empty/weak answers low and the band sends borderline candidates
    # to a human instead of auto-dropping them. Comp/logistics stay flags only.
    from src.services.stage_runner import advance_candidate

    async with session_scope() as session:
        await log_audit(
            session,
            application_id=application_id,
            candidate_id=candidate_id,
            action="screening_routed",
            actor="agent",
            details={
                "overall_score": evaluation.overall_score,
                "red_flags": list(evaluation.red_flags) if evaluation.red_flags else [],
            },
        )

    await advance_candidate(
        application_id=application_id,
        completed_stage_key="screening",
        score=evaluation.overall_score,
        result_ref={"overall_score": evaluation.overall_score},
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

    parse_result = None
    try:
        parse_result = await parse_assignment(
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

    # Candidate submitted: route through the scored path when the parser produced
    # an overall_score so assignment can auto-reject (auto mode) or park-with-score
    # (manual mode) just like voice / meeting stages. When no score is available,
    # fall back to NEEDS_REVIEW park for HR.
    try:
        from src.services.stage_runner import complete_assignment_submission

        overall_score = (
            parse_result.overall_score
            if parse_result is not None
            else None
        )
        await complete_assignment_submission(
            application_id,
            overall_score=overall_score,
            result_ref={
                "stage": "assignment",
                "report": "ready",
                "overall_score": overall_score,
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("assignment advance failed for %s", application_id)
        await _log_error(application_id, candidate_id, "assignment_advance", e)

    # Set V1 legacy current_stage without overwriting the V2 stage_key
    # (advance_candidate already set it to the correct next stage).
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app:
            app.current_stage = PipelineStage.REPORT_READY.value

    # Notify candidate their application is under final review
    try:
        await notify_candidate_stage_change(application_id, "report_ready")
    except Exception:  # noqa: BLE001
        logger.warning("stage notification failed for %s", application_id)

    # Best-effort: email the technical panel (notification only; does not set stage).
    try:
        await _request_tech_panel_review(application_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("tech_panel_review request failed for %s", application_id)
        await _log_error(application_id, candidate_id, "tech_panel_review_request", e)


async def _request_tech_panel_review(application_id: UUID) -> None:
    """After report_ready, email the technical panel (notification only -- stage
    progression is owned by the stage-runner)."""
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
    # NB: stage progression is owned by the stage-runner (called from
    # run_assignment_processing); this function only sends the panel notification.
