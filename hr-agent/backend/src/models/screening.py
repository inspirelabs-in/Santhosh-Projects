"""Screening question, response, and composite scoring schemas (Stages 5–6)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class KnockOutType(StrEnum):
    CTC_CHECK = "ctc_check"
    NOTICE_PERIOD_CHECK = "notice_period_check"
    MUST_HAVE_SKILL = "must_have_skill"
    LOCATION_CHECK = "location_check"


class ScreeningQuestion(BaseModel):
    """One question delivered on the screening form. Mirrors RoleRecord.screening_questions."""

    id: str
    question: str
    type: str  # "ctc_check" | "notice_period_check" | "must_have_skill" | "scored" | "open_text"
    weight: float = 0.0
    knock_out_value: str | None = None
    rubric_description: str | None = None
    max_score: int = 10
    options: list[str] | None = None
    required: bool = True


class ScreeningResponseItem(BaseModel):
    """One answer. `score`/`rationale` filled by the scorer, not the candidate."""

    question_id: str
    answer: str
    score: float | None = None
    rationale: str | None = None
    flags: list[str] = Field(default_factory=list)


class ScreeningResponse(BaseModel):
    """Row from `screening_responses` table."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    application_id: UUID
    responses: list[ScreeningResponseItem]
    knock_out_triggered: bool = False
    knock_out_reason: str | None = None
    composite_score: int | None = None
    submitted_at: datetime | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ScoreResult(BaseModel):
    """Output of the screening scorer activity."""

    application_id: UUID
    knock_out_triggered: bool
    knock_out_reason: str | None = None
    composite_score: int  # 0-100
    per_question_scores: list[ScreeningResponseItem]
    needs_hr_review: bool = False  # grey-zone (cut_line ± 10)
    cut_line: int
    recommendation: str  # "shortlist" | "reject" | "hr_review"
