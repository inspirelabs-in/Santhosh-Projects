"""V2-agentic activity: dispatch outbound AI phone-screen call.

Pipeline position: runs after ``screening_evaluated`` (or on demand from HR).
Generates spoken questions, persists a ``voice_calls`` row, then asks the
configured voice provider to dial the candidate. Pipecat owns the live
turn-taking; this module owns persistence + state transitions only.

Post-call evaluation lives in ``v1_evaluate_voice_call.py`` and is invoked
from the ``/webhooks/voice/pipecat`` handler when the call ends.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from src.activities.v1_generate_screening import generate_screening_questions  # reuse for fallback
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
from src.llm.client import get_llm_client
from src.llm.model_registry import Stage, model_for
from src.llm.prompt_manager import compile_prompt
from src.llm.prompts.voice_screening import (
    VOICE_SCREEN_GEN_V1,
    VOICE_SCREEN_GEN_VERSION,
)
from src.models.candidate import CandidateProfile
from src.models.v1 import (
    CallKind,
    GeneratedScreeningSet,
    PipelineStage,
    VoiceCallStatus,
    VoiceQuestion,
)
from src.services.voice_context import build_candidate_context
from src.services.voice_prompts import build_voice_prompt
from src.services.voice_provider import VoiceCallSpec, get_voice_provider

logger = logging.getLogger(__name__)


async def dispatch_voice_screening(
    *,
    application_id: UUID,
    attempt_no: int | None = None,
    scheduled_at: datetime | None = None,
) -> UUID:
    """Generate spoken questions and dial the candidate.

    Returns the new ``voice_calls.id``. Raises if prerequisites are missing
    (no phone, no role, feature flag off).
    """

    settings = get_settings()
    if not settings.enable_voice_screening:
        raise RuntimeError("voice screening disabled (ENABLE_VOICE_SCREENING=false)")

    async with session_scope() as session:
        # Auto-increment attempt_no based on existing rows for this
        # application, and mark any in-flight rows as superseded so the UI
        # doesn't accumulate stale "attempt 1 / dialing" cards on every click.
        if attempt_no is None:
            attempt_no = await next_attempt_no(session, application_id)

        # Reuse questions from the most recent prior attempt so callback /
        # no-pickup retries don't re-burn LLM tokens AND don't shift the
        # baseline so per-question scores stay comparable across attempts.
        from src.db.base import VoiceCall as _VC

        prior_questions: list[dict[str, Any]] | None = None
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
                prior_questions = prior_row.questions

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

        voice_ctx = await build_candidate_context(
            session,
            application_id,
            company_name=settings.voice_agent_company_name,
        )

        questions_reused = False
        if prior_questions:
            # Skip the LLM round-trip on retries: same screen, same scoring
            # baseline, faster dispatch (~30s saved per call).
            questions = [
                VoiceQuestion.model_validate(q) for q in prior_questions
            ][: settings.voice_agent_max_questions]
            questions_reused = True
        else:
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
                **scoring_prompt_vars(role.evaluation_spec, role.company_context),
            )

            client = get_llm_client()
            result = await client.complete(
                prompt=prompt,
                response_model=_VoiceQuestionSet,
                model=model_for(Stage.VOICE_SCREEN_GEN),
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

        questions_jsonable = [q.model_dump(mode="json") for q in questions]

        voice_row = await create_voice_call(
            session,
            application_id=application_id,
            candidate_phone=candidate.phone,
            questions=questions_jsonable,
            scheduled_at=scheduled_at or datetime.now(UTC),
            attempt_no=attempt_no,
            provider="elevenlabs",
        )
        voice_call_id = voice_row.id

        await set_stage(
            session, application_id, PipelineStage.VOICE_SCREEN_SCHEDULED, force=True
        )
        await log_audit(
            session,
            candidate_id=candidate.id,
            application_id=application_id,
            action="voice_screen_dispatched",
            actor="agent",
            details={
                "voice_call_id": str(voice_call_id),
                "attempt_no": attempt_no,
                "question_count": len(questions),
                "questions_reused": questions_reused,
            },
            prompt_version=VOICE_SCREEN_GEN_VERSION,
        )

        # Capture values needed after we leave the session.
        candidate_name = candidate.name or "Candidate"
        candidate_phone = candidate.phone
        role_title = role.title

    # Outside the DB session: make the HTTP call to the voice provider.
    company_name = settings.voice_agent_company_name
    system_prompt, first_message_override = build_voice_prompt(
        kind=CallKind.SCREENING, context=voice_ctx, attempt_no=attempt_no
    )

    spec = VoiceCallSpec(
        application_id=application_id,
        voice_call_id=voice_call_id,
        candidate_name=candidate_name,
        candidate_phone=candidate_phone,
        role_title=role_title,
        company_name=company_name,
        questions=questions,
        system_prompt=system_prompt,
        webhook_url=f"{settings.app_base_url.rstrip('/')}/webhooks/voice/elevenlabs",
        max_seconds=settings.voice_agent_max_call_seconds,
        first_message_override=first_message_override,
    )

    provider = get_voice_provider()
    try:
        handle = await provider.create_call(spec)
    except Exception as exc:  # noqa: BLE001 -- want to capture any provider error
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
                action="voice_screen_dispatch_failed",
                actor="agent",
                details={"error": str(exc)[:500]},
            )
        raise

    async with session_scope() as session:
        await mark_dispatched(
            session, voice_call_id, provider_call_id=handle.provider_call_id
        )
    return voice_call_id


# ---------------------------------------------------------------------------
# Internal pydantic shim so the LLM call validates against our exact shape.
# ---------------------------------------------------------------------------


from pydantic import BaseModel, Field  # noqa: E402  -- placed after to keep public imports tidy


class _VoiceQuestionSet(BaseModel):
    questions: list[VoiceQuestion] = Field(default_factory=list)


# Reuse hint -- some callers may want a path that falls back to text screening
# if the candidate has no phone on file.
__all__ = ["dispatch_voice_screening", "generate_screening_questions"]
