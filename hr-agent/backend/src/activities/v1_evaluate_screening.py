"""V1 activity: evaluate candidate's screening submission.

Verdict: clear_pass | needs_hr_review | clear_reject.
V1 rule: only clear_pass auto-advances to ASSIGNMENT_SENT.
Everything else lands in NEEDS_HR_REVIEW. No auto-reject.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from src.db.base import Application, Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.v1_application import save_screening_evaluation
from src.llm.client import get_llm_client
from src.llm.prompts import SCREENING_EVAL_V1, SCREENING_EVAL_VERSION
from src.models.candidate import CandidateProfile
from src.models.v1 import ScreeningEvaluation, ScreeningSubmission

logger = logging.getLogger(__name__)


async def evaluate_screening(
    *,
    application_id: UUID,
    candidate_id: UUID,
    role_id: UUID,
    profile: CandidateProfile,
    questions: list[dict],
    submission: ScreeningSubmission,
) -> ScreeningEvaluation:
    async with session_scope() as session:
        role = await session.get(Role, role_id)
        if role is None:
            raise ValueError(f"role {role_id} not found")

        prompt = SCREENING_EVAL_V1.format(
            role_title=role.title,
            jd_text=role.jd_text[:4000],
            ctc_min_lpa=role.ctc_min_lpa if role.ctc_min_lpa is not None else "n/a",
            ctc_max_lpa=role.ctc_max_lpa if role.ctc_max_lpa is not None else "n/a",
            max_notice_days=role.max_notice_days if role.max_notice_days is not None else "n/a",
            candidate_profile_json=json.dumps(
                profile.model_dump(mode="json", exclude_none=True), ensure_ascii=False
            )[:4000],
            questions_json=json.dumps(questions, ensure_ascii=False)[:4000],
            responses_json=json.dumps(
                submission.model_dump(mode="json"), ensure_ascii=False
            )[:6000],
        )

        client = get_llm_client()
        result = await client.complete(
            prompt=prompt,
            response_model=ScreeningEvaluation,
            # Smart model for evaluation quality.
            model=client.smart,
            trace_name="screening_eval",
            prompt_version=SCREENING_EVAL_VERSION,
            candidate_id=candidate_id,
            application_id=application_id,
            system="You evaluate candidate screening responses objectively. Output JSON only.",
            max_tokens=2000,
        )

        evaluation = result.parsed
        evaluation.evaluated_at = datetime.now(UTC)
        evaluation.prompt_version = SCREENING_EVAL_VERSION

        await save_screening_evaluation(
            session, application_id, evaluation.model_dump(mode="json")
        )
        await log_audit(
            session,
            candidate_id=candidate_id,
            application_id=application_id,
            action="screening_evaluated",
            actor="agent",
            details={
                "verdict": evaluation.verdict,
                "overall_score": evaluation.overall_score,
                "trace_id": result.trace_id,
            },
            model_version=result.model,
            prompt_version=SCREENING_EVAL_VERSION,
            langfuse_trace_id=result.trace_id,
        )
        return evaluation
