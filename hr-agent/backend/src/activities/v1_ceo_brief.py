"""Aggregate every round into a single CEO-facing markdown brief.

Pulls: resume parse + fit score, email screening evaluation, voice call,
assessment results, technical-meeting analysis. The CEO meeting itself is
analyzed separately and rendered alongside this brief on the dashboard.

Persists the markdown to ``applications.journey_report`` so the existing
``v1_dashboard`` queries can surface it without schema changes.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select

from src.db.base import (
    Application,
    AssessmentResult,
    Candidate,
    CandidateProfileRow,
    MeetingSession,
    Role,
    VoiceCall,
)
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.v1_application import save_journey_report
from src.llm.client import get_llm_client
from src.llm.prompt_manager import compile_prompt
from src.llm.prompts.ceo_brief import CEO_BRIEF_V1, CEO_BRIEF_VERSION
from src.llm.model_registry import Stage, model_for
from src.services.scoring_context import scoring_prompt_vars

logger = logging.getLogger(__name__)


async def generate_ceo_brief(*, application_id: UUID) -> str:
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise ValueError(f"application {application_id} not found")
        candidate = await session.get(Candidate, app.candidate_id)
        if candidate is None:
            raise ValueError("candidate missing")
        role = await session.get(Role, app.role_id) if app.role_id else None
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
        candidate_summary = (
            profile_row.parsed_data if profile_row is not None else {}
        )

        voice_calls = (
            await session.execute(
                select(VoiceCall)
                .where(VoiceCall.application_id == application_id)
                .order_by(VoiceCall.created_at.desc())
            )
        ).scalars().all()
        latest_voice = voice_calls[0] if voice_calls else None
        voice_block: dict[str, Any] = {}
        if latest_voice is not None:
            voice_block = {
                "status": latest_voice.status,
                "overall_score": latest_voice.overall_score,
                "verdict": latest_voice.verdict,
                "answers": latest_voice.answers,
                "evaluation": latest_voice.evaluation,
                "emotion_features": latest_voice.emotion_features,
            }

        assessments = (
            await session.execute(
                select(AssessmentResult)
                .where(AssessmentResult.application_id == application_id)
                .order_by(AssessmentResult.created_at.asc())
            )
        ).scalars().all()
        assessment_block = [
            {
                "kind": a.assessment_kind,
                "provider": a.provider,
                "fit_band": a.fit_band,
                "score": float(a.normalized_score) if a.normalized_score is not None else None,
                "percentile": float(a.percentile) if a.percentile is not None else None,
                "sub_scores": (a.normalized or {}).get("sub_scores", {}),
            }
            for a in assessments
        ]

        technical = (
            await session.execute(
                select(MeetingSession)
                .where(
                    MeetingSession.application_id == application_id,
                    MeetingSession.round == "technical",
                )
                .order_by(MeetingSession.created_at.desc())
            )
        ).scalars().first()
        technical_block: dict[str, Any] = {}
        if technical is not None:
            technical_block = {
                "overall_score": technical.overall_score,
                "technical_score": technical.technical_score,
                "communication_score": technical.communication_score,
                "confidence_score": technical.confidence_score,
                "verdict": technical.verdict,
                "report": technical.report,
                "llm_report": technical.llm_report,
            }

        screening_evaluation = app.screening_evaluation or {}
        candidate_id = candidate.id
        candidate_name = candidate.name or "Candidate"
        role_title = role.title
        jd_excerpt = role.jd_text[:3500]
        fit_score = app.fit_score
        fit_tier = app.fit_tier or "n/a"
        role_evaluation_spec = role.evaluation_spec
        role_company_context = role.company_context

        # Label for the technical interview round is role-tuned (the stage can be
        # relabelled per role). Resolve it from role_pipeline_stages, else use
        # the literal default.
        from src.db.repositories import role_pipeline_stage as stage_repo

        tech_stage = await stage_repo.get_stage(session, role.id, "technical")
        tech_round_label = (
            tech_stage.label if tech_stage and tech_stage.label else "Technical interview"
        )

    _NA = "DATA_NOT_AVAILABLE"
    prompt = compile_prompt(
        "ceo_brief",
        fallback=CEO_BRIEF_V1,
        role_title=role_title,
        jd_text=jd_excerpt,
        tech_round_label=tech_round_label,
        **scoring_prompt_vars(role_evaluation_spec, role_company_context),
        candidate_json=json.dumps(candidate_summary, ensure_ascii=False)[:4000],
        fit_score=fit_score if fit_score is not None else _NA,
        fit_tier=fit_tier if fit_score is not None else _NA,
        screening_evaluation_json=(
            json.dumps(screening_evaluation, ensure_ascii=False)[:3000]
            if screening_evaluation else _NA
        ),
        voice_call_json=(
            json.dumps(voice_block, ensure_ascii=False)[:4000]
            if voice_block else _NA
        ),
        assessment_json=(
            json.dumps(assessment_block, ensure_ascii=False)[:2000]
            if assessment_block else _NA
        ),
        technical_meeting_json=(
            json.dumps(technical_block, ensure_ascii=False)[:5000]
            if technical_block else _NA
        ),
    )

    # The shared LLM client returns Pydantic-validated JSON. Wrap the brief in
    # a single-field model so we keep using the same retry/audit machinery.
    from pydantic import BaseModel

    class _Brief(BaseModel):
        markdown: str

    client = get_llm_client()
    result = await client.complete(
        prompt=prompt
        + '\n\nReturn JSON only: {"markdown": "<the full markdown brief>"}',
        response_model=_Brief,
        model=model_for(Stage.CEO_BRIEF),
        trace_name="ceo_brief",
        prompt_version=CEO_BRIEF_VERSION,
        candidate_id=candidate_id,
        application_id=application_id,
        system="Output JSON only with the markdown field.",
        max_tokens=2200,
    )
    markdown = result.parsed.markdown
    trace_id = result.trace_id
    model_name = result.model

    async with session_scope() as session:
        await save_journey_report(session, application_id, markdown)
        await log_audit(
            session,
            candidate_id=candidate_id,
            application_id=application_id,
            action="ceo_brief_generated",
            actor="agent",
            details={
                "length_chars": len(markdown),
                "trace_id": trace_id,
            },
            model_version=model_name,
            prompt_version=CEO_BRIEF_VERSION,
            langfuse_trace_id=trace_id,
        )
    return markdown
