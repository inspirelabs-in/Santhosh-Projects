"""Pure unit tests for the pipeline engine planner (no DB, no IO).

These lock the core progression logic that drives every candidate:
the engine walks a role's ordered ``role_pipeline_stages`` and decides the next
action from the candidate's current position. The most important guarantee is
that human gates park at their OWN stage with their OWN action — so "schedule the
HR interview" can never be confused with "review the assignment" (the bug that
re-sent the assignment when HR scheduled the final round).
"""

from __future__ import annotations

from src.models.pipeline import DEFAULT_PIPELINE
from src.services.pipeline_engine import (
    Plan,
    StageAction,
    StageView,
    plan_transition,
)


def _default_stages() -> list[StageView]:
    """The seeded default pipeline as StageViews (intake..offer)."""
    return [
        StageView(
            stage_key=s["stage_key"],
            stage_type=s["stage_type"],
            mode=s["mode"],
            is_enabled=True,
            position=i,
            label=s["label"],
        )
        for i, s in enumerate(DEFAULT_PIPELINE)
    ]


def _plan(stages: list[StageView], current: str | None) -> Plan:
    return plan_transition(stages, current)


# ---------------------------------------------------------------------------
# Default pipeline walk: the documented ideal flow.
# ---------------------------------------------------------------------------


def test_intake_advances_past_inline_to_voice_screen():
    """From intake (inline), the first real action is the auto voice_screen stage
    (voice IS the screen by default — no separate written screening)."""
    p = _plan(_default_stages(), "intake")
    assert p.action == StageAction.FIRE_VOICE_SCREEN
    assert p.stage and p.stage.stage_key == "voice_screen"


def test_fit_advances_to_voice_screen():
    """fit is inline; completing it leads to the voice screen."""
    p = _plan(_default_stages(), "fit")
    assert p.action == StageAction.FIRE_VOICE_SCREEN


def test_added_screening_stage_runs_before_voice():
    """A role CAN add a written screening stage before voice; then intake -> screening."""
    stages = _default_stages()
    screening = StageView("screening", "screening", "auto", True, position=35, label="Resume Screening")
    stages.append(screening)  # plan_transition sorts by position; 35 sits between fit(2) and voice(3)
    # Give it a position between fit and voice_screen.
    fixed: list[StageView] = []
    for s in stages:
        pos = s.position
        if s.stage_key == "fit":
            pos = 30
        elif s.stage_key == "screening":
            pos = 31
        elif s.stage_key == "voice_screen":
            pos = 32
        fixed.append(StageView(s.stage_key, s.stage_type, s.mode, s.is_enabled, pos, s.label))
    p = _plan(fixed, "fit")
    assert p.action == StageAction.FIRE_SCREENING
    assert p.stage and p.stage.stage_key == "screening"


def test_voice_screen_advances_to_assignment():
    p = _plan(_default_stages(), "voice_screen")
    assert p.action == StageAction.FIRE_ASSIGNMENT
    assert p.stage and p.stage.stage_key == "assignment"


def test_assignment_advances_to_assessment_review_gate():
    """After assignment (auto), the assessment_review gate (manual) parks for review."""
    p = _plan(_default_stages(), "assignment")
    assert p.action == StageAction.PARK_REVIEW
    assert p.stage and p.stage.stage_key == "assessment_review"


def test_assessment_review_advances_to_technical_schedule():
    """Approving the assessment_review leads to the technical interview, which
    PARKS for scheduling (an interview always needs a human)."""
    p = _plan(_default_stages(), "assessment_review")
    assert p.action == StageAction.PARK_SCHEDULE
    assert p.stage and p.stage.stage_key == "technical"


def test_technical_advances_to_ceo_schedule():
    p = _plan(_default_stages(), "technical")
    assert p.action == StageAction.PARK_SCHEDULE
    assert p.stage and p.stage.stage_key == "ceo"


def test_ceo_advances_to_hr_schedule():
    p = _plan(_default_stages(), "ceo")
    assert p.action == StageAction.PARK_SCHEDULE
    assert p.stage and p.stage.stage_key == "hr"


# ---------------------------------------------------------------------------
# THE BUG: scheduling/advancing the final HR interview must NOT fire assignment.
# ---------------------------------------------------------------------------


def test_hr_interview_advances_to_decision_not_assignment():
    """Completing the HR interview parks at the decision gate — it must never
    loop back to FIRE_ASSIGNMENT. This is the exact regression that re-sent the
    assignment when HR handled the final round."""
    p = _plan(_default_stages(), "hr")
    assert p.action == StageAction.PARK_DECISION
    assert p.stage and p.stage.stage_key == "decision"
    assert p.action != StageAction.FIRE_ASSIGNMENT


def test_decision_advances_to_offer():
    """The default offer stage is manual, so the decision gate leads to a parked
    offer (HR triggers it). With an auto offer it fires directly (next test)."""
    p = _plan(_default_stages(), "decision")
    assert p.action == StageAction.PARK_MANUAL
    assert p.stage and p.stage.stage_key == "offer"


