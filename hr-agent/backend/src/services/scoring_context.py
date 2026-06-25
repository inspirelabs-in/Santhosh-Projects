"""Shared helpers: serialize a role's persona-derived evaluation spec + company
context into prompt variables, and compute the weighted overall score from the
dynamic evaluation_spec dimensions.

Every scoring stage (fit, screening, voice, assignment, meeting) should use
``scoring_prompt_vars`` so they all ground their judgment in the SAME
role-specific criteria instead of hardcoded, one-size-fits-all culture text.

``compute_spec_weighted_score`` is the canonical weighted-average helper used
by fit_score.py (and can be reused by any other stage that scores
criteria_scores).

Usage in a scoring activity:
    from src.services.scoring_context import scoring_prompt_vars, compute_spec_weighted_score
    ...
    prompt = compile_prompt("fit_score", fallback=FIT_SCORE_V1, ...,
                            **scoring_prompt_vars(role.evaluation_spec, role.company_context))
    ...
    spec_overall = compute_spec_weighted_score(assessment.criteria_scores)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.models.llm_outputs import CriterionScore


def default_evaluation_spec(weights: dict[str, int]) -> dict[str, Any]:
    """Synthetic evaluation_spec for a role that has none yet (JD generation
    skipped/cleared). Shaped exactly like a real ``EvaluationSpec`` so it flows
    through the SAME ``criteria_scores`` path as a role-authored spec -- there
    is no separate hardcoded output schema for the "no spec" case, just a
    generic 2-dimension spec instead of a role-specific one.

    Normalizes to sum exactly 100 regardless of what's passed in: ``EvaluationSpec``
    rejects weights that don't sum to ~100, and a rejected spec would silently
    fall back to an empty ``evaluation_spec_json`` -- the exact silent-failure
    mode this rewrite exists to eliminate.
    """
    skills_w = max(0, int(weights.get("skills", 65)))
    experience_w = max(0, int(weights.get("experience", 35)))
    total = skills_w + experience_w
    if total <= 0:
        skills_w, experience_w = 65, 35
    elif total != 100:
        skills_w = round(skills_w * 100 / total)
        experience_w = 100 - skills_w

    return {
        "dimensions": [
            {
                "key": "skills_match",
                "label": "Skills Match",
                "weight": skills_w,
                "what_good_looks_like": [
                    "Demonstrates the specific skills, tools, and technologies named in "
                    "the job description with direct, concrete evidence (named projects, "
                    "named tools, real usage)."
                ],
                "anti_signals": [
                    "Lists tools/skills by name with no evidence of actual use or depth."
                ],
            },
            {
                "key": "experience_level",
                "label": "Experience & Trajectory",
                "weight": experience_w,
                "what_good_looks_like": [
                    "Years of relevant experience and career progression fit the "
                    "seniority this role expects."
                ],
                "anti_signals": [
                    "Experience is in an unrelated field, or the seniority claimed "
                    "doesn't match the actual scope of past roles."
                ],
            },
        ],
    }


def scoring_prompt_vars(
    evaluation_spec: dict[str, Any] | None,
    company_context: dict[str, Any] | None,
) -> dict[str, str]:
    """Return ``{evaluation_spec_json, company_context_json}`` for prompt
    substitution.

    ``evaluation_spec_json`` is now a **clean JSON array of dimensions only**
    (strips spec-level metadata like knockouts, generated_from_persona_version,
    etc.).  Each item carries exactly:
        {key, label, weight, what_good_looks_like, anti_signals}

    This lets the prompt iterate over the array directly and score each entry
    by its own weight/signals — no prompt-side parsing of the full spec object.

    Falls back to ``"[]"`` only on a malformed spec (e.g. weights that don't
    sum to ~100) -- callers that need the dynamic-only contract (fit_score)
    should pass ``default_evaluation_spec(...)`` instead of an empty/missing
    spec so this never has to fall back in practice.

    ``company_context_json`` is the full context dict (unchanged).
    """
    dims_json = "[]"
    if evaluation_spec:
        try:
            from src.models.evaluation import EvaluationSpec

            spec = EvaluationSpec.model_validate(evaluation_spec)
            dims_json = json.dumps(
                [
                    d.model_dump(
                        include={"key", "label", "weight", "what_good_looks_like", "anti_signals"}
                    )
                    for d in spec.dimensions
                ],
                ensure_ascii=False,
                indent=2,
            )
        except Exception:  # noqa: BLE001
            pass

    return {
        "evaluation_spec_json": dims_json,
        "company_context_json": json.dumps(company_context or {}, ensure_ascii=False, indent=2),
    }


def compute_spec_weighted_score(criteria_scores: list["CriterionScore"]) -> int | None:
    """Weighted average over the role's OWN evaluation_spec dimensions.

    Uses each ``CriterionScore``'s ``weight`` field (the recruiter's own
    per-dimension weights) to compute the overall.  Items with ``score is None``
    are skipped.  Returns ``None`` when no item carries a real score OR when
    total weight of scored items is 0, so the caller can fall back to the
    legacy skills/experience rubric.

    Args:
        criteria_scores: list of CriterionScore objects (from FitAssessment or
            equivalent stage output).

    Returns:
        Rounded integer overall score in [0, 100], or None.
    """
    scored = [
        (c.score, max(0, int(c.weight or 0)))
        for c in (criteria_scores or [])
        if getattr(c, "score", None) is not None
    ]
    scored = [(s, w) for (s, w) in scored if w > 0]
    if not scored:
        return None
    total_weight = sum(w for _, w in scored)
    if total_weight == 0:
        return None
    return round(sum(s * w for s, w in scored) / total_weight)


