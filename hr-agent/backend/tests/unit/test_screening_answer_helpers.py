"""Pure helper tests for ``src.db.repositories.screening_answer``.

The repo's ``is_complete`` predicate is the gate that decides whether the
agent transitions screening -> assignment. We pin its behaviour against
partial / full / None rows so a future refactor doesn't silently let
candidates through with missing logistics.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.db.repositories.screening_answer import is_complete


def _row(**fields):
    """Stand-in for an ORM row with only the fields ``is_complete`` reads."""
    base = dict(
        tailored_a1=None,
        tailored_a2=None,
        current_ctc_lpa=None,
        expected_ctc_lpa=None,
        notice_period_days=None,
        willing_to_relocate=None,
    )
    base.update(fields)
    return SimpleNamespace(**base)


def test_is_complete_returns_false_for_none():
    assert is_complete(None) is False


def test_is_complete_returns_false_when_missing_any_field():
    row = _row(
        tailored_a1="a1",
        tailored_a2="a2",
        current_ctc_lpa=12.0,
        expected_ctc_lpa=18.0,
        notice_period_days=30,
        willing_to_relocate=None,  # missing
    )
    assert is_complete(row) is False


def test_is_complete_true_when_all_present_including_relocate_false():
    """``willing_to_relocate=False`` is still an answer; do not treat as missing."""
    row = _row(
        tailored_a1="a1",
        tailored_a2="a2",
        current_ctc_lpa=12.0,
        expected_ctc_lpa=18.0,
        notice_period_days=30,
        willing_to_relocate=False,
    )
    assert is_complete(row) is True


def test_is_complete_true_when_ctc_zero():
    """A 0-LPA expected CTC is unusual but valid (intern/fresher); not missing."""
    row = _row(
        tailored_a1="a1",
        tailored_a2="a2",
        current_ctc_lpa=0.0,
        expected_ctc_lpa=0.0,
        notice_period_days=0,
        willing_to_relocate=True,
    )
    assert is_complete(row) is True