def test_auto_offer_fires_after_decision():
    stages = [
        s if s.stage_key != "offer"
        else StageView(s.stage_key, s.stage_type, "auto", True, s.position, s.label)
        for s in _default_stages()
    ]
    p = _plan(stages, "decision")
    assert p.action == StageAction.FIRE_OFFER
    assert p.stage and p.stage.stage_key == "offer"


def test_offer_is_last_stage_done():
    p = _plan(_default_stages(), "offer")
    assert p.action == StageAction.DONE
    assert p.stage is None


# ---------------------------------------------------------------------------
# Customisation: each JD defines its own ordered set + modes.
# ---------------------------------------------------------------------------


def test_role_can_drop_assignment_stage():
    """A role with no assignment goes voice_screen -> assessment_review directly."""
    stages = [s for s in _default_stages() if s.stage_key != "assignment"]
    p = _plan(stages, "voice_screen")
    assert p.action == StageAction.PARK_REVIEW
    assert p.stage and p.stage.stage_key == "assessment_review"


def test_default_pipeline_has_no_written_screening():
    """The default flow uses voice as the screen; there is no 'screening' stage."""
    keys = {s.stage_key for s in _default_stages()}
    assert "screening" not in keys
    assert "voice_screen" in keys


def test_disabled_stage_is_skipped():
    """A disabled stage is walked over as if absent."""
    stages = _default_stages()
    stages = [
        s if s.stage_key != "assignment"
        else StageView(s.stage_key, s.stage_type, s.mode, False, s.position, s.label)
        for s in stages
    ]
    p = _plan(stages, "voice_screen")
    assert p.action == StageAction.PARK_REVIEW


def test_extra_interview_round_supported():
    """A role can insert a 2nd technical round; each interview parks to schedule."""
    stages = _default_stages()
    # Insert system_design interview between technical and ceo.
    sd = StageView("system_design", "interview", "manual", True, position=75, label="System Design")
    stages.append(sd)  # plan_transition sorts by position
    # Re-position technical=70 implicitly via DEFAULT order; system_design=75 sits after.
    # Walk: technical -> system_design (next by position) -> schedule.
    # Fix positions so system_design follows technical (idx 7) and precedes ceo (idx 8).
    fixed: list[StageView] = []
    for s in stages:
        pos = s.position
        if s.stage_key == "technical":
            pos = 70
        elif s.stage_key == "system_design":
            pos = 71
        elif s.stage_key == "ceo":
            pos = 72
        elif s.stage_key == "hr":
            pos = 73
        elif s.stage_key == "decision":
            pos = 74
        elif s.stage_key == "offer":
            pos = 75
        fixed.append(StageView(s.stage_key, s.stage_type, s.mode, s.is_enabled, pos, s.label))
    p = _plan(fixed, "technical")
    assert p.action == StageAction.PARK_SCHEDULE
    assert p.stage and p.stage.stage_key == "system_design"


# ---------------------------------------------------------------------------
# Mode flips: auto/manual change behaviour at the same stage.
# ---------------------------------------------------------------------------


def test_auto_assessment_review_is_skipped():
    """An assessment_review marked auto auto-approves: skip to the next interview."""
    stages = [
        s if s.stage_key != "assessment_review"
        else StageView(s.stage_key, s.stage_type, "auto", True, s.position, s.label)
        for s in _default_stages()
    ]
    p = _plan(stages, "assignment")
    assert p.action == StageAction.PARK_SCHEDULE
    assert p.stage and p.stage.stage_key == "technical"


def test_manual_voice_screen_parks_instead_of_firing():
    """A voice_screen set to manual parks for HR to trigger, not auto-fire."""
    stages = [
        s if s.stage_key != "voice_screen"
        else StageView(s.stage_key, s.stage_type, "manual", True, s.position, s.label)
        for s in _default_stages()
    ]
    p = _plan(stages, "fit")
    assert p.action == StageAction.PARK_MANUAL
    assert p.stage and p.stage.stage_key == "voice_screen"


def test_interview_stays_manual_even_when_marked_auto():
    """An interview can't be auto-advanced: a human runs it, so it always parks."""
    stages = [
        s if s.stage_key != "technical"
        else StageView(s.stage_key, s.stage_type, "auto", True, s.position, s.label)
        for s in _default_stages()
    ]
    p = _plan(stages, "assessment_review")
    assert p.action == StageAction.PARK_SCHEDULE


# ---------------------------------------------------------------------------
# Edge cases.
# ---------------------------------------------------------------------------


def test_no_pipeline_is_noop():
    assert _plan([], "intake").action == StageAction.NOOP


def test_none_current_starts_from_first_actionable():
    """A candidate with no stage key yet starts at the first actionable stage
    (voice_screen — intake/parse/fit are inline)."""
    p = _plan(_default_stages(), None)
    assert p.action == StageAction.FIRE_VOICE_SCREEN


def test_unknown_current_key_restarts_from_top():
    """A stale/disabled current key falls back to the first actionable stage."""
    p = _plan(_default_stages(), "some_removed_stage")
    assert p.action == StageAction.FIRE_VOICE_SCREEN


def test_terminal_offer_only_pipeline_done():
    stages = [StageView("offer", "offer", "auto", True, 0, "Offer")]
    assert _plan(stages, "offer").action == StageAction.DONE
