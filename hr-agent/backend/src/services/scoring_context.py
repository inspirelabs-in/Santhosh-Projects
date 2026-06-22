"""Shared helper: serialize a role's persona-derived evaluation spec + company
context into prompt variables, so every scoring stage (fit, screening, voice,
assignment, meeting) grounds its judgment in the SAME role-specific criteria
instead of hardcoded, one-size-fits-all culture text.

Usage in a scoring activity:
    from src.services.scoring_context import scoring_prompt_vars
    ...
    prompt = compile_prompt("fit_score", fallback=FIT_SCORE_V1, ...,
                            **scoring_prompt_vars(role.evaluation_spec, role.company_context))
"""

from __future__ import annotations

import json
from typing import Any


def scoring_prompt_vars(
    evaluation_spec: dict[str, Any] | None,
    company_context: dict[str, Any] | None,
) -> dict[str, str]:
    """Return ``{evaluation_spec_json, company_context_json}`` for prompt
    substitution. Empty objects when unset (the prompt then falls back to the JD
    + a generic, clearly-flagged stance rather than wrong culture defaults)."""
    return {
        "evaluation_spec_json": json.dumps(evaluation_spec or {}, ensure_ascii=False, indent=2),
        "company_context_json": json.dumps(company_context or {}, ensure_ascii=False, indent=2),
    }
