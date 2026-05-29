"""Unit tests for the deterministic bits of fit_score:

- Tier rule (`_determine_tier`)
- Hard knock-out computation (`_compute_hard_knockouts`)

The LLM call itself is covered by activity integration tests that mock the
LLMClient; here we guard the pure logic because it overrides the model.
"""

from __future__ import annotations

import pytest

from src.activities.fit_score import _compute_hard_knockouts, _determine_tier
from src.models.candidate import CandidateProfile, FitTier


class _Role:
    def __init__(self, *, max_notice_days=None, ctc_max_lpa=None):
        self.max_notice_days = max_notice_days
        self.ctc_max_lpa = ctc_max_lpa


@pytest.mark.parametrize(
    ("score", "knocks", "expected"),
    [
        (85, [], FitTier.GREEN),
        (70, [], FitTier.GREEN),
        (69, [], FitTier.AMBER),
        (50, [], FitTier.AMBER),
        (49, [], FitTier.RED),
        (95, ["anything"], FitTier.RED),  # knock-out always wins
    ],
)
def test_determine_tier(score, knocks, expected):
    assert _determine_tier(score, knocks) == expected


def test_knockouts_notice_period_exceeds_max():
    profile = CandidateProfile(notice_period_days=90)
    role = _Role(max_notice_days=60)
    assert _compute_hard_knockouts(profile, role) == [
        "notice_period_exceeds_max:90d>60d"
    ]


def test_knockouts_expected_ctc_over_budget_15pct():
    profile = CandidateProfile(expected_ctc_lpa=30.0)
    role = _Role(ctc_max_lpa=25.0)  # 15% over = 28.75; 30 triggers knock-out
    [knock] = _compute_hard_knockouts(profile, role)
    assert knock.startswith("expected_ctc_exceeds_budget:30.0L>25.0L")


def test_knockouts_pass_when_within_tolerance():
    profile = CandidateProfile(expected_ctc_lpa=28.0, notice_period_days=45)
    role = _Role(ctc_max_lpa=25.0, max_notice_days=60)  # 28 < 28.75, 45 <= 60
    assert _compute_hard_knockouts(profile, role) == []


def test_knockouts_ignored_when_role_has_no_ceiling():
    profile = CandidateProfile(expected_ctc_lpa=100.0, notice_period_days=180)
    role = _Role(ctc_max_lpa=None, max_notice_days=None)
    assert _compute_hard_knockouts(profile, role) == []
