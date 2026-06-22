"""V1 activity: generate tailored screening questions per applicant.

Called from the webhook BackgroundTask after intake+parse produce a
CandidateProfile. Writes the questions onto applications.screening_questions
and transitions stage to SCREENING_SENT once the email is dispatched.

Plain async function (no Temporal) -- runs inside FastAPI BackgroundTasks.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from src.db.base import Application, Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.v1_application import save_screening_questions
from src.llm.client import get_llm_client
from src.llm.model_registry import Stage, model_for
from src.llm.prompt_manager import compile_prompt
from src.llm.prompts import SCREENING_GEN_V1, SCREENING_GEN_VERSION
from src.models.candidate import CandidateProfile
from src.models.v1 import GeneratedScreeningSet
from src.services.scoring_context import scoring_prompt_vars

logger = logging.getLogger(__name__)


async def generate_screening_questions(
    *,
    application_id: UUID,
    candidate_id: UUID,
    role_id: UUID,
    profile: CandidateProfile,
) -> GeneratedScreeningSet:
    """Generate 5-7 tailored screening questions for this candidate+role.

    Writes result to applications.screening_questions. Does NOT send email or
    change stage -- caller wires that.
    """
    async with session_scope() as session:
        role = await session.get(Role, role_id)
        if role is None:
            raise ValueError(f"role {role_id} not found")

        prompt = compile_prompt(
            "screening_gen",
            fallback=SCREENING_GEN_V1,
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
            **scoring_prompt_vars(role.evaluation_spec, role.company_context),
        )

        client = get_llm_client()
        result = await client.complete(
            prompt=prompt,
            response_model=GeneratedScreeningSet,
            model=model_for(Stage.SCREENING_GEN),
            trace_name="screening_gen",
            prompt_version=SCREENING_GEN_VERSION,
            candidate_id=candidate_id,
            application_id=application_id,
            system="You draft tailored candidate screening questionnaires. Output JSON only.",
            max_tokens=1500,
        )

        generated = result.parsed
        generated.generated_at = datetime.now(UTC)
        generated.prompt_version = SCREENING_GEN_VERSION

        await save_screening_questions(
            session, application_id, generated.model_dump(mode="json")
        )
        await log_audit(
            session,
            candidate_id=candidate_id,
            application_id=application_id,
            action="screening_questions_generated",
            actor="agent",
            details={
                "count": len(generated.questions),
                "model": result.model,
                "trace_id": result.trace_id,
            },
            model_version=result.model,
            prompt_version=SCREENING_GEN_VERSION,
            langfuse_trace_id=result.trace_id,
        )
        return generated
