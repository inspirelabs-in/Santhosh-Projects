"""Stage 6: Score screening responses.

Three-layer scoring:
  1. Knock-outs (deterministic)      -- CTC, notice period, must-have skill, location
  2. Scored questions (weighted)     -- candidate's pick matches/doesn't match rubric
  3. Open-text (LLM rubric)          -- SCORE_OPEN_TEXT_V1 for each free-text answer

Composite is the weighted average scaled to 0-100. Candidates within
±10 points of the role's `cut_line` go to `needs_hr_review`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from src.config import get_settings
from src.db.base import CandidateProfileRow, ScreeningResponseRow
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.candidate import get_application, update_application_status
from src.db.repositories.role import get_role
from src.llm.client import get_llm_client
from src.llm.prompts import SCORE_OPEN_TEXT_V1, SCORE_OPEN_TEXT_VERSION
from src.models.candidate import ApplicationStatus, CandidateProfile
from src.models.llm_outputs import OpenTextScore
from src.models.screening import (
    KnockOutType,
    ScoreResult,
    ScreeningQuestion,
    ScreeningResponseItem,
)

logger = logging.getLogger(__name__)
_settings = get_settings()

GREY_ZONE_WIDTH = 10  # ±N points around cut_line


@dataclass
class ScoreResponsesInput:
    candidate_id: UUID
    application_id: UUID


# ---------------------------------------------------------------------------
# Knock-out checks
# ---------------------------------------------------------------------------


def _check_knockouts(
    questions: list[ScreeningQuestion],
    responses: list[ScreeningResponseItem],
    profile: CandidateProfile,
    role_ctc_max_lpa: float | None,
    role_max_notice_days: int | None,
) -> tuple[bool, str | None]:
    responses_by_id = {r.question_id: r for r in responses}

    for q in questions:
        if not q.knock_out_value:
            continue

        if q.type == KnockOutType.CTC_CHECK.value:
            # Candidate's expected CTC from profile (or from the answer itself)
            expected = profile.expected_ctc_lpa
            if expected is None:
                r = responses_by_id.get(q.id)
                try:
                    expected = float(r.answer) if r and r.answer else None
                except ValueError:
                    expected = None
            if (
                expected is not None
                and role_ctc_max_lpa is not None
                and expected > role_ctc_max_lpa * 1.15
            ):
                return (
                    True,
                    f"Expected CTC ({expected}L) exceeds budget ({role_ctc_max_lpa}L) by >15%",
                )

        elif q.type == KnockOutType.NOTICE_PERIOD_CHECK.value:
            notice = profile.notice_period_days
            if notice is None:
                r = responses_by_id.get(q.id)
                try:
                    notice = int(r.answer) if r and r.answer else None
                except ValueError:
                    notice = None
            if (
                notice is not None
                and role_max_notice_days is not None
                and notice > role_max_notice_days
            ):
                return (
                    True,
                    f"Notice period ({notice}d) exceeds max ({role_max_notice_days}d)",
                )

        elif q.type == KnockOutType.MUST_HAVE_SKILL.value:
            r = responses_by_id.get(q.id)
            if r is None or r.answer.strip().lower() in ("no", "false", "0", ""):
                return True, f"Missing must-have: {q.question}"

        elif q.type == KnockOutType.LOCATION_CHECK.value:
            r = responses_by_id.get(q.id)
            if r is None or r.answer.strip().lower() != q.knock_out_value.lower():
                return True, f"Location requirement not met: {q.question}"

    return False, None


# ---------------------------------------------------------------------------
# Scored + open-text calculation
# ---------------------------------------------------------------------------


async def _score_open_text(
    *,
    question: ScreeningQuestion,
    response: ScreeningResponseItem,
    candidate_id: UUID,
    application_id: UUID,
) -> OpenTextScore:
    prompt = SCORE_OPEN_TEXT_V1.format(
        question_text=question.question,
        rubric_description=question.rubric_description or "relevance, specificity, depth",
        answer_text=response.answer[:4000],
    )
    client = get_llm_client()
    result = await client.complete(
        prompt=prompt,
        response_model=OpenTextScore,
        model=_settings.llm_model_smart,
        trace_name="score_open_text",
        prompt_version=SCORE_OPEN_TEXT_VERSION,
        candidate_id=candidate_id,
        application_id=application_id,
        temperature=0.0,
        max_tokens=500,
        metadata={"question_id": question.id},
    )
    return result.parsed


def _scored_question_score(
    question: ScreeningQuestion, answer: str, rubric: dict
) -> float:
    """Normalised 0..1 score for a non-open-text scored question.

    The `scoring_rubric` JSONB column on Role contains a map
    `{question_id: {"answer_scores": {"value": 0..10}}}` that overrides the
    default (1 if answer non-empty, else 0). Anything beyond that lives in
    the rubric -- keep the activity logic simple.
    """
    per_q = (rubric or {}).get("per_question", {}).get(question.id) or {}
    mapping: dict[str, float] = per_q.get("answer_scores", {})
    if not mapping:
        return 1.0 if answer.strip() else 0.0
    raw = mapping.get(answer.strip().lower())
    if raw is None:
        return 0.0
    return max(0.0, min(float(raw) / question.max_score, 1.0))


async def _get_latest_screening_response(
    session: AsyncSession, application_id: UUID
) -> ScreeningResponseRow | None:
    return await session.scalar(
        select(ScreeningResponseRow)
        .where(ScreeningResponseRow.application_id == application_id)
        .order_by(ScreeningResponseRow.id.desc())
        .limit(1)
    )


async def _get_latest_profile_for_candidate(
    session: AsyncSession, candidate_id: UUID
) -> CandidateProfileRow | None:
    return await session.scalar(
        select(CandidateProfileRow)
        .where(CandidateProfileRow.candidate_id == candidate_id)
        .order_by(CandidateProfileRow.created_at.desc())
        .limit(1)
    )


async def run_score_responses(payload: ScoreResponsesInput) -> ScoreResult:
    async with session_scope() as session:
        app = await get_application(session, payload.application_id)
        if app is None or app.role_id is None:
            raise ValueError("application missing or has no role")
        role = await get_role(session, app.role_id)
        if role is None:
            raise ValueError("role not found")
        response_row = await _get_latest_screening_response(session, payload.application_id)
        if response_row is None:
            raise ValueError("no screening response found")
        profile_row = await _get_latest_profile_for_candidate(session, payload.candidate_id)
        profile = (
            CandidateProfile.model_validate(profile_row.parsed_data)
            if profile_row is not None
            else CandidateProfile()
        )

        questions = [
            ScreeningQuestion.model_validate(q) for q in (role.screening_questions or [])
        ]
        responses = [
            ScreeningResponseItem.model_validate(r) for r in (response_row.responses or [])
        ]
        role_snapshot = {
            "cut_line": role.cut_line,
            "ctc_max_lpa": role.ctc_max_lpa,
            "max_notice_days": role.max_notice_days,
            "scoring_rubric": role.scoring_rubric or {},
        }
        response_row_id = response_row.id

    # 1. Knock-outs
    knocked, reason = _check_knockouts(
        questions,
        responses,
        profile,
        role_snapshot["ctc_max_lpa"],
        role_snapshot["max_notice_days"],
    )
    if knocked:
        composite = 0
        recommendation = "reject"
    else:
        # 2. Weighted scored + open-text questions
        total_weight = 0.0
        weighted_sum = 0.0
        per_question: list[ScreeningResponseItem] = []

        responses_by_id = {r.question_id: r for r in responses}

        for q in questions:
            r = responses_by_id.get(q.id)
            if r is None:
                per_question.append(
                    ScreeningResponseItem(
                        question_id=q.id, answer="", score=0.0, rationale="no_answer"
                    )
                )
                total_weight += q.weight
                continue

            if q.type == "open_text":
                scored = await _score_open_text(
                    question=q,
                    response=r,
                    candidate_id=payload.candidate_id,
                    application_id=payload.application_id,
                )
                normalised = scored.score / q.max_score
                weighted_sum += normalised * q.weight
                total_weight += q.weight
                per_question.append(
                    ScreeningResponseItem(
                        question_id=q.id,
                        answer=r.answer,
                        score=float(scored.score),
                        rationale=scored.rationale,
                        flags=scored.flags,
                    )
                )
            elif q.type == "scored":
                normalised = _scored_question_score(q, r.answer, role_snapshot["scoring_rubric"])
                weighted_sum += normalised * q.weight
                total_weight += q.weight
                per_question.append(
                    ScreeningResponseItem(
                        question_id=q.id,
                        answer=r.answer,
                        score=round(normalised * q.max_score, 2),
                        rationale="scored_by_rubric",
                    )
                )
            else:
                # Knock-out-style questions passed the knock-out check -- give full credit.
                per_question.append(
                    ScreeningResponseItem(
                        question_id=q.id,
                        answer=r.answer,
                        score=float(q.max_score),
                        rationale="knockout_passed",
                    )
                )

        composite = int(round((weighted_sum / total_weight) * 100)) if total_weight > 0 else 0
        cut = role_snapshot["cut_line"]
        if abs(composite - cut) <= GREY_ZONE_WIDTH:
            recommendation = "hr_review"
        elif composite >= cut:
            recommendation = "shortlist"
        else:
            recommendation = "reject"

    # Persist the scored responses + status
    async with session_scope() as session:
        fresh = await session.get(ScreeningResponseRow, response_row_id)
        if fresh is not None:
            fresh.knock_out_triggered = knocked
            fresh.knock_out_reason = reason
            fresh.composite_score = composite
            fresh.responses = [r.model_dump(mode="json") for r in (per_question if not knocked else responses)]

        app = await get_application(session, payload.application_id)
        if app is not None:
            app.screening_score = composite
            next_status = {
                "shortlist": ApplicationStatus.SHORTLISTED,
                "hr_review": ApplicationStatus.NEEDS_HR_REVIEW,
                "reject": ApplicationStatus.REJECTED,
            }[recommendation]
            await update_application_status(session, payload.application_id, next_status)

        await log_audit(
            session,
            action="screening_scored",
            actor="agent",
            candidate_id=payload.candidate_id,
            application_id=payload.application_id,
            details={
                "composite_score": composite,
                "cut_line": role_snapshot["cut_line"],
                "knock_out_triggered": knocked,
                "knock_out_reason": reason,
                "recommendation": recommendation,
                "per_question": [p.model_dump(mode="json") for p in (per_question if not knocked else [])],
            },
            prompt_version=SCORE_OPEN_TEXT_VERSION,
        )

    return ScoreResult(
        application_id=payload.application_id,
        knock_out_triggered=knocked,
        knock_out_reason=reason,
        composite_score=composite,
        per_question_scores=per_question if not knocked else [],
        needs_hr_review=(recommendation == "hr_review"),
        cut_line=role_snapshot["cut_line"],
        recommendation=recommendation,
    )


@activity.defn(name="score_responses")
async def score_responses_activity(payload: ScoreResponsesInput) -> ScoreResult:
    return await run_score_responses(payload)
