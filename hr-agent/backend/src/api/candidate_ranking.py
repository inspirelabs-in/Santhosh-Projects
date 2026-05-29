"""Candidate ranking — compare and stack-rank applicants for the same role.

Produces a composite score from fit_score + screening + assignment + interview
feedback, with AI-generated comparison summary.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, select

from src.api.auth import require_viewer
from src.config import get_settings
from src.db.base import (
    Application,
    AssignmentRow,
    Candidate,
    CandidateProfileRow,
    Interview,
    Role,
    ScreeningAnswerRow,
    VoiceCall,
)
from src.db.connection import session_scope
from src.llm.client import get_llm_client

logger = logging.getLogger(__name__)
_settings = get_settings()

router = APIRouter(prefix="/ranking", tags=["ranking"])


class RankedCandidate(BaseModel):
    rank: int
    application_id: UUID
    candidate_id: UUID
    candidate_name: str | None
    candidate_email: str | None
    composite_score: float
    fit_score: int | None
    screening_score: int | None
    assignment_score: int | None
    interview_score: int | None
    voice_score: int | None
    current_stage: str
    strengths: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)


class RankingResponse(BaseModel):
    role_id: UUID
    role_title: str
    candidates: list[RankedCandidate]
    ai_summary: str | None = None


def _composite(
    fit: int | None,
    screening: int | None,
    assignment: int | None,
    interview: int | None,
    voice: int | None,
) -> float:
    """Weighted composite score. Handles missing components gracefully."""
    weights = {
        "fit": (fit, 0.25),
        "screening": (screening, 0.25),
        "assignment": (assignment, 0.25),
        "interview": (interview, 0.15),
        "voice": (voice, 0.10),
    }
    total_weight = 0.0
    total_score = 0.0
    for _, (score, weight) in weights.items():
        if score is not None:
            total_weight += weight
            total_score += score * weight

    if total_weight == 0:
        return 0.0
    return round(total_score / total_weight, 1)


@router.get("/role/{role_id}", response_model=RankingResponse)
async def rank_candidates_for_role(
    role_id: UUID,
    _: Annotated[str, Depends(require_viewer)],
    include_rejected: bool = Query(False),
    generate_summary: bool = Query(False, description="AI-generated comparison (costs 1 LLM call)"),
) -> RankingResponse:
    """Stack-rank all candidates for a role by composite score."""
    async with session_scope() as session:
        role = await session.get(Role, role_id)
        if role is None:
            raise HTTPException(404, "Role not found")

        filters = [Application.role_id == role_id]
        if not include_rejected:
            filters.append(Application.status.notin_(("rejected", "withdrawn")))

        app_rows = (
            await session.execute(
                select(Application, Candidate)
                .join(Candidate, Candidate.id == Application.candidate_id)
                .where(and_(*filters))
                .order_by(desc(Application.fit_score).nullslast())
            )
        ).all()

        candidates: list[RankedCandidate] = []
        summary_inputs: list[dict] = []

        for app, cand in app_rows:
            # Assignment score
            assignment_score: int | None = None
            assignment_row = await session.scalar(
                select(AssignmentRow)
                .where(AssignmentRow.application_id == app.id)
                .limit(1)
            )
            if assignment_row and assignment_row.score is not None:
                assignment_score = assignment_row.score

            # Interview score (latest feedback)
            interview_score: int | None = None
            interview = await session.scalar(
                select(Interview)
                .where(
                    and_(
                        Interview.application_id == app.id,
                        Interview.feedback.isnot(None),
                    )
                )
                .order_by(desc(Interview.created_at))
                .limit(1)
            )
            if interview and interview.feedback:
                interview_score = interview.feedback.get("overall_score")

            # Voice score
            voice_score: int | None = None
            voice = await session.scalar(
                select(VoiceCall)
                .where(
                    and_(
                        VoiceCall.application_id == app.id,
                        VoiceCall.overall_score.isnot(None),
                    )
                )
                .order_by(desc(VoiceCall.created_at))
                .limit(1)
            )
            if voice:
                voice_score = voice.overall_score

            composite = _composite(
                app.fit_score,
                app.screening_score,
                assignment_score,
                interview_score,
                voice_score,
            )

            # Extract strengths/concerns from screening evaluation
            strengths: list[str] = []
            concerns: list[str] = []
            eval_data = app.screening_evaluation or {}
            if isinstance(eval_data, dict):
                strengths = list(eval_data.get("strengths", []))[:3]
                concerns = list(eval_data.get("red_flags", []))[:3]

            candidates.append(RankedCandidate(
                rank=0,
                application_id=app.id,
                candidate_id=cand.id,
                candidate_name=cand.name,
                candidate_email=cand.email,
                composite_score=composite,
                fit_score=app.fit_score,
                screening_score=app.screening_score,
                assignment_score=assignment_score,
                interview_score=interview_score,
                voice_score=voice_score,
                current_stage=app.current_stage,
                strengths=strengths,
                concerns=concerns,
            ))

            if generate_summary:
                summary_inputs.append({
                    "name": cand.name,
                    "composite": composite,
                    "fit": app.fit_score,
                    "screening": app.screening_score,
                    "assignment": assignment_score,
                    "stage": app.current_stage,
                    "strengths": strengths,
                    "concerns": concerns,
                })

    # Sort by composite score descending, assign ranks
    candidates.sort(key=lambda c: c.composite_score, reverse=True)
    for i, c in enumerate(candidates):
        c.rank = i + 1

    # Optional AI summary
    ai_summary: str | None = None
    if generate_summary and summary_inputs:
        try:
            client = get_llm_client()
            prompt = f"""Compare these candidates for the role "{role.title}".
Rank them and explain who is the strongest hire and why. Be concise (under 200 words).

Candidates:
{json.dumps(summary_inputs, indent=2, default=str)}"""

            result = await client.complete(
                prompt=prompt,
                model=_settings.llm_model_smart,
                trace_name="candidate_ranking",
                temperature=0.2,
                max_tokens=500,
            )
            ai_summary = result.text
        except Exception:
            logger.exception("AI ranking summary failed")

    return RankingResponse(
        role_id=role_id,
        role_title=role.title,
        candidates=candidates,
        ai_summary=ai_summary,
    )
