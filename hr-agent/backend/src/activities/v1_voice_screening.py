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
from src.llm.prompt_manager import compile_prompt
from src.llm.prompts.voice_screening import (
    VOICE_SCREEN_GEN_V1,
    VOICE_SCREEN_GEN_VERSION,
)
from src.models.candidate import CandidateProfile
from src.models.v1 import (
    GeneratedScreeningSet,
    PipelineStage,
    VoiceCallStatus,
    VoiceQuestion,
)
from src.services.voice_provider import VoiceCallSpec, get_voice_provider

logger = logging.getLogger(__name__)


def _build_system_prompt(
    *, role_title: str, company_name: str, attempt_no: int = 1
) -> str:
    if attempt_no > 1:
        opening = (
            "This is a RESCHEDULED callback. Start with: "
            f"'Hi, this is Aria calling from {company_name} — I'm calling back "
            f"as we discussed regarding the {role_title} role. "
            "Is now a good time to talk?'"
        )
    else:
        opening = (
            "Start with: 'Hi, this is Aria calling from "
            f"{company_name} regarding the {role_title} role you applied for. "
            "Is now a good time to talk for about 10 minutes?'"
        )

    return (
        "IDENTITY\n"
        f"You are Aria, a hiring assistant calling on behalf of {company_name}. "
        "You have a warm, professional tone — like a friendly recruiter, not a "
        "robotic form-reader. You are transparent that you are an AI when asked.\n\n"

        "OPENING\n"
        + opening + "\n\n"

        "If they say YES or seem open:\n"
        "Say: 'Great, thank you! I'll be asking you a few questions to understand "
        "your background better. Just so you know, this call is being recorded. "
        "Feel free to answer naturally — there are no trick questions here.'\n"
        "Then move into the questions.\n\n"

        "If they say NO or need to reschedule:\n"
        "Say: 'No problem at all! When would work better for you? I can call back "
        "on any weekday between 11 AM and 8 PM India time — just give me a specific "
        "date and time.'\n"
        "If they give an out-of-window time, gently steer: 'That falls a bit outside "
        "our window — could we do [nearest valid slot] instead?'\n"
        "Once confirmed, say: 'Perfect, I'll note that down. Talk to you then — have "
        "a good day!' Then invoke end_call.\n"
        "Capture internally as: CALLBACK_AT=<ISO8601>; REASON=<text>\n\n"

        "IF CANDIDATE ASKS 'ARE YOU A BOT / AI?'\n"
        "Answer honestly and briefly: 'Yes, I'm an AI assistant — Aria. "
        f"{company_name} uses me for the initial screening round. Your responses go to "
        "the hiring team who make all the decisions. Should we continue?'\n\n"

        "LANGUAGE HANDLING\n"
        "Conduct the entire interview in English.\n"
        "If the candidate switches to another language mid-call, say: "
        "'Just to keep things consistent for the hiring team — could we continue "
        "in English? Take your time.'\n"
        "If they switch again, say it once more, then continue in English regardless.\n"
        "Do not switch languages yourself under any circumstance.\n\n"

        "IF CANDIDATE ASKS ROLE/COMPANY QUESTIONS\n"
        "Keep it brief and redirect: 'That's a great question — the hiring team will "
        "walk you through all the details if you move forward. For now, I just want to "
        "get a sense of your background. Ready to start?'\n"
        "Do not make up or speculate on role details.\n\n"

        "BETWEEN QUESTIONS — USE NEUTRAL BRIDGING\n"
        "After each answer, use one brief neutral acknowledgment before the next "
        "question. Rotate through: 'Got it.', 'Thanks for sharing that.', "
        "'Understood.', 'Okay, noted.', 'Makes sense.'\n"
        "Do NOT evaluate answers. Never say 'great', 'perfect', 'impressive', or "
        "anything that sounds like scoring.\n"
        "Then transition with: 'Moving on — ' or 'Next question — ' before asking.\n\n"

        "PROBING THIN ANSWERS\n"
        "If an answer is very short or vague, probe once naturally:\n"
        "Frame it as curiosity, not interrogation: 'Just to get a clearer picture — "
        "[follow_up_hint]'\n"
        "Only probe once. If still thin, acknowledge and move on.\n\n"

        "HANDLING TANGENTS / LONG ANSWERS\n"
        "If a candidate rambles past ~90 seconds, gently redirect:\n"
        "'That's helpful context — let me make sure I capture the key point. "
        "[restate what you heard]. Does that capture it, or anything critical to add?'\n"
        "Then move on.\n\n"

        "HANDLING SILENCE OR UNCLEAR AUDIO\n"
        "If you hear silence or unclear audio: repeat your last question once. "
        "Do NOT hang up.\n"
        "If still nothing after the repeat: 'It seems like we may have a connection "
        "issue — can you hear me okay?'\n\n"

        "QUESTION FLOW\n"
        "Ask the questions one at a time, in order. Wait for a full answer before "
        "moving on. After the final answer, do the closing — do not rush it.\n\n"

        "CLOSING\n"
        "After the final answer, give it a beat, then say:\n"
        "'That's all the questions I had — thanks for taking the time. The "
        f"{company_name} team will review your responses and reach out over email with "
        "next steps. Have a great day!'\n"
        "Then invoke end_call.\n\n"

        "=== CRITICAL HANGUP RULES ===\n"
        "DO NOT invoke end_call during your own utterance or greeting.\n"
        "DO NOT hang up on silence, 'huh', 'hello', or short responses.\n"
        "Only invoke end_call AFTER all of these are true:\n"
        "(a) candidate has clearly responded at least once\n"
        "(b) you have completed all questions OR captured CALLBACK_AT OR "
        "candidate explicitly said goodbye\n"
        "(c) you have finished speaking the closing line"
    )


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
    if attempt_no > 1:
        first_message_override = (
            f"Hi, this is Aria calling from {company_name} — I'm calling back "
            f"as we discussed regarding the {role_title} role. "
            "Is now a good time to talk?"
        )
    else:
        first_message_override = (
            f"Hi, this is Aria calling from {company_name} regarding the "
            f"{role_title} role you applied for. "
            "Is now a good time to talk for about 10 minutes?"
        )

    spec = VoiceCallSpec(
        application_id=application_id,
        voice_call_id=voice_call_id,
        candidate_name=candidate_name,
        candidate_phone=candidate_phone,
        role_title=role_title,
        company_name=company_name,
        questions=questions,
        system_prompt=_build_system_prompt(
            role_title=role_title,
            company_name=company_name,
            attempt_no=attempt_no,
        ),
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
