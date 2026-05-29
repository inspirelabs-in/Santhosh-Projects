"""Fallback manager — handles graceful degradation when external services fail.

Central place for "if X fails, do Y instead" logic. Every external service
failure in the pipeline should route through here so we have consistent
fallback behavior.
"""

from __future__ import annotations

import logging
from uuid import UUID

from src.db.base import Application, Candidate, Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.v1_application import set_stage
from src.models.v1 import PipelineStage
from src.services.events import publish_event

logger = logging.getLogger(__name__)


async def voice_to_chat_fallback(
    application_id: UUID,
    error: str,
) -> str:
    """When voice screening fails, fall back to chat-first screening.

    This handles: ElevenLabs out of credits, API errors, phone number
    issues, candidate unreachable, circuit breaker open.
    """
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            return "skipped:app_not_found"
        candidate_id = app.candidate_id
        role_id = app.role_id

        await set_stage(session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True)
        await log_audit(
            session,
            action="voice_fallback_to_review",
            actor="fallback_manager",
            candidate_id=candidate_id,
            application_id=application_id,
            details={
                "original_error": error[:500],
                "fallback_action": "parked_for_hr_review",
                "reason": "Voice screening unavailable. HR can re-trigger voice or switch to chat.",
            },
        )

    await publish_event(
        application_id,
        event="chat_stage_change",
        data={
            "to": "needs_hr_review",
            "reason": f"Voice screening failed: {error[:200]}",
        },
    )
    return "fallback:voice_to_hr_review"


async def voice_to_chat_auto_fallback(
    application_id: UUID,
    candidate_id: UUID,
    role_id: UUID | None,
    error: str,
) -> str:
    """Auto-fallback: when voice fails AND auto_fallback_to_chat is enabled,
    skip HR review and directly send chat screening invite."""
    try:
        from src.pipeline.chat_invite import run_apply_to_chat

        async with session_scope() as session:
            await log_audit(
                session,
                action="voice_auto_fallback_to_chat",
                actor="fallback_manager",
                candidate_id=candidate_id,
                application_id=application_id,
                details={
                    "original_error": error[:500],
                    "fallback_action": "auto_switch_to_chat_screening",
                },
            )

        await run_apply_to_chat(
            application_id=application_id,
            candidate_id=candidate_id,
            role_id=role_id,
        )
        return "fallback:voice_to_chat_auto"

    except Exception as e:
        logger.exception("Auto chat fallback also failed for %s", application_id)
        return await voice_to_chat_fallback(application_id, f"voice+chat both failed: {e}")


async def email_fallback(
    application_id: UUID,
    candidate_id: UUID,
    template: str,
    error: str,
) -> None:
    """When email sending fails, log for manual follow-up."""
    async with session_scope() as session:
        await log_audit(
            session,
            action="email_delivery_failed",
            actor="fallback_manager",
            candidate_id=candidate_id,
            application_id=application_id,
            details={
                "template": template,
                "error": error[:500],
                "action_required": "HR must manually contact candidate",
            },
        )

    await publish_event(
        application_id,
        event="delivery_failed",
        data={"channel": "email", "template": template, "error": error[:200]},
    )


async def llm_fallback(
    application_id: UUID,
    candidate_id: UUID | None,
    stage: str,
    error: str,
) -> None:
    """When LLM call fails, park for HR review with context."""
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            return

        await set_stage(session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True)
        await log_audit(
            session,
            action="llm_failure_parked",
            actor="fallback_manager",
            candidate_id=candidate_id or app.candidate_id,
            application_id=application_id,
            details={
                "failed_stage": stage,
                "error": error[:500],
                "action_required": "LLM service failed. HR can retry or proceed manually.",
            },
        )

    await publish_event(
        application_id,
        event="chat_stage_change",
        data={"to": "needs_hr_review", "reason": f"AI service error at {stage}"},
    )


async def webhook_timeout_fallback(
    application_id: UUID,
    expected_event: str,
    waited_hours: float,
) -> None:
    """When a webhook callback never arrives, park for HR review."""
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            return

        await set_stage(session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True)
        await log_audit(
            session,
            action="webhook_timeout_parked",
            actor="fallback_manager",
            candidate_id=app.candidate_id,
            application_id=application_id,
            details={
                "expected_event": expected_event,
                "waited_hours": round(waited_hours, 1),
                "action_required": (
                    f"Expected {expected_event} webhook never arrived after "
                    f"{waited_hours:.1f}h. HR should check external service status."
                ),
            },
        )

    await publish_event(
        application_id,
        event="chat_stage_change",
        data={
            "to": "needs_hr_review",
            "reason": f"Webhook timeout: {expected_event} after {waited_hours:.1f}h",
        },
    )
