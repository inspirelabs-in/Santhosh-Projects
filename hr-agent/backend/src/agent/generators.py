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
from src.llm.model_registry import Stage, model_for
from src.llm.prompt_manager import compile_prompt
from src.services.scoring_context import scoring_prompt_vars

logger = logging.getLogger(__name__)
_settings = get_settings()


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "...[truncated]"


async def _company_persona() -> tuple[str, str]:
    """Fetch the org's company persona block + name from DB for prompt injection.

    Never raises: on any failure returns a minimal block so assignment
    generation still proceeds (degraded grounding beats a hard failure).
    """
    try:
        from src.db.connection import session_scope
        from src.db.repositories import organization as org_repo

        async with session_scope() as session:
            return await org_repo.company_persona_block(session)
    except Exception:  # noqa: BLE001
        logger.warning("company_persona fetch failed; using minimal block", exc_info=True)
        return (
            "# Company\n\nUse the company name in all candidate-facing text. "
            "Never invent fictional company names.",
            "the company",
        )


# [SCRAPE] dead: gen_tailored_questions (Chat-V2). KEEP gen_assignment (live).
async def gen_tailored_questions(
    *,
    role_title: str,
    jd_text: str,
    candidate_profile: dict[str, Any],
    application_id: UUID,
    candidate_id: UUID,
) -> TailoredQuestionsOut:
    prompt = compile_prompt(
        "tailored_qs",
        fallback=TAILORED_QS_V1,
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
    time_budget_hours: int,
    deadline_days: int,
    user_brief: str | None = None,
    evaluation_spec: dict | list | None = None,
    company_context: dict | None = None,
    problem_count: int = 2,
    application_id: UUID,
    candidate_id: UUID,
) -> AssignmentBriefOut:
    """Generate a ROLE-level take-home brief from the JD.

    There is no candidate at generation time (the brief is produced when the
    role is created/applied), so no candidate profile or screening answers are
    used. ``application_id``/``candidate_id`` are role-scoped UUIDs used only for
    LLM tracing.
    """
    # Company persona is org-scoped and editable in DB (organizations row), not
    # hardcoded in the prompt. Fetch + inject it so each org grounds assignments
    # in its own context. Falls back to a minimal block if the org is unset.
    company_persona, company_name = await _company_persona()
    user_brief_section = (
        (
            "## Recruiter's Requirements (MANDATORY — overrides JD)\n\n"
            "The recruiter EXPLICITLY specified these requirements. You MUST generate problems "
            "that DIRECTLY MATCH what the recruiter described. The recruiter's requirements "
            "are the PRIMARY SOURCE of truth — override any conflicting signals from the JD below. "
            "Do NOT fall back to generic JD-based problems. The recruiter's instructions:\n\n"
            + user_brief
        )
        if user_brief
        else ""
    )
    prompt = compile_prompt(
        "assignment_gen",
        fallback=ASSIGNMENT_GEN_V1,
        company_persona=company_persona,
        company_name=company_name,
        user_brief_section=user_brief_section,
        role_title=role_title,
        jd_text=_truncate(jd_text, 8000),
        time_budget_hours=time_budget_hours,
        deadline_days=deadline_days,
        problem_count=problem_count,
        **scoring_prompt_vars(evaluation_spec, company_context),
    )
    client = get_llm_client()
    # v2 schema is large (5 problems with rich per-problem fields + cover
    # context). 8K tokens fits the slimmed v2.1 schema. Single attempt for
    # latency reasons; failures bubble up to the caller which can fall back.
    result = await client.complete(
        prompt=prompt,
        response_model=AssignmentBriefOut,
        model=model_for(Stage.ASSIGNMENT_GEN),
        trace_name="agent.assignment_gen",
        prompt_version=ASSIGNMENT_GEN_VERSION,
        candidate_id=candidate_id,
        application_id=application_id,
        temperature=0.7,
        max_tokens=5000,
        max_attempts=2,
    )
    return result.parsed


# [SCRAPE] dead: extract_turn (Chat-V2). No live caller.
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
    prompt = compile_prompt(
        "extract_turn",
        fallback=EXTRACT_TURN_V1,
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
