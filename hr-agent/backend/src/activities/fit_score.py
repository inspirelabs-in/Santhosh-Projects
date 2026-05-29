"""Stage 4: Fit Score.

Scores the latest parsed `CandidateProfile` against the application's Role
via FIT_SCORE_V1 (Claude Sonnet). Produces per-dimension scores + evidence +
recommended tier. The tier decides whether the workflow advances to
screening or jumps to the rejection flow.

Tier rules (references/pipeline-stages.md):
  - any knock_out → red
  - overall ≥ 70  → green
  - overall ≥ 50  → amber
  - else          → red
The LLM's `recommended_tier` is treated as a hint; the deterministic rule
above wins when they disagree, and the divergence is written to audit.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from uuid import UUID

from temporalio import activity

from src.channels import teams as teams_channel
from src.config import get_settings
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.candidate import get_application
from src.db.repositories.candidate_profile import get_latest_profile
from src.db.repositories.role import get_role
from src.llm.client import get_llm_client
from src.llm.prompts import FIT_SCORE_V1, FIT_SCORE_VERSION
from src.models.candidate import (
    ApplicationStatus,
    CandidateProfile,
    FitTier,
    RemotePolicy,
)
from src.models.llm_outputs import FitAssessment

logger = logging.getLogger(__name__)
_settings = get_settings()

# Default weights (sum to 100). Overridden per-role via Role.scoring_rubric["weights"].
_DEFAULT_WEIGHTS = {
    "skills": 50,
    "experience": 25,
    "ctc": 15,
    "logistics": 10,
}


@dataclass
class FitScoreInput:
    candidate_id: UUID
    application_id: UUID


@dataclass
class FitScoreOutput:
    candidate_id: UUID
    application_id: UUID
    overall_score: int
    tier: FitTier
    knock_outs: list[str]
    trace_id: str | None


def _determine_tier(overall_score: int, knock_outs: list[str]) -> FitTier:
    if knock_outs:
        return FitTier.RED
    if overall_score >= 70:
        return FitTier.GREEN
    if overall_score >= 50:
        return FitTier.AMBER
    return FitTier.RED


def _compute_hard_knockouts(profile: CandidateProfile, role) -> list[str]:
    """Deterministic gates that run before the LLM sees anything.

    Kept separate from the screening-stage knock-outs (those require candidate
    answers). Here we only enforce the role's hard floors/ceilings when the
    resume explicitly contradicts them.
    """
    knocks: list[str] = []
    if (
        role.max_notice_days is not None
        and profile.notice_period_days is not None
        and profile.notice_period_days > role.max_notice_days
    ):
        knocks.append(
            f"notice_period_exceeds_max:{profile.notice_period_days}d>{role.max_notice_days}d"
        )
    if (
        role.ctc_max_lpa is not None
        and profile.expected_ctc_lpa is not None
        and profile.expected_ctc_lpa > role.ctc_max_lpa * 1.15
    ):
        knocks.append(
            f"expected_ctc_exceeds_budget:{profile.expected_ctc_lpa}L>{role.ctc_max_lpa}L+15%"
        )
    return knocks


async def run_fit_score(payload: FitScoreInput) -> FitScoreOutput:
    async with session_scope() as session:
        application = await get_application(session, payload.application_id)
        if application is None:
            raise ValueError(f"application {payload.application_id} not found")
        if application.role_id is None:
            raise ValueError(
                f"application {payload.application_id} has no role -- cannot score"
            )
        role = await get_role(session, application.role_id)
        if role is None:
            raise ValueError(f"role {application.role_id} not found")
        profile_row = await get_latest_profile(session, payload.candidate_id)
        if profile_row is None:
            logger.info("no parsed profile for candidate %s — defaulting to AMBER", payload.candidate_id)
            return FitScoreOutput(
                candidate_id=payload.candidate_id,
                application_id=payload.application_id,
                overall_score=55,
                tier=FitTier.AMBER,
                knock_outs=[],
                trace_id=None,
            )

        profile = CandidateProfile.model_validate(profile_row.parsed_data)
        weights = {**_DEFAULT_WEIGHTS, **(role.scoring_rubric or {}).get("weights", {})}

        # Snapshot role details for use outside the session.
        role_snapshot = {
            "title": role.title,
            "jd_text": role.jd_text,
            "ctc_min_lpa": role.ctc_min_lpa,
            "ctc_max_lpa": role.ctc_max_lpa,
            "max_notice_days": role.max_notice_days,
            "location": role.location,
            "remote_policy": role.remote_policy or RemotePolicy.ONSITE.value,
        }

    knock_outs = _compute_hard_knockouts(profile, type("R", (), role_snapshot))

    prompt = FIT_SCORE_V1.format(
        jd_text=role_snapshot["jd_text"][:8000],
        role_title=role_snapshot["title"],
        ctc_min_lpa=role_snapshot["ctc_min_lpa"] if role_snapshot["ctc_min_lpa"] is not None else "n/a",
        ctc_max_lpa=role_snapshot["ctc_max_lpa"] if role_snapshot["ctc_max_lpa"] is not None else "n/a",
        max_notice_days=role_snapshot["max_notice_days"] if role_snapshot["max_notice_days"] is not None else "n/a",
        role_location=role_snapshot["location"] or "n/a",
        remote_policy=role_snapshot["remote_policy"],
        candidate_profile_json=json.dumps(profile.model_dump(mode="json"), ensure_ascii=False, indent=2),
        skills_weight=weights["skills"],
        experience_weight=weights["experience"],
        ctc_weight=weights["ctc"],
        logistics_weight=weights["logistics"],
    )

    client = get_llm_client()
    result = await client.complete(
        prompt=prompt,
        response_model=FitAssessment,
        model=_settings.llm_model_smart,
        trace_name="fit_score",
        prompt_version=FIT_SCORE_VERSION,
        candidate_id=payload.candidate_id,
        application_id=payload.application_id,
        temperature=0.0,
        max_tokens=2000,
        metadata={"role_id": str(role_snapshot["title"])},
    )
    assessment = result.parsed

    deterministic_tier = _determine_tier(assessment.overall_score, knock_outs)
    tier_matches_model = deterministic_tier == assessment.recommended_tier

    async with session_scope() as session:
        application = await get_application(session, payload.application_id)
        if application is not None:
            application.fit_score = assessment.overall_score
            application.fit_tier = deterministic_tier.value
            application.status = (
                ApplicationStatus.REJECTED.value
                if deterministic_tier == FitTier.RED
                else ApplicationStatus.SCORED.value
            )

        await log_audit(
            session,
            action="fit_scored",
            actor="agent",
            candidate_id=payload.candidate_id,
            application_id=payload.application_id,
            details={
                "overall_score": assessment.overall_score,
                "dimensions": assessment.dimensions.model_dump(),
                "red_flags": assessment.red_flags,
                "green_flags": assessment.green_flags,
                "llm_tier": assessment.recommended_tier.value,
                "deterministic_tier": deterministic_tier.value,
                "tier_agreement": tier_matches_model,
                "knock_outs": knock_outs,
                "summary": assessment.summary,
                "weights_used": weights,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            },
            model_version=result.model,
            prompt_version=result.prompt_version,
            langfuse_trace_id=result.trace_id,
        )

    # Slack alerts: new green-tier candidate (HR wants to see these immediately),
    # and tier disagreement between deterministic rule + LLM (debugging signal).
    if deterministic_tier == FitTier.GREEN:
        await teams_channel.notify_hr(
            title=f"Green-tier candidate: {role_snapshot['title']}",
            text=(
                f"*Score:* {assessment.overall_score}/100\n"
                f"*Summary:* {assessment.summary}"
            ),
            fields={
                "application_id": str(payload.application_id),
                "candidate_id": str(payload.candidate_id),
                "green_flags": ", ".join(assessment.green_flags) or "—",
            },
        )
    if not tier_matches_model:
        await teams_channel.notify_alerts(
            title="Tier disagreement (agent vs rule)",
            text=(
                f"LLM said `{assessment.recommended_tier.value}`, "
                f"deterministic rule says `{deterministic_tier.value}`. "
                f"Score: {assessment.overall_score}."
            ),
            fields={
                "application_id": str(payload.application_id),
                "knock_outs": ", ".join(knock_outs) or "none",
            },
        )

    return FitScoreOutput(
        candidate_id=payload.candidate_id,
        application_id=payload.application_id,
        overall_score=assessment.overall_score,
        tier=deterministic_tier,
        knock_outs=knock_outs,
        trace_id=result.trace_id,
    )


@activity.defn(name="fit_score")
async def fit_score_activity(payload: FitScoreInput) -> FitScoreOutput:
    return await run_fit_score(payload)
