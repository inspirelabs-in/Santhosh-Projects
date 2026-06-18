"""Tool implementations for the recruiter agent.

Each tool is an async function returning a JSON-serialisable dict. The
agent's system prompt declares OpenAI-format tool schemas (see
``schemas.RECRUITER_TOOLS``); this module just provides the runtime.

Tools call the SAME repository helpers the dashboard endpoints use, so we
don't duplicate auth / business logic.
"""

from __future__ import annotations

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
    Role,
)
from src.db.connection import session_scope

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
    pipeline_template: list[str] | None = None,
    time_budget_hours: int = 6,
    deadline_days: int = 7,
    _prerendered_brief: dict | None = None,
    **_ignored: Any,
) -> dict[str, Any]:
    """One-shot: create a role + draft + persist a take-home with N problems.

    Single confirm card shows the full role + assignment. On confirm, both
    are saved atomically so the role detail page renders the brief
    immediately.
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
        pipeline_template=pipeline_template,
    )
    if "error" in role_resp:
        return role_resp
    role_id = role_resp["id"]

    # Generate + persist the assignment in one go (save=True). When the
    # confirm-card prerender already drafted the brief, reuse it instead of
    # paying for a second LLM call.
    asg = await generate_assignment_for_role(
        role_id=role_id,
        n_problems=n_problems,
        time_budget_hours=time_budget_hours,
        deadline_days=deadline_days,
        save=True,
        prerendered=_prerendered_brief,
    )

    return {
        "ok": True,
        "role": role_resp,
        "assignment": asg,
        "role_id": role_id,
        "role_url": f"/roles/{role_id}",
        "message": (
            f"Created role '{title}' with a {asg.get('problem_count') or n_problems}-problem "
            f"take-home assignment. Both saved."
        ),
    }


async def generate_assignment_for_role(
    *,
    role_id: str,
    n_problems: int = 2,
    time_budget_hours: int = 6,
    deadline_days: int = 7,
    save: bool = False,
    prerendered: dict | None = None,
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

    try:
        brief = await gen_assignment(
            role_title=role.title,
            jd_text=role.jd_text or "",
            candidate_profile={},
            screening_answers=None,
            time_budget_hours=int(time_budget_hours),
            deadline_days=int(deadline_days),
            application_id=rid,  # role-scoped; reuse generator with role uuid
            candidate_id=rid,
        )
    except Exception as e:  # noqa: BLE001
        return {"error": f"assignment_gen_failed: {e}"}

    payload = brief.model_dump()

    # The candidate-side generator caps problems at 3. Pad with synthesized
    # follow-ups when the recruiter wanted 5+. We do this in-prompt-style by
    # asking the LLM for additional variations.
    if len(payload.get("problems", [])) < n:
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
                model=client.smart,
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
                r.assignment_brief = payload["brief_md"]
                r.assignment_instructions = (payload.get("submission_format") or {}).get("instructions")
                r.assignment_deadline_days = (payload.get("submission_format") or {}).get("deadline_days") or deadline_days
                if pdf_key:
                    r.assignment_problem_doc_key = pdf_key
                    r.assignment_problem_filename = pdf_filename

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


# ---------------------------------------------------------------------------
# LinkedIn drafting + publishing
# ---------------------------------------------------------------------------


_LINKEDIN_STYLE_EXAMPLE = (
    "Resumes will be rejected. Output is the only currency.\n\n"
    "We are officially building an elite AI Labs team within the Founder's Office at GrabOn, "
    "and we aren't looking for traditional applicants.\n\n"
    "We're looking for Vibe Coders.\n\n"
    "If you live in Claude Code, deploy OpenClaw, build agents while others are still writing PRDs, "
    "and believe shipping is the only metric that matters - this is your front-row seat. No middle "
    "layers, no bureaucracy, just pure 0-to-1 building directly alongside the founder.\n\n"
    "We don't care about your degree; we care about what you've built.\n"
    "The mission: Scan the frontier, prototype fast, kill fast, and scale the future of AI.\n\n"
    "⚡ Apply via the instructions below (Hint: Don't just send a CV)."
)


async def draft_linkedin_post(
    *,
    role_id: str | None = None,
    role_title: str | None = None,
    angle: str | None = None,
    apply_url: str | None = None,
) -> dict[str, Any]:
    """Draft a punchy LinkedIn post for an opening.

    Either ``role_id`` (load from DB) or ``role_title`` (free-form) is required.
    ``angle`` is an optional hook ("we want vibe coders", "founder's office team",
    "Bangalore-only", etc.). ``apply_url`` defaults to the canonical careers page.
    """
    if not role_id and not role_title:
        return {"error": "role_id or role_title required"}

    title = role_title
    jd = ""
    location: str | None = None
    if role_id:
        async with session_scope() as session:
            role = await session.get(Role, UUID(role_id))
            if role is None:
                return {"error": "role_not_found"}
            title = role.title
            jd = role.jd_text or ""
            location = role.location

    from src.agent.schemas import (  # late import to avoid loading LLM stack at module import
        AssignmentBriefOut,  # noqa: F401  (pyright keepalive)
    )
    from src.llm.client import get_llm_client
    from pydantic import BaseModel, Field

    class _LinkedInPostOut(BaseModel):
        text: str = Field(min_length=80, max_length=3000)
        hashtags: list[str] = Field(default_factory=list, max_length=8)

    prompt = (
        "You are a recruiter copywriter. Draft ONE LinkedIn post in the EXACT "
        "tone, cadence, and structure of the example below. Punchy, contrarian, "
        "founder-voice, short paragraphs, single ⚡ emoji at the end. No "
        "markdown headers. Plain text. Hashtags optional and only at the very "
        "bottom if they add reach.\n\n"
        f"## STYLE EXAMPLE (do not copy verbatim, but match the energy):\n"
        f"{_LINKEDIN_STYLE_EXAMPLE}\n\n"
        f"## ROLE\n"
        f"Title: {title}\n"
        f"Location: {location or 'India'}\n"
        f"Hook angle: {angle or '(none -- pick the strongest from the JD)'}\n"
        f"Apply URL: {apply_url or 'https://www.grabon.in/careers'}\n\n"
        f"## JD CONTEXT\n{jd[:3500]}\n\n"
        "Output JSON: {\"text\": \"<the post body>\", \"hashtags\": [\"#X\", ...]}. "
        "Body must be 120-300 words. End with a single line CTA + ⚡ pointing "
        "to Apply URL."
    )
    client = get_llm_client()
    try:
        result = await client.complete(
            prompt=prompt,
            response_model=_LinkedInPostOut,
            trace_name="recruiter.linkedin_draft",
            prompt_version="v1",
            application_id=UUID(role_id) if role_id else uuid_zero(),
            candidate_id=UUID(role_id) if role_id else uuid_zero(),
            temperature=0.7,
            max_tokens=1200,
        )
    except Exception as e:  # noqa: BLE001
        return {"error": f"draft_failed: {e}"}

    full = result.parsed.text.strip()
    if result.parsed.hashtags:
        full += "\n\n" + " ".join(result.parsed.hashtags)

    return {
        "ok": True,
        "role_id": role_id,
        "role_title": title,
        "text": full,
        "char_count": len(full),
        "hashtags": result.parsed.hashtags,
        "apply_url": apply_url or "https://www.grabon.in/careers",
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
    pipeline_template: list[str] | None = None,
    **_extra: Any,
) -> dict[str, Any]:
    """Persist a new role. Returns the created row's id + URL."""
    screening_modality = "voice"
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
            pipeline_template=resolved_template,
            status="open",
        )
        session.add(role)
        await session.flush()
        role_id = role.id
    return {
        "ok": True,
        "id": str(role_id),
        "title": title,
        "url": f"/roles/{role_id}",
        "message": f"Created role '{title}'.",
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
    return {"ok": True, "id": role_id, "message": "Assignment brief saved."}


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
    "smart_defaults_for_role": smart_defaults_for_role,
    "generate_assignment_for_role": generate_assignment_for_role,
    "draft_linkedin_post": draft_linkedin_post,
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
) -> dict[str, Any]:
    fn = TOOLS.get(name)
    if fn is None:
        return {"error": f"unknown_tool: {name}"}
    args = dict(args or {})
    if name in _ACTOR_HASH_TOOLS:
        if not actor_hash:
            return {"error": "actor_hash_missing"}
        args["actor_hash"] = actor_hash
    try:
        return await fn(**args)
    except TypeError as e:
        return {"error": f"bad_args: {e}"}
    except Exception as e:  # noqa: BLE001
        logger.exception("tool %s crashed", name)
        return {"error": f"tool_error: {e}"}
