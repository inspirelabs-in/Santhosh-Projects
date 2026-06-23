"""Pydantic schemas for structured LLM outputs in the agent.

Wire-level shapes only. These are validated by ``LLMClient.complete()``;
runtime persistence shapes live in ``src.db.base``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


# [SCRAPE] dead: TailoredQuestion/TailoredQuestionsOut/ExtractedTurn (Chat-V2). KEEP AssignmentBriefOut etc (live).
class TailoredQuestion(BaseModel):
    id: Literal["q1", "q2"]
    question: str = Field(min_length=10, max_length=400)
    tied_to_resume: str
    tied_to_jd: str
    expected_signal: str


class TailoredQuestionsOut(BaseModel):
    questions: list[TailoredQuestion] = Field(min_length=2, max_length=2)


class ExtractedTurn(BaseModel):
    """Output of the per-turn extractor."""

    tailored_a1: str | None = None
    tailored_a2: str | None = None
    current_ctc_lpa: float | None = None
    expected_ctc_lpa: float | None = None
    notice_period_days: int | None = None
    willing_to_relocate: bool | None = None
    intent: Literal["answer", "ask_clarification", "out_of_scope", "request_pause"]
    next_question_to_ask: Literal[
        "q1", "q2", "ctc_current", "ctc_expected", "notice", "relocate", "none"
    ]


class AssignmentProblem(BaseModel):
    id: str
    title: str
    statement: str = Field(max_length=2000)
    expected_artifacts: list[str] = Field(default_factory=list)
    tied_to_jd: str
    estimated_minutes: int = Field(ge=15, le=720)
    # --- v2 enrichment fields (all optional for backward compatibility) ---
    vertical: str | None = None
    difficulty: str | None = None
    tags: list[str] = Field(default_factory=list)
    challenge: str | None = None
    why_it_matters: str | None = None
    technical_requirements: list[str] = Field(default_factory=list)
    what_to_submit: str | None = None
    evaluation_bullets: list[str] = Field(default_factory=list)


class SubmissionFormat(BaseModel):
    type: Literal["github_repo", "zip_upload", "text_only", "url"]
    instructions: str
    deadline_days: int = Field(ge=1, le=21)


class RubricCriterion(BaseModel):
    name: str
    weight: int = Field(ge=1, le=100)
    description: str = ""


class EvaluationRubric(BaseModel):
    criteria: list[RubricCriterion] = Field(min_length=1, max_length=8)

    @model_validator(mode="before")
    @classmethod
    def _normalise_criteria(cls, data):
        """Coerce the criteria shapes LLMs actually emit into RubricCriterion.

        The assignment prompt lists criteria as bare dimension NAMES, so the
        model commonly returns ``["Demo Quality", "AI/Tool Usage", ...]`` (plain
        strings) or ``[{name: description}]`` single-key dicts. Both must become
        ``{name, weight, description}`` objects rather than raising
        ``Input should be a valid dictionary or instance of RubricCriterion``.
        """
        if not isinstance(data, dict):
            return data
        raw = data.get("criteria")
        if not isinstance(raw, list) or not raw:
            return data
        normalised = []
        equal_weight = max(1, 100 // len(raw))
        for item in raw:
            if isinstance(item, str):
                # Bare dimension name -> object with an even weight.
                normalised.append({"name": item, "weight": equal_weight, "description": ""})
            elif isinstance(item, dict) and "name" not in item and len(item) == 1:
                name, desc = next(iter(item.items()))
                normalised.append({"name": name, "weight": equal_weight, "description": str(desc)})
            elif isinstance(item, dict) and "weight" not in item and item.get("name"):
                # Object missing only the weight -> backfill an even weight.
                normalised.append({**item, "weight": equal_weight})
            else:
                normalised.append(item)
        data["criteria"] = normalised
        return data


class AssignmentBriefOut(BaseModel):
    brief_md: str = Field(min_length=50)
    problems: list[AssignmentProblem] = Field(min_length=1, max_length=10)
    submission_format: SubmissionFormat
    evaluation_rubric: EvaluationRubric
    # --- v2 cover-page enrichment (all optional) ---
    cover_title: str | None = None
    confidential_tag: str | None = None
    company_context: str | None = None
    new_initiatives: str | None = None
    strategic_context: str | None = None
    what_we_look_for: list[str] = Field(default_factory=list)
    submission_requirements: list[str] = Field(default_factory=list)
