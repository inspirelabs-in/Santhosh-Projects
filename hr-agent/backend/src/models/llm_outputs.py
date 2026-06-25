"""Pydantic models for every structured LLM output in the pipeline.

Each model corresponds to one versioned prompt template in src/llm/prompts/.
LiteLLM returns raw JSON; the wrapper validates it against one of these
models before any value is used downstream.
"""

from __future__ import annotations

from typing import Iterable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.models.candidate import FitTier


def literal_coercer(
    allowed: Iterable[str],
    default: str,
    synonyms: Mapping[str, str] | None = None,
) -> classmethod:
    """Build a ``mode="before"`` validator that keeps a strict ``Literal`` field
    from crashing the whole parse when the LLM emits an out-of-set value.

    LLMs invent enum values constantly -- a question ``type`` of ``"collaboration"``
    when fed dynamic evaluation-spec dimensions, ``"moderate"`` for a ``"medium"``
    level, ``"pass"`` for a ``"clear_pass"`` verdict. A bare ``Literal`` rejects
    these and raises ``LLMParseError``, which kills the activity even though the
    field is usually cosmetic metadata. This normalizer lowercases/strips the
    value, maps known synonyms, and falls back to ``default`` for anything else --
    mirroring the existing ``_coerce_score_to_int`` / ``_lower`` validators.

    Reuse: ``_v = field_validator("x", mode="before")(literal_coercer((...), "..."))``.
    """

    allowed_set = {a.lower() for a in allowed}
    syn = {k.lower(): v.lower() for k, v in (synonyms or {}).items()}

    def _coerce(cls: type, v: object) -> object:
        if isinstance(v, str):
            s = v.strip().lower()
            if s in allowed_set:
                return s
            return syn.get(s, default)
        return v

    return classmethod(_coerce)


# ---------------------------------------------------------------------------
# Stage 2: CLASSIFY_EMAIL_V1
# ---------------------------------------------------------------------------


class ClassificationResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    is_application: bool
    confidence: float = Field(ge=0.0, le=1.0)
    detected_role: str
    detected_role_confidence: float = Field(ge=0.0, le=1.0)
    is_referral: bool = False
    referrer_indicators: str = ""
    reasoning: str
    flags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Stage 4: FIT_SCORE_V1
# ---------------------------------------------------------------------------


class CriterionScore(BaseModel):
    """A score for ONE role-specific evaluation_spec dimension, keyed by the
    dimension's own `key`/`label`/`weight`. When the role carries an
    evaluation_spec, the model scores each dimension here and the overall is the
    weighted average of these (using the recruiter's own weights), so the
    dynamic criteria actually drive the number instead of a fixed rubric."""

    model_config = ConfigDict(extra="ignore")

    key: str = ""
    label: str = ""
    weight: int = 0
    score: int | None = Field(default=None, ge=0, le=100)
    rationale: str = ""
    evidence: list[str] = Field(default_factory=list)
    data_status: Literal["verified", "pending_verification"] = "verified"

    _norm_data_status = field_validator("data_status", mode="before")(
        literal_coercer(("verified", "pending_verification"), "verified")
    )

    @property
    def is_scored(self) -> bool:
        return self.score is not None


class FitAssessment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    overall_score: int = Field(ge=0, le=100)
    # The ONLY scored structure. Always populated -- the caller (fit_score.py)
    # synthesizes a generic 2-dimension spec for roles with no recruiter-authored
    # evaluation_spec, so there is never a parallel hardcoded dimension set to
    # keep in sync with this one.
    criteria_scores: list[CriterionScore] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    green_flags: list[str] = Field(default_factory=list)
    pending_verification: list[str] = Field(default_factory=list)
    # Score-only contract (v6+): the prompt no longer emits a verdict/tier; code
    # routes pass/needs_review/reject from overall_score. Kept optional for
    # backward-compat with any legacy response that still includes it.
    recommended_tier: FitTier | None = None
    summary: str

    @field_validator("recommended_tier", mode="before")
    @classmethod
    def _lower(cls, v: object) -> object:
        if isinstance(v, str):
            v = v.lower()
            if v == "amber":
                v = "green"
        return v


# ---------------------------------------------------------------------------
# Stage 6: SCORE_OPEN_TEXT_V1
# ---------------------------------------------------------------------------


class OpenTextScore(BaseModel):
    model_config = ConfigDict(extra="ignore")

    score: int = Field(ge=0, le=10)
    rationale: str
    evidence_quality: Literal["strong", "moderate", "weak"] = "moderate"
    flags: list[str] = Field(default_factory=list)

    _norm_evidence_quality = field_validator("evidence_quality", mode="before")(
        literal_coercer(
            ("strong", "moderate", "weak"),
            "moderate",
            synonyms={"medium": "moderate", "high": "strong", "low": "weak"},
        )
    )


# ---------------------------------------------------------------------------
# Stage 8a: REJECTION_MESSAGE_V1
# ---------------------------------------------------------------------------


class RejectionDraft(BaseModel):
    """Wraps the LLM's rejection body (the prompt returns the body only)."""

    model_config = ConfigDict(extra="ignore")

    body: str
    category_used: str  # one of SAFE_REJECTION_REASONS keys


# ---------------------------------------------------------------------------
# Stage 8b: INTERVIEW_REPORT_V1
# ---------------------------------------------------------------------------


class CompetencyRating(BaseModel):
    model_config = ConfigDict(extra="ignore")

    competency: str
    rating: int = Field(ge=0, le=5)
    evidence: list[str] = Field(default_factory=list)


class InterviewReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str
    strengths: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    competencies: list[CompetencyRating] = Field(default_factory=list)
    recommendation: Literal["strong_yes", "yes", "borderline", "no", "strong_no"] = "borderline"
    rationale: str
    follow_up_questions: list[str] = Field(default_factory=list)

    _norm_recommendation = field_validator("recommendation", mode="before")(
        literal_coercer(
            ("strong_yes", "yes", "borderline", "no", "strong_no"),
            "borderline",
            synonyms={
                "strong yes": "strong_yes",
                "strong no": "strong_no",
                "hire": "yes",
                "no_hire": "no",
                "no hire": "no",
                "maybe": "borderline",
                "neutral": "borderline",
            },
        )
    )
