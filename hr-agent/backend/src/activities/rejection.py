"""Stage 8a: Rejection flow.

Produces a respectful rejection message via REJECTION_MESSAGE_V1 using a
safe-reasons library, then sends it via email only. Internal scoring
rationale never reaches the candidate.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from uuid import UUID

from src.channels.email import send_email
from src.config import get_settings
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.candidate import get_application, get_candidate
from src.db.repositories.role import get_role
from src.llm.client import get_llm_client
from src.llm.prompt_manager import compile_prompt
from src.llm.prompts import REJECTION_MESSAGE_V1, REJECTION_MESSAGE_VERSION
from src.llm.model_registry import Stage, model_for


def _get_company_name_for_rejection() -> str:
    try:
        return get_settings().voice_agent_company_name
    except Exception:
        return "the company"


# A trailing closer line ("Best regards," etc.) plus everything after it.
_SIGNOFF_RE = re.compile(
    r"\n[ \t]*(?:warm(?:est)?\s+regards|kind\s+regards|best\s+regards|"
    r"best\s+wishes|all\s+the\s+best|regards|sincerely|warmly|cheers|best)"
    r"[ \t]*,?[ \t]*(?:\n[\s\S]*)?$",
    re.IGNORECASE,
)
# A trailing placeholder line ("[your name]", "[Your Name], GrabOn", ...).
_PLACEHOLDER_RE = re.compile(r"\n[ \t]*[-—]?[ \t]*\[[^\]]*\][^\n]*$", re.IGNORECASE)


def _strip_trailing_signoff(body: str) -> str:
    """Remove any greeting/sign-off the model tacked on.

    The email template appends the canonical "Best regards, GrabOn Team"
    sign-off, so a model-authored one -- often with a "[your name]"
    placeholder -- would double up. Strip it defensively regardless of which
    prompt (code fallback or the Langfuse-hosted copy) produced the body.
    """
    cleaned = _SIGNOFF_RE.sub("", body)
    cleaned = _PLACEHOLDER_RE.sub("", cleaned).rstrip()
    return cleaned or body
from src.models.candidate import ApplicationStatus, CandidateStatus
from src.models.llm_outputs import RejectionDraft
from src.services.rejection_reasons import pick_category, render_template

logger = logging.getLogger(__name__)
_settings = get_settings()


@dataclass
class RejectionInput:
    candidate_id: UUID
    application_id: UUID
    stage: str = "screening"  # "fit_score" | "screening" | "interview"
    internal_knock_outs: list[str] | None = None
    internal_red_flags: list[str] | None = None
    # Optional overrides HR can supply when manually rejecting.
    category_override: str | None = None
    domain_hint: str | None = None
    skill_hint: str | None = None


@dataclass
class RejectionResult:
    sent: bool
    category: str
    trace_id: str | None


async def run_rejection(payload: RejectionInput) -> RejectionResult:
    async with session_scope() as session:
        app = await get_application(session, payload.application_id)
        candidate = await get_candidate(session, payload.candidate_id)
        if app is None or candidate is None:
            raise ValueError("application or candidate not found")
        role = await get_role(session, app.role_id) if app.role_id else None
        snapshot = {
            "candidate_email": candidate.email,
            "candidate_name": candidate.name,
            "role_title": role.title if role else "the role you applied for",
            "max_notice_days": role.max_notice_days if role else None,
            "location": role.location if role else None,
        }

    # Pick a safe category (HR can override).
    category = payload.category_override or pick_category(
        knock_outs=payload.internal_knock_outs,
        red_flags=payload.internal_red_flags,
        stage=payload.stage,
    )
    reason_template = render_template(
        category,  # type: ignore[arg-type]
        domain=payload.domain_hint,
        skill=payload.skill_hint,
        max_days=snapshot["max_notice_days"],
        location_requirement=snapshot["location"],
    )

    # LLM draft.
    client = get_llm_client()
    prompt = compile_prompt(
        "rejection_message",
        fallback=REJECTION_MESSAGE_V1,
        company_name=_get_company_name_for_rejection(),
        candidate_name=snapshot["candidate_name"] or "there",
        role_title=snapshot["role_title"],
        rejection_category=category,
        reason_template=reason_template,
    )
    llm_result = await client.complete(
        prompt=prompt,
        response_model=RejectionDraft,
        model=model_for(Stage.REJECTION_MESSAGE),
        trace_name="rejection_message",
        prompt_version=REJECTION_MESSAGE_VERSION,
        candidate_id=payload.candidate_id,
        application_id=payload.application_id,
        temperature=0.3,  # a bit of warmth is fine here
        max_tokens=500,
    )
    draft = llm_result.parsed

    # Render body_html from body text (preserve paragraphs). Strip any
    # greeting/sign-off the model added -- the template adds its own.
    body_text = _strip_trailing_signoff(draft.body.strip())
    paragraphs = [p for p in body_text.split("\n\n") if p.strip()]
    body_html = "\n".join(f"<p>{p}</p>" for p in paragraphs)

    sent = False
    if snapshot["candidate_email"]:
        email_result = await send_email(
            to=snapshot["candidate_email"],
            template="rejection",
            variables={
                "candidate_name": snapshot["candidate_name"],
                "body_html": body_html,
                "application_id": str(payload.application_id),
                "talent_pool_url": f"{_settings.app_base_url.rstrip('/')}/talent-pool/opt-in/{payload.application_id}",
                "feedback_url": f"{_settings.app_base_url.rstrip('/')}/feedback/{payload.application_id}",
            },
            tags={"stage": "rejection", "category": category},
        )
        sent = email_result.success

    async with session_scope() as session:
        app = await get_application(session, payload.application_id)
        if app is not None:
            app.status = ApplicationStatus.REJECTED.value
        cand = await get_candidate(session, payload.candidate_id)
        if cand is not None:
            cand.status = CandidateStatus.REJECTED.value
        await log_audit(
            session,
            action="rejection_sent" if sent else "rejection_send_failed",
            actor="agent",
            candidate_id=payload.candidate_id,
            application_id=payload.application_id,
            details={
                "category": category,
                "stage": payload.stage,
                "reason_template": reason_template,
                "internal_knock_outs": payload.internal_knock_outs,
                "internal_red_flags": payload.internal_red_flags,
                "body_preview": draft.body[:200],
            },
            model_version=llm_result.model,
            prompt_version=llm_result.prompt_version,
            langfuse_trace_id=llm_result.trace_id,
        )

    return RejectionResult(sent=sent, category=category, trace_id=llm_result.trace_id)
