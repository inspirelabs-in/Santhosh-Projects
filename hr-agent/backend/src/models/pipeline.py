"""Per-role pipeline model.

A role's pipeline is an ordered list of stages, stored relationally in
``role_pipeline_stages`` (one row per stage). Each stage is an *instance* of a
type drawn from a fixed catalog (``StageType``), so a role can have any number of
``interview`` rounds (``technical``, ``system_design``, ``ceo``, ``hr`` ...),
drop ``screening`` entirely, reorder, and mark each stage ``auto`` or ``manual``.

The flexible per-stage bits (operational config, the per-stage evaluation subset)
live in JSONB columns on that row — the "relational shell + JSONB content" hybrid.

An application's position in its role's pipeline is tracked by
``applications.current_stage_key`` (which stage) + ``applications.stage_status``
(the within-stage lifecycle), replacing the old fixed 30-value global enum.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class StageType(StrEnum):
    """Fixed catalog of stage kinds. Instances are rows in role_pipeline_stages."""

    INTAKE = "intake"
    PARSE = "parse"
    EMAIL_FILTER = "email_filter"  # deterministic inbound-mail funnel as a real stage
    FIT = "fit"
    SCREENING = "screening"  # resume / written screening — removable per role
    VOICE_SCREEN = "voice_screen"
    ASSIGNMENT = "assignment"
    # ASSESSMENT_REVIEW = "assessment_review"  # DEPRECATED: folded into the assignment stage (which now parks for review on submit). Kept so legacy pipelines/rows still resolve; no longer seeded into new roles.
    INTERVIEW = "interview"  # repeatable: technical / system_design / ceo / hr ...
    DECISION = "decision"
    OFFER = "offer"


class StageMode(StrEnum):
    """Whether the pipeline auto-advances this stage or parks it for a human."""

    AUTO = "auto"
    MANUAL = "manual"


class StageStatus(StrEnum):
    """Within-stage lifecycle, tracked on applications.stage_status."""

    PENDING = "pending"
    ACTIVE = "active"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    PASSED = "passed"
    FAILED = "failed"
    PARKED = "parked"
    SKIPPED = "skipped"


class StageVerdict(StrEnum):
    """The decision outcome of a stage for one candidate (the generic runner).

    Distinct from StageStatus (the within-stage lifecycle). A stage can be
    ``processed`` (status) yet ``on_going`` (verdict) -- e.g. an interview that
    happened but whose result isn't decided. Stored per stage_key in
    applications.stage_results.
    """

    PENDING = "pending"      # not started / not reached
    ON_GOING = "on_going"    # started, awaiting a decision (call placed, meeting booked)
    PASS = "pass"            # cleared -> the runner advances
    NEEDS_REVIEW = "needs_review"  # borderline score -> park for HR; HR's pass/reject decides
    FAIL = "fail"            # failed -> the runner rejects (+ rejection email on auto lane)


class StageEvalSpec(BaseModel):
    """Per-stage evaluation: which of the role's canonical evaluation dimensions
    this stage scores, plus an optional stage-specific rubric/threshold.

    ``dimension_keys`` reference ``EvaluationDimension.key`` on the role's
    ``EvaluationSpec`` (see models/evaluation.py). Stored in
    ``role_pipeline_stages.eval_spec`` (JSONB).
    """

    dimension_keys: list[str] = Field(default_factory=list)
    weight_overrides: dict[str, int] = Field(default_factory=dict)
    rubric: str | None = None
    pass_threshold: int | None = None
    notes: str | None = None


class PipelineStageConfig(BaseModel):
    """Operational config for a stage. Permissive — different stage types use
    different subsets (panel for interviews, deadline for assignment, etc.).
    Stored in ``role_pipeline_stages.config`` (JSONB)."""

    model_config = ConfigDict(extra="allow")

    panel_member_ids: list[str] = Field(default_factory=list)
    panel_emails: list[str] = Field(default_factory=list)
    duration_minutes: int | None = None
    bot_enabled: bool | None = None
    bot_provider: str | None = None
    deadline_days: int | None = None
    max_questions: int | None = None


class PipelineStageDef(BaseModel):
    """Wire shape of one pipeline stage (mirrors a role_pipeline_stages row)."""

    stage_key: str
    stage_type: StageType
    label: str
    position: int
    mode: StageMode = StageMode.MANUAL
    is_enabled: bool = True
    config: PipelineStageConfig = Field(default_factory=PipelineStageConfig)
    eval_spec: StageEvalSpec = Field(default_factory=StageEvalSpec)


# Default pipeline seeded onto roles with none (and on the migration backfill).
# Mirrors the ideal flow: auto from intake through voice screen, no HR
# intervention. The assignment stage auto-sends the take-home when the candidate
# reaches it and then parks for HR review once they submit (mode=manual), so there
# is no separate review stage. Voice screen IS the screen — there is no separate
# written-screening stage by default (a role can add one). Reorder / remove /
# duplicate stages per role to customise.
DEFAULT_PIPELINE: list[dict] = [
    {"stage_key": "intake", "stage_type": "intake", "label": "Intake", "mode": "auto"},
    {"stage_key": "parse", "stage_type": "parse", "label": "Resume Parse", "mode": "auto"},
    {"stage_key": "fit", "stage_type": "fit", "label": "Fit Score", "mode": "auto"},
    {"stage_key": "voice_screen", "stage_type": "voice_screen", "label": "Voice Screen", "mode": "auto"},
    {"stage_key": "assignment", "stage_type": "assignment", "label": "Assignment", "mode": "manual"},
    {"stage_key": "technical", "stage_type": "interview", "label": "Technical Interview", "mode": "manual"},
    # stage_key stays "ceo" (all round/booking/analysis machinery keys off it); the
    # human-facing LABEL is "Management Round" — the FE renders labels from stage_view.
    {"stage_key": "ceo", "stage_type": "interview", "label": "Management Round", "mode": "manual"},
    {"stage_key": "hr", "stage_type": "interview", "label": "HR Interview", "mode": "manual"},
    {"stage_key": "decision", "stage_type": "decision", "label": "Decision", "mode": "manual"},
    {"stage_key": "offer", "stage_type": "offer", "label": "Offer", "mode": "manual"},
]
