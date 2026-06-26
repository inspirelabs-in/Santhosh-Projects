"""Tool implementations for the recruiter agent.

Each tool is an async function returning a JSON-serialisable dict. The
agent's system prompt declares OpenAI-format tool schemas (see
``schemas.RECRUITER_TOOLS``); this module just provides the runtime.

Tools call the SAME repository helpers the dashboard endpoints use, so we
don't duplicate auth / business logic.
"""

from __future__ import annotations

import contextvars
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import desc, func, select

from src.db.base import (
    Application,
    AssignmentRow,
    AuditLog,
    Candidate,
    RecruiterMessage,
    Role,
)
from src.db.connection import session_scope
from src.llm.model_registry import Stage, model_for
from src.models.artifacts import RoleDraftContent

# Allows propose_role_draft to know which conversation it belongs to without
# changing the tool schema visible to the LLM.
_conversation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tool_conversation_id", default=None
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stage_label(s: str | None) -> str:
    return (s or "applied").replace("_", " ")


# ---------------------------------------------------------------------------
# list_candidates
# ---------------------------------------------------------------------------


async def list_candidates(
    *,
    stage: str | None = None,
    role_id: str | None = None,
    days_since_applied: int | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """List recent applications with candidate + role context.

    Filters: stage, role_id, days_since_applied. Returns up to ``limit``
    rows ordered by most recent.
    """
    limit = max(1, min(int(limit), 100))
    async with session_scope() as session:
        q = (
            select(Application, Candidate, Role)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .join(Role, Role.id == Application.role_id, isouter=True)
            .order_by(desc(Application.created_at))
            .limit(limit)
        )
        if stage:
            q = q.where(Application.current_stage == stage)
        if role_id:
            q = q.where(Application.role_id == UUID(role_id))
        if days_since_applied is not None:
            cutoff = datetime.now(tz=UTC) - timedelta(days=int(days_since_applied))
            q = q.where(Application.created_at >= cutoff)
        rows = (await session.execute(q)).all()

    items = [
        {
            "application_id": str(app.id),
            "candidate_name": cand.name,
            "candidate_email": cand.email,
            "role_title": role.title if role else "(no role)",
            "stage": app.current_stage,
            "stage_label": _stage_label(app.current_stage),
            "fit_tier": app.fit_tier,
            "fit_score": app.fit_score,
            "screening_score": app.screening_score,
            "applied_at": app.created_at.isoformat() if app.created_at else None,
        }
        for app, cand, role in rows
    ]
    return {"items": items, "count": len(items)}


# ---------------------------------------------------------------------------
# get_candidate
# ---------------------------------------------------------------------------


async def get_candidate(*, application_id: str) -> dict[str, Any]:
    """Full candidate detail: stage, screening summary, assignment, links."""
    app_id = UUID(application_id)
    async with session_scope() as session:
        app = await session.get(Application, app_id)
        if app is None:
            return {"error": "application_not_found"}
        cand = await session.get(Candidate, app.candidate_id)
        role = await session.get(Role, app.role_id) if app.role_id else None
        screening = None  # screening_answers table dropped (migration 0030)
        assignment = (
            await session.execute(
                select(AssignmentRow).where(AssignmentRow.application_id == app_id)
            )
        ).scalar_one_or_none()

    return {
        "application_id": str(app.id),
        "candidate": {
            "id": str(cand.id) if cand else None,
            "name": cand.name if cand else None,
            "email": cand.email if cand else None,
            "phone": cand.phone if cand else None,
            "linkedin": cand.linkedin_url if cand else None,
        },
        "role": {
            "id": str(role.id) if role else None,
            "title": role.title if role else None,
            "ctc_range": (
                f"{role.ctc_min_lpa}-{role.ctc_max_lpa} LPA"
                if role and role.ctc_min_lpa and role.ctc_max_lpa
                else None
            ),
            "location": role.location if role else None,
            "screening_modality": role.screening_modality if role else None,
        },
        "stage": app.current_stage,
        "fit": {"score": app.fit_score, "tier": app.fit_tier},
        "screening": (
            {
                "current_ctc_lpa": float(screening.current_ctc_lpa)
                if screening and screening.current_ctc_lpa is not None
                else None,
                "expected_ctc_lpa": float(screening.expected_ctc_lpa)
                if screening and screening.expected_ctc_lpa is not None
                else None,
                "notice_period_days": screening.notice_period_days if screening else None,
                "willing_to_relocate": screening.willing_to_relocate if screening else None,
                "score": screening.composite_score if screening else None,
                "knock_out": screening.knock_out_triggered if screening else False,
                "knock_out_reason": screening.knock_out_reason if screening else None,
                "tailored_q1": screening.tailored_q1 if screening else None,
                "tailored_a1": screening.tailored_a1 if screening else None,
                "tailored_q2": screening.tailored_q2 if screening else None,
                "tailored_a2": screening.tailored_a2 if screening else None,
            }
            if screening
            else None
        ),
        "assignment": (
            {
                "submitted_at": assignment.submitted_at.isoformat()
                if assignment and assignment.submitted_at
                else None,
                "submission_url": assignment.submission_url if assignment else None,
                "score": assignment.score if assignment else None,
            }
            if assignment
            else None
        ),
        "candidate_detail_url": f"/candidates/{app.candidate_id}",
    }


# ---------------------------------------------------------------------------
# list_roles
# ---------------------------------------------------------------------------


async def list_roles(*, status: str | None = None, limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    async with session_scope() as session:
        q = select(Role).order_by(desc(Role.created_at)).limit(limit)
        if status:
            q = q.where(Role.status == status)
        roles = (await session.execute(q)).scalars().all()
        # candidate count per role
        counts: dict[str, int] = {}
        for r in roles:
            n = (
                await session.execute(
                    select(func.count(Application.id)).where(Application.role_id == r.id)
                )
            ).scalar_one()
            counts[str(r.id)] = int(n or 0)

    return {
        "items": [
            {
                "id": str(r.id),
                "title": r.title,
                "status": r.status,
                "screening_modality": r.screening_modality,
                "ctc_range": (
                    f"{r.ctc_min_lpa}-{r.ctc_max_lpa} LPA"
                    if r.ctc_min_lpa and r.ctc_max_lpa
                    else None
                ),
                "location": r.location,
                "applicant_count": counts.get(str(r.id), 0),
                "role_url": f"/roles/{r.id}",
            }
            for r in roles
        ]
    }


# ---------------------------------------------------------------------------
# pipeline_metrics
# ---------------------------------------------------------------------------


async def pipeline_metrics() -> dict[str, Any]:
    """Aggregate counts per pipeline stage + last-7-day funnel."""
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Application.current_stage, func.count(Application.id))
                .group_by(Application.current_stage)
            )
        ).all()
        by_stage = {stage or "applied": int(n) for stage, n in rows}
        cutoff = datetime.now(tz=UTC) - timedelta(days=7)
        recent = (
            await session.execute(
                select(func.count(Application.id)).where(Application.created_at >= cutoff)
            )
        ).scalar_one()
    return {
        "by_stage": by_stage,
        "applied_last_7_days": int(recent or 0),
        "total_applications": sum(by_stage.values()),
    }


# ---------------------------------------------------------------------------
# stuck_applications
# ---------------------------------------------------------------------------


