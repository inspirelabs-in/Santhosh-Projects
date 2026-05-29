"""Curated safe-reasons library for candidate rejection messages.

Internal scoring reasons (e.g. "skills_match score: 4/10") are NEVER exposed
to the candidate. The agent picks a category from this library; the LLM then
drafts the message using only the safe template.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RejectionCategory = Literal[
    "experience_mismatch",
    "ctc_mismatch",
    "notice_period",
    "skills_gap",
    "location_mismatch",
    "general",
]


@dataclass(frozen=True)
class SafeReason:
    category: RejectionCategory
    template: str


SAFE_REJECTION_REASONS: dict[RejectionCategory, SafeReason] = {
    "experience_mismatch": SafeReason(
        "experience_mismatch",
        "We were looking for deeper experience in {domain}, which was the primary requirement for this role.",
    ),
    "ctc_mismatch": SafeReason(
        "ctc_mismatch",
        "The compensation expectations for this role did not align with the budget allocated for this position.",
    ),
    "notice_period": SafeReason(
        "notice_period",
        "We needed someone who could start within {max_days} days, and your current notice period did not fit this timeline.",
    ),
    "skills_gap": SafeReason(
        "skills_gap",
        "This role requires strong expertise in {skill}, and we found candidates with more direct experience in this area.",
    ),
    "location_mismatch": SafeReason(
        "location_mismatch",
        "This role requires {location_requirement}, which did not match your current preferences.",
    ),
    "general": SafeReason(
        "general",
        "After careful review, we have decided to move forward with candidates whose profiles more closely match the specific requirements of this role.",
    ),
}


def pick_category(
    *,
    knock_outs: list[str] | None,
    red_flags: list[str] | None,
    stage: str,
) -> RejectionCategory:
    """Map the internal rejection signal to a safe, candidate-facing category.

    Heuristic, kept simple: first matching category wins. When multiple
    reasons exist (e.g. CTC + experience mismatch) we bias toward the one
    least likely to start an argument -- `general` is the fallback.
    """
    k = " ".join(knock_outs or []).lower()
    f = " ".join(red_flags or []).lower()
    blob = f"{k} {f}"

    if "notice" in blob:
        return "notice_period"
    if "ctc" in blob or "budget" in blob or "compensation" in blob:
        return "ctc_mismatch"
    if "skill" in blob or "expertise" in blob:
        return "skills_gap"
    if "location" in blob or "relocate" in blob or "onsite" in blob:
        return "location_mismatch"
    if "experience" in blob or "seniority" in blob:
        return "experience_mismatch"
    return "general"


def render_template(
    category: RejectionCategory,
    *,
    domain: str | None = None,
    skill: str | None = None,
    max_days: int | None = None,
    location_requirement: str | None = None,
) -> str:
    """Fill the safe template with role-specific substitutions, best effort.

    Missing variables fall back to generic phrasing so the template never
    ships with a raw `{placeholder}` to the candidate.
    """
    t = SAFE_REJECTION_REASONS[category].template
    return (
        t.replace("{domain}", domain or "the core area this role focuses on")
        .replace("{skill}", skill or "the primary technology this role requires")
        .replace("{max_days}", str(max_days) if max_days is not None else "a short window")
        .replace(
            "{location_requirement}",
            location_requirement or "on-site presence in Hyderabad",
        )
    )
