"""Role evaluation spec — persona-derived, role-specific scoring criteria.

The problem this fixes: scoring criteria were hardcoded, so a *coupon editor* got
judged on "builder mindset" — a category error. Instead, an evaluation spec is
**generated at JD creation** from the org's ``hiring_persona`` + the role, then
**approved/edited by the user** and stored on ``roles.evaluation_spec``. Every
scoring prompt (fit, screening, voice, assignment, each interview) reads it, and
each pipeline stage selects the relevant subset via ``StageEvalSpec``.

Two levels:
  - role.evaluation_spec  -> the canonical dimensions for the whole role (here)
  - stage.eval_spec       -> which of those dimensions a given stage scores
                             (see models/pipeline.py: StageEvalSpec)
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class EvaluationDimension(BaseModel):
    """One scoring dimension for the role. ``key`` is the stable id that pipeline
    stages reference. Dimensions are generated *for this role* from the persona,
    never assumed — an engineering role and a content role get different sets."""

    key: str
    label: str
    weight: int = Field(ge=0, le=100)
    what_good_looks_like: list[str] = Field(default_factory=list)
    anti_signals: list[str] = Field(default_factory=list)
    description: str | None = None


class Knockout(BaseModel):
    """A hard disqualifier for the role (e.g. 'no editorial experience at all')."""

    key: str
    rule: str


class EvaluationSpec(BaseModel):
    """Stored on ``roles.evaluation_spec`` (JSONB). Generated from the persona,
    user-editable. Empty/unset = the role is ungoverned and falls back to a
    generic spec (flagged), never silently to engineering defaults."""

    dimensions: list[EvaluationDimension] = Field(default_factory=list)
    knockouts: list[Knockout] = Field(default_factory=list)
    generated_from_persona_version: int | None = None
    edited_by_user: bool = False
    generated_at: datetime | None = None
    prompt_version: str | None = None

    @model_validator(mode="after")
    def _weights_sane(self) -> "EvaluationSpec":
        # An empty spec is valid (not yet generated). Once dimensions exist,
        # weights should sum to ~100 (small tolerance for LLM rounding).
        if self.dimensions:
            total = sum(d.weight for d in self.dimensions)
            if not (95 <= total <= 105):
                raise ValueError(
                    f"evaluation dimension weights must sum to ~100, got {total}"
                )
        return self
