"""Generic voice call dispatch for any call purpose.

Handles all CallKind values. Screening calls generate LLM questions;
other purposes skip question generation and inject rich candidate context.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from src.config import get_settings
from src.db.base import Application, Candidate, CandidateProfileRow, Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.v1_application import set_stage
from src.db.repositories.voice_call import (
    cancel_in_flight,
    create_voice_call,
    mark_dispatched,
    mark_failed,
    next_attempt_no,
)
from src.models.v1 import CallKind, PipelineStage, VoiceCallStatus, VoiceQuestion
from src.services.voice_context import build_candidate_context
from src.services.voice_prompts import build_voice_prompt
from src.services.voice_provider import VoiceCallSpec, get_voice_provider

logger = logging.getLogger(__name__)

# Call kinds that should change pipeline stage on dispatch
_STAGE_CHANGING_KINDS = {CallKind.SCREENING}


async def dispatch_voice_call(
    *,
    application_id: UUID,
    call_kind: CallKind,
    attempt_no: int | None = None,
    scheduled_at: datetime | None = None,
    campaign_id: UUID | None = None,
) -> UUID:
    """Dispatch a voice call of any purpose.

    Returns the new ``voice_calls.id``.
    """

    settings = get_settings()
    if not (settings.enable_voice_screening or settings.enable_voice_calls):
        raise RuntimeError("voice calls disabled")

    async with session_scope() as session:
        if attempt_no is None:
            attempt_no = await next_attempt_no(session, application_id)

        await cancel_in_flight(session, application_id)

        app = await session.get(Application, application_id)
        if app is None:
            raise ValueError(f"application {application_id} not found")
        if app.role_id is None:
            raise ValueError("application has no role")

        candidate = await session.get(Candidate, app.candidate_id)
        if candidate is None or not candidate.phone:
            raise ValueError("candidate phone missing -- cannot dial")

        role = await session.get(Role, app.role_id)
        if role is None:
            raise ValueError("role missing")

        # Build context for prompt
        ctx = await build_candidate_context(
            session, application_id, settings.voice_agent_company_name
        )

        # Generate questions only for screening calls
        questions: list[VoiceQuestion] = []
        questions_reused = False
        if call_kind == CallKind.SCREENING:
            questions, questions_reused = await _generate_or_reuse_questions(
                session, application_id, attempt_no, candidate, role, settings
            )

        questions_jsonable = [q.model_dump(mode="json") for q in questions]

        voice_row = await create_voice_call(
            session,
            application_id=application_id,
            candidate_phone=candidate.phone,
            questions=questions_jsonable,
            scheduled_at=scheduled_at or datetime.now(UTC),
            attempt_no=attempt_no,
            provider="elevenlabs",
            call_kind=call_kind.value,
        )
        voice_call_id = voice_row.id

        # Set campaign_id if this is part of a bulk campaign
        if campaign_id is not None:
            voice_row.campaign_id = campaign_id

        # Only screening calls change pipeline stage
        if call_kind in _STAGE_CHANGING_KINDS:
            await set_stage(
                session, application_id, PipelineStage.VOICE_SCREEN_SCHEDULED, force=True
            )

        await log_audit(
            session,
            candidate_id=candidate.id,
            application_id=application_id,
            action=f"voice_{call_kind.value}_dispatched",
            actor="agent",
            details={
                "voice_call_id": str(voice_call_id),
                "call_kind": call_kind.value,
                "attempt_no": attempt_no,
                "question_count": len(questions),
                "questions_reused": questions_reused,
                "campaign_id": str(campaign_id) if campaign_id else None,
            },
        )

        candidate_name = candidate.name or "Candidate"
        candidate_phone = candidate.phone
        role_title = role.title

    # Build prompt and first message
    system_prompt, first_message = build_voice_prompt(
        kind=call_kind, context=ctx, attempt_no=attempt_no
    )

    spec = VoiceCallSpec(
        application_id=application_id,
        voice_call_id=voice_call_id,
        candidate_name=candidate_name,
        candidate_phone=candidate_phone,
        role_title=role_title,
        company_name=settings.voice_agent_company_name,
        questions=questions,
        system_prompt=system_prompt,
        webhook_url=f"{settings.app_base_url.rstrip('/')}/webhooks/voice/elevenlabs",
        max_seconds=settings.voice_agent_max_call_seconds,
        mode=call_kind.value,
        first_message_override=first_message,
    )

    provider = get_voice_provider()
    try:
        handle = await provider.create_call(spec)
    except Exception as exc:
        async with session_scope() as session:
            await mark_failed(
                session,
                voice_call_id,
                error=str(exc),
                status=VoiceCallStatus.FAILED,
            )
            await log_audit(
                session,
                application_id=application_id,
                action=f"voice_{call_kind.value}_dispatch_failed",
                actor="agent",
                details={"error": str(exc)[:500]},
            )
        raise

    async with session_scope() as session:
        await mark_dispatched(
            session, voice_call_id, provider_call_id=handle.provider_call_id
        )
    return voice_call_id


async def _generate_or_reuse_questions(
    session, application_id, attempt_no, candidate, role, settings
) -> tuple[list[VoiceQuestion], bool]:
    """Generate or reuse screening questions. Returns (questions, reused)."""
    from src.db.base import VoiceCall as _VC
    from src.llm.client import get_llm_client
    from src.llm.prompt_manager import compile_prompt
    from src.llm.prompts.voice_screening import (
        VOICE_SCREEN_GEN_V1,
        VOICE_SCREEN_GEN_VERSION,
    )
    from src.models.candidate import CandidateProfile

    # Reuse from prior attempt
    if attempt_no > 1:
        prior_row = (
            await session.execute(
                select(_VC)
                .where(
                    _VC.application_id == application_id,
                    _VC.questions.isnot(None),
                )
                .order_by(_VC.attempt_no.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if prior_row is not None and isinstance(prior_row.questions, list):
            return (
                [VoiceQuestion.model_validate(q) for q in prior_row.questions][
                    : settings.voice_agent_max_questions
                ],
                True,
            )

    # Generate new questions via LLM
    profile_row = (
        await session.execute(
            select(CandidateProfileRow)
            .where(CandidateProfileRow.candidate_id == candidate.id)
            .order_by(CandidateProfileRow.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if profile_row is None:
        raise ValueError("candidate profile not parsed yet")

    profile = CandidateProfile.model_validate(profile_row.parsed_data)

    from pydantic import BaseModel, Field

    class _VoiceQuestionSet(BaseModel):
        questions: list[VoiceQuestion] = Field(default_factory=list)

    prompt = compile_prompt(
        "voice_screen_gen",
        fallback=VOICE_SCREEN_GEN_V1,
        role_title=role.title,
        jd_text=role.jd_text[:4000],
        ctc_min_lpa=role.ctc_min_lpa if role.ctc_min_lpa is not None else "n/a",
        ctc_max_lpa=role.ctc_max_lpa if role.ctc_max_lpa is not None else "n/a",
        max_notice_days=role.max_notice_days if role.max_notice_days is not None else "n/a",
        role_location=role.location or "n/a",
        remote_policy=role.remote_policy or "n/a",
        candidate_profile_json=json.dumps(
            profile.model_dump(mode="json", exclude_none=True), ensure_ascii=False
        )[:6000],
    )

    client = get_llm_client()
    result = await client.complete(
        prompt=prompt,
        response_model=_VoiceQuestionSet,
        trace_name="voice_screen_gen",
        prompt_version=VOICE_SCREEN_GEN_VERSION,
        candidate_id=candidate.id,
        application_id=application_id,
        system="You design tailored spoken phone-screen questions. JSON only.",
        max_tokens=1500,
    )
    questions = result.parsed.questions[: settings.voice_agent_max_questions]
    if not questions:
        raise RuntimeError("voice question generation returned empty set")

    return questions, False
