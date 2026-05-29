"""Pydantic models for every structured LLM output in the pipeline.

Each model corresponds to one versioned prompt template in src/llm/prompts/.
LiteLLM returns raw JSON; the wrapper validates it against one of these
models before any value is used downstream.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.models.candidate import FitTier


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


class DimensionScore(BaseModel):
    model_config = ConfigDict(extra="ignore")

    score: int = Field(ge=0, le=100)
    rationale: str
    evidence: list[str] = Field(default_factory=list)


class FitDimensions(BaseModel):
    model_config = ConfigDict(extra="ignore")

    skills_match: DimensionScore
    experience_level: DimensionScore
    ctc_fit: DimensionScore
    location_notice_fit: DimensionScore


class FitAssessment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    overall_score: int = Field(ge=0, le=100)
    dimensions: FitDimensions
    red_flags: list[str] = Field(default_factory=list)
    green_flags: list[str] = Field(default_factory=list)
    recommended_tier: FitTier
    summary: str

    @field_validator("recommended_tier", mode="before")
    @classmethod
    def _lower(cls, v: object) -> object:
        return v.lower() if isinstance(v, str) else v


# ---------------------------------------------------------------------------
# Stage 6: SCORE_OPEN_TEXT_V1
# ---------------------------------------------------------------------------


class OpenTextScore(BaseModel):
    model_config = ConfigDict(extra="ignore")

    score: int = Field(ge=0, le=10)
    rationale: str
    evidence_quality: Literal["strong", "moderate", "weak"]
    flags: list[str] = Field(default_factory=list)


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
    recommendation: Literal["strong_yes", "yes", "borderline", "no", "strong_no"]
    rationale: str
    follow_up_questions: list[str] = Field(default_factory=list)
