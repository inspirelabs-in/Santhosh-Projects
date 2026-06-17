"""Stage 4: Fit Score.

Scores the latest parsed `CandidateProfile` against the application's Role
via FIT_SCORE_V1 (Claude Sonnet). Produces per-dimension scores + evidence +
recommended tier. The tier decides whether the workflow advances to
screening or jumps to the rejection flow.

Tier rules (binary — no amber):
  - any knock_out → red
  - overall ≥ threshold (default 60) → green (advance to voice screen)
  - else → red (reject)

Dimensions are conditional: CTC and logistics are only scored when real data
exists (post-voice enrichment). The overall score is a weighted average of
only the scored dimensions, renormalized to sum to 100%.
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
from src.db.repositories.evidence import record_decision, record_evidence_batch_verified
from src.db.repositories.policy import resolve_policy
from src.db.repositories.role import get_role
from src.llm.client import get_llm_client
from src.llm.prompt_manager import compile_prompt
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

_DEFAULT_WEIGHTS = {
    "skills": 50,
    "experience": 25,
    "ctc": 15,
    "logistics": 10,
}

_DIM_KEY_MAP = {
    "skills_match": "skills",
    "experience_level": "experience",
    "ctc_fit": "ctc",
    "location_notice_fit": "logistics",
}

_WEIGHT_KEY_MAP = {v: k for k, v in _DIM_KEY_MAP.items()}


@dataclass
class FitScoreInput:
    candidate_id: UUID
    application_id: UUID
    suppress_notifications: bool = False


@dataclass
class FitScoreOutput:
    candidate_id: UUID
    application_id: UUID
    overall_score: int
    tier: FitTier
    knock_outs: list[str]
    trace_id: str | None


def _determine_tier(
    overall_score: int,
    knock_outs: list[str],
    green_threshold: int = 60,
) -> FitTier:
    if knock_outs:
        return FitTier.RED
    if overall_score >= green_threshold:
        return FitTier.GREEN
    return FitTier.RED


def _compute_weighted_score(assessment: FitAssessment, weights: dict[str, int]) -> int:
    """Compute overall score from only dimensions that have real data."""
    scored_dims: list[tuple[str, int, int]] = []
    dims = assessment.dimensions

    for dim_field, weight_key in _DIM_KEY_MAP.items():
        dim_score = getattr(dims, dim_field, None)
        if dim_score is not None and dim_score.is_scored:
            scored_dims.append((dim_field, dim_score.score, weights.get(weight_key, 0)))

    if not scored_dims:
        return 0

    total_weight = sum(w for _, _, w in scored_dims)
    if total_weight == 0:
        return 0

    weighted_sum = sum(score * weight for _, score, weight in scored_dims)
    return round(weighted_sum / total_weight)


from src.services.knockout import check_hard_knockouts


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

        green_threshold, green_rule_id = await resolve_policy(
            session, "fit_green_threshold", role.id, fallback=60
        )
        ctc_multiplier, ctc_rule_id = await resolve_policy(
            session, "ctc_overshoot_multiplier", role.id, fallback=1.15
        )
        no_profile_score, no_profile_rule_id = await resolve_policy(
            session, "fit_no_profile_default_score", role.id, fallback=40
        )
        w_skills, _ = await resolve_policy(session, "scoring_weights.skills", role.id, fallback=50)
        w_experience, _ = await resolve_policy(session, "scoring_weights.experience", role.id, fallback=25)
        w_ctc, _ = await resolve_policy(session, "scoring_weights.ctc", role.id, fallback=15)
        w_logistics, _ = await resolve_policy(session, "scoring_weights.logistics", role.id, fallback=10)
        default_weights = {"skills": w_skills, "experience": w_experience, "ctc": w_ctc, "logistics": w_logistics}

        profile_row = await get_latest_profile(session, payload.candidate_id)
        if profile_row is None:
            logger.info("no parsed profile for candidate %s — defaulting to RED", payload.candidate_id)
            return FitScoreOutput(
                candidate_id=payload.candidate_id,
                application_id=payload.application_id,
                overall_score=no_profile_score,
                tier=FitTier.RED,
                knock_outs=[],
                trace_id=None,
            )

        profile = CandidateProfile.model_validate(profile_row.parsed_data)
        weights = {**default_weights, **(role.scoring_rubric or {}).get("weights", {})}

        role_snapshot = {
            "title": role.title,
            "jd_text": role.jd_text,
            "ctc_min_lpa": role.ctc_min_lpa,
            "ctc_max_lpa": role.ctc_max_lpa,
            "max_notice_days": role.max_notice_days,
            "location": role.location,
            "remote_policy": role.remote_policy or RemotePolicy.ONSITE.value,
        }

    ko_result = check_hard_knockouts(
        expected_ctc=profile.expected_ctc_lpa,
        notice_days=profile.notice_period_days,
        role_ctc_max=role_snapshot["ctc_max_lpa"],
        role_max_notice_days=role_snapshot["max_notice_days"],
        role_remote_policy=role_snapshot["remote_policy"],
        ctc_multiplier=ctc_multiplier,
    )
    knock_outs = ko_result.reasons

    prompt = compile_prompt(
        "fit_score",
        fallback=FIT_SCORE_V1,
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

    overall = _compute_weighted_score(assessment, weights)
    assessment.overall_score = overall

    deterministic_tier = _determine_tier(
        overall, knock_outs,
        green_threshold=green_threshold,
    )
    tier_matches_model = deterministic_tier == assessment.recommended_tier

    async with session_scope() as session:
        application = await get_application(session, payload.application_id)
        if application is not None:
            application.fit_score = overall
            application.fit_tier = deterministic_tier.value
            application.status = (
                ApplicationStatus.REJECTED.value
                if deterministic_tier == FitTier.RED
                else ApplicationStatus.SCORED.value
            )

        dims_dump = assessment.dimensions.model_dump()
        pending = assessment.pending_verification

        audit_row = await log_audit(
            session,
            action="fit_scored",
            actor="agent",
            candidate_id=payload.candidate_id,
            application_id=payload.application_id,
            details={
                "overall_score": overall,
                "dimensions": dims_dump,
                "red_flags": assessment.red_flags,
                "green_flags": assessment.green_flags,
                "pending_verification": pending,
                "llm_tier": assessment.recommended_tier.value,
                "deterministic_tier": deterministic_tier.value,
                "tier_agreement": tier_matches_model,
                "knock_outs": knock_outs,
                "summary": assessment.summary,
                "weights_used": weights,
                "scoring_pass": "post_voice" if _has_voice_data(dims_dump) else "resume_only",
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            },
            model_version=result.model,
            prompt_version=result.prompt_version,
            langfuse_trace_id=result.trace_id,
        )

        if _settings.enable_evidence_collection:
            evidence_rows = []
            for dim_field in ("skills_match", "experience_level", "ctc_fit", "location_notice_fit"):
                dim_data = dims_dump.get(dim_field, {})
                if dim_data.get("score") is None:
                    continue
                is_post_voice = _has_voice_data(dims_dump)
                source_type = "voice_transcript" if is_post_voice and dim_field in ("ctc_fit", "location_notice_fit") else "resume"
                evidence_rows.append({
                    "application_id": payload.application_id,
                    "candidate_id": payload.candidate_id,
                    "fact_key": f"fit_score.{dim_field}",
                    "fact_value": dim_data.get("score"),
                    "source_stage": "fit_score",
                    "source_type": source_type,
                    "extraction_method": "llm",
                    "evidence_text": dim_data.get("rationale", "")[:500],
                    "confidence": None,
                    "langfuse_trace_id": result.trace_id,
                    "model_version": result.model,
                })
            for dim_field in ("skills_match", "experience_level", "ctc_fit", "location_notice_fit"):
                dim_data = dims_dump.get(dim_field, {})
                if dim_data.get("data_status") == "pending_verification":
                    evidence_rows.append({
                        "application_id": payload.application_id,
                        "candidate_id": payload.candidate_id,
                        "fact_key": f"fit_score.{dim_field}.pending",
                        "fact_value": "pending_verification",
                        "source_stage": "fit_score",
                        "source_type": "resume",
                        "extraction_method": "deterministic",
                        "evidence_text": dim_data.get("rationale", "Data not available in resume")[:500],
                    })
            for flag in assessment.red_flags:
                evidence_rows.append({
                    "application_id": payload.application_id,
                    "candidate_id": payload.candidate_id,
                    "fact_key": "fit_score.red_flag",
                    "fact_value": flag,
                    "source_stage": "fit_score",
                    "source_type": "resume",
                    "extraction_method": "llm",
                    "evidence_text": flag[:500],
                    "langfuse_trace_id": result.trace_id,
                    "model_version": result.model,
                })
            for ko in knock_outs:
                evidence_rows.append({
                    "application_id": payload.application_id,
                    "candidate_id": payload.candidate_id,
                    "fact_key": "fit_score.knockout",
                    "fact_value": ko,
                    "source_stage": "fit_score",
                    "source_type": "resume",
                    "extraction_method": "deterministic",
                    "evidence_text": ko[:500],
                })
            saved_evidence, _ = await record_evidence_batch_verified(
                session, records=evidence_rows, application_id=payload.application_id
            )
            policy_ids = [pid for pid in (green_rule_id, ctc_rule_id) if pid]
            await record_decision(
                session,
                application_id=payload.application_id,
                candidate_id=payload.candidate_id,
                decision_type="fit_score",
                outcome=deterministic_tier.value,
                outcome_value={
                    "overall_score": overall,
                    "knock_outs": knock_outs,
                    "llm_tier": assessment.recommended_tier.value,
                    "pending_verification": pending,
                },
                evidence_ids=[e.id for e in saved_evidence],
                policy_rule_ids=policy_ids,
                audit_log_id=audit_row.id,
                langfuse_trace_id=result.trace_id,
                model_version=result.model,
                prompt_version=result.prompt_version,
            )

    if not payload.suppress_notifications:
        if deterministic_tier == FitTier.GREEN:
            await teams_channel.notify_hr(
                title=f"Green-tier candidate: {role_snapshot['title']}",
                text=(
                    f"*Score:* {overall}/100\n"
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
                    f"Score: {overall}."
                ),
                fields={
                    "application_id": str(payload.application_id),
                    "knock_outs": ", ".join(knock_outs) or "none",
                },
            )

    return FitScoreOutput(
        candidate_id=payload.candidate_id,
        application_id=payload.application_id,
        overall_score=overall,
        tier=deterministic_tier,
        knock_outs=knock_outs,
        trace_id=result.trace_id,
    )


def _has_voice_data(dims_dump: dict) -> bool:
    """Check if CTC or logistics dimensions have real scores (post-voice)."""
    for key in ("ctc_fit", "location_notice_fit"):
        dim = dims_dump.get(key, {})
        if dim.get("score") is not None and dim.get("data_status") == "verified":
            return True
    return False


@activity.defn(name="fit_score")
async def fit_score_activity(payload: FitScoreInput) -> FitScoreOutput:
    return await run_fit_score(payload)
