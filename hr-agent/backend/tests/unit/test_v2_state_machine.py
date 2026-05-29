"""V2 agentic pipeline state-machine tests.

Locks the transition graph so no future refactor silently widens or breaks
the agentic pipeline. Whenever a stage changes shape, these tests must be
updated *consciously* -- that is the point.
"""

from __future__ import annotations

import pytest

from src.models.v1 import ALLOWED_TRANSITIONS, PipelineStage, can_transition


# ---------------------------------------------------------------------------
# Coverage: every stage appears in the transition map.
# ---------------------------------------------------------------------------


def test_every_stage_has_transition_entry():
    for stage in PipelineStage:
        assert stage in ALLOWED_TRANSITIONS, f"{stage} missing from ALLOWED_TRANSITIONS"


def test_terminal_stages_have_no_outgoing():
    assert ALLOWED_TRANSITIONS[PipelineStage.REJECTED] == set()
    assert ALLOWED_TRANSITIONS[PipelineStage.HIRED] == set()


# ---------------------------------------------------------------------------
# Voice-screen happy path: clear pass.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src,dst,expected",
    [
        (PipelineStage.SCREENING_EVALUATED, PipelineStage.VOICE_SCREEN_SCHEDULED, True),
        (PipelineStage.VOICE_SCREEN_SCHEDULED, PipelineStage.VOICE_SCREEN_IN_PROGRESS, True),
        (PipelineStage.VOICE_SCREEN_IN_PROGRESS, PipelineStage.VOICE_SCREEN_COMPLETED, True),
        (PipelineStage.VOICE_SCREEN_COMPLETED, PipelineStage.VOICE_SCREEN_EVALUATED, True),
        (PipelineStage.VOICE_SCREEN_EVALUATED, PipelineStage.ASSESSMENT_INVITED, True),
    ],
)
def test_voice_screen_happy_path(src, dst, expected):
    assert can_transition(src, dst) is expected


# ---------------------------------------------------------------------------
# Voice-screen callback loop.
# ---------------------------------------------------------------------------


def test_voice_screen_callback_loop():
    # In-progress -> callback_requested -> back to scheduled (re-dispatch).
    assert can_transition(
        PipelineStage.VOICE_SCREEN_IN_PROGRESS,
        PipelineStage.VOICE_SCREEN_CALLBACK_REQUESTED,
    )
    assert can_transition(
        PipelineStage.VOICE_SCREEN_CALLBACK_REQUESTED,
        PipelineStage.VOICE_SCREEN_SCHEDULED,
    )


# ---------------------------------------------------------------------------
# Assessment + technical + CEO chain.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src,dst",
    [
        (PipelineStage.ASSESSMENT_INVITED, PipelineStage.ASSESSMENT_COMPLETED),
        (PipelineStage.ASSESSMENT_COMPLETED, PipelineStage.ASSESSMENT_EVALUATED),
        (PipelineStage.ASSESSMENT_EVALUATED, PipelineStage.TECHNICAL_MEETING_SCHEDULED),
        (
            PipelineStage.TECHNICAL_MEETING_SCHEDULED,
            PipelineStage.TECHNICAL_MEETING_IN_PROGRESS,
        ),
        (
            PipelineStage.TECHNICAL_MEETING_IN_PROGRESS,
            PipelineStage.TECHNICAL_MEETING_COMPLETED,
        ),
        (
            PipelineStage.TECHNICAL_MEETING_COMPLETED,
            PipelineStage.TECHNICAL_EVALUATED,
        ),
        (PipelineStage.TECHNICAL_EVALUATED, PipelineStage.CEO_MEETING_SCHEDULED),
        (PipelineStage.CEO_MEETING_SCHEDULED, PipelineStage.CEO_MEETING_IN_PROGRESS),
        (PipelineStage.CEO_MEETING_IN_PROGRESS, PipelineStage.CEO_MEETING_COMPLETED),
        (PipelineStage.CEO_MEETING_COMPLETED, PipelineStage.HIRED),
    ],
)
def test_post_voice_chain(src, dst):
    assert can_transition(src, dst), f"missing transition {src} -> {dst}"


