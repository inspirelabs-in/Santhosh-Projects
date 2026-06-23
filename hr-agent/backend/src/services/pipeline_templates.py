"""Pipeline template registry.

Each Role can have a ``pipeline_template`` — an ordered list of step IDs
that defines the hiring flow for that role.  The auto-progress engine reads
this list to decide what action to fire after each stage completes.

When ``role.pipeline_template`` is NULL, the legacy hardcoded flow is used
(backward compatible).

Step definitions live here so both the API (validation, presets endpoint)
and the auto-progress engine share the same source of truth.
"""

# DEPRECATED: This module is retained for backward-compatible validation
# and preset lookup by the roles API and recruiter_agent tools. The real
# pipeline is driven by role_pipeline_stages rows (see pipeline_engine.py).
# Do NOT add new logic here.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.models.v1 import PipelineStage


@dataclass(frozen=True)
class StepDef:
    """Metadata for a single pipeline step."""

    id: str
    label: str
    category: str  # screening | assessment | interview | verification | terminal
    # Stages this step owns — auto_progress checks if current stage
    # is a "completed" stage for this step before advancing.
    entry_stage: PipelineStage | None = None
    completed_stages: frozenset[PipelineStage] = field(default_factory=frozenset)
    # The action key auto_progress uses to fire this step.
    action: str | None = None
    # If True, this step represents a meeting round; the round name
    # is derived from id (e.g. "technical_interview" -> "technical").
    is_meeting: bool = False
    meeting_round: str | None = None


STEP_REGISTRY: dict[str, StepDef] = {}


def _reg(s: StepDef) -> StepDef:
    STEP_REGISTRY[s.id] = s
    return s


# ── Screening ──────────────────────────────────────────────────────────

_reg(StepDef(
    id="fit_score",
    label="Fit Score",
    category="screening",
    entry_stage=PipelineStage.APPLIED,
    completed_stages=frozenset({PipelineStage.APPLIED}),
    action="fit_score",
))

_reg(StepDef(
    id="voice_screen",
    label="Voice Screen",
    category="screening",
    entry_stage=PipelineStage.VOICE_SCREEN_SCHEDULED,
    completed_stages=frozenset({
        PipelineStage.VOICE_SCREEN_EVALUATED,
        PipelineStage.SCREENING_EVALUATED,
        PipelineStage.REPORT_READY,
    }),
    action="voice_screen",
))


# ── Assessment ─────────────────────────────────────────────────────────

_reg(StepDef(
    id="assignment",
    label="Take-Home Assignment",
    category="assessment",
    entry_stage=PipelineStage.ASSIGNMENT_SENT,
    completed_stages=frozenset({
        PipelineStage.ASSESSMENT_EVALUATED,
        PipelineStage.REPORT_READY,
    }),
    action="assessment",
))

# [TODO] cognitive_test = an external test LINK (cognitive/aptitude) the candidate
# completes ALONGSIDE the take-home assignment. NOT YET IMPLEMENTED: there is no
# dispatcher that sends the link, so this step currently behaves as a manual review
# gate (see _STEP_TYPE in recruiter_agent/tools.py). When building it, send the link
# together with the assignment email rather than as a separate auto-fired stage.
_reg(StepDef(
    id="cognitive_test",
    label="Cognitive Test",
    category="assessment",
    entry_stage=PipelineStage.ASSESSMENT_INVITED,
    completed_stages=frozenset({PipelineStage.ASSESSMENT_EVALUATED}),
    action="cognitive_test",
))

# ── Interview rounds ───────────────────────────────────────────────────

_reg(StepDef(
    id="technical_interview",
    label="Technical Round",
    category="interview",
    entry_stage=PipelineStage.TECHNICAL_MEETING_SCHEDULED,
    completed_stages=frozenset({
        PipelineStage.TECHNICAL_EVALUATED,
        PipelineStage.TECHNICAL_PENDING_APPROVAL,
    }),
    action="meeting",
    is_meeting=True,
    meeting_round="technical",
))

_reg(StepDef(
    id="hiring_manager",
    label="Hiring Manager",
    category="interview",
    entry_stage=PipelineStage.TECHNICAL_MEETING_SCHEDULED,
    completed_stages=frozenset({
        PipelineStage.TECHNICAL_EVALUATED,
        PipelineStage.TECHNICAL_PENDING_APPROVAL,
    }),
    action="meeting",
    is_meeting=True,
    meeting_round="technical",
))

_reg(StepDef(
    id="ceo_interview",
    label="CEO Round",
    category="interview",
    entry_stage=PipelineStage.CEO_MEETING_SCHEDULED,
    completed_stages=frozenset({
        PipelineStage.CEO_PENDING_APPROVAL,
        PipelineStage.CEO_MEETING_COMPLETED,
    }),
    action="meeting",
    is_meeting=True,
    meeting_round="ceo",
))

_reg(StepDef(
    id="hr_interview",
    label="HR Discussion",
    category="interview",
    entry_stage=PipelineStage.HR_MEETING_SCHEDULED,
    completed_stages=frozenset({
        PipelineStage.HR_EVALUATED,
        PipelineStage.HR_MEETING_COMPLETED,
    }),
    action="meeting",
    is_meeting=True,
    meeting_round="hr",
))

