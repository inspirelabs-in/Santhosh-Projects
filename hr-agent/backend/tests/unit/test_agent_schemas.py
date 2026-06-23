"""Validate that the V2 agent's structured-output schemas accept the shapes
its prompts ask for, and reject obviously broken outputs.

These tests guard against the silent drift that happens when a prompt is
edited but the corresponding Pydantic model isn't (or vice versa).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.agent.schemas import (
    AssignmentBriefOut,
    ExtractedTurn,
    TailoredQuestionsOut,
)


# ---------------------------------------------------------------------------
# TailoredQuestionsOut
# ---------------------------------------------------------------------------


def test_tailored_questions_accepts_two():
    out = TailoredQuestionsOut.model_validate(
        {
            "questions": [
                {
                    "id": "q1",
                    "question": "Walk me through your migration design for the billing service",
                    "tied_to_resume": "Stripe migration project",
                    "tied_to_jd": "Owns billing platform",
                    "expected_signal": "Trade-offs between dual-write vs CDC",
                },
                {
                    "id": "q2",
                    "question": "What's the worst incident your last system caused, and how did you respond?",
                    "tied_to_resume": "On-call experience",
                    "tied_to_jd": "Owns reliability",
                    "expected_signal": "Honesty + structured RCA",
                },
            ]
        }
    )
    assert len(out.questions) == 2
    assert out.questions[0].id == "q1"


def test_tailored_questions_rejects_one():
    with pytest.raises(ValidationError):
        TailoredQuestionsOut.model_validate(
            {
                "questions": [
                    {
                        "id": "q1",
                        "question": "Tell me about your last project",
                        "tied_to_resume": "x",
                        "tied_to_jd": "y",
                        "expected_signal": "z",
                    }
                ]
            }
        )


def test_tailored_questions_rejects_three():
    qs = [
        {
            "id": qid,
            "question": "x" * 30,
            "tied_to_resume": "x",
            "tied_to_jd": "y",
            "expected_signal": "z",
        }
        for qid in ("q1", "q2", "q1")  # extra slot still triggers max-length 2
    ]
    with pytest.raises(ValidationError):
        TailoredQuestionsOut.model_validate({"questions": qs})


# ---------------------------------------------------------------------------
# ExtractedTurn
# ---------------------------------------------------------------------------


def test_extracted_turn_minimal_payload():
    out = ExtractedTurn.model_validate(
        {
            "intent": "answer",
            "next_question_to_ask": "q2",
        }
    )
    assert out.tailored_a1 is None
    assert out.current_ctc_lpa is None
    assert out.intent == "answer"


def test_extracted_turn_rejects_unknown_intent():
    with pytest.raises(ValidationError):
        ExtractedTurn.model_validate(
            {"intent": "complain", "next_question_to_ask": "q1"}
        )


def test_extracted_turn_rejects_unknown_next_question():
    with pytest.raises(ValidationError):
        ExtractedTurn.model_validate(
            {"intent": "answer", "next_question_to_ask": "salary"}
        )


def test_extracted_turn_accepts_full_payload():
    out = ExtractedTurn.model_validate(
        {
            "tailored_a1": "Built X using Y to handle Z",
            "tailored_a2": None,
            "current_ctc_lpa": 14.5,
            "expected_ctc_lpa": 22,
            "notice_period_days": 30,
            "willing_to_relocate": True,
            "intent": "answer",
            "next_question_to_ask": "notice",
        }
    )
    assert out.current_ctc_lpa == 14.5
    assert out.notice_period_days == 30
    assert out.willing_to_relocate is True


# ---------------------------------------------------------------------------
# AssignmentBriefOut
# ---------------------------------------------------------------------------


def _valid_brief() -> dict:
    return {
        "brief_md": "# Take-home\n\nDesign a small payments reconciliation worker.",
        "problems": [
            {
                "id": "p1",
                "title": "Reconciliation worker",
                "statement": "Read settlement files, match them to ledger rows, output diffs.",
                "expected_artifacts": ["src/", "README.md", "tests/"],
                "tied_to_jd": "Owns billing pipeline",
                "estimated_minutes": 180,
            }
        ],
        "submission_format": {
            "type": "github_repo",
            "instructions": "Push a private repo + add reviewer.",
            "deadline_days": 5,
        },
        "evaluation_rubric": {
            "criteria": [
                {"name": "Correctness", "weight": 50, "description": "Matches ground truth diffs"},
                {"name": "Tests", "weight": 30, "description": "Coverage of edge cases"},
                {"name": "Code quality", "weight": 20, "description": "Readability, structure"},
            ]
        },
    }


def test_assignment_brief_accepts_valid_shape():
    out = AssignmentBriefOut.model_validate(_valid_brief())
    assert out.problems[0].estimated_minutes == 180
    assert out.submission_format.type == "github_repo"
    assert sum(c.weight for c in out.evaluation_rubric.criteria) == 100


def test_assignment_brief_rejects_too_many_problems():
    payload = _valid_brief()
    extra = dict(payload["problems"][0])
    payload["problems"] = [payload["problems"][0]] + [
        {**extra, "id": f"p{i}"} for i in range(2, 6)
    ]
    with pytest.raises(ValidationError):
        AssignmentBriefOut.model_validate(payload)


def test_assignment_brief_rejects_unknown_submission_type():
    payload = _valid_brief()
    payload["submission_format"]["type"] = "telepathy"
    with pytest.raises(ValidationError):
        AssignmentBriefOut.model_validate(payload)


def test_assignment_brief_rejects_estimated_minutes_zero():
    payload = _valid_brief()
    payload["problems"][0]["estimated_minutes"] = 0
    with pytest.raises(ValidationError):
        AssignmentBriefOut.model_validate(payload)


# ---------------------------------------------------------------------------
# EvaluationRubric — tolerate the shapes the assignment prompt actually elicits
# ---------------------------------------------------------------------------


def test_rubric_normalises_bare_string_criteria():
    """The assignment prompt lists rubric criteria as bare dimension NAMES, so
    the model returns ``criteria: ["Demo Quality", ...]`` (plain strings). The
    schema must coerce these into RubricCriterion objects instead of exploding
    with ``Input should be a valid dictionary or instance of RubricCriterion``.
    Regression for the no-assignment-on-apply bug.
    """
    payload = _valid_brief()
    payload["evaluation_rubric"]["criteria"] = [
        "Technical Depth",
        "Product Thinking",
        "Demo Quality",
        "AI/Tool Usage",
        "Code Quality & Documentation",
    ]
    out = AssignmentBriefOut.model_validate(payload)
    names = [c.name for c in out.evaluation_rubric.criteria]
    assert names == [
        "Technical Depth",
        "Product Thinking",
        "Demo Quality",
        "AI/Tool Usage",
        "Code Quality & Documentation",
    ]
    # Weights are auto-assigned and valid (1..100).
    assert all(1 <= c.weight <= 100 for c in out.evaluation_rubric.criteria)


def test_rubric_still_normalises_name_description_dicts():
    """Existing tolerance for ``{name: description}`` single-key dicts stays."""
    payload = _valid_brief()
    payload["evaluation_rubric"]["criteria"] = [
        {"Correctness": "Matches ground truth"},
        {"Tests": "Edge case coverage"},
    ]
    out = AssignmentBriefOut.model_validate(payload)
    assert [c.name for c in out.evaluation_rubric.criteria] == ["Correctness", "Tests"]
    assert out.evaluation_rubric.criteria[0].description == "Matches ground truth"


def test_rubric_handles_mixed_strings_and_objects():
    payload = _valid_brief()
    payload["evaluation_rubric"]["criteria"] = [
        "Strategic Thinking",
        {"name": "Analytical Rigor", "weight": 40, "description": "Depth of analysis"},
    ]
    out = AssignmentBriefOut.model_validate(payload)
    assert out.evaluation_rubric.criteria[0].name == "Strategic Thinking"
    assert out.evaluation_rubric.criteria[1].weight == 40
