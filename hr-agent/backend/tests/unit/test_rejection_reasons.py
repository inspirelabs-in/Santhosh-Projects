"""Unit tests for the safe-reasons picker + template renderer.

These are pure deterministic rules. They exist to guard the mapping from
internal signals → candidate-facing category so we never regress into
leaking internal rationale.
"""

from __future__ import annotations

import pytest

from src.services.rejection_reasons import (
    SAFE_REJECTION_REASONS,
    pick_category,
    render_template,
)


@pytest.mark.parametrize(
    ("knocks", "flags", "expected"),
    [
        (["notice_period_exceeds_max:90d>60d"], None, "notice_period"),
        (None, ["CTC above budget"], "ctc_mismatch"),
        (["expected_ctc_exceeds_budget:30L>25L+15%"], None, "ctc_mismatch"),
        (None, ["Missing expertise in Go"], "skills_gap"),
        (None, ["Candidate wants remote; role is onsite"], "location_mismatch"),
        (None, ["Seniority below target"], "experience_mismatch"),
        (None, None, "general"),
        (None, ["vague feedback"], "general"),
    ],
)
def test_pick_category(knocks, flags, expected):
    assert pick_category(knock_outs=knocks, red_flags=flags, stage="screening") == expected


def test_render_template_fills_placeholders():
    out = render_template("notice_period", max_days=30)
    assert "{max_days}" not in out
    assert "30" in out


def test_render_template_fallback_when_variable_missing():
    out = render_template("skills_gap", skill=None)
    assert "{skill}" not in out
    # sensible fallback included
    assert "primary" in out.lower() or "technology" in out.lower()


def test_all_categories_covered():
    assert set(SAFE_REJECTION_REASONS) == {
        "experience_mismatch",
        "ctc_mismatch",
        "notice_period",
        "skills_gap",
        "location_mismatch",
        "general",
    }
