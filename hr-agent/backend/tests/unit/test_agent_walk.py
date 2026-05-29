"""End-to-end ish walk through the screening state machine.

Simulates 6 candidate turns by patching the AgentState directly between
each ``_pick_first_pending`` call. We aren't running the LangGraph (that
would require Postgres + Redis); we are pinning the field-ask contract
that ``node_route`` depends on so a future change to the order or
``_screening_complete`` predicate has to update this golden.

The DB-bound parts (``_advance_pipeline_to_assignment``,
``screening_repo.upsert``) are covered separately by an integration
suite once a Postgres test fixture lands. For now this gives us the
sequence-correctness guarantee with zero infra.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from src.agent.graph import (
    _evaluate_screening,
    _pick_first_pending,
    _screening_complete,
)
from src.agent.state import empty_state


def _new_state():
    return empty_state(
        conversation_id=uuid4(),
        application_id=uuid4(),
        candidate_id=uuid4(),
        role_id=uuid4(),
    )


def test_full_screening_walk_six_turns():
    """Six turns, six fields, one stage flip. Pins the entire flow."""
    state = _new_state()
    state["tailored_questions"] = [
        {"id": "q1", "question": "Walk me through the Stripe migration."},
        {"id": "q2", "question": "Worst incident, how did you handle it?"},
    ]

    # Turn 1: agent asks q1.
    assert _pick_first_pending(state) == "q1"
    assert _screening_complete(state) is False
    state["tailored_answers"]["q1"] = "Designed dual-write rollout with shadow ledger."

    # Turn 2: agent asks q2.
    assert _pick_first_pending(state) == "q2"
    state["tailored_answers"]["q2"] = "Took down search; ran 5-whys, added preflight check."

    # Turn 3: logistics, current CTC.
    assert _pick_first_pending(state) == "ctc_current"
    state["logistics"]["current_ctc_lpa"] = 14.0

    # Turn 4: expected.
    assert _pick_first_pending(state) == "ctc_expected"
    state["logistics"]["expected_ctc_lpa"] = 22.0

    # Turn 5: notice.
    assert _pick_first_pending(state) == "notice"
    state["logistics"]["notice_period_days"] = 30

    # Turn 6: relocate. Last field; after this screening_complete.
    assert _pick_first_pending(state) == "relocate"
    assert _screening_complete(state) is False  # not yet, relocate still null
    state["logistics"]["willing_to_relocate"] = True

    # Post-turn-6: screening done, agent should transition to assignment.
    assert _pick_first_pending(state) == "none"
    assert _screening_complete(state) is True


def test_walk_with_knockout_outcome():
    """When the candidate's final answer triggers a knock-out, the
    evaluator must flag it AND the screening_complete predicate must
    still be True (the route node uses both signals separately)."""
    state = _new_state()
    state["tailored_questions"] = [
        {"id": "q1", "question": "..."},
        {"id": "q2", "question": "..."},
    ]
    state["tailored_answers"] = {"q1": "x", "q2": "y"}
    state["logistics"] = {
        "current_ctc_lpa": 14.0,
        "expected_ctc_lpa": 80.0,  # blowout
        "notice_period_days": 30,
        "willing_to_relocate": True,
    }
    assert _screening_complete(state) is True

    role = SimpleNamespace(ctc_max_lpa=20.0, max_notice_days=60, remote_policy="remote")
    out = _evaluate_screening(logistics=state["logistics"], role=role)
    assert out["knock_out_triggered"] is True
    assert out["composite_score"] == 50


def test_partial_walk_resume_from_logistics():
    """Resume scenario: candidate already answered tailored Qs in a prior
    session and reconnects. The next-question pointer must skip the
    completed fields and jump straight to the first missing logistic."""
    state = _new_state()
    state["tailored_answers"] = {"q1": "a1", "q2": "a2"}
    state["logistics"] = {"current_ctc_lpa": 12.0}

    assert _pick_first_pending(state) == "ctc_expected"
    state["logistics"]["expected_ctc_lpa"] = 18.0
    assert _pick_first_pending(state) == "notice"