# ---------------------------------------------------------------------------
# Every non-terminal V2 stage can drop to needs_hr_review or rejected.
# ---------------------------------------------------------------------------


V2_STAGES = [
    PipelineStage.VOICE_SCREEN_SCHEDULED,
    PipelineStage.VOICE_SCREEN_IN_PROGRESS,
    PipelineStage.VOICE_SCREEN_CALLBACK_REQUESTED,
    PipelineStage.VOICE_SCREEN_COMPLETED,
    PipelineStage.VOICE_SCREEN_EVALUATED,
    PipelineStage.ASSESSMENT_INVITED,
    PipelineStage.ASSESSMENT_COMPLETED,
    PipelineStage.ASSESSMENT_EVALUATED,
    PipelineStage.TECHNICAL_MEETING_SCHEDULED,
    PipelineStage.TECHNICAL_MEETING_IN_PROGRESS,
    PipelineStage.TECHNICAL_MEETING_COMPLETED,
    PipelineStage.TECHNICAL_EVALUATED,
    PipelineStage.CEO_MEETING_SCHEDULED,
    PipelineStage.CEO_MEETING_IN_PROGRESS,
    PipelineStage.CEO_MEETING_COMPLETED,
]


@pytest.mark.parametrize("stage", V2_STAGES)
def test_v2_stage_can_reject(stage):
    assert can_transition(stage, PipelineStage.REJECTED)


@pytest.mark.parametrize("stage", V2_STAGES)
def test_v2_stage_can_park_for_hr(stage):
    assert can_transition(stage, PipelineStage.NEEDS_HR_REVIEW)


# ---------------------------------------------------------------------------
# Disallowed jumps: ensure the agent cannot skip rounds.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src,dst",
    [
        # Cannot leap from voice scheduled directly to evaluated.
        (PipelineStage.VOICE_SCREEN_SCHEDULED, PipelineStage.VOICE_SCREEN_EVALUATED),
        # Cannot send assessment before voice screen evaluation.
        (PipelineStage.VOICE_SCREEN_SCHEDULED, PipelineStage.ASSESSMENT_INVITED),
        # Cannot hire from technical -- only CEO_MEETING_COMPLETED can.
        (PipelineStage.TECHNICAL_EVALUATED, PipelineStage.HIRED),
        # Cannot rewind from CEO back to phone.
        (PipelineStage.CEO_MEETING_SCHEDULED, PipelineStage.VOICE_SCREEN_SCHEDULED),
        # Cannot leave terminal states.
        (PipelineStage.HIRED, PipelineStage.REJECTED),
        (PipelineStage.REJECTED, PipelineStage.HIRED),
    ],
)
def test_disallowed_jumps(src, dst):
    assert not can_transition(src, dst), f"{src} -> {dst} should be blocked"


# ---------------------------------------------------------------------------
# V1 paths still intact (regression guard).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src,dst",
    [
        (PipelineStage.APPLIED, PipelineStage.SCREENING_SENT),
        (PipelineStage.SCREENING_SENT, PipelineStage.SCREENING_SUBMITTED),
        (PipelineStage.SCREENING_SUBMITTED, PipelineStage.SCREENING_EVALUATED),
        (PipelineStage.SCREENING_EVALUATED, PipelineStage.ASSIGNMENT_SENT),
        (PipelineStage.ASSIGNMENT_SENT, PipelineStage.ASSIGNMENT_SUBMITTED),
        (PipelineStage.ASSIGNMENT_SUBMITTED, PipelineStage.REPORT_READY),
        (PipelineStage.REPORT_READY, PipelineStage.HIRED),
    ],
)
def test_v1_path_unchanged(src, dst):
    assert can_transition(src, dst)
