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
from src.db.repositories.evidence import record_decision, record_evidence_batch_verified
from src.db.repositories.policy import resolve_policy
from src.db.repositories.v1_application import set_stage
from src.db.repositories.voice_call import get_voice_call, save_evaluation
from src.services.auto_progress import auto_progress
from src.llm.client import get_llm_client
from src.llm.prompt_manager import compile_prompt
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


from src.classifiers.voicemail import classify_voicemail


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
            # Try to reconstruct answers from the raw transcript file.
            if voice.transcript_r2_key:
                logger.warning(
                    "voice_call %s has no structured answers but has transcript — "
                    "building single-answer fallback from raw text",
                    voice_call_id,
                )
                try:
                    from src.services.file_storage import download
                    bucket = settings.r2_bucket_resumes
                    raw = await download(bucket, voice.transcript_r2_key)
                    transcript_text = raw.decode("utf-8", errors="replace").strip()
                    if transcript_text:
                        voice.answers = [{
                            "question_id": "full_transcript",
                            "question": "Full voice screening conversation",
                            "answer_transcript": transcript_text,
                            "duration_sec": voice.duration_sec,
                        }]
                except Exception:
                    logger.exception("failed to load transcript fallback for %s", voice_call_id)
            if not voice.answers:
                raise ValueError(f"voice_call {voice_call_id} has no answers to evaluate")

        # Quality gate: refuse to score effectively-blank transcripts. A call
        # that the webhook accepted but where the candidate barely spoke is a
        # telephony/agent failure, not a "this candidate is bad" signal. If we
        # let the LLM score it, it returns clear_reject with score=0 and the
        # candidate is effectively rejected on the basis of a dropped call.
        # The early-disconnect path in webhooks_voice already covers most of
        # these; this is the last-line guard for anything that slips through.
        blank_char_threshold, _ = await resolve_policy(
            session, "voice_screen_blank_char_threshold", fallback=80
        )
        blank_ratio_threshold, _ = await resolve_policy(
            session, "voice_screen_blank_ratio_threshold", fallback=0.5
        )

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
            total_chars < blank_char_threshold
            or (q_total >= 2 and ratio < blank_ratio_threshold)
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

        is_voicemail, vm_confidence, vm_reasoning = await classify_voicemail(
            voice.answers,
            application_id=voice.application_id,
            voice_call_id=voice_call_id,
        )
        if is_voicemail:
            await log_audit(
                session,
                application_id=voice.application_id,
                action="voice_call_voicemail_detected",
                actor="agent",
                details={
                    "voice_call_id": str(voice_call_id),
                    "confidence": vm_confidence,
                    "reasoning": vm_reasoning,
                },
            )
            from src.models.v1 import VoiceCallStatus
            from src.db.repositories.voice_call import mark_failed
            await mark_failed(
                session,
                voice_call_id,
                error=f"voicemail detected ({vm_confidence:.0%}): {vm_reasoning}",
                status=VoiceCallStatus.NO_ANSWER,
            )
            from src.services.typed_event_bus import EventType, publish_event
            app_for_event = await session.get(Application, voice.application_id)
            await publish_event(
                session, EventType.VOICEMAIL_DETECTED,
                application_id=voice.application_id,
                candidate_id=app_for_event.candidate_id if app_for_event else None,
                payload={"voice_call_id": str(voice_call_id), "confidence": vm_confidence, "reasoning": vm_reasoning},
                dedup_extra=str(voice_call_id),
            )
            raise ValueError("voicemail detected -- skipping LLM eval")

        application = await session.get(Application, voice.application_id)
        if application is None:
            raise ValueError("application missing")
        role = await session.get(Role, application.role_id) if application.role_id else None
        if role is None:
            raise ValueError("role missing for application")

        prompt = compile_prompt(
            "voice_screen_eval",
            fallback=VOICE_SCREEN_EVAL_V1,
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

        if score.verdict == "clear_pass":
            await set_stage(session, application.id, PipelineStage.VOICE_SCREEN_EVALUATED)
            transition = "pass"
        else:
            await set_stage(session, application.id, PipelineStage.REJECTED, force=True)
            application.status = "rejected"
            transition = "rejected"
            await log_audit(
                session,
                candidate_id=application.candidate_id,
                application_id=application.id,
                action="candidate_rejected",
                actor="agent",
                details={
                    "reason": score.verdict_rationale,
                    "source": "voice_screening",
                    "overall_score": score.overall_score,
                    "red_flags": score.red_flags[:5] if score.red_flags else [],
                },
            )

        audit_row = await log_audit(
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

        if settings.enable_evidence_collection and score.extracted_facts:
            facts = score.extracted_facts
            _common = {
                "application_id": application.id,
                "candidate_id": application.candidate_id,
                "source_stage": "voice_screening",
                "source_type": "voice_transcript",
                "extraction_method": "llm",
                "langfuse_trace_id": result.trace_id,
                "model_version": result.model,
            }
            evidence_rows = []
            for fk, fv in [
                ("total_experience_years", facts.total_experience_years),
                ("current_ctc_lpa", facts.current_ctc_lpa),
                ("expected_ctc_lpa", facts.expected_ctc_lpa),
                ("notice_period_days", facts.notice_period_days),
                ("current_location", facts.current_location),
                ("willing_to_relocate", facts.willing_to_relocate),
                ("current_employer", facts.current_employer),
                ("current_title", facts.current_title),
                ("primary_skills", facts.primary_skills),
            ]:
                if fv is None or fv == "" or fv == []:
                    continue
                evidence_rows.append({**_common, "fact_key": fk, "fact_value": fv})
            for flag in score.red_flags:
                evidence_rows.append({
                    **_common, "fact_key": "voice_screen.red_flag",
                    "fact_value": flag, "evidence_text": flag[:500],
                })
            saved_evidence, _ = (
                await record_evidence_batch_verified(
                    session, records=evidence_rows, application_id=application.id
                )
                if evidence_rows else ([], [])
            )
            await record_decision(
                session,
                application_id=application.id,
                candidate_id=application.candidate_id,
                decision_type="voice_screening",
                outcome=score.verdict,
                outcome_value={
                    "overall_score": score.overall_score,
                    "verdict_rationale": score.verdict_rationale[:500],
                },
                evidence_ids=[e.id for e in saved_evidence],
                audit_log_id=audit_row.id,
                langfuse_trace_id=result.trace_id,
                model_version=result.model,
                prompt_version=VOICE_SCREEN_EVAL_VERSION,
            )

    # Re-score fit with enriched profile data (CTC, notice, location from voice).
    # Runs outside the DB session so it reads the committed profile updates.
    try:
        from src.activities.fit_score import FitScoreInput, run_fit_score
        rescore_result = await run_fit_score(FitScoreInput(
            candidate_id=application.candidate_id,
            application_id=application.id,
            suppress_notifications=True,
        ))
        logger.info(
            "fit re-score after voice: app=%s score=%d tier=%s",
            application.id, rescore_result.overall_score, rescore_result.tier.value,
        )
        if transition == "pass" and rescore_result.tier.value == "red":
            logger.warning(
                "fit re-score RED after voice pass — rejecting: app=%s knockouts=%s",
                application.id, rescore_result.knock_outs,
            )
            async with session_scope() as sess:
                await set_stage(sess, application.id, PipelineStage.REJECTED, force=True)
                app_obj = await sess.get(Application, application.id)
                if app_obj:
                    app_obj.status = "rejected"
            transition = "rejected"
    except Exception:
        logger.exception("fit re-score after voice failed for app=%s — non-fatal", application.id)

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
