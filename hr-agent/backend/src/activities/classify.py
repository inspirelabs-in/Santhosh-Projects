"""Stage 2: Classify & (soft-)Dedupe.

Decides whether an email is a job application, detects the role, and produces
a confidence score. Dedup already ran in Stage 1 -- here we only look up the
role the candidate meant.

Routing rules (from references/pipeline-stages.md):
  - not is_application          → status="not_application" (terminal)
  - confidence < 0.7            → status="needs_hr_review" + Slack alert
  - detected_role == "Unknown"  → status="role_unclear" but advance to parsing
  - else                        → link role to application, status="classified"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from temporalio import activity

from src.channels import teams as teams_channel
from src.config import get_settings
from src.db.base import Application, Candidate
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.candidate import update_application_status
from src.db.repositories.policy import resolve_policy
from src.db.repositories.role import list_open_roles, match_role_by_title
from src.llm.client import get_llm_client
from src.llm.prompt_manager import compile_prompt
from src.llm.prompts import CLASSIFY_EMAIL_V1, CLASSIFY_EMAIL_VERSION
from src.models.candidate import ApplicationStatus
from src.models.llm_outputs import ClassificationResult

logger = logging.getLogger(__name__)
_settings = get_settings()

@dataclass
class ClassifyInput:
    candidate_id: UUID
    application_id: UUID
    # Optional context from intake — lets the LLM actually see what the
    # candidate sent. When omitted we fall back to placeholders (careers-form
    # path where the body isn't captured).
    subject: str | None = None
    body_text: str | None = None
    attachment_filenames: list[str] | None = None


@dataclass
class ClassifyOutput:
    is_application: bool
    confidence: float
    detected_role: str
    matched_role_id: UUID | None
    status_after: str
    needs_hr_review: bool
    trace_id: str | None


async def run_classify(payload: ClassifyInput) -> ClassifyOutput:
    async with session_scope() as session:
        candidate = await session.get(Candidate, payload.candidate_id)
        application = await session.get(Application, payload.application_id)
        if candidate is None or application is None:
            raise ValueError("candidate or application not found")

        open_roles = await list_open_roles(session)
        open_roles_list = ", ".join(r.title for r in open_roles) or "(no open roles)"

        sender_email = candidate.email or "(unknown)"
        subject = payload.subject or "(no subject)"
        body_text = payload.body_text or "(body not captured)"
        if payload.attachment_filenames:
            attachment_filenames = ", ".join(payload.attachment_filenames)
        else:
            attachment_filenames = (
                "(one resume uploaded)" if candidate.status != "intake" else "(pending upload)"
            )

    prompt = compile_prompt(
        "classify_email",
        fallback=CLASSIFY_EMAIL_V1,
        open_roles_list=open_roles_list,
        subject=subject,
        sender_email=sender_email,
        body_text=body_text,
        attachment_filenames=attachment_filenames,
    )

    client = get_llm_client()
    result = await client.complete(
        prompt=prompt,
        response_model=ClassificationResult,
        model=_settings.llm_model_fast,
        trace_name="classify_email",
        prompt_version=CLASSIFY_EMAIL_VERSION,
        candidate_id=payload.candidate_id,
        application_id=payload.application_id,
        temperature=0.0,
        max_tokens=800,
    )
    classification = result.parsed

    matched_role_id: UUID | None = None
    needs_hr_review = False

    async with session_scope() as session:
        hr_review_threshold, _ = await resolve_policy(
            session, "classification_hr_review_confidence", fallback=0.7
        )

        if not classification.is_application:
            await update_application_status(
                session, payload.application_id, ApplicationStatus.NOT_APPLICATION
            )
            status_after = ApplicationStatus.NOT_APPLICATION.value
        elif classification.confidence < hr_review_threshold:
            await update_application_status(
                session, payload.application_id, ApplicationStatus.NEEDS_HR_REVIEW
            )
            status_after = ApplicationStatus.NEEDS_HR_REVIEW.value
            needs_hr_review = True
        else:
            role = await match_role_by_title(session, classification.detected_role)
            if role is None:
                await update_application_status(
                    session, payload.application_id, ApplicationStatus.ROLE_UNCLEAR
                )
                status_after = ApplicationStatus.ROLE_UNCLEAR.value
            else:
                app = (
                    await session.scalars(
                        select(Application).where(Application.id == payload.application_id)
                    )
                ).one()
                app.role_id = role.id
                app.status = ApplicationStatus.CLASSIFIED.value
                matched_role_id = role.id
                status_after = ApplicationStatus.CLASSIFIED.value

        await log_audit(
            session,
            action="classified",
            actor="agent",
            candidate_id=payload.candidate_id,
            application_id=payload.application_id,
            details={
                "is_application": classification.is_application,
                "confidence": classification.confidence,
                "detected_role": classification.detected_role,
                "detected_role_confidence": classification.detected_role_confidence,
                "matched_role_id": str(matched_role_id) if matched_role_id else None,
                "flags": classification.flags,
                "reasoning": classification.reasoning,
                "status_after": status_after,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            },
            model_version=result.model,
            prompt_version=result.prompt_version,
            langfuse_trace_id=result.trace_id,
        )

    if needs_hr_review:
        await teams_channel.notify_hr(
            title="Low-confidence application classification",
            text=(
                f"Candidate `{payload.candidate_id}` classified with confidence "
                f"{classification.confidence:.2f}. Detected role: "
                f"*{classification.detected_role}*. Reason: {classification.reasoning}"
            ),
            fields={"application_id": str(payload.application_id)},
        )

    return ClassifyOutput(
        is_application=classification.is_application,
        confidence=classification.confidence,
        detected_role=classification.detected_role,
        matched_role_id=matched_role_id,
        status_after=status_after,
        needs_hr_review=needs_hr_review,
        trace_id=result.trace_id,
    )


@activity.defn(name="classify_email")
async def classify_activity(payload: ClassifyInput) -> ClassifyOutput:
    return await run_classify(payload)