async def stuck_applications(*, hours: int = 48, limit: int = 25) -> dict[str, Any]:
    """Applications sitting in any non-terminal stage longer than ``hours``."""
    cutoff = datetime.now(tz=UTC) - timedelta(hours=int(hours))
    terminal = ("hired", "rejected")
    async with session_scope() as session:
        q = (
            select(Application, Candidate, Role)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .join(Role, Role.id == Application.role_id, isouter=True)
            .where(Application.updated_at <= cutoff)
            .where(Application.current_stage.notin_(terminal))
            .order_by(Application.updated_at.asc())
            .limit(limit)
        )
        rows = (await session.execute(q)).all()

    def _fmt_duration(total_hours: float) -> str:
        days = int(total_hours // 24)
        hrs = round(total_hours % 24, 1)
        if days > 0:
            return f"{days}d {hrs}h"
        return f"{hrs}h"

    return {
        "items": [
            {
                "application_id": str(app.id),
                "candidate_name": cand.name,
                "candidate_email": cand.email,
                "role_title": role.title if role else None,
                "stage": app.current_stage,
                "hours_in_stage": round(
                    (datetime.now(tz=UTC) - (app.updated_at or app.created_at)).total_seconds() / 3600, 1
                ),
                "time_in_stage": _fmt_duration(
                    (datetime.now(tz=UTC) - (app.updated_at or app.created_at)).total_seconds() / 3600
                ),
            }
            for app, cand, role in rows
        ]
    }


# ---------------------------------------------------------------------------
# audit_tail
# ---------------------------------------------------------------------------


async def audit_tail(
    *,
    application_id: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Recent audit-log rows. Useful for "what has the agent done lately?"."""
    limit = max(1, min(int(limit), 100))
    async with session_scope() as session:
        q = select(AuditLog).order_by(desc(AuditLog.created_at)).limit(limit)
        if application_id:
            q = q.where(AuditLog.application_id == UUID(application_id))
        rows = (await session.execute(q)).scalars().all()

    return {
        "items": [
            {
                "ts": r.created_at.isoformat() if r.created_at else None,
                "action": r.action,
                "actor": r.actor,
                "application_id": str(r.application_id) if r.application_id else None,
                "details": r.details,
            }
            for r in rows
        ]
    }


# ---------------------------------------------------------------------------
# Phase 1 -- write tools (RBAC-gated by the runner).
# ---------------------------------------------------------------------------


async def create_role_with_assignment(
    *,
    title: str,
    jd_text: str,
    n_problems: int = 2,
    ctc_min_lpa: float | None = None,
    ctc_max_lpa: float | None = None,
    location: str | None = None,
    remote_policy: str | None = None,
    max_notice_days: int | None = None,
    screening_modality: str = "voice",
    evaluation_spec: dict | None = None,
    company_context: dict | None = None,
    pipeline_template: list[str] | None = None,
    time_budget_hours: int = 6,
    deadline_days: int = 7,
    brief: str | None = None,
    _prerendered_brief: dict | None = None,
    **_ignored: Any,
) -> dict[str, Any]:
    """Create a role. Does NOT auto-generate a take-home assignment.

    Pulse never auto-generates assignments. If the recruiter explicitly
    supplied a ``brief``, it is persisted as text (no PDF render here). The
    actual assignment PDF is drafted separately via
    ``generate_assignment_for_role`` (or uploaded). If the seeded pipeline has
    an assignment stage and no problem doc exists yet, the role is held at
    ``status="draft"`` until a PDF is added; otherwise it goes ``"open"``.
    """
    role_resp = await create_role(
        title=title,
        jd_text=jd_text,
        ctc_min_lpa=ctc_min_lpa,
        ctc_max_lpa=ctc_max_lpa,
        location=location,
        remote_policy=remote_policy,
        max_notice_days=max_notice_days,
        screening_modality=screening_modality,
        evaluation_spec=evaluation_spec,
        company_context=company_context,
        pipeline_template=pipeline_template,
        auto_assignment=False,  # assignment is generated separately, not here
    )
    if "error" in role_resp:
        return role_resp
    role_id = role_resp["id"]

    # No auto-generation. Persist a recruiter-provided brief as text only, and
    # compute status: draft when an assignment stage exists but no PDF yet.
    _captured_brief = (brief or "").strip()
    async with session_scope() as session:
        role = await session.get(Role, UUID(role_id))
        if role is not None:
            if _captured_brief:
                role.assignment_brief = _captured_brief
            from src.db.repositories import role_pipeline_stage as stage_repo
            stages = await stage_repo.for_role(session, role.id, enabled_only=True)
            has_assignment_stage = any(
                (str(getattr(s, "stage_type", "")) == "assignment"
                 or getattr(s, "stage_key", "") == "assignment")
                for s in (stages or [])
            )
            has_problem_doc = bool(getattr(role, "assignment_problem_doc_key", None))
            role.status = (
                "draft" if (has_assignment_stage and not has_problem_doc) else "open"
            )
            computed_status = role.status
        else:
            computed_status = "open"

    if computed_status == "draft":
        message = (
            f"Created role '{title}'. It's held as a draft until a take-home "
            f"assignment PDF is added (upload one or ask me to draft it via "
            f"generate_assignment_for_role), then it goes live."
        )
    else:
        message = f"Created role '{title}'. It's open for applications."

    return {
        "ok": True,
        "role": role_resp,
        "role_id": role_id,
        "role_url": f"/roles/{role_id}",
        "status": computed_status,
        "message": message,
    }


async def generate_assignment_for_role(
    *,
    role_id: str,
    n_problems: int = 2,
    time_budget_hours: int = 6,
    deadline_days: int = 7,
    save: bool = False,
    prerendered: dict | None = None,
    user_brief: str | None = None,
) -> dict[str, Any]:
    """LLM-draft a take-home assignment with ``n_problems`` distinct problems
    tailored to the role's JD.

    When ``save=True``, persists the markdown brief on
    ``roles.assignment_brief`` (uses ``set_role_assignment_brief`` semantics).
    Default is preview-only so the confirm-card flow can show the draft to
    the user first.
    """
    rid = UUID(role_id)
    async with session_scope() as session:
        role = await session.get(Role, rid)
        if role is None:
            return {"error": "role_not_found"}

    # ROOT-CAUSE FIX (artifact-brief-discarded bug): the recruiter's saved brief is
    # the SOURCE OF TRUTH for what the assignment is about. On apply,
    # draft.assignment.brief — which captures BOTH what the recruiter typed in the
    # panel AND what they described in chat (jd_generation funnels chat →
    # draft.assignment.brief) — is persisted to role.assignment_brief.
    #
    # The LLM agent is an UNRELIABLE narrator for user_brief: in practice it passes a
    # hallucinated value (observed: user_brief="nodejs + typescript" instead of the
    # recruiter's actual problems), which silently replaced the real brief with
    # generic boilerplate. So when the role already carries a saved brief, it
    # OVERRIDES whatever the agent passed. The agent's user_brief is only used when
    # there is no saved brief yet (e.g. brand-new ideas described in chat).
    _existing_brief = (getattr(role, "assignment_brief", None) or "").strip()
    if _existing_brief:
        _agent_brief = (user_brief or "").strip()
        if _agent_brief and _agent_brief != _existing_brief:
            logger.warning(
                "generate_assignment_for_role: ignoring agent-passed user_brief=%r; "
                "grounding on the recruiter's saved role.assignment_brief instead (%s)",
                _agent_brief[:120], role_id,
            )
        user_brief = _existing_brief
    elif (user_brief or "").strip():
        logger.info(
            "generate_assignment_for_role: no saved brief; using agent user_brief for %s",
            role_id,
        )

    n = max(1, min(int(n_problems), 8))
    # Cached path: confirm-card prerender already produced a brief. Reuse it.
    if isinstance(prerendered, dict) and prerendered.get("problems"):
        payload = dict(prerendered)
        # Fall through to the save block below.
        try:
            return await _persist_assignment(
                rid=rid,
                role=role,
                payload=payload,
                deadline_days=deadline_days,
                save=save,
                role_id=role_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("persist prerendered assignment failed: %s", e)

    from src.agent.generators import gen_assignment

    # Pass evaluation_spec / company_context so the generator can ground the
    # problems in the role's eval dimensions + company context, and problem_count
    # so it produces the requested number directly. These are new optional params
    # on gen_assignment (added by the prompts worker); safe getattr fallbacks
    # because role.evaluation_spec / role.company_context may be None.
    try:
        brief = await gen_assignment(
            role_title=role.title,
            jd_text=role.jd_text or "",
            time_budget_hours=int(time_budget_hours),
            deadline_days=int(deadline_days),
            user_brief=user_brief,
            application_id=rid,  # role-scoped; reuse generator with role uuid
            candidate_id=rid,
            evaluation_spec=getattr(role, "evaluation_spec", None),
            company_context=getattr(role, "company_context", None),
            problem_count=n,
        )
    except Exception as e:  # noqa: BLE001
        return {"error": f"assignment_gen_failed: {e}"}

    payload = brief.model_dump()

    # Pad with synthesized follow-ups when more problems were requested than
    # produced — but NOT when grounded on the recruiter's brief: there the brief's
    # own list defines the count (e.g. "1 assignment" => exactly 1, no padding).
    if not _existing_brief and len(payload.get("problems", [])) < n:
        from src.llm.client import get_llm_client
        from src.agent.schemas import AssignmentBriefOut

        client = get_llm_client()
        try:
            extra = await client.complete(
                prompt=(
                    f"Existing assignment brief for role '{role.title}':\n"
                    f"{json.dumps(payload, ensure_ascii=False)[:6000]}\n\n"
                    f"Add {n - len(payload['problems'])} MORE distinct problems "
                    f"covering different facets of the role (system design, "
                    f"debugging, code quality, product sense, scaling). Match "
                    f"the same JSON shape and return the FULL revised brief "
                    f"with all {n} problems."
                ),
                response_model=AssignmentBriefOut,
                model=model_for(Stage.ASSIGNMENT_GEN),
                trace_name="recruiter.assignment_extend",
                prompt_version="v2",
                application_id=rid,
                candidate_id=rid,
                temperature=0.5,
                max_tokens=12000,
            )
            payload = extra.parsed.model_dump()
        except Exception as e:  # noqa: BLE001
            logger.warning("assignment_extend failed (non-fatal): %s", e)

    return await _persist_assignment(
        rid=rid,
        role=role,
        payload=payload,
        deadline_days=deadline_days,
        save=save,
        role_id=role_id,
    )


async def _persist_assignment(
    *,
    rid: UUID,
    role: Role,
    payload: dict[str, Any],
    deadline_days: int,
    save: bool,
    role_id: str,
) -> dict[str, Any]:
    pdf_key: str | None = None
    pdf_filename: str | None = None
    if save:
        # Render the brief into a polished PDF and upload to object store so
        # the assignment email can attach it verbatim. The candidate then
        # receives a multi-page document, not a bare markdown blob.
        try:
            from src.config import get_settings as _settings_fn
            from src.services.assignment_pdf import render_assignment_pdf
            from src.services.file_storage import upload_blob

            _s = _settings_fn()
            pdf_bytes = render_assignment_pdf(
                payload,
                company_name=(
                    getattr(_s, "voice_agent_company_name", None)
                    or "GrabOn"
                ),
                role_title=role.title,
            )
            safe_title = "".join(
                c if c.isalnum() else "-" for c in (role.title or "role")
            ).strip("-") or "role"
            pdf_filename = f"{safe_title}-challenge.pdf"
            pdf_key = f"assignments/{rid}/{pdf_filename}"
            await upload_blob(
                bucket=_s.r2_bucket_resumes,
                key=pdf_key,
                content=pdf_bytes,
                content_type="application/pdf",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("assignment PDF render/upload failed (non-fatal): %s", e)

        async with session_scope() as session:
            r = await session.get(Role, rid)
            if r is not None:
                # Keep assignment_brief in sync with what the recruiter typed: only
                # write the generated brief_md when there is no recruiter brief. The
                # expanded text still goes into the PDF; the brief stays verbatim.
                if not (r.assignment_brief or "").strip():
                    r.assignment_brief = payload["brief_md"]
                if not (r.assignment_instructions or "").strip():
                    r.assignment_instructions = (payload.get("submission_format") or {}).get("instructions")
                r.assignment_deadline_days = (payload.get("submission_format") or {}).get("deadline_days") or deadline_days
                if pdf_key:
                    r.assignment_problem_doc_key = pdf_key
                    r.assignment_problem_filename = pdf_filename
                # Flip paused/draft → open now that the assignment is persisted.
                if r.status in ("draft", "paused"):
                    r.status = "open"

    return {
        "ok": True,
        "role_id": role_id,
        "role_title": role.title,
        "saved": save,
        "brief_md": payload.get("brief_md"),
        "problem_count": len(payload.get("problems", [])),
        "problems": payload.get("problems"),
        "submission_format": payload.get("submission_format"),
        "evaluation_rubric": payload.get("evaluation_rubric"),
        "pdf_attached": bool(pdf_key),
        "pdf_filename": pdf_filename,
    }


async def ensure_role_assignment(
    *,
    role_id: str,
    n_problems: int = 2,
    time_budget_hours: int = 6,
    deadline_days: int = 7,
    source: str = "create",
    attempts: int = 2,
    user_brief: str | None = None,
) -> dict[str, Any]:
    """Generate + persist a take-home for a role *reliably*.

    Wraps ``generate_assignment_for_role(save=True)`` with a retry and a durable
    audit trail on BOTH success (``assignment_generated``) and failure
    (``assignment_generation_failed``). This closes the silent-failure hole that
    left roles brief-less: ``generate_assignment_for_role`` returns
    ``{"error": ...}`` (it does not raise) on an LLM hiccup, so callers that only
    wrapped it in try/except never noticed, and dispatch_assessment then skipped
    the candidate email. Never raises; returns ``{"ok": bool, "error"?: str, ...}``.
    """
    from src.db.connection import session_scope
    from src.db.repositories.audit import log_audit

    # Never generate over a brief the team already provided. If the role already
    # carries an assignment_brief (the company pasted/uploaded their own, captured
    # during scoping), that IS the take-home -- skip generation entirely.
    async with session_scope() as session:
        _role = await session.get(Role, UUID(role_id))
        _existing_brief = (getattr(_role, "assignment_brief", None) or "").strip() if _role else ""
    if _existing_brief:
        logger.info("ensure_role_assignment: role %s already has a company brief, skipping generation", role_id)
        return {"ok": True, "role_id": role_id, "brief_md": _existing_brief, "source": "company_provided", "skipped": True}

    last_err: str | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            res = await generate_assignment_for_role(
                role_id=role_id,
                n_problems=n_problems,
                time_budget_hours=time_budget_hours,
                deadline_days=deadline_days,
                save=True,
                user_brief=user_brief,
            )
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            logger.warning("ensure_role_assignment attempt %d failed for %s: %s", attempt, role_id, e)
            continue
        if res.get("ok") and res.get("brief_md"):
            async with session_scope() as session:
                await log_audit(
                    session,
                    action="assignment_generated",
                    actor="agent",
                    details={
                        "role_id": role_id,
                        "source": source,
                        "attempt": attempt,
                        "problem_count": res.get("problem_count"),
                        "pdf_attached": res.get("pdf_attached"),
                    },
                )
            return res
        last_err = str(res.get("error") or "no_brief_returned")
        logger.warning(
            "ensure_role_assignment attempt %d for %s returned no brief: %s",
            attempt, role_id, last_err,
        )

    async with session_scope() as session:
        await log_audit(
            session,
            action="assignment_generation_failed",
            actor="agent",
            details={"role_id": role_id, "source": source, "error": (last_err or "unknown")[:300]},
        )
    return {"ok": False, "role_id": role_id, "error": f"assignment_generation_failed: {last_err}"}


# ---------------------------------------------------------------------------
# LinkedIn drafting + publishing
# ---------------------------------------------------------------------------


async def draft_linkedin_post(
    *,
    role_id: str | None = None,
    role_title: str | None = None,
    angle: str | None = None,
    apply_url: str | None = None,
) -> dict[str, Any]:
    """Return the role's JD reformatted as a plain-text LinkedIn post.

    The JD is already the vibey post; no LLM regeneration is needed.
    ``role_id`` is required (we need the saved JD text). If only
    ``role_title`` is provided without a ``role_id``, there is no JD to
    reuse and the function returns an error.
    ``angle`` and ``apply_url`` are accepted for forward-compatibility but
    are not used when the JD already contains the apply block.
    """
    if not role_id and not role_title:
        return {"error": "role_id or role_title required"}

    if not role_id:
        return {"error": "role_id required to reuse the role JD as the post"}

    from src.services.linkedin_format import jd_to_linkedin_text

    async with session_scope() as session:
        role = await session.get(Role, UUID(role_id))
        if role is None:
            return {"error": "role_not_found"}
        title = role.title
        jd = role.jd_text or ""

    text = jd_to_linkedin_text(jd)

    return {
        "ok": True,
        "role_id": role_id,
        "role_title": title,
        "text": text,
        "char_count": len(text),
        "hashtags": [],
        "apply_url": apply_url or "",
    }


def uuid_zero() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000000")


async def publish_linkedin_post(
    *,
    text: str,
    visibility: str = "PUBLIC",
) -> dict[str, Any]:
    """POST a UGC text post to LinkedIn on the configured author URN.

    Requires (in env or config_settings):
        LINKEDIN_ACCESS_TOKEN   -- OAuth2 user-access-token with `w_member_social` scope
        LINKEDIN_AUTHOR_URN     -- e.g. ``urn:li:person:abc123`` (or ``urn:li:organization:...``)

    Use the LinkedIn ``/rest/posts`` API (v2, X-Restli-Protocol-Version 2.0.0).
    """
    import os as _os
    import httpx

    from src.config import get_settings
    settings = get_settings()
    token = (
        getattr(settings, "linkedin_access_token", None)
        or _os.environ.get("LINKEDIN_ACCESS_TOKEN")
    )
    author = (
        getattr(settings, "linkedin_author_urn", None)
        or _os.environ.get("LINKEDIN_AUTHOR_URN")
    )
    if not token or not author:
        return {
            "error": "linkedin_not_configured",
            "missing": [
                k for k in ("LINKEDIN_ACCESS_TOKEN", "LINKEDIN_AUTHOR_URN")
                if not (token if k == "LINKEDIN_ACCESS_TOKEN" else author)
            ],
            "setup_hint": (
                "Create a LinkedIn Developer app with 'Share on LinkedIn' product, "
                "complete OAuth 2.0 (3-legged) to obtain a member access token + "
                "the user's URN, then set LINKEDIN_ACCESS_TOKEN and "
                "LINKEDIN_AUTHOR_URN in backend/.env. See docs/linkedin-setup.md."
            ),
        }

    body = {
        "author": author,
        "commentary": text,
        "visibility": visibility,
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": "202501",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post("https://api.linkedin.com/rest/posts", json=body, headers=headers)
    except httpx.HTTPError as e:
        return {"error": f"network_error: {e}"}

    if r.status_code in (200, 201):
        post_id = r.headers.get("x-restli-id") or r.headers.get("X-Restli-Id")
        return {
            "ok": True,
            "post_id": post_id,
            "url": f"https://www.linkedin.com/feed/update/{post_id}" if post_id else None,
            "message": "Posted to LinkedIn.",
        }
    return {
        "error": f"linkedin_api_{r.status_code}",
        "body": r.text[:500],
    }


async def smart_defaults_for_role(
    *,
    title: str,
) -> dict[str, Any]:
    """Suggest role defaults based on similar existing roles + seniority hints.

    Returns a JSON block with ``ctc_min_lpa``, ``ctc_max_lpa``, ``location``,
    ``remote_policy``, ``max_notice_days``, ``screening_modality``,
    ``jd_skeleton`` -- enough that Pulse can call ``create_role`` directly
    without asking the user.
    """
    title_lc = (title or "").lower()

    # Seniority -> CTC band fallback.
    if any(k in title_lc for k in ("intern", "trainee")):
        ctc_min, ctc_max = 5.0, 8.0
        seniority = "intern"
    elif any(k in title_lc for k in ("junior", "associate", "i ", "ii ")):
        ctc_min, ctc_max = 8.0, 15.0
        seniority = "junior"
    elif any(k in title_lc for k in ("staff", "principal", "lead", "architect")):
        ctc_min, ctc_max = 35.0, 60.0
        seniority = "staff"
    elif any(k in title_lc for k in ("vp", "head", "director")):
        ctc_min, ctc_max = 50.0, 90.0
        seniority = "leadership"
    elif "senior" in title_lc or "sr." in title_lc or "sr " in title_lc:
        ctc_min, ctc_max = 20.0, 40.0
        seniority = "senior"
    else:
        ctc_min, ctc_max = 12.0, 25.0
        seniority = "mid"

    # Find a similar existing role to copy concrete defaults from.
    similar: dict[str, Any] | None = None
    async with session_scope() as session:
        # Loose match on any keyword in title.
        keywords = [w for w in title_lc.split() if len(w) > 3]
        for kw in keywords:
            row = (
                await session.execute(
                    select(Role).where(Role.title.ilike(f"%{kw}%")).limit(1)
                )
            ).scalar_one_or_none()
            if row is not None:
                similar = {
                    "title": row.title,
                    "location": row.location,
                    "remote_policy": row.remote_policy,
                    "max_notice_days": row.max_notice_days,
                    "screening_modality": row.screening_modality,
                    "ctc_min_lpa": float(row.ctc_min_lpa) if row.ctc_min_lpa else None,
                    "ctc_max_lpa": float(row.ctc_max_lpa) if row.ctc_max_lpa else None,
                }
                break

    out = {
        "title": title,
        "seniority": seniority,
        "ctc_min_lpa": (similar or {}).get("ctc_min_lpa") or ctc_min,
        "ctc_max_lpa": (similar or {}).get("ctc_max_lpa") or ctc_max,
        "location": (similar or {}).get("location") or "Hyderabad",
        "remote_policy": (similar or {}).get("remote_policy") or "hybrid",
        "max_notice_days": (similar or {}).get("max_notice_days") or 60,
        "screening_modality": (similar or {}).get("screening_modality") or "voice",
        "similar_role": similar,
        "jd_skeleton": _jd_skeleton(title, seniority),
    }
    return out


def _jd_skeleton(title: str, seniority: str) -> str:
    """Stock JD scaffold Pulse can rewrite + extend before calling create_role."""
    return (
        f"## About the role\n"
        f"We're hiring a {seniority}-level {title} to own meaningful surface area "
        f"on our engineering / product team and ship work that customers feel.\n\n"
        f"## You'll\n"
        f"- Own the design, build, and rollout of features end-to-end\n"
        f"- Partner with PM, design, and ops to translate goals into shipped surface\n"
        f"- Improve reliability + observability of systems you touch\n"
        f"- Mentor teammates / set bar via reviews\n\n"
        f"## We're looking for\n"
        f"- Strong fundamentals in your stack (data structures, design, debugging)\n"
        f"- Track record shipping features that moved a metric\n"
        f"- Good written + verbal communication\n"
        f"- Bias to action and ownership\n\n"
        f"## Nice to have\n"
        f"- Experience scaling systems past 100k users\n"
        f"- Open-source contributions or side projects\n"
    )


async def search_talent_pool(
    *,
    query: str,
    role_id: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Semantic search across the talent pool using pgvector embeddings.

    When ``role_id`` is given, matches candidates against the role's JD
    embedding instead of the free-text query.
    """
    import litellm
    from src.config import get_settings
    from src.db.base import CandidateProfileRow, Candidate, Application

    _s = get_settings()
    limit = max(1, min(int(limit), 50))

    if role_id:
        rid = UUID(role_id)
        async with session_scope() as session:
            role = await session.get(Role, rid)
            if role is None:
                return {"error": "role_not_found"}
            query = role.jd_text or role.title or ""

    if not (query or "").strip():
        return {"matches": [], "total": 0}

    try:
        embed_resp = await litellm.aembedding(
            model=_s.embedding_model or "text-embedding-3-large",
            input=[query[:8000]],
        )
        query_embedding = embed_resp.data[0]["embedding"]
    except Exception as e:
        return {"error": f"embedding_failed: {e}"}

    async with session_scope() as session:
        distance_expr = CandidateProfileRow.embedding.cosine_distance(query_embedding)
        similarity_expr = (1 - distance_expr).label("similarity")

        q = (
            select(CandidateProfileRow, Candidate, similarity_expr)
            .join(Candidate, Candidate.id == CandidateProfileRow.candidate_id)
            .where(CandidateProfileRow.embedding.isnot(None))
            .order_by(distance_expr.asc())
            .limit(limit)
        )
        rows = (await session.execute(q)).all()

        candidate_ids = [r[1].id for r in rows]
        app_map: dict[UUID, str] = {}
        if candidate_ids:
            app_rows = (
                await session.execute(
                    select(Application)
                    .where(Application.candidate_id.in_(candidate_ids))
                    .order_by(Application.created_at.desc())
                )
            ).scalars().all()
            for a in app_rows:
                if a.candidate_id not in app_map:
                    app_map[a.candidate_id] = a.current_stage

    matches = []
    for profile, cand, sim in rows:
        pd = profile.parsed_data or {}
        skills = pd.get("skills") or pd.get("top_skills") or []
        if isinstance(skills, str):
            skills = [s.strip() for s in skills.split(",")]
        work = pd.get("work_history") or pd.get("experience") or []
        cur_title = cur_company = None
        if isinstance(work, list) and work and isinstance(work[0], dict):
            cur_title = work[0].get("title") or work[0].get("designation")
            cur_company = work[0].get("company") or work[0].get("organization")
        exp = pd.get("total_experience_years") or pd.get("experience_years")

        matches.append({
            "candidate_id": str(cand.id),
            "candidate_name": cand.name,
            "candidate_email": cand.email,
            "current_title": cur_title,
            "current_company": cur_company,
            "top_skills": (skills[:8] if isinstance(skills, list) else []),
            "location": pd.get("location") or pd.get("city"),
            "experience_years": float(exp) if exp else None,
            "similarity": round(float(sim), 3),
            "last_application_stage": app_map.get(cand.id),
        })

    return {"matches": matches, "total": len(matches)}


async def search_candidates(
    *,
    query: str,
    limit: int = 25,
) -> dict[str, Any]:
    """Free-text search over candidate name / email + role title.

    Falls back to ILIKE since the existing schema doesn't have FTS yet.
    """
    limit = max(1, min(int(limit), 100))
    needle = f"%{(query or '').strip()}%"
    if not needle.strip("%"):
        return {"items": [], "count": 0}
    async with session_scope() as session:
        q = (
            select(Application, Candidate, Role)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .join(Role, Role.id == Application.role_id, isouter=True)
            .where(
                (Candidate.name.ilike(needle))
                | (Candidate.email.ilike(needle))
                | (Role.title.ilike(needle))
            )
            .order_by(desc(Application.created_at))
            .limit(limit)
        )
        rows = (await session.execute(q)).all()
    items = [
        {
            "application_id": str(app.id),
            "candidate_name": cand.name,
            "candidate_email": cand.email,
            "role_title": role.title if role else None,
            "stage": app.current_stage,
            "stage_label": _stage_label(app.current_stage),
            "fit_score": app.fit_score,
        }
        for app, cand, role in rows
    ]
    return {"items": items, "count": len(items)}


async def create_role(
    *,
    title: str,
    jd_text: str,
    ctc_min_lpa: float | None = None,
    ctc_max_lpa: float | None = None,
    location: str | None = None,
    remote_policy: str | None = None,
    max_notice_days: int | None = None,
    screening_modality: str = "voice",
    evaluation_spec: dict | None = None,
    company_context: dict | None = None,
    pipeline_template: list[str] | None = None,
    auto_assignment: bool = True,
    **_extra: Any,
) -> dict[str, Any]:
    """Persist a new role. Returns the created row's id + URL.

    This function does NOT generate a take-home assignment. The assignment is
    created separately via ``generate_assignment_for_role`` (or the recruiter
    uploads a PDF). The ``auto_assignment`` parameter is retained for backward
    compatibility but has no effect — no generation happens here.
    """
    title = (title or "").strip()
    jd_text = (jd_text or "").strip()
    if len(title) < 3:
        return {"error": "title_too_short"}
    if len(jd_text) < 30:
        return {"error": "jd_text_too_short (>=30 chars)"}
    resolved_template = None
    if pipeline_template:
        if isinstance(pipeline_template, list) and len(pipeline_template) == 1:
            from src.services.pipeline_templates import PRESETS
            preset = PRESETS.get(pipeline_template[0])
            if preset:
                resolved_template = preset["steps"]
        if not resolved_template:
            from src.services.pipeline_templates import validate_template
            errors = validate_template(pipeline_template)
            if errors:
                return {"error": f"invalid pipeline_template: {', '.join(errors)}"}
            resolved_template = pipeline_template
    async with session_scope() as session:
        role = Role(
            title=title,
            jd_text=jd_text,
            screening_questions=[],
            scoring_rubric={},
            interviewer_panel=[],
            ctc_min_lpa=ctc_min_lpa,
            ctc_max_lpa=ctc_max_lpa,
            location=location,
            remote_policy=remote_policy,
            max_notice_days=max_notice_days,
            screening_modality=screening_modality,
            evaluation_spec=evaluation_spec,
            company_context=company_context,
            pipeline_template=resolved_template,
            status="open",
        )
        session.add(role)
        await session.flush()
        role_id = role.id

        # Seed role_pipeline_stages (the real pipeline source of truth).
        from src.db.repositories import role_pipeline_stage as stage_repo

        if resolved_template:
            from src.services.pipeline_templates import STEP_REGISTRY
            from src.db.base import RolePipelineStage

            _STEP_TYPE = {
                "fit_score": "fit", "voice_screen": "voice_screen",
                "assignment": "assignment",
                # [TODO] cognitive_test is a future EXTERNAL TEST LINK (e.g. a
                # cognitive/aptitude test) the candidate does ALONGSIDE the
                # take-home assignment -- not yet implemented. Until it is, route
                # it as a manual review gate so it never misfires the take-home
                # dispatcher (it previously mapped to "assignment" and silently
                # skipped). When built, send the link with the assignment email.
                "cognitive_test": "assessment_review",
                "technical_interview": "interview", "hiring_manager": "interview",
                "ceo_interview": "interview", "hr_interview": "interview",
                "panel_interview": "interview", "bar_raiser": "interview",
                "reference_check": "decision", "background_check": "decision",
                "offer": "offer",
            }
            _STEP_KEY = {
                "fit_score": "fit", "voice_screen": "voice_screen",
                "assignment": "assignment", "cognitive_test": "cognitive_test",
                "technical_interview": "technical", "hiring_manager": "technical",
                "ceo_interview": "ceo", "hr_interview": "hr",
                "panel_interview": "panel", "bar_raiser": "bar_raiser",
                "reference_check": "reference_check",
                "background_check": "background_check",
                "offer": "offer",
            }
            for pos, step_id in enumerate(resolved_template):
                step_def = STEP_REGISTRY.get(step_id)
                label = step_def.label if step_def else step_id
                stype = _STEP_TYPE.get(step_id, "interview")
                skey = _STEP_KEY.get(step_id, step_id)
                mode = (
                    "auto"
                    if stype in ("fit", "voice_screen", "assignment", "offer")
                    else "manual"
                )
                session.add(
                    RolePipelineStage(
                        role_id=role_id,
                        position=pos,
                        stage_type=stype,
                        stage_key=skey,
                        label=label,
                        mode=mode,
                    )
                )
            await session.flush()
        else:
            await stage_repo.seed_default(session, role_id=role_id)

    return {
        "ok": True,
        "id": str(role_id),
        "role_id": str(role_id),
        "title": title,
        "url": f"/roles/{role_id}",
        "message": f"Role '{title}' created. role_id: {role_id} (use this as role_id for assignment generation or updates).",
    }


async def update_role(
    *,
    role_id: str,
    title: str | None = None,
    jd_text: str | None = None,
    ctc_min_lpa: float | None = None,
    ctc_max_lpa: float | None = None,
    location: str | None = None,
    remote_policy: str | None = None,
    max_notice_days: int | None = None,
    screening_modality: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    rid = UUID(role_id)
    async with session_scope() as session:
        role = await session.get(Role, rid)
        if role is None:
            return {"error": "role_not_found"}
        for field, val in {
            "title": title,
            "jd_text": jd_text,
            "ctc_min_lpa": ctc_min_lpa,
            "ctc_max_lpa": ctc_max_lpa,
            "location": location,
            "remote_policy": remote_policy,
            "max_notice_days": max_notice_days,
            "screening_modality": screening_modality,
            "status": status,
        }.items():
            if val is not None:
                setattr(role, field, val)
    return {"ok": True, "id": role_id, "message": "Role updated."}


async def archive_role(*, role_id: str) -> dict[str, Any]:
    return await update_role(role_id=role_id, status="closed")


async def get_role(
    *,
    role_id: str,
) -> dict[str, Any]:
    """Read a role by id — returns title, status, pipeline stages,
    assignment brief + doc state, deadline, and evaluation spec."""
    rid = UUID(role_id)
    async with session_scope() as session:
        role = await session.get(Role, rid)
        if role is None:
            return {"error": "role_not_found"}
        from src.db.repositories import role_pipeline_stage as stage_repo
        stages = await stage_repo.for_role(session, rid, enabled_only=False)
        return {
            "ok": True,
            "id": str(role.id),
            "title": role.title,
            "status": role.status,
            "jd_text": role.jd_text,
            "ctc_min_lpa": role.ctc_min_lpa,
            "ctc_max_lpa": role.ctc_max_lpa,
            "location": role.location,
            "remote_policy": role.remote_policy,
            "pipeline_template": [
                {"stage_key": s.stage_key, "stage_type": s.stage_type, "label": s.label,
                 "mode": s.mode, "is_enabled": s.is_enabled}
                for s in stages
            ],
            "assignment_brief": role.assignment_brief,
            "assignment_instructions": role.assignment_instructions,
            "assignment_deadline_days": role.assignment_deadline_days,
            "has_problem_doc": bool(role.assignment_problem_doc_key),
            "assignment_problem_filename": role.assignment_problem_filename,
            "evaluation_spec": role.evaluation_spec,
        }


async def set_role_assignment_brief(
    *,
    role_id: str,
    assignment_brief: str,
    assignment_instructions: str | None = None,
    assignment_deadline_days: int | None = None,
) -> dict[str, Any]:
    rid = UUID(role_id)
    async with session_scope() as session:
        role = await session.get(Role, rid)
        if role is None:
            return {"error": "role_not_found"}
        role.assignment_brief = assignment_brief.strip()
        if assignment_instructions is not None:
            role.assignment_instructions = assignment_instructions
        if assignment_deadline_days is not None:
            role.assignment_deadline_days = int(assignment_deadline_days)
        # Editing the brief invalidates the existing PDF; role must be re-published.
        if role.assignment_problem_doc_key:
            role.assignment_problem_doc_key = None
            role.assignment_problem_filename = None
        if role.status == "open":
            role.status = "draft"
    return {"ok": True, "id": role_id, "message": "Assignment brief saved. Post the assignment to re-publish the PDF and re-open the role."}


async def override_stage(
    *,
    application_id: str,
    to_stage: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Force a pipeline-stage transition. Audited.

    HR override -- bypasses the ALLOWED_TRANSITIONS DAG.
    """
    from src.db.repositories.audit import log_audit
    from src.db.repositories.v1_application import set_stage
    from src.models.v1 import PipelineStage

    app_id = UUID(application_id)
    try:
        target = PipelineStage(to_stage)
    except ValueError:
        return {"error": f"unknown_stage: {to_stage}"}
    async with session_scope() as session:
        app = await session.get(Application, app_id)
        if app is None:
            return {"error": "application_not_found"}
        prev = app.current_stage
        try:
            await set_stage(session, app_id, target, force=True)
        except Exception as e:  # noqa: BLE001
            return {"error": f"set_stage_failed: {e}"}
        # set_stage is legacy-only now (writes current_stage only). Advance the V2
        # cursor too, or the auto_progress() call below plans from a STALE
        # current_stage_key and can re-dispatch the wrong stage. (V1->V2 migration
        # stale-cursor bug.)
        from src.services.state_machine import legacy_stage_to_key

        _skey, _sstatus = legacy_stage_to_key(target)
        if _skey:
            app.current_stage_key = _skey
            app.stage_status = _sstatus
        await log_audit(
            session,
            application_id=app_id,
            candidate_id=app.candidate_id,
            action="hr_stage_override",
            actor="agent_via_chat",
            details={"from": prev, "to": to_stage, "reason": reason},
        )
    from src.services.auto_progress import auto_progress
    try:
        await auto_progress(application_id=app_id)
    except Exception:  # noqa: BLE001
        pass

    return {
        "ok": True,
        "application_id": application_id,
        "from": prev,
        "to": to_stage,
        "message": f"Stage moved {prev} -> {to_stage}.",
    }


async def send_custom_email(
    *,
    application_id: str,
    subject: str,
    body_markdown: str,
) -> dict[str, Any]:
    from src.channels.email import send_email
    from src.db.repositories.audit import log_audit

    app_id = UUID(application_id)
    async with session_scope() as session:
        app = await session.get(Application, app_id)
        if app is None:
            return {"error": "application_not_found"}
        cand = await session.get(Candidate, app.candidate_id)
        if cand is None or not cand.email:
            return {"error": "candidate_email_missing"}
        result = await send_email(
            to=cand.email,
            template="custom_recruiter",
            variables={
                "candidate_name": cand.name,
                "subject": subject,
                "body_markdown": body_markdown,
            },
            tags={"type": "custom_recruiter", "application_id": application_id},
            idempotency_key=f"{application_id}:custom:{abs(hash(subject + body_markdown))}",
            application_id=application_id,
            candidate_id=str(cand.id),
        )
        await log_audit(
            session,
            application_id=app_id,
            candidate_id=cand.candidate_id if hasattr(cand, "candidate_id") else cand.id,
            action="custom_email_sent",
            actor="agent_via_chat",
            details={"subject": subject[:200], "ok": bool(result and result.success)},
        )
    if not result:
        return {"error": "send_skipped"}
    return {"ok": result.success, "provider": result.provider, "message_id": result.message_id, "message": "Email queued."}


async def add_candidate_note(*, application_id: str, note: str) -> dict[str, Any]:
    """Append a free-form note to the application's audit_log under
    ``recruiter_note``. Cheap; no separate notes table needed.
    """
    from src.db.repositories.audit import log_audit

    app_id = UUID(application_id)
    async with session_scope() as session:
        app = await session.get(Application, app_id)
        if app is None:
            return {"error": "application_not_found"}
        await log_audit(
            session,
            application_id=app_id,
            candidate_id=app.candidate_id,
            action="recruiter_note",
            actor="agent_via_chat",
            details={"note": (note or "").strip()[:2000]},
        )
    return {"ok": True, "message": "Note saved."}


async def get_journey_report(*, application_id: str) -> dict[str, Any]:
    app_id = UUID(application_id)
    async with session_scope() as session:
        app = await session.get(Application, app_id)
        if app is None:
            return {"error": "application_not_found"}
    return {
        "application_id": application_id,
        "stage": app.current_stage,
        "report_md": app.journey_report or "(no journey report yet)",
    }


async def metrics_period(*, days: int = 30) -> dict[str, Any]:
    """Funnel for the last N days."""
    cutoff = datetime.now(tz=UTC) - timedelta(days=int(days))
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Application.current_stage, func.count(Application.id))
                .where(Application.created_at >= cutoff)
                .group_by(Application.current_stage)
            )
        ).all()
    return {
        "days": int(days),
        "by_stage": {s or "applied": int(n) for s, n in rows},
        "total": sum(int(n) for _, n in rows),
    }


async def list_meetings(*, days: int = 14, limit: int = 50) -> dict[str, Any]:
    from src.db.base import MeetingSession

    cutoff = datetime.now(tz=UTC) - timedelta(days=int(days))
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(MeetingSession)
                .where(MeetingSession.scheduled_at >= cutoff)
                .order_by(MeetingSession.scheduled_at.asc())
                .limit(limit)
            )
        ).scalars().all()
    return {
        "items": [
            {
                "id": str(m.id),
                "application_id": str(m.application_id),
                "round": m.round,
                "scheduled_at": m.scheduled_at.isoformat() if m.scheduled_at else None,
                "bot_status": m.bot_status,
                "verdict": m.verdict,
                "overall_score": m.overall_score,
            }
            for m in rows
        ]
    }


async def list_voice_calls(*, days: int = 14, limit: int = 50) -> dict[str, Any]:
    from src.db.base import VoiceCall

    cutoff = datetime.now(tz=UTC) - timedelta(days=int(days))
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(VoiceCall)
                .where(VoiceCall.created_at >= cutoff)
                .order_by(desc(VoiceCall.created_at))
                .limit(limit)
            )
        ).scalars().all()
    return {
        "items": [
            {
                "id": str(c.id),
                "application_id": str(c.application_id),
                "status": c.status,
                "verdict": c.verdict,
                "overall_score": c.overall_score,
                "duration_sec": c.duration_sec,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in rows
        ]
    }


async def read_audit(*, application_id: str, limit: int = 30) -> dict[str, Any]:
    return await audit_tail(application_id=application_id, limit=limit)


async def schedule_interview(
    *,
    application_id: str,
    scheduled_at: str,
    meeting_link: str | None = None,
) -> dict[str, Any]:
    """Persist an Interview row + log audit. Email panel members is left to the
    propose_slots / existing scheduler activities -- this is the manual path
    when HR has already agreed a slot."""
    from src.db.base import Interview
    from src.db.repositories.audit import log_audit

    app_id = UUID(application_id)
    try:
        when = datetime.fromisoformat(scheduled_at)
    except ValueError:
        return {"error": "scheduled_at must be ISO-8601"}
    async with session_scope() as session:
        app = await session.get(Application, app_id)
        if app is None:
            return {"error": "application_not_found"}
        iv = Interview(
            application_id=app_id,
            scheduled_at=when,
            meeting_link=meeting_link,
            status="scheduled",
        )
        session.add(iv)
        await session.flush()
        iv_id = iv.id
        await log_audit(
            session,
            application_id=app_id,
            candidate_id=app.candidate_id,
            action="interview_scheduled",
            actor="agent_via_chat",
            details={"interview_id": str(iv_id), "scheduled_at": scheduled_at},
        )
    return {"ok": True, "interview_id": str(iv_id), "message": "Interview saved."}


async def propose_slots(*, application_id: str, count: int = 3) -> dict[str, Any]:
    """Generate proposed interview slots via the existing activity."""
    try:
        from src.activities.schedule import ProposeSlotsInput, run_propose_slots

        out = await run_propose_slots(
            ProposeSlotsInput(application_id=UUID(application_id), count=int(count))
        )
        return out.model_dump() if hasattr(out, "model_dump") else dict(out)
    except Exception as e:  # noqa: BLE001
        return {"error": f"propose_slots_failed: {e}"}


async def suggest_meeting_slots(*, business_days: int = 5, limit: int = 6) -> dict[str, Any]:
    """Recommend interview slots over the next few business days (excl. Sat/Sun).

    Returns ISO-8601 UTC timestamps plus human labels so the recruiter (or
    Pulse) can pick one to pass to schedule_meeting / reschedule_meeting.
    """
    from src.services.slot_suggest import format_slot, suggest_slots

    slots = suggest_slots(business_days=int(business_days), limit=int(limit))
    return {
        "slots": [
            {"scheduled_at": s.isoformat(), "label": format_slot(s)} for s in slots
        ]
    }


async def schedule_meeting(
    *,
    application_id: str,
    round: str,
    scheduled_at: str,
    panel_emails: list[str] | str,
    duration_minutes: int = 45,
) -> dict[str, Any]:
    """Book an interview meeting (Google Meet) for a candidate via chat:
    creates the calendar event, emails the candidate + panel, arms the bot.
    """
    from src.services.chat_meeting import book_meeting

    try:
        app_id = UUID(application_id)
    except (ValueError, TypeError, AttributeError):
        return {"error": "invalid_application_id"}
    try:
        when = datetime.fromisoformat(scheduled_at)
    except (ValueError, TypeError):
        return {"error": "scheduled_at must be ISO-8601, e.g. 2026-06-23T15:00:00+05:30"}
    if isinstance(panel_emails, str):
        panel_emails = [e.strip() for e in panel_emails.split(",") if e.strip()]
    return await book_meeting(
        application_id=app_id,
        round=round,
        scheduled_at=when,
        panel_emails=list(panel_emails or []),
        duration_minutes=int(duration_minutes or 45),
    )


async def reschedule_meeting(
    *,
    new_scheduled_at: str,
    application_id: str | None = None,
    meeting_session_id: str | None = None,
    round: str | None = None,
    duration_minutes: int | None = None,
    panel_emails: list[str] | str | None = None,
) -> dict[str, Any]:
    """Move an existing interview meeting to a new time and send fresh invites.
    Identify the meeting by meeting_session_id, or by application_id (+ optional
    round; otherwise the most recent meeting for that application).
    """
    from src.services.chat_meeting import reschedule_meeting as _reschedule

    try:
        when = datetime.fromisoformat(new_scheduled_at)
    except (ValueError, TypeError):
        return {"error": "new_scheduled_at must be ISO-8601"}
    ms_id = None
    app_id = None
    if meeting_session_id:
        try:
            ms_id = UUID(meeting_session_id)
        except (ValueError, TypeError):
            return {"error": "invalid_meeting_session_id"}
    if application_id:
        try:
            app_id = UUID(application_id)
        except (ValueError, TypeError):
            return {"error": "invalid_application_id"}
    if not ms_id and not app_id:
        return {"error": "application_id_or_meeting_session_id_required"}
    if isinstance(panel_emails, str):
        panel_emails = [e.strip() for e in panel_emails.split(",") if e.strip()]
    return await _reschedule(
        new_scheduled_at=when,
        meeting_session_id=ms_id,
        application_id=app_id,
        round=round,
        duration_minutes=int(duration_minutes) if duration_minutes else None,
        panel_emails=list(panel_emails) if panel_emails else None,
    )


async def set_panel_member(
    *,
    role_id: str,
    panel_member_id: str,
    round: str,
) -> dict[str, Any]:
    rid = UUID(role_id)
    async with session_scope() as session:
        role = await session.get(Role, rid)
        if role is None:
            return {"error": "role_not_found"}
        panel = list(role.interviewer_panel or [])
        panel = [p for p in panel if not (isinstance(p, dict) and p.get("round") == round)]
        panel.append({"round": round, "panel_member_id": panel_member_id})
        role.interviewer_panel = panel
    return {"ok": True, "message": f"Panel member set for {round}."}


async def add_panel_member(
    *,
    name: str,
    email: str,
    role_type: str,
    job_title: str | None = None,
    expertise_tags: list[str] | None = None,
    department: str | None = None,
    seniority_level: str | None = None,
) -> dict[str, Any]:
    """Create a new panel member in the workspace directory.

    The agent calls this when HR mentions a new interviewer who isn't
    in the system yet. Requires confirmation.
    """
    from src.db.base import PanelMember

    if role_type not in ("technical", "hr", "ceo"):
        return {"error": "role_type must be technical, hr, or ceo"}

    if seniority_level and seniority_level not in ("junior", "mid", "senior", "lead", "executive"):
        return {"error": "seniority_level must be junior, mid, senior, lead, or executive"}

    async with session_scope() as session:
        # Check for duplicate email
        existing = (
            await session.execute(
                select(PanelMember).where(PanelMember.email == email)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {"error": f"panel member with email {email} already exists", "existing_id": str(existing.id)}

        row = PanelMember(
            name=name,
            email=email,
            role_type=role_type,
            job_title=job_title,
            expertise_tags=expertise_tags,
            department=department,
            seniority_level=seniority_level,
        )
        session.add(row)
        await session.flush()
        member_id = row.id

    return {
        "ok": True,
        "id": str(member_id),
        "name": name,
        "email": email,
        "role_type": role_type,
        "message": f"Panel member '{name}' ({email}) added as {role_type} interviewer.",
    }


async def update_setting(*, key: str, value: Any) -> dict[str, Any]:
    """Write to ``config_settings`` table. Admin-only (enforced by RBAC)."""
    from src.db.base import ConfigSetting
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    async with session_scope() as session:
        stmt = (
            pg_insert(ConfigSetting)
            .values(key=key, value=value, updated_by="agent_via_chat", is_secret=False)
            .on_conflict_do_update(
                index_elements=["key"],
                set_={"value": value, "updated_by": "agent_via_chat"},
            )
        )
        await session.execute(stmt)
    return {"ok": True, "key": key, "message": "Setting saved."}


async def parse_attachment(
    *,
    file_ref: str | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    """Parse a previously-uploaded file (by R2 key) or fetch + parse a URL."""
    if file_ref:
        from src.recruiter_agent.parsers import parse_bytes
        from src.services.file_storage import download

        from src.config import get_settings

        bucket = get_settings().r2_bucket_resumes
        try:
            data = await download(bucket=bucket, key=file_ref)
        except Exception as e:  # noqa: BLE001
            return {"error": f"download_failed: {e}"}
        # Crude kind detection from extension.
        kind = file_ref.lower().rsplit(".", 1)[-1] if "." in file_ref else "text"
        return await parse_bytes(kind=kind, data=data)
    if url:
        from src.recruiter_agent.parsers import fetch_url
        return await fetch_url(url)
    return {"error": "file_ref_or_url_required"}


async def remember(
    *,
    actor_hash: str,
    key: str,
    value: Any,
    scope: str = "self",
) -> dict[str, Any]:
    """Long-term per-recruiter memory write. Wired by the runner; receives
    ``actor_hash`` injected automatically (not asked from LLM)."""
    from src.db.repositories.recruiter_memory import upsert as memory_upsert

    async with session_scope() as session:
        await memory_upsert(session, actor_hash=actor_hash, key=key, value=value, scope=scope)
    return {"ok": True, "message": f"Remembered: {key}"}


async def recall(
    *,
    actor_hash: str,
    prefix: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    from src.db.repositories.recruiter_memory import list_by_actor

    async with session_scope() as session:
        rows = await list_by_actor(session, actor_hash=actor_hash, prefix=prefix, limit=limit)
    return {
        "items": [
            {"key": r.key, "value": r.value, "scope": r.scope, "updated_at": r.updated_at.isoformat()}
            for r in rows
        ]
    }


async def _format_conversation_for_draft(conversation_id: str) -> str:
    """Pull recruiter + assistant messages from the conversation and format as
    a compact transcript the LLM can read to infer role details."""
    from src.db.repositories import recruiter_chat as chat_repo

    lines: list[str] = []
    async with session_scope() as session:
        msgs = await chat_repo.list_messages(session, UUID(conversation_id))
    for m in msgs:
        if m.role == "user":
            text = (m.content or "").strip()
            if text:
                lines.append(f"RECRUITER: {text}")
        elif m.role == "assistant" and not m.tool_calls:
            text = (m.content or "").strip()
            if text:
                lines.append(f"PULSE: {text}")
    return "\n".join(lines[-20:])  # last 20 exchanges at most


async def _complete_draft_from_context(
    conversation_id: str, partial: dict[str, Any]
) -> dict[str, Any] | None:
    """Generate a COMPLETE, strictly-validated ``RoleDraftContent`` from the
    conversation transcript.

    The model previously failed because its prompt never described the nested
    shapes ``RoleDraftContent`` requires (``PipelineStageDef`` needs a
    ``stage_type`` from a fixed enum + ``position``; ``EvaluationSpec`` weights
    must sum to ~100), so it emitted plausible-but-invalid JSON and the whole
    draft was rejected -> empty JD.

    Fix: the prompt below spells out the EXACT schema -- every field, the
    allowed enum values, a copy-paste standard pipeline, and the weight rules --
    so the model produces output that passes STRICT validation. Returns None only
    when there is no transcript or validation still fails after retries (logged
    loudly, never swallowed silently).
    """
    transcript = await _format_conversation_for_draft(conversation_id)
    if not transcript.strip():
        logger.warning("auto_complete_draft: empty transcript for %s", conversation_id)
        return None

    from src.llm.client import get_llm_client
    from src.llm.prompt_manager import compile_prompt
    from src.llm.prompts.jd_generation import JD_GENERATION_SYSTEM, JD_GENERATION_VERSION
    from src.models.artifacts import RoleDraftContent
    from src.models.candidate import RemotePolicy
    from src.models.pipeline import StageType

    # Real, non-invented values for the JD's "How to apply" block.
    from src.config import get_settings as _gs
    _s = _gs()
    company_name = (_s.voice_agent_company_name or "the company").strip()
    apply_email = (_s.resend_from_email or "careers@grabon.in").strip()

    # Build placeholder values from code so the prompt stays schema-agnostic.
    _remote_policy = (
        "<one of: "
        + ", ".join(m.value for m in RemotePolicy)
        + " -- derive from the conversation, match what the recruiter stated>"
    )
    _pipeline_guidance = (
        "<array of PipelineStageDef objects; see the `pipeline:` rule below for"
        " the required shape and allowed stage_type values."
        " IMPORTANT: intake, parse, fit are mandatory pre-stages that always run"
        " automatically — do NOT include them in the array. Start from voice_screen onwards.>"
    )
    _pipeline_stage_types = ", ".join(
        m.value for m in StageType
        if m not in (StageType.INTAKE, StageType.PARSE, StageType.FIT)
    )

    client = get_llm_client()
    try:
        # JD_GENERATION outputs the flat RoleDraftContent shape directly. We do
        # NOT route this through Langfuse role_draft_system: that prompt emits a
        # different {message, draft, quick_replies, ...} envelope and gutted the
        # draft. (role_draft_system stays in use only for the legacy /roles/new
        # flow.)
        result = await client.complete(
            prompt=(
                compile_prompt(
                    "jd_generation",
                    fallback=JD_GENERATION_SYSTEM,
                    remote_policy=_remote_policy,
                    pipeline_guidance=_pipeline_guidance,
                    pipeline_stage_types=_pipeline_stage_types,
                )
                + f"\nCONTEXT (use these REAL values, never invent):\n"
                + f"- Company name: {company_name}\n"
                + f"- Application email (for the '## How to apply' section): {apply_email}\n"
                + f"\nPARTIAL DATA ALREADY KNOWN (merge these in, fill the rest):\n"
                + json.dumps(partial, indent=2)
                + f"\n\nCONVERSATION:\n{transcript}\n\n"
                + "Respond with ONLY the JSON object specified above, fully filled."
            ),
            response_model=RoleDraftContent,
            model=model_for(Stage.ROLE_DRAFT_CHAT),
            trace_name="recruiter.auto_complete_draft",
            prompt_version=JD_GENERATION_VERSION,
            temperature=0.4,
            max_tokens=4000,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "auto_complete_draft failed for %s (strict RoleDraftContent validation): %s",
            conversation_id, e, exc_info=True,
        )
        return None

    draft = result.parsed
    if not (draft.jd_text and draft.jd_text.strip()):
        logger.warning("auto_complete_draft produced empty jd_text for %s", conversation_id)
        return None
    if not draft.title and isinstance(partial, dict):
        draft.title = partial.get("title")
    return {
        "ok": True,
        "artifact_type": "role_draft",
        "title": draft.title or "Role draft",
        "content": draft.model_dump(mode="json"),
    }


async def propose_role_draft(
    *, content: dict[str, Any] | None = None, **fields: Any
) -> dict[str, Any]:
    """Write/replace the role draft for the current conversation.

    Call this once you've gathered enough context. The runner upserts it into
    the conversation's artifact and opens the editable panel; call again to
    revise the same artifact.

    If you pass empty or partial data, the system auto-completes the draft from
    the conversation history (title, JD, pipeline, evaluation, company context).
    Does not persist a Role -- that happens only when the user clicks Apply on
    the artifact panel. This tool is not confirm-gated.
    """
    raw = dict(content) if isinstance(content, dict) else {}
    if not raw and fields:
        raw = {k: v for k, v in fields.items() if v is not None}

    # If the model handed us a SUBSTANTIVE inline draft, trust it and return
    # directly. A thin/truncated jd_text (the model tried to write the JD into
    # the tool call and the streaming token cap clipped it) is NOT trusted: we
    # fall through to the conversation-driven completer, which writes a full JD.
    _jd = (raw.get("jd_text") or "").strip()
    if raw.get("title") and len(_jd) >= 200:
        try:
            draft = RoleDraftContent.model_validate(raw)
        except Exception as e:  # noqa: BLE001
            return {"error": f"invalid_role_draft: {e}"}
        return {
            "ok": True,
            "artifact_type": "role_draft",
            "title": draft.title or "Role draft",
            "content": draft.model_dump(mode="json"),
        }

    # Auto-complete from conversation history when the LLM passes empty/partial args.
    conv_id = _conversation_id_var.get()
    if conv_id:
        completed = await _complete_draft_from_context(conv_id, raw)
        if completed:
            return completed

    # Last resort: wrap whatever the model passed inline. If there is no usable
    # JD (auto-complete found no transcript or failed), surface an error instead
    # of silently opening an empty panel -- the agent then asks for more detail.
    try:
        draft = RoleDraftContent.model_validate(raw)
    except Exception as e:  # noqa: BLE001
        return {"error": f"invalid_role_draft: {e}"}
    if not (draft.jd_text and draft.jd_text.strip()):
        return {
            "error": "could_not_draft",
            "message": (
                "Couldn't build the draft yet -- I need a bit more about the role "
                "(what they'll own, the must-have skills) before I can write the JD."
            ),
        }
    return {
        "ok": True,
        "artifact_type": "role_draft",
        "title": draft.title or "Role draft",
        "content": draft.model_dump(mode="json"),
    }


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------


TOOLS: dict[str, Any] = {
    # Reads
    "list_candidates": list_candidates,
    "get_candidate": get_candidate,
    "list_roles": list_roles,
    "pipeline_metrics": pipeline_metrics,
    "stuck_applications": stuck_applications,
    "audit_tail": audit_tail,
    "search_candidates": search_candidates,
    "search_talent_pool": search_talent_pool,
    "get_journey_report": get_journey_report,
    "metrics_period": metrics_period,
    "list_meetings": list_meetings,
    "list_voice_calls": list_voice_calls,
    "read_audit": read_audit,
    "recall": recall,
    "get_role": get_role,
    "smart_defaults_for_role": smart_defaults_for_role,
    "generate_assignment_for_role": generate_assignment_for_role,
    "draft_linkedin_post": draft_linkedin_post,
    "propose_role_draft": propose_role_draft,
    # Writes
    "create_role_with_assignment": create_role_with_assignment,
    "create_role": create_role,
    "update_role": update_role,
    "archive_role": archive_role,
    "set_role_assignment_brief": set_role_assignment_brief,
    "override_stage": override_stage,
    "send_custom_email": send_custom_email,
    "add_candidate_note": add_candidate_note,
    "schedule_interview": schedule_interview,
    "propose_slots": propose_slots,
    "suggest_meeting_slots": suggest_meeting_slots,
    "schedule_meeting": schedule_meeting,
    "reschedule_meeting": reschedule_meeting,
    "set_panel_member": set_panel_member,
    "add_panel_member": add_panel_member,
    "parse_attachment": parse_attachment,
    "remember": remember,
    "publish_linkedin_post": publish_linkedin_post,
    # Admin
    "update_setting": update_setting,
}


_ACTOR_HASH_TOOLS = frozenset({"remember", "recall"})


async def call_tool(
    name: str,
    args: dict[str, Any],
    *,
    actor_hash: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    if name == "give_choice":
        return {"error": "give_choice should never be executed on the backend"}
    fn = TOOLS.get(name)
    if fn is None:
        return {"error": f"unknown_tool: {name}"}
    args = dict(args or {})
    if name in _ACTOR_HASH_TOOLS:
        if not actor_hash:
            return {"error": "actor_hash_missing"}
        args["actor_hash"] = actor_hash
    # Inject conversation context for propose_role_draft auto-completion.
    if name == "propose_role_draft" and conversation_id:
        token = _conversation_id_var.set(conversation_id)
        try:
            return await fn(**args)
        finally:
            _conversation_id_var.reset(token)
    try:
        return await fn(**args)
    except TypeError as e:
        return {"error": f"bad_args: {e}"}
    except Exception as e:  # noqa: BLE001
        logger.exception("tool %s crashed", name)
        return {"error": f"tool_error: {e}"}