_reg(StepDef(
    id="panel_interview",
    label="Panel Round",
    category="interview",
    entry_stage=PipelineStage.TECHNICAL_MEETING_SCHEDULED,
    completed_stages=frozenset({
        PipelineStage.TECHNICAL_EVALUATED,
        PipelineStage.TECHNICAL_PENDING_APPROVAL,
    }),
    action="meeting",
    is_meeting=True,
    meeting_round="technical",
))

_reg(StepDef(
    id="bar_raiser",
    label="Bar Raiser",
    category="interview",
    entry_stage=PipelineStage.CEO_MEETING_SCHEDULED,
    completed_stages=frozenset({
        PipelineStage.CEO_PENDING_APPROVAL,
        PipelineStage.CEO_MEETING_COMPLETED,
    }),
    action="meeting",
    is_meeting=True,
    meeting_round="ceo",
))

# ── Verification ───────────────────────────────────────────────────────

_reg(StepDef(
    id="reference_check",
    label="Reference Check",
    category="verification",
    completed_stages=frozenset({PipelineStage.HR_EVALUATED}),
    action="reference_check",
))

_reg(StepDef(
    id="background_check",
    label="Background Check",
    category="verification",
    completed_stages=frozenset({PipelineStage.HR_EVALUATED}),
    action="background_check",
))

# ── Terminal ───────────────────────────────────────────────────────────

_reg(StepDef(
    id="offer",
    label="Offer",
    category="terminal",
    entry_stage=PipelineStage.HIRED,
    completed_stages=frozenset({PipelineStage.HIRED}),
    action="offer",
))


# ── Presets ────────────────────────────────────────────────────────────

PRESETS: dict[str, dict[str, Any]] = {
    "standard_engineer": {
        "label": "Standard Engineer",
        "description": "Full pipeline with assignment + 3 interview rounds",
        "steps": ["fit_score", "voice_screen", "assignment", "technical_interview", "ceo_interview", "hr_interview", "offer"],
    },
    "senior_engineer": {
        "label": "Senior / Staff Engineer",
        "description": "Technical depth with bar raiser round",
        "steps": ["fit_score", "voice_screen", "assignment", "technical_interview", "bar_raiser", "ceo_interview", "offer"],
    },
    "intern": {
        "label": "Intern / Fresher",
        "description": "Lightweight: voice screen, assignment and HR only",
        "steps": ["fit_score", "voice_screen", "assignment", "hr_interview", "offer"],
    },
    "executive": {
        "label": "Executive / CXO",
        "description": "CEO-heavy, no assignment",
        "steps": ["fit_score", "voice_screen", "ceo_interview", "hr_interview", "offer"],
    },
    "referral": {
        "label": "Referral",
        "description": "Voice screen + assignment for trusted sources",
        "steps": ["fit_score", "voice_screen", "assignment", "technical_interview", "hr_interview", "offer"],
    },
    "contract": {
        "label": "Contract / Freelance",
        "description": "Voice screen + technical, fast track",
        "steps": ["fit_score", "voice_screen", "technical_interview", "offer"],
    },
    "campus": {
        "label": "Campus / Bulk",
        "description": "Voice screen for volume hiring",
        "steps": ["fit_score", "voice_screen", "hr_interview", "offer"],
    },
    "internal_transfer": {
        "label": "Internal Transfer",
        "description": "Voice screen + minimal formality",
        "steps": ["fit_score", "voice_screen", "hiring_manager", "hr_interview", "offer"],
    },
    "rehire": {
        "label": "Re-hire",
        "description": "Voice screen + HR",
        "steps": ["fit_score", "voice_screen", "hr_interview", "offer"],
    },
}

DEFAULT_TEMPLATE = PRESETS["standard_engineer"]["steps"]


def validate_template(steps: list[str]) -> list[str]:
    """Validate and normalize a pipeline template. Returns list of errors."""
    errors: list[str] = []
    if not steps:
        errors.append("Pipeline must have at least one step")
        return errors
    if steps[-1] != "offer":
        errors.append("Pipeline must end with 'offer'")
    if "voice_screen" not in steps:
        errors.append("Pipeline must include 'voice_screen' — voice screening is mandatory for all roles")
    for s in steps:
        if s not in STEP_REGISTRY:
            errors.append(f"Unknown step: {s}")
    seen = set()
    for s in steps:
        if s in seen:
            errors.append(f"Duplicate step: {s}")
        seen.add(s)
    return errors


def get_next_step(template: list[str], current_step_id: str) -> str | None:
    """Given current completed step, return next step ID or None if done."""
    try:
        idx = template.index(current_step_id)
    except ValueError:
        return None
    if idx + 1 < len(template):
        return template[idx + 1]
    return None


def find_current_step(template: list[str], stage: PipelineStage) -> str | None:
    """Given a PipelineStage, find which template step the candidate is at."""
    for step_id in reversed(template):
        step_def = STEP_REGISTRY.get(step_id)
        if step_def and stage in step_def.completed_stages:
            return step_id
    for step_id in template:
        step_def = STEP_REGISTRY.get(step_id)
        if step_def and step_def.entry_stage == stage:
            return step_id
    return None
