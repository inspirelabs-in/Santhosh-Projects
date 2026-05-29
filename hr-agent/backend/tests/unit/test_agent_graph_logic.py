"""Pure-function tests for the V2 chat agent's graph helpers.

We exercise the pieces of ``src.agent.graph`` and ``src.agent.state`` that
don't touch the DB or the LLM:

  * ``_pick_first_pending``   -- order of fields the agent should ask next
  * ``_screening_complete``   -- gate for the screening->assignment flip
  * ``_evaluate_screening``   -- knock-out heuristic + composite score
  * ``empty_state``           -- shape of the initial AgentState dict

The async DB-bound nodes (``node_extract``, ``node_route``,
``node_maybe_gen_assignment``) are covered separately by integration tests
once a real Postgres test fixture is wired up.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.agent.graph import (
    _evaluate_screening,
    _ordered_tailored_answers,
    _pick_first_pending,
    _screening_complete,
)
from src.agent.state import empty_state


# ---------------------------------------------------------------------------
# Field-ask ordering.
# ---------------------------------------------------------------------------


def test_pick_first_pending_starts_with_q1():
    state = empty_state(
        conversation_id=uuid4(),
        application_id=uuid4(),
        candidate_id=uuid4(),
        role_id=uuid4(),
    )
    assert _pick_first_pending(state) == "q1"


def test_pick_first_pending_advances_through_tailored_then_logistics():
    state = empty_state(
        conversation_id=uuid4(),
        application_id=uuid4(),
        candidate_id=uuid4(),
        role_id=uuid4(),
    )
    state["tailored_answers"] = {"q1": "answered q1"}
    assert _pick_first_pending(state) == "q2"

    state["tailored_answers"] = {"q1": "a1", "q2": "a2"}
    assert _pick_first_pending(state) == "ctc_current"

    state["logistics"] = {"current_ctc_lpa": 12.0}
    assert _pick_first_pending(state) == "ctc_expected"

    state["logistics"] = {"current_ctc_lpa": 12.0, "expected_ctc_lpa": 18.0}
    assert _pick_first_pending(state) == "notice"

    state["logistics"] = {
        "current_ctc_lpa": 12.0,
        "expected_ctc_lpa": 18.0,
        "notice_period_days": 30,
    }
    assert _pick_first_pending(state) == "relocate"

    state["logistics"] = {
        "current_ctc_lpa": 12.0,
        "expected_ctc_lpa": 18.0,
        "notice_period_days": 30,
        "willing_to_relocate": True,
    }
    assert _pick_first_pending(state) == "none"


def test_screening_complete_requires_all_six():
    state = empty_state(
        conversation_id=uuid4(),
        application_id=uuid4(),
        candidate_id=uuid4(),
        role_id=uuid4(),
    )
    assert _screening_complete(state) is False
    state["tailored_answers"] = {"q1": "a1", "q2": "a2"}
    state["logistics"] = {
        "current_ctc_lpa": 12.0,
        "expected_ctc_lpa": 18.0,
        "notice_period_days": 30,
        "willing_to_relocate": False,
    }
    assert _screening_complete(state) is True


def test_ordered_tailored_answers_preserves_q1_q2_order():
    state = empty_state(
        conversation_id=uuid4(),
        application_id=uuid4(),
        candidate_id=uuid4(),
        role_id=uuid4(),
    )
    # Insert in reverse to guarantee the helper isn't reading dict iteration
    # order accidentally.
    state["tailored_answers"] = {"q2": "second", "q1": "first"}
    assert _ordered_tailored_answers(state) == ["first", "second"]


# ---------------------------------------------------------------------------
# Heuristic screening evaluator.
# ---------------------------------------------------------------------------


def _role(
    *,
    ctc_max=20.0,
    notice_max=60,
    remote_policy="on_site",
):
    """Lightweight role stand-in. Only the columns the evaluator reads."""
    return SimpleNamespace(
        ctc_max_lpa=ctc_max,
        max_notice_days=notice_max,
        remote_policy=remote_policy,
    )


def test_evaluate_screening_passes_when_within_band():
    out = _evaluate_screening(
        logistics={
            "current_ctc_lpa": 10.0,
            "expected_ctc_lpa": 18.0,
            "notice_period_days": 30,
            "willing_to_relocate": True,
        },
        role=_role(),
    )
    assert out["knock_out_triggered"] is False
    assert out["composite_score"] == 75
    assert out["evaluation"]["reasons"] == []


def test_evaluate_screening_knocks_out_on_ctc_overshoot():
    # Role ceiling 20 LPA, +15% buffer => 23 LPA. 30 should knock out.
    out = _evaluate_screening(
        logistics={"expected_ctc_lpa": 30.0},
        role=_role(),
    )
    assert out["knock_out_triggered"] is True
    assert "expected_ctc" in out["knock_out_reason"]
    assert out["composite_score"] == 50


def test_evaluate_screening_allows_15pct_buffer_above_ceiling():
    # 22 LPA on a 20 ceiling is within +15% so it must NOT knock out.
    out = _evaluate_screening(
        logistics={"expected_ctc_lpa": 22.0},
        role=_role(),
    )
    assert out["knock_out_triggered"] is False


def test_evaluate_screening_knocks_out_on_long_notice():
    out = _evaluate_screening(
        logistics={"notice_period_days": 90},
        role=_role(notice_max=60),
    )
    assert out["knock_out_triggered"] is True
    assert "notice_period" in out["knock_out_reason"]


def test_evaluate_screening_knocks_out_on_relocate_refusal_for_onsite():
    out = _evaluate_screening(
        logistics={"willing_to_relocate": False},
        role=_role(remote_policy="on_site"),
    )
    assert out["knock_out_triggered"] is True
    assert "relocat" in out["knock_out_reason"].lower()


def test_evaluate_screening_ignores_relocate_for_remote_role():
    out = _evaluate_screening(
        logistics={"willing_to_relocate": False},
        role=_role(remote_policy="remote"),
    )
    assert out["knock_out_triggered"] is False


def test_evaluate_screening_aggregates_multiple_reasons():
    out = _evaluate_screening(
        logistics={
            "expected_ctc_lpa": 50.0,
            "notice_period_days": 120,
            "willing_to_relocate": False,
        },
        role=_role(remote_policy="on_site"),
    )
    assert out["knock_out_triggered"] is True
    # All three reasons should be captured in the structured evaluation.
    assert len(out["evaluation"]["reasons"]) == 3


# ---------------------------------------------------------------------------
# State shape.
# ---------------------------------------------------------------------------


def test_empty_state_has_required_keys():
    state = empty_state(
        conversation_id=uuid4(),
        application_id=uuid4(),
        candidate_id=uuid4(),
        role_id=uuid4(),
    )
    for key in (
        "conversation_id",
        "application_id",
        "candidate_id",
        "role_id",
        "stage",
        "tailored_questions",
        "tailored_answers",
        "logistics",
        "messages",
    ):
        assert key in state
    assert state["stage"] == "intake"
    assert state["tailored_answers"] == {}
    assert state["logistics"] == {}
