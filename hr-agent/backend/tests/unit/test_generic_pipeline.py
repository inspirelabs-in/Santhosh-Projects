"""Generic-pipeline tests: prove that ANY configured pipeline walks correctly.

These are pure planner tests (no DB) that exercise the exact scenarios the owner
asked for: a 1-stage pipeline, a pipeline with no CEO/HR rounds (the P-0 case),
and arbitrary reorders -- each driven by the SAME engine with no hardcoded "next
stage". Together with test_pipeline_engine (the default flow) they lock the claim
"configure each step however you want and it just works."
"""

from __future__ import annotations

from src.models.pipeline import StageType, StageVerdict
from src.services.pipeline_engine import StageAction, StageView, plan_transition


def _stages(specs: list[tuple[str, str, str]]) -> list[StageView]:
    """specs: list of (stage_key, stage_type, mode) in pipeline order."""
    return [
        StageView(stage_key=k, stage_type=ty, mode=m, is_enabled=True, position=i, label=k)
        for i, (k, ty, m) in enumerate(specs)
    ]


# ---------------------------------------------------------------------------
# Requirement #2: a 1-step JD (email filter only) works.
# ---------------------------------------------------------------------------


def test_single_email_filter_pipeline_is_done_after_intake():
    """A pipeline of just [email_filter] (inline) has nothing to fire/park: the
    candidate is immediately DONE. The 1-step JD the owner asked for."""
    stages = _stages([("email_filter", StageType.EMAIL_FILTER.value, "auto")])
    p = plan_transition(stages, "email_filter")
    assert p.action == StageAction.DONE


def test_email_filter_then_fit_only():
    """[email_filter, fit] -- both inline -- also completes to DONE (no rounds)."""
    stages = _stages(
        [
            ("email_filter", StageType.EMAIL_FILTER.value, "auto"),
            ("fit", StageType.FIT.value, "auto"),
        ]
    )
    # From the start, the planner skips both inline stages -> DONE.
    assert plan_transition(stages, None).action == StageAction.DONE
    assert plan_transition(stages, "email_filter").action == StageAction.DONE
    assert plan_transition(stages, "fit").action == StageAction.DONE


# ---------------------------------------------------------------------------
# Requirement #1 + P-0: tech -> offer (NO ceo/hr) completes to offer.
# ---------------------------------------------------------------------------


def test_email_fit_tech_offer_no_ceo_hr():
    """The P-0 case: email_filter -> fit -> technical interview -> offer, with NO
    CEO or HR rounds. After the technical interview the engine goes straight to
    the offer stage (never stalls waiting for a CEO round)."""
    stages = _stages(
        [
            ("email_filter", StageType.EMAIL_FILTER.value, "auto"),
            ("fit", StageType.FIT.value, "auto"),
            ("technical", StageType.INTERVIEW.value, "manual"),
            ("offer", StageType.OFFER.value, "auto"),
        ]
    )
    # From fit (inline) -> the technical interview parks for scheduling.
    p1 = plan_transition(stages, "fit")
    assert p1.action == StageAction.PARK_SCHEDULE
    assert p1.stage and p1.stage.stage_key == "technical"

    # After the technical interview completes -> offer fires (NOT a CEO round).
    p2 = plan_transition(stages, "technical")
    assert p2.action == StageAction.FIRE_OFFER
    assert p2.stage and p2.stage.stage_key == "offer"


def test_tech_then_manual_offer_parks():
    """Same pipeline but offer is manual -> after technical, offer parks for a
    human trigger instead of firing."""
    stages = _stages(
        [
            ("fit", StageType.FIT.value, "auto"),
            ("technical", StageType.INTERVIEW.value, "manual"),
            ("offer", StageType.OFFER.value, "manual"),
        ]
    )
    p = plan_transition(stages, "technical")
    assert p.action == StageAction.PARK_MANUAL
    assert p.stage and p.stage.stage_key == "offer"


# ---------------------------------------------------------------------------
# Requirement #3: reorder / arbitrary configs all walk generically.
# ---------------------------------------------------------------------------


def test_two_interview_rounds_then_decision_then_offer():
    """A richer config: voice -> assignment -> review -> tech -> sys_design ->
    decision -> offer. Each step lands on the next configured stage with no
    hardcoding; two interview rounds both park for scheduling in order."""
    stages = _stages(
        [
            ("voice_screen", StageType.VOICE_SCREEN.value, "auto"),
            ("assignment", StageType.ASSIGNMENT.value, "auto"),
            ("review", StageType.ASSESSMENT_REVIEW.value, "manual"),
            ("technical", StageType.INTERVIEW.value, "manual"),
            ("system_design", StageType.INTERVIEW.value, "manual"),
            ("decision", StageType.DECISION.value, "manual"),
            ("offer", StageType.OFFER.value, "auto"),
        ]
    )
    assert plan_transition(stages, "voice_screen").stage.stage_key == "assignment"
    assert plan_transition(stages, "assignment").action == StageAction.PARK_REVIEW
    assert plan_transition(stages, "review").stage.stage_key == "technical"
    assert plan_transition(stages, "technical").stage.stage_key == "system_design"
    assert plan_transition(stages, "system_design").action == StageAction.PARK_DECISION
    assert plan_transition(stages, "decision").action == StageAction.FIRE_OFFER
    assert plan_transition(stages, "offer").action == StageAction.DONE


def test_auto_decision_gate_is_skipped():
    """A decision gate set to auto is auto-advanced (skipped), so the engine goes
    straight to offer -- the 'auto: move forward without manual approval' mode."""
    stages = _stages(
        [
            ("technical", StageType.INTERVIEW.value, "manual"),
            ("decision", StageType.DECISION.value, "auto"),
            ("offer", StageType.OFFER.value, "auto"),
        ]
    )
    # After technical, the auto decision gate is skipped -> offer fires.
    assert plan_transition(stages, "technical").action == StageAction.FIRE_OFFER


def test_disabled_stage_is_skipped():
    """A disabled stage is walked past (config without deleting the row)."""
    stages = _stages(
        [
            ("voice_screen", StageType.VOICE_SCREEN.value, "auto"),
            ("assignment", StageType.ASSIGNMENT.value, "auto"),
            ("offer", StageType.OFFER.value, "auto"),
        ]
    )
    stages[1] = StageView(
        stage_key="assignment", stage_type=StageType.ASSIGNMENT.value,
        mode="auto", is_enabled=False, position=1, label="assignment",
    )
    # voice_screen -> (assignment disabled) -> offer.
    assert plan_transition(stages, "voice_screen").stage.stage_key == "offer"


# ---------------------------------------------------------------------------
# Verdict enum sanity (used by the stage-runner to drive the planner).
# ---------------------------------------------------------------------------


def test_stage_verdict_values():
    assert StageVerdict.PENDING.value == "pending"
    assert StageVerdict.ON_GOING.value == "on_going"
    assert StageVerdict.PASS.value == "pass"
    assert StageVerdict.FAIL.value == "fail"
