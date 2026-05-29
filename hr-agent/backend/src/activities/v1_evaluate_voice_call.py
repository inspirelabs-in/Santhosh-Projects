"""Post-call evaluator: score the phone-screen transcript and transition stage.

Invoked from ``/webhooks/voice/pipecat`` once the call ends and the transcript +
recording are persisted. Mirrors the email-screening evaluator but consumes
spoken answers and (optionally) paralinguistic features from the audio.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from src.config import get_settings
from src.db.base import Application, Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.v1_application import set_stage
from src.db.repositories.voice_call import get_voice_call, save_evaluation
from src.services.auto_progress import auto_progress, auto_reject_if_configured
from src.llm.client import get_llm_client
from src.llm.prompts.voice_screening import (
    VOICE_SCREEN_EVAL_V1,
    VOICE_SCREEN_EVAL_VERSION,
)
from src.models.v1 import (
    EmotionFeatures,
    PipelineStage,
    VoiceCallScore,
)

logger = logging.getLogger(__name__)


_VOICEMAIL_PATTERNS = (
    "leave a message",
    "leave your message",
    "after the beep",
    "after the tone",
    "voicemail",
    "voice mail",
    "currently unavailable",
    "not available right now",
    "please record",
    "you have reached",
)


def _detect_voicemail(answers: list[dict] | None, transcript_turns: int = 0) -> bool:
    """Heuristic voicemail detection from candidate answers.

    Scans the candidate-side text for telecom voicemail phrases. If matched,
    treat the call as a no-answer rather than a poor performance score.
    """
    if not answers:
        return False
    blob = " ".join(
        str(a.get("answer", "")).lower()
        for a in answers
        if isinstance(a, dict)
    )
    if not blob.strip():
        return False
    return any(p in blob for p in _VOICEMAIL_PATTERNS)


async def evaluate_voice_call(
    *,
    voice_call_id: UUID,
    paralinguistic: EmotionFeatures | None = None,
) -> VoiceCallScore:
    settings = get_settings()
    async with session_scope() as session:
        voice = await get_voice_call(session, voice_call_id)
        if voice is None:
            raise ValueError(f"voice_call {voice_call_id} not found")
        if not voice.answers:
            raise ValueError(f"voice_call {voice_call_id} has no answers to evaluate")

        # Quality gate: refuse to score effectively-blank transcripts. A call
        # that the webhook accepted but where the candidate barely spoke is a
        # telephony/agent failure, not a "this candidate is bad" signal. If we
        # let the LLM score it, it returns clear_reject with score=0 and the
        # candidate is effectively rejected on the basis of a dropped call.
        # The early-disconnect path in webhooks_voice already covers most of
        # these; this is the last-line guard for anything that slips through.
        total_chars = sum(
            len(str(a.get("answer_transcript") or a.get("answer") or "").strip())
            for a in (voice.answers or [])
            if isinstance(a, dict)
        )
        answered = sum(
            1
            for a in (voice.answers or [])
            if isinstance(a, dict)
            and (str(a.get("answer_transcript") or a.get("answer") or "").strip())
        )
        q_total = len(voice.answers or [])
        ratio = (answered / q_total) if q_total else 0.0
        too_blank = (
            total_chars < 80
            or (q_total >= 2 and ratio < 0.5)
        )
        if too_blank:
            from src.db.repositories.voice_call import mark_failed
            from src.models.v1 import VoiceCallStatus

            await mark_failed(
                session,
                voice_call_id,
                error=(
                    f"transcript_too_short chars={total_chars} "
                    f"answered={answered}/{q_total}"
                ),
                status=VoiceCallStatus.NO_ANSWER,
            )
            await set_stage(
                session,
                voice.application_id,
                PipelineStage.NEEDS_HR_REVIEW,
                force=True,
            )
            await log_audit(
                session,
                application_id=voice.application_id,
                action="voice_call_evaluation_skipped_blank",
                actor="agent",
                details={
                    "voice_call_id": str(voice_call_id),
                    "total_answer_chars": total_chars,
                    "answered_questions": answered,
                    "total_questions": q_total,
                },
            )
            # Do NOT call the LLM. Do NOT write a verdict. HR retries manually
            # or the dispatch_voice_screening retry path picks this up.
            raise ValueError(
                f"voice_call {voice_call_id} transcript too short to evaluate "
                f"({total_chars} chars, {answered}/{q_total} answered)"
            )

        # Voicemail short-circuit: if transcript matches voicemail patterns,
        # mark as no-answer and bail before LLM scoring. The retry path in
        # webhooks_voice will redial.
        if _detect_voicemail(voice.answers):
            await log_audit(
                session,
                application_id=voice.application_id,
                action="voice_call_voicemail_detected",
                actor="agent",
                details={"voice_call_id": str(voice_call_id)},
            )
            from src.models.v1 import VoiceCallStatus
            from src.db.repositories.voice_call import mark_failed
            await mark_failed(
                session,
                voice_call_id,
                error="voicemail detected in transcript",
                status=VoiceCallStatus.NO_ANSWER,
            )
            raise ValueError("voicemail detected -- skipping LLM eval")

        application = await session.get(Application, voice.application_id)
        if application is None:
            raise ValueError("application missing")
        role = await session.get(Role, application.role_id) if application.role_id else None
        if role is None:
            raise ValueError("role missing for application")

        prompt = VOICE_SCREEN_EVAL_V1.format(
            role_title=role.title,
            jd_text=role.jd_text[:4000],
            ctc_min_lpa=role.ctc_min_lpa if role.ctc_min_lpa is not None else "n/a",
            ctc_max_lpa=role.ctc_max_lpa if role.ctc_max_lpa is not None else "n/a",
            max_notice_days=role.max_notice_days if role.max_notice_days is not None else "n/a",
            role_location=role.location or "n/a",
            remote_policy=role.remote_policy or "n/a",
            answers_json=json.dumps(voice.answers, ensure_ascii=False)[:8000],
            paralinguistic_json=json.dumps(
                paralinguistic.model_dump(mode="json") if paralinguistic else {},
                ensure_ascii=False,
            ),
        )

        client = get_llm_client()
        result = await client.complete(
            prompt=prompt,
            response_model=VoiceCallScore,
            model=client.smart,
            trace_name="voice_screen_eval",
            prompt_version=VOICE_SCREEN_EVAL_VERSION,
            candidate_id=application.candidate_id,
            application_id=application.id,
            system="You evaluate phone-screen transcripts objectively. JSON only.",
            max_tokens=2000,
        )
        score = result.parsed
        score.evaluated_at = datetime.now(UTC)
        score.prompt_version = VOICE_SCREEN_EVAL_VERSION
        score.paralinguistic = paralinguistic

        await save_evaluation(
            session,
            voice_call_id,
            evaluation=score.model_dump(mode="json"),
            emotion_features=paralinguistic.model_dump(mode="json")
            if paralinguistic
            else None,
        )

        # Backfill candidate + application from extracted facts. Only writes
        # when the field is currently null/empty -- never overwrites resume
        # values, which the recruiter already curated.
        await _apply_extracted_facts(session, application, score.extracted_facts)

        # Stage routing: clear_pass auto-advances; everything else parks for
        # HR. Auto-reject is intentionally disabled during dev/test -- even
        # clear_reject verdicts route to NEEDS_HR_REVIEW so a human ratifies
        # the rejection. The verdict + rationale are still saved on the row
        # so HR sees the agent's recommendation.
        if score.verdict == "clear_pass" and score.overall_score >= settings.voice_agent_pass_threshold:
            await set_stage(session, application.id, PipelineStage.VOICE_SCREEN_EVALUATED)
            transition = "pass"
        else:
            await set_stage(session, application.id, PipelineStage.NEEDS_HR_REVIEW, force=True)
            transition = "hr_review"

        await log_audit(
            session,
            candidate_id=application.candidate_id,
            application_id=application.id,
            action="voice_screen_evaluated",
            actor="agent",
            details={
                "voice_call_id": str(voice_call_id),
                "verdict": score.verdict,
                "overall_score": score.overall_score,
                "trace_id": result.trace_id,
            },
            model_version=result.model,
            prompt_version=VOICE_SCREEN_EVAL_VERSION,
            langfuse_trace_id=result.trace_id,
        )

    # Outside the DB session: hand off to the auto-progression engine on a
    # clean pass. Auto-reject is disabled during dev; HR makes the call.
    if transition == "pass":
        await auto_progress(application_id=application.id)
    return score


async def _apply_extracted_facts(session, application, facts) -> None:
    """Write extracted phone-screen facts onto candidate / candidate_profile.

    Conservative: only fills fields that are currently null. Resume values
    take precedence -- HR sees both via the audit trail.
    """
    if facts is None:
        return
    from sqlalchemy import select as _select
    from src.db.base import Candidate, CandidateProfileRow

    candidate = await session.get(Candidate, application.candidate_id)
    if candidate is None:
        return

    written: dict[str, object] = {}

    # Candidate-level: location stored on profile, not Candidate. Skill list
    # also lives on profile. Only candidate.timezone may be filled here.
    if facts.current_location and (candidate.name or "").lower() != "":
        # placeholder: candidate has no `current_location` column, store on profile
        pass

    # Candidate profile (latest). Append-only updates.
    profile_row = (
        await session.execute(
            _select(CandidateProfileRow)
            .where(CandidateProfileRow.candidate_id == application.candidate_id)
            .order_by(CandidateProfileRow.created_at.desc())
            .limit(1)
        )
    ).scalars().first()
    if profile_row is not None:
        existing = dict(profile_row.parsed_data or {})
        # Only fill missing keys
        for key, val in [
            ("current_ctc_lpa", facts.current_ctc_lpa),
            ("expected_ctc_lpa", facts.expected_ctc_lpa),
            ("notice_period_days", facts.notice_period_days),
            ("current_location", facts.current_location),
            ("preferred_location", facts.preferred_location),
            ("willing_to_relocate", facts.willing_to_relocate),
            ("work_authorization", facts.work_authorization),
            ("total_experience_years", facts.total_experience_years),
            ("relevant_experience_years", facts.relevant_experience_years),
            ("current_employer", facts.current_employer),
            ("current_title", facts.current_title),
            ("highest_qualification", facts.highest_qualification),
        ]:
            if val is None or val == "" or val == []:
                continue
            if existing.get(key) in (None, "", [], 0):
                existing[key] = val
                written[key] = val
        # Skills: union without dropping existing
        if facts.primary_skills:
            cur_skills = list(existing.get("primary_skills") or [])
            merged = list({*cur_skills, *facts.primary_skills})
            if len(merged) != len(cur_skills):
                existing["primary_skills"] = merged
                written["primary_skills_added"] = list(set(facts.primary_skills) - set(cur_skills))
        if facts.languages_spoken:
            cur_langs = list(existing.get("languages_spoken") or [])
            merged_l = list({*cur_langs, *facts.languages_spoken})
            if len(merged_l) != len(cur_langs):
                existing["languages_spoken"] = merged_l
                written["languages_added"] = list(set(facts.languages_spoken) - set(cur_langs))
        if facts.notes:
            voice_notes = list(existing.get("voice_screen_notes") or [])
            voice_notes.append(facts.notes[:500])
            existing["voice_screen_notes"] = voice_notes
            written["notes_appended"] = True
        # Force JSONB rewrite by reassigning a new dict.
        profile_row.parsed_data = existing

    if written:
        from src.db.repositories.audit import log_audit
        await log_audit(
            session,
            candidate_id=application.candidate_id,
            application_id=application.id,
            action="voice_facts_extracted",
            actor="agent",
            details={"fields_filled": written},
        )
