"""V1 self-hosted candidate endpoints: GET/POST /apply/{token}.

Single JWT-scoped token routes to either screening submission or assignment
submission based on `action` claim + current stage. Frontend renders the
appropriate form.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Header,
    HTTPException,
    Request,
    status,
)
from src.services.request_rate_limit import enforce_rate_limit
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from src.config import get_settings
from src.db.base import Application, Candidate, Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.v1_application import set_stage
from src.models.v1 import (
    AssignmentSubmission,
    PipelineStage,
    ScreeningAnswer,
    ScreeningSubmission,
)
from src.config import get_settings
from src.pipeline.v1 import run_assignment_processing, run_screening_evaluation
from src.services.screening_url import InvalidScreeningToken, verify_apply_token

_settings = get_settings()

logger = logging.getLogger(__name__)
_settings = get_settings()

router = APIRouter(prefix="/apply", tags=["apply"])


class ApplyContext(BaseModel):
    application_id: UUID
    action: str
    role_title: str
    candidate_name: str | None
    current_stage: str
    already_submitted: bool = False
    # Only populated when action == "screening"
    questions: list[dict] | None = None
    # Only populated when action == "assignment"
    assignment_brief: str | None = None
    assignment_instructions: str | None = None
    deadline_days: int | None = None
    problem_doc_url: str | None = None
    problem_doc_filename: str | None = None
    expires_at: datetime


def _decode(token: str) -> Any:
    try:
        return verify_apply_token(token)
    except InvalidScreeningToken as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=f"invalid_token: {e}"
        ) from e


@router.get("/{token}", summary="Resolve token -> form context (HTML redirects to frontend)")
async def get_context(
    token: str,
    accept: Annotated[str | None, Header()] = None,
):
    """Browsers (Accept: text/html) get redirected to the React form on the
    frontend base URL. JSON callers (the React app itself) get the typed
    ``ApplyContext`` payload as before. This lets a single ngrok tunnel on
    the backend serve the candidate-facing email link without exposing raw
    JSON to the candidate.
    """
    if accept and "text/html" in accept.lower():
        target = f"{_settings.frontend_base_url.rstrip('/')}/apply/{token}"
        return RedirectResponse(url=target, status_code=302)

    claims = _decode(token)
    async with session_scope() as session:
        app = await session.get(Application, claims.application_id)
        if app is None:
            raise HTTPException(status_code=404, detail="application_not_found")
        role = await session.get(Role, app.role_id) if app.role_id else None
        candidate = await session.get(Candidate, app.candidate_id)

        questions = None
        if claims.action == "screening":
            qs = app.screening_questions or {}
            questions = qs.get("questions", []) if isinstance(qs, dict) else []

        # Problem doc is delivered as an email attachment in send_assignment_email;
        # apply page no longer exposes a download link. Keep filename for display.
        problem_doc_url: str | None = None
        problem_doc_filename: str | None = (
            role.assignment_problem_filename
            if claims.action == "assignment" and role and role.assignment_problem_doc_key
            else None
        )

        # Lock the form once the candidate has already submitted for this stage.
        POST_SCREENING = {
            PipelineStage.SCREENING_SUBMITTED.value,
            PipelineStage.SCREENING_EVALUATED.value,
            PipelineStage.ASSIGNMENT_SENT.value,
            PipelineStage.ASSIGNMENT_SUBMITTED.value,
            PipelineStage.REPORT_READY.value,
            PipelineStage.NEEDS_HR_REVIEW.value,
            PipelineStage.HIRED.value,
            PipelineStage.REJECTED.value,
        }
        POST_ASSIGNMENT = {
            PipelineStage.ASSIGNMENT_SUBMITTED.value,
            PipelineStage.REPORT_READY.value,
            PipelineStage.HIRED.value,
            PipelineStage.REJECTED.value,
        }
        already_submitted = False
        if claims.action == "screening" and app.current_stage in POST_SCREENING:
            already_submitted = True
        if claims.action == "assignment" and app.current_stage in POST_ASSIGNMENT:
            already_submitted = True

        return ApplyContext(
            application_id=app.id,
            action=claims.action,
            role_title=role.title if role else "Role",
            candidate_name=candidate.name if candidate else None,
            current_stage=app.current_stage,
            already_submitted=already_submitted,
            questions=questions,
            assignment_brief=role.assignment_brief if role else None,
            assignment_instructions=role.assignment_instructions if role else None,
            deadline_days=role.assignment_deadline_days if role else None,
            problem_doc_url=problem_doc_url,
            problem_doc_filename=problem_doc_filename,
            expires_at=claims.expires_at,
        )


class ScreeningSubmitBody(BaseModel):
    answers: list[ScreeningAnswer]
    timezone: str | None = None  # IANA tz from browser, used for slot picking


@router.post(
    "/{token}/screening",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit screening answers",
)
async def submit_screening(
    request: Request,
    token: str,
    body: Annotated[ScreeningSubmitBody, Body()],
    background: BackgroundTasks,
) -> dict:
    await enforce_rate_limit(request, "apply_screening", limit=10, window_seconds=60)
    claims = _decode(token)
    if claims.action != "screening":
        raise HTTPException(status_code=403, detail="token_action_mismatch")

    async with session_scope() as session:
        app = await session.get(Application, claims.application_id)
        if app is None:
            raise HTTPException(status_code=404, detail="application_not_found")
        if app.current_stage not in (
            PipelineStage.SCREENING_SENT.value,
            PipelineStage.APPLIED.value,
        ):
            raise HTTPException(
                status_code=409,
                detail=f"cannot_submit_in_stage_{app.current_stage}",
            )

        # Reject garbage submissions: every question must have a non-empty answer
        # of at least 4 chars. Prevents the evaluator from scoring air.
        questions = (app.screening_questions or {}).get("questions", []) if isinstance(
            app.screening_questions, dict
        ) else []
        answered_ids = {
            a.question_id for a in body.answers if (a.answer or "").strip()
        }
        required_ids = {q.get("id") for q in questions if q.get("required", True)}
        missing = required_ids - answered_ids
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"missing_answers_for_{len(missing)}_required_questions",
            )
        too_short = [
            a.question_id for a in body.answers
            if (a.answer or "").strip() and len((a.answer or "").strip()) < 4
        ]
        if too_short:
            raise HTTPException(
                status_code=422,
                detail=f"answers_too_short:{','.join(too_short)}",
            )

        submission = ScreeningSubmission(
            answers=body.answers, submitted_at=datetime.now(UTC)
        )
        # Capture candidate's timezone for downstream slot-picker.
        if body.timezone:
            cand = await session.get(Candidate, app.candidate_id)
            if cand is not None and not cand.timezone:
                cand.timezone = body.timezone[:64]
        # Stash answers into screening_questions JSONB so HR can see them too.
        # Assign a NEW dict so SQLAlchemy detects the JSONB change.
        existing = dict(app.screening_questions or {})
        existing["submission"] = submission.model_dump(mode="json")
        app.screening_questions = existing

        await set_stage(
            session, claims.application_id, PipelineStage.SCREENING_SUBMITTED
        )
        await log_audit(
            session,
            application_id=claims.application_id,
            candidate_id=app.candidate_id,
            action="screening_submitted",
            actor="candidate",
            details={"answer_count": len(body.answers)},
        )

    background.add_task(
        run_screening_evaluation,
        application_id=claims.application_id,
        submission=submission,
    )
    return {"status": "accepted", "application_id": str(claims.application_id)}


class AssignmentSubmitBody(BaseModel):
    project_choice: str
    github_url: str
    loom_url: str
    deployed_url: str | None = None
    notes: str | None = None


@router.post(
    "/{token}/assignment",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit assignment (GitHub repo + Loom walkthrough + optional notes)",
)
async def submit_assignment(
    request: Request,
    token: str,
    body: Annotated[AssignmentSubmitBody, Body()],
    background: BackgroundTasks,
) -> dict:
    await enforce_rate_limit(request, "apply_assignment", limit=10, window_seconds=60)
    claims = _decode(token)
    if claims.action != "assignment":
        raise HTTPException(status_code=403, detail="token_action_mismatch")

    gh = body.github_url.strip()
    lm = body.loom_url.strip()
    dep = (body.deployed_url or "").strip() or None
    proj = (body.project_choice or "").strip()
    if not proj or len(proj) < 3:
        raise HTTPException(status_code=400, detail="project_choice_required")
    if not gh.lower().startswith("http"):
        raise HTTPException(status_code=400, detail="github_url_invalid")
    if not lm.lower().startswith("http"):
        raise HTTPException(status_code=400, detail="loom_url_invalid")
    if "github." not in gh.lower():
        raise HTTPException(status_code=400, detail="not_a_github_url")
    if "loom." not in lm.lower():
        raise HTTPException(status_code=400, detail="not_a_loom_url")
    if dep and not dep.lower().startswith("http"):
        raise HTTPException(status_code=400, detail="deployed_url_invalid")

    async with session_scope() as session:
        app = await session.get(Application, claims.application_id)
        if app is None:
            raise HTTPException(status_code=404, detail="application_not_found")
        if app.current_stage not in {
            PipelineStage.ASSIGNMENT_SENT.value,
            PipelineStage.ASSESSMENT_INVITED.value,
        }:
            raise HTTPException(
                status_code=409,
                detail=f"cannot_submit_in_stage_{app.current_stage}",
            )
        candidate_id = app.candidate_id
        is_agentic_path = app.current_stage == PipelineStage.ASSESSMENT_INVITED.value

    links = [f"github: {gh}", f"loom: {lm}"]
    if dep:
        links.append(f"deployed: {dep}")
    submission = AssignmentSubmission(
        files=[],
        links=links,
        notes=body.notes,
        submitted_at=datetime.now(UTC),
        project_choice=proj,
        deployed_url=dep,
    )

    async with session_scope() as session:
        app = await session.get(Application, claims.application_id)
        if app:
            app.assignment_submission = submission.model_dump(mode="json")
        if is_agentic_path:
            await set_stage(
                session, claims.application_id, PipelineStage.ASSESSMENT_COMPLETED
            )
            await set_stage(
                session, claims.application_id, PipelineStage.ASSESSMENT_PENDING_REVIEW
            )
        else:
            await set_stage(
                session, claims.application_id, PipelineStage.ASSIGNMENT_SUBMITTED
            )
        await log_audit(
            session,
            application_id=claims.application_id,
            candidate_id=candidate_id,
            action="assignment_submitted",
            actor="candidate",
            details={
                "github_url": gh,
                "loom_url": lm,
                "deployed_url": dep,
                "project_choice": proj,
                "agentic": is_agentic_path,
            },
        )
        await log_audit(
            session,
            application_id=claims.application_id,
            candidate_id=candidate_id,
            action="project_choice_confirmed",
            actor="candidate",
            details={"project_choice": proj},
        )

    if is_agentic_path:
        try:
            from src.services.admin_notify import notify_round_complete
            background.add_task(
                notify_round_complete,
                application_id=claims.application_id,
                round="assessment",
                analysis=None,
            )
        except Exception:  # noqa: BLE001
            pass
        # The candidate submitted the take-home: park the assignment stage for HR
        # review (or, on a legacy pipeline that still has a separate
        # assessment_review gate, pass through to that gate). The set_stage calls
        # above only write the legacy column; this advances the V2 cursor so
        # progression does not mis-plan. See complete_assignment_submission.
        from src.services.stage_runner import complete_assignment_submission
        background.add_task(
            complete_assignment_submission,
            claims.application_id,
            result_ref={"submitted": True, "agentic": True},
        )
    else:
        background.add_task(
            run_assignment_processing,
            application_id=claims.application_id,
            submission=submission,
        )
    return {"status": "accepted", "application_id": str(claims.application_id)}


# ---------------------------------------------------------------------------
# Data-subject request (DPDP / GDPR compliance scaffold)
# ---------------------------------------------------------------------------


class DSRRequestBody(BaseModel):
    action: str  # "delete" | "export"
    reason: str | None = None


@router.post(
    "/{token}/dsr",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Data-subject request: delete or export this candidate's data",
)
async def candidate_dsr(
    request: Request,
    token: str,
    body: Annotated[DSRRequestBody, Body()],
) -> dict:
    await enforce_rate_limit(request, "apply_dsr", limit=3, window_seconds=300)
    claims = _decode(token)
    if body.action not in {"delete", "export"}:
        raise HTTPException(status_code=400, detail="action must be delete|export")
    async with session_scope() as session:
        app = await session.get(Application, claims.application_id)
        if app is None:
            raise HTTPException(status_code=404, detail="application_not_found")
        await log_audit(
            session,
            application_id=claims.application_id,
            candidate_id=app.candidate_id,
            action=f"dsr_{body.action}_requested",
            actor="candidate",
            details={"reason": (body.reason or "")[:300]},
        )
    # Real fulfilment runs offline by HR after verification. We only log here.
    return {
        "status": "received",
        "ticket_id": str(claims.application_id),
        "next_step": (
            "HR will verify your identity and complete the request within 30 days. "
            "DPO contact: see policy."
        ),
    }
