"""Non-streaming LLM helpers used by pre-warm + assignment generation.

Run on the FAST model with structured-output validation. Called both at
invite-time (prewarm) and during the conversation (e.g. when the candidate
nudges the agent to share the assignment early).
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from src.agent.prompts import (
    ASSIGNMENT_GEN_V1,
    ASSIGNMENT_GEN_VERSION,
    EXTRACT_TURN_V1,
    EXTRACT_TURN_VERSION,
    TAILORED_QS_V1,
    TAILORED_QS_VERSION,
)
from src.agent.schemas import (
    AssignmentBriefOut,
    ExtractedTurn,
    TailoredQuestionsOut,
)
from src.config import get_settings
from src.llm.client import get_llm_client

logger = logging.getLogger(__name__)
_settings = get_settings()


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "...[truncated]"


async def gen_tailored_questions(
    *,
    role_title: str,
    jd_text: str,
    candidate_profile: dict[str, Any],
    application_id: UUID,
    candidate_id: UUID,
) -> TailoredQuestionsOut:
    prompt = TAILORED_QS_V1.format(
        role_title=role_title,
        jd_text=_truncate(jd_text, 4000),
        candidate_profile_json=json.dumps(candidate_profile, ensure_ascii=False)[:4000],
    )
    client = get_llm_client()
    result = await client.complete(
        prompt=prompt,
        response_model=TailoredQuestionsOut,
        trace_name="agent.tailored_qs",
        prompt_version=TAILORED_QS_VERSION,
        candidate_id=candidate_id,
        application_id=application_id,
        temperature=0.3,
        max_tokens=900,
    )
    return result.parsed


async def gen_assignment(
    *,
    role_title: str,
    jd_text: str,
    candidate_profile: dict[str, Any],
    screening_answers: dict[str, Any] | None,
    time_budget_hours: int,
    deadline_days: int,
    application_id: UUID,
    candidate_id: UUID,
) -> AssignmentBriefOut:
    prompt = ASSIGNMENT_GEN_V1.format(
        role_title=role_title,
        jd_text=_truncate(jd_text, 4000),
        candidate_profile_json=json.dumps(candidate_profile, ensure_ascii=False)[:4000],
        screening_answers_json=json.dumps(screening_answers or {}, ensure_ascii=False)[:2000],
        time_budget_hours=time_budget_hours,
        deadline_days=deadline_days,
    )
    client = get_llm_client()
    # v2 schema is large (5 problems with rich per-problem fields + cover
    # context). 8K tokens fits the slimmed v2.1 schema. Single attempt for
    # latency reasons; failures bubble up to the caller which can fall back.
    result = await client.complete(
        prompt=prompt,
        response_model=AssignmentBriefOut,
        model=client.smart,
        trace_name="agent.assignment_gen",
        prompt_version=ASSIGNMENT_GEN_VERSION,
        candidate_id=candidate_id,
        application_id=application_id,
        temperature=0.4,
        max_tokens=4000,
        max_attempts=2,
    )
    return result.parsed


async def extract_turn(
    *,
    candidate_message: str,
    already_captured: dict[str, Any],
    pending_questions: list[dict[str, Any]],
    recent_history: list[dict[str, str]],
    application_id: UUID,
    candidate_id: UUID,
) -> ExtractedTurn:
    history_str = "\n".join(
        f"{m['role']}: {_truncate(m.get('content') or '', 400)}"
        for m in recent_history[-4:]
    )
    prompt = EXTRACT_TURN_V1.format(
        candidate_message=_truncate(candidate_message, 2000),
        already_captured_json=json.dumps(already_captured, ensure_ascii=False),
        pending_questions_json=json.dumps(pending_questions, ensure_ascii=False),
        recent_history=history_str or "(none)",
    )
    client = get_llm_client()
    result = await client.complete(
        prompt=prompt,
        response_model=ExtractedTurn,
        trace_name="agent.extract_turn",
        prompt_version=EXTRACT_TURN_VERSION,
        candidate_id=candidate_id,
        application_id=application_id,
        temperature=0.0,
        max_tokens=400,
    )
    return result.parsed
