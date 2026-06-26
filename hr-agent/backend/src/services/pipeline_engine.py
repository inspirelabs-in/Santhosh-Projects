"""Pipeline engine — the single planner that decides a candidate's next move.

Stage 2 of the cut-over: progression is driven by the **role's configured
pipeline** (``role_pipeline_stages``), not the legacy hardcoded chain or the old
``pipeline_template`` JSONB. This module is the *pure* decision core: given a
role's ordered stages and the candidate's current position, it returns the next
action. It does NO IO, so it is fully unit-testable (see tests/test_pipeline_engine).

The flow a JD defines is fixed at JD time: an ordered list of stages, each with a
``mode`` (auto | manual). The engine just maps the candidate's current stage to
the next one and says what to do:

  - a "fire" stage (screening / voice_screen / assignment) → the engine dispatches
    that stage's activity. Its outbound work belongs to *entering* the stage, so it
    runs whether the stage is auto or manual; ``mode`` governs only the move to the
    NEXT stage, and these outbound stages pause for the candidate's response rather
    than auto-advancing, so there is nothing for ``manual`` to gate. (``offer`` is the
    exception: firing it is terminal — HIRED — so a manual offer parks for HR.)
  - a **manual** gate (interview / decision)
    → the engine PARKS the candidate at that stage and raises a distinct
      ``requires_action`` item (so "schedule the HR round" can never be confused
      with "review the assignment" — that overloading was the root of the
      re-send-assignment bug).
  - **inline** stages (intake / parse / fit) run during intake, so the engine
    skips over them.
  - an **auto** gate (decision marked auto) is auto-advanced (the human review is
    skipped); an **interview** always needs a human, so it parks for scheduling
    regardless of mode.

  Note (SM-7): ``assessment_review`` is RETIRED — there is no separate "review the
  assignment" stage. The assignment stage itself auto-sends on entry and parks for
  HR review on submit, so a legacy ``assessment_review`` row hits the "unknown stage
  type → skip" fallthrough in ``_action_for`` and the engine walks past it. The
  ``PARK_REVIEW`` action below is consequently never produced (kept only so old
  references resolve).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from src.models.pipeline import StageType


class StageAction(StrEnum):
    """What the engine decided to do next. The IO layer (auto_progress) maps each
    to a concrete dispatch or a park-and-notify."""

    FIRE_SCREENING = "fire_screening"
    FIRE_VOICE_SCREEN = "fire_voice_screen"
    FIRE_ASSIGNMENT = "fire_assignment"
    FIRE_OFFER = "fire_offer"
    PARK_REVIEW = "park_review"        # DEAD (SM-7): assessment_review retired; never produced
    PARK_SCHEDULE = "park_schedule"    # interview gate (HR schedules the meeting)
    PARK_DECISION = "park_decision"    # decision gate (hire / reject)
    PARK_MANUAL = "park_manual"        # a "fire" stage set to manual mode
    DONE = "done"                      # reached the end of the pipeline
    NOOP = "noop"                      # nothing to do (no pipeline, etc.)


# Stage types the engine can execute itself, mapped to their fire action.
_FIRE_ACTIONS: dict[str, StageAction] = {
    StageType.SCREENING.value: StageAction.FIRE_SCREENING,
    StageType.VOICE_SCREEN.value: StageAction.FIRE_VOICE_SCREEN,
    StageType.ASSIGNMENT.value: StageAction.FIRE_ASSIGNMENT,
    StageType.OFFER.value: StageAction.FIRE_OFFER,
}

# Human gates, mapped to their park action.
_GATE_ACTIONS: dict[str, StageAction] = {
    StageType.INTERVIEW.value: StageAction.PARK_SCHEDULE,
    StageType.DECISION.value: StageAction.PARK_DECISION,
}

# Stages produced during intake; the engine never fires these — it skips past.
_INLINE_TYPES: frozenset[str] = frozenset(
    {
        StageType.INTAKE.value,
        StageType.PARSE.value,
        StageType.EMAIL_FILTER.value,  # runs at mail_ingest (pre-application)
        StageType.FIT.value,           # runs inline at intake (pipeline/v1)
    }
)

# Gates that may be auto-advanced when mode=auto (the human review is skipped).
# An ``interview`` is never auto-advanced: someone has to actually run it, so it
# always parks for scheduling.
_AUTO_ADVANCEABLE_GATES: frozenset[str] = frozenset(
    {StageType.DECISION.value}
)

# Fire stages where ``manual`` mode genuinely gates the dispatch, because firing is a
# *terminal* action HR should trigger explicitly. Every OTHER fire stage does its own
# outbound work on entry regardless of mode -- ``mode`` only governs whether we
# auto-advance to the NEXT stage, and these outbound stages don't auto-advance anyway
# (they pause for the candidate's response: a submitted assignment, a placed screen
# call), so there is nothing for ``manual`` to gate. ``offer`` is the exception: firing
# it is the terminal HIRED action, so a manual offer parks for HR to trigger.
_MANUAL_GATED_FIRE_TYPES: frozenset[str] = frozenset({StageType.OFFER.value})

_AUTO = "auto"
_MANUAL = "manual"


@dataclass(frozen=True)
class StageView:
    """DB-free view of one ``role_pipeline_stages`` row (keeps the planner pure)."""

    stage_key: str
    stage_type: str
    mode: str
    is_enabled: bool
    position: int
    label: str = ""

    @classmethod
    def from_row(cls, row: object) -> "StageView":
        return cls(
            stage_key=getattr(row, "stage_key"),
            stage_type=getattr(row, "stage_type"),
            mode=(getattr(row, "mode", None) or _MANUAL),
            is_enabled=bool(getattr(row, "is_enabled", True)),
            position=int(getattr(row, "position", 0)),
            label=getattr(row, "label", "") or "",
        )


@dataclass(frozen=True)
class Plan:
    """The engine's decision. ``stage`` is the stage the action applies to
    (the stage to fire or park at); None for DONE / NOOP."""

    action: StageAction
    stage: StageView | None
    reason: str

    @property
    def is_terminal(self) -> bool:
        return self.action in (StageAction.DONE, StageAction.NOOP)


def _action_for(stage: StageView) -> StageAction | None:
    """The action for a single stage, or None when it should be skipped (inline,
    or an auto-advanceable gate in auto mode)."""
    stype = stage.stage_type
    mode = (stage.mode or _MANUAL).lower()

    if stype in _INLINE_TYPES:
        return None  # handled during intake; skip forward

    if stype in _FIRE_ACTIONS:
        # A fire stage's outbound work (send the assignment, place the screen call,
        # email the questionnaire) is intrinsic to *entering* the stage, so it runs
        # whether the stage is auto or manual -- mode only gates the move to the NEXT
        # stage. The lone exception is a terminal fire-stage (offer): there, firing
        # IS the advance, so a manual one parks for HR to trigger.
        if mode == _AUTO or stype not in _MANUAL_GATED_FIRE_TYPES:
            return _FIRE_ACTIONS[stype]
        return StageAction.PARK_MANUAL  # manual terminal fire-stage (offer) → HR triggers

    if stype in _GATE_ACTIONS:
        if mode == _AUTO and stype in _AUTO_ADVANCEABLE_GATES:
            return None  # auto-approve / auto-decide → skip the human gate
        return _GATE_ACTIONS[stype]

    return None  # unknown stage type → skip


def plan_transition(
    stages: Sequence[StageView], current_stage_key: str | None
) -> Plan:
    """Decide the next action after ``current_stage_key`` completes.

    Walks the enabled stages in order starting *after* ``current_stage_key``
    (or from the first stage when it is None / unknown), skipping inline stages
    and auto-advanced gates, and returns the first actionable stage. Returns
    ``DONE`` when the pipeline is exhausted and ``NOOP`` when there is no pipeline.
    """
    enabled = sorted(
        (s for s in stages if s.is_enabled), key=lambda s: s.position
    )
    if not enabled:
        return Plan(StageAction.NOOP, None, "role has no enabled pipeline stages")

    # Index of the stage we're advancing FROM. -1 means "before the first stage".
    start = -1
    if current_stage_key is not None:
        for i, s in enumerate(enabled):
            if s.stage_key == current_stage_key:
                start = i
                break
        else:
            # current_stage_key not in the enabled pipeline (disabled or stale):
            # fall back to starting from the top so the candidate still moves.
            start = -1

    for s in enabled[start + 1 :]:
        action = _action_for(s)
        if action is None:
            continue  # inline or auto-advanced — keep walking
        return Plan(action, s, f"next stage '{s.stage_key}' ({s.stage_type}/{s.mode})")

    return Plan(StageAction.DONE, None, "reached the end of the pipeline")
