"""Pure-function tests for the screening scorer.

We exercise `_check_knockouts` and `_scored_question_score` without touching
the DB or the LLM. The full activity flow is covered by an integration test.
"""

from __future__ import annotations

import pytest

from src.activities.score_responses import _check_knockouts, _scored_question_score
from src.models.candidate import CandidateProfile
from src.models.screening import KnockOutType, ScreeningQuestion, ScreeningResponseItem


def _q(id_: str, type_: str, *, knock_out_value: str | None = None, weight: float = 1.0):
    return ScreeningQuestion(
        id=id_, question=f"Q{id_}", type=type_, weight=weight,
        knock_out_value=knock_out_value, max_score=10,
    )


def test_knockout_ctc_exceeds_budget():
    profile = CandidateProfile(expected_ctc_lpa=30.0)
    qs = [_q("q1", KnockOutType.CTC_CHECK.value, knock_out_value="must")]
    responses: list[ScreeningResponseItem] = [
        ScreeningResponseItem(question_id="q1", answer="30")
    ]
    triggered, reason = _check_knockouts(qs, responses, profile, role_ctc_max_lpa=25.0, role_max_notice_days=None)
    assert triggered is True
    assert reason is not None and "Expected CTC" in reason


def test_knockout_notice_period_exceeds_max():
    profile = CandidateProfile(notice_period_days=90)
    qs = [_q("q1", KnockOutType.NOTICE_PERIOD_CHECK.value, knock_out_value="must")]
    responses = [ScreeningResponseItem(question_id="q1", answer="90")]
    triggered, reason = _check_knockouts(qs, responses, profile, role_ctc_max_lpa=None, role_max_notice_days=60)
    assert triggered is True
    assert reason is not None and "Notice period" in reason


def test_knockout_must_have_skill_no_answer():
    qs = [_q("q1", KnockOutType.MUST_HAVE_SKILL.value, knock_out_value="yes")]
    responses = [ScreeningResponseItem(question_id="q1", answer="no")]
    triggered, reason = _check_knockouts(qs, responses, CandidateProfile(), None, None)
    assert triggered is True
    assert reason is not None and "Missing must-have" in reason


def test_knockout_passes_when_all_conditions_met():
    profile = CandidateProfile(expected_ctc_lpa=20.0, notice_period_days=30)
    qs = [
        _q("q1", KnockOutType.CTC_CHECK.value, knock_out_value="must"),
        _q("q2", KnockOutType.NOTICE_PERIOD_CHECK.value, knock_out_value="must"),
        _q("q3", KnockOutType.MUST_HAVE_SKILL.value, knock_out_value="yes"),
    ]
    responses = [
        ScreeningResponseItem(question_id="q1", answer="20"),
        ScreeningResponseItem(question_id="q2", answer="30"),
        ScreeningResponseItem(question_id="q3", answer="yes"),
    ]
    triggered, reason = _check_knockouts(qs, responses, profile, 25.0, 60)
    assert triggered is False
    assert reason is None


def test_scored_question_uses_rubric_mapping():
    q = _q("q1", "scored")
    rubric = {"per_question": {"q1": {"answer_scores": {"a": 10, "b": 5, "c": 0}}}}
    assert _scored_question_score(q, "a", rubric) == 1.0
    assert _scored_question_score(q, "b", rubric) == 0.5
    assert _scored_question_score(q, "c", rubric) == 0.0
    assert _scored_question_score(q, "unknown", rubric) == 0.0


def test_scored_question_falls_back_to_nonempty_default():
    q = _q("q1", "scored")
    assert _scored_question_score(q, "anything", {}) == 1.0
    assert _scored_question_score(q, "", {}) == 0.0
