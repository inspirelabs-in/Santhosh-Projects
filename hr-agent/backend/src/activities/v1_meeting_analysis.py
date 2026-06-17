"""Analyze a finished Teams interview: LLM scoring + emotion timeline.

The Recall.ai webhook fires this activity once the bot delivers a transcript.
We pull the transcript JSON, render it for the LLM, and persist a structured
``MeetingAnalysis`` against the ``meeting_sessions`` row. Stage advances
based on round + verdict.
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
from src.db.repositories.meeting_session import get_session, save_analysis
from src.db.repositories.v1_application import set_stage
from src.llm.client import get_llm_client
from src.llm.prompt_manager import compile_prompt
from src.llm.prompts.meeting_analysis import (
    MEETING_ANALYSIS_V1,
    MEETING_ANALYSIS_VERSION,
)
from src.models.v1 import MeetingAnalysis, MeetingRound, PipelineStage
from src.services.auto_progress import auto_progress, auto_reject_if_configured
from src.services.file_storage import download

logger = logging.getLogger(__name__)


async def analyze_meeting(*, meeting_session_id: UUID) -> MeetingAnalysis:
    settings = get_settings()
    if not settings.enable_meeting_analysis:
        raise RuntimeError("meeting analysis disabled (ENABLE_MEETING_ANALYSIS=false)")

    async with session_scope() as session:
        meeting = await get_session(session, meeting_session_id)
        if meeting is None:
            raise ValueError(f"meeting_session {meeting_session_id} not found")
        if not meeting.transcript_r2_key:
            raise ValueError("transcript not yet stored")

        application = await session.get(Application, meeting.application_id)
        if application is None:
            raise ValueError("application missing")
        role = await session.get(Role, application.role_id) if application.role_id else None
        if role is None:
            raise ValueError("role missing")

        bucket = settings.r2_bucket_resumes
        candidate_id = application.candidate_id
        round_value = meeting.round
        scoring_rubric = role.scoring_rubric or {}
        role_title = role.title
        jd_excerpt = role.jd_text[:3500]
        emotion_features = meeting.candidate_emotion_timeline or []

    transcript_bytes = await download(bucket, meeting.transcript_r2_key)
    transcript_text = transcript_bytes.decode("utf-8", errors="replace")
    # Truncate aggressively to keep token cost in check; the LLM scores from
    # the most relevant 24k chars (~6k tokens). For longer interviews, swap
    # in a chunked map-reduce evaluator later.
    transcript_excerpt = transcript_text[:24_000]

    prompt = compile_prompt(
        "meeting_analysis",
        fallback=MEETING_ANALYSIS_V1,
        round=round_value,
        role_title=role_title,
        jd_text=jd_excerpt,
        scoring_rubric_json=json.dumps(scoring_rubric, ensure_ascii=False)[:2000],
        transcript_json=transcript_excerpt,
        paralinguistic_json=json.dumps(emotion_features, ensure_ascii=False)[:2000],
    )

    client = get_llm_client()
    result = await client.complete(
        prompt=prompt,
        response_model=MeetingAnalysis,
        model=client.smart,
        trace_name="meeting_analysis",
        prompt_version=MEETING_ANALYSIS_VERSION,
        candidate_id=candidate_id,
        application_id=application.id,
        system="You evaluate interview transcripts objectively. JSON only.",
        max_tokens=2500,
    )
    analysis = result.parsed
    analysis.evaluated_at = datetime.now(UTC)
    analysis.prompt_version = MEETING_ANALYSIS_VERSION

    async with session_scope() as session:
        await save_analysis(
            session,
            meeting_session_id,
            report=analysis.model_dump(mode="json"),
            llm_report=analysis.summary,
            technical_score=analysis.technical_score,
            communication_score=analysis.communication_score,
            confidence_score=analysis.confidence_score,
            overall_score=analysis.overall_score,
            verdict=analysis.verdict,
            candidate_emotion_timeline=[
                e.model_dump(mode="json")
                for e in analysis.candidate_emotion_timeline
            ],
        )

        # Stage routing depends on the round.
        transition = "noop"
        if round_value == MeetingRound.TECHNICAL.value:
            if analysis.verdict == "clear_pass":
                await set_stage(
                    session, application.id, PipelineStage.TECHNICAL_EVALUATED
                )
                await set_stage(
                    session, application.id, PipelineStage.TECHNICAL_PENDING_APPROVAL
                )
                transition = "pending_admin"
            elif analysis.verdict == "clear_reject":
                await set_stage(
                    session, application.id, PipelineStage.REJECTED, force=True
                )
                transition = "reject"
            else:
                from src.services.confidence_gate import should_auto_advance_gate
                can_skip = await should_auto_advance_gate(
                    session, application.id, "technical_evaluated",
                    role_id=application.role_id,
                    role_rubric=role.scoring_rubric if role else None,
                )
                if can_skip:
                    await set_stage(session, application.id, PipelineStage.TECHNICAL_EVALUATED)
                    await set_stage(session, application.id, PipelineStage.TECHNICAL_PENDING_APPROVAL)
                    transition = "confidence_auto_advance_to_pending"
                else:
                    await set_stage(
                        session, application.id, PipelineStage.NEEDS_HR_REVIEW, force=True
                    )
                    transition = "hr_review"
        elif round_value == MeetingRound.CEO.value:
            if analysis.verdict == "clear_reject":
                await set_stage(
                    session, application.id, PipelineStage.REJECTED, force=True
                )
                transition = "reject"
            else:
                await set_stage(
                    session, application.id, PipelineStage.CEO_MEETING_COMPLETED, force=True
                )
                await set_stage(
                    session, application.id, PipelineStage.CEO_PENDING_APPROVAL
                )
                transition = "pending_admin"
        elif round_value == MeetingRound.HR.value:
            if analysis.verdict == "clear_reject":
                await set_stage(
                    session, application.id, PipelineStage.REJECTED, force=True
                )
                transition = "reject"
            else:
                await set_stage(
                    session, application.id, PipelineStage.HR_MEETING_COMPLETED, force=True
                )
                await set_stage(
                    session, application.id, PipelineStage.HR_EVALUATED
                )
                transition = "pending_admin"

        audit_row = await log_audit(
            session,
            candidate_id=candidate_id,
            application_id=application.id,
            action="meeting_analyzed",
            actor="agent",
            details={
                "meeting_session_id": str(meeting_session_id),
                "round": round_value,
                "verdict": analysis.verdict,
                "overall_score": analysis.overall_score,
                "trace_id": result.trace_id,
            },
            model_version=result.model,
            prompt_version=MEETING_ANALYSIS_VERSION,
            langfuse_trace_id=result.trace_id,
        )

        if settings.enable_evidence_collection:
            _common = {
                "application_id": application.id,
                "candidate_id": candidate_id,
                "source_stage": f"meeting_{round_value}",
                "source_type": "meeting_transcript",
                "extraction_method": "llm",
                "langfuse_trace_id": result.trace_id,
                "model_version": result.model,
            }
            evidence_rows = []
            for flag in analysis.red_flags:
                evidence_rows.append({
                    **_common, "fact_key": f"meeting.{round_value}.red_flag",
                    "fact_value": flag, "evidence_text": flag[:500],
                })
            for strength in analysis.strengths:
                evidence_rows.append({
                    **_common, "fact_key": f"meeting.{round_value}.strength",
                    "fact_value": strength, "evidence_text": strength[:500],
                })
            if analysis.technical_score is not None:
                evidence_rows.append({
                    **_common, "fact_key": f"meeting.{round_value}.technical_score",
                    "fact_value": analysis.technical_score,
                })
            if analysis.communication_score is not None:
                evidence_rows.append({
                    **_common, "fact_key": f"meeting.{round_value}.communication_score",
                    "fact_value": analysis.communication_score,
                })
            if analysis.confidence_score is not None:
                evidence_rows.append({
                    **_common, "fact_key": f"meeting.{round_value}.confidence_score",
                    "fact_value": analysis.confidence_score,
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
                candidate_id=candidate_id,
                decision_type=f"meeting_{round_value}",
                outcome=analysis.verdict,
                outcome_value={
                    "overall_score": analysis.overall_score,
                    "summary": analysis.summary[:500],
                },
                evidence_ids=[e.id for e in saved_evidence],
                audit_log_id=audit_row.id,
                langfuse_trace_id=result.trace_id,
                model_version=result.model,
                prompt_version=MEETING_ANALYSIS_VERSION,
            )

    # Auto-send feedback requests to interview panel
    try:
        from src.services.interview_intelligence import send_feedback_requests
        sent = await send_feedback_requests(meeting_session_id, round_value=round_value)
        if sent:
            logger.info("feedback requests sent to %d panelists", len(sent))
    except Exception:
        logger.warning("auto feedback request failed", exc_info=True)

    # Hand-off to the auto-progression engine.
    if transition == "pass":
        await auto_progress(application_id=application.id)
    elif transition == "reject":
        await auto_reject_if_configured(
            application_id=application.id,
            reason=f"meeting verdict={analysis.verdict} round={round_value}",
        )
    elif transition == "pending_admin":
        try:
            from src.services.admin_notify import notify_round_complete
            await notify_round_complete(
                application_id=application.id,
                round=round_value,
                analysis=analysis,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("admin notify failed: %s", exc)
    return analysis
