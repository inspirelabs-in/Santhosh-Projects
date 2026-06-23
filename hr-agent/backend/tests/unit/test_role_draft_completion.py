"""Guards the role-draft auto-completion contract (strict validation).

``_complete_draft_from_context`` generates a COMPLETE ``RoleDraftContent`` and
validates it STRICTLY. That only works if the prompt tells the model the exact
nested shapes the schema requires. These tests pin both sides of that contract:

  * The exact pipeline / evaluation_spec / company_context shapes the prompt
    instructs the model to emit MUST validate (so prompt<->schema stays in sync).
  * ``RoleDraftContent`` rejects the malformed shapes an under-specified prompt
    used to elicit -- which is WHY the prompt must spell the shapes out.
  * An empty pipeline is still valid (the apply endpoint seeds the default).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models.artifacts import RoleDraftContent

_JD = "About the role.\n\n" + ("Responsibility. " * 40)

# Mirrors EXACTLY what the _complete_draft_from_context prompt tells the model to
# emit. If RoleDraftContent changes shape, this test fails -> update the prompt.
_VALID_FULL_DRAFT = {
    "title": "Full Stack Engineer (Fresher)",
    "jd_text": _JD,
    "ctc_min_lpa": 6,
    "ctc_max_lpa": 6,
    "location": "Hyderabad",
    "remote_policy": "onsite",
    "max_notice_days": 0,
    "pipeline": [
        {"stage_key": "fit", "stage_type": "fit", "label": "Fit Score", "position": 0, "mode": "auto"},
        {"stage_key": "voice_screen", "stage_type": "voice_screen", "label": "Voice Screen", "position": 1, "mode": "auto"},
        {"stage_key": "assignment", "stage_type": "assignment", "label": "Assignment", "position": 2, "mode": "manual"},
        {"stage_key": "technical", "stage_type": "interview", "label": "Technical Interview", "position": 3, "mode": "manual"},
        {"stage_key": "hr", "stage_type": "interview", "label": "HR Interview", "position": 4, "mode": "manual"},
        {"stage_key": "offer", "stage_type": "offer", "label": "Offer", "position": 5, "mode": "manual"},
    ],
    "evaluation_spec": {
        "dimensions": [
            {"key": "fundamentals", "label": "CS Fundamentals", "weight": 20, "what_good_looks_like": ["solid DS/algo"], "anti_signals": ["rote answers"]},
            {"key": "mern", "label": "MERN Proficiency", "weight": 20, "what_good_looks_like": ["ships features"], "anti_signals": ["tutorial-only"]},
            {"key": "ownership", "label": "Ownership", "weight": 20, "what_good_looks_like": ["end to end"], "anti_signals": ["needs hand-holding"]},
            {"key": "comms", "label": "Communication", "weight": 20, "what_good_looks_like": ["clear writing"], "anti_signals": ["vague"]},
            {"key": "learning", "label": "Learning Velocity", "weight": 20, "what_good_looks_like": ["picks up fast"], "anti_signals": ["static"]},
        ]
    },
    "company_context": {
        "intensity": "standard",
        "summary": "Builder-first team; freshers own real surface area.",
        "what_matters_here": ["ships working software", "thinks in systems"],
        "hiring_bar": "Delivers a working solution to an ambiguous problem within a week.",
    },
    "assignment": {"enabled": True, "n_problems": 2, "time_budget_hours": 6, "deadline_days": 7},
}


def test_prompt_template_shape_validates_strictly():
    """The exact full-draft shape the prompt asks for passes strict validation."""
    draft = RoleDraftContent.model_validate(_VALID_FULL_DRAFT)
    assert draft.jd_text.strip()
    assert len(draft.pipeline) == 6
    assert sum(d.weight for d in draft.evaluation_spec.dimensions) == 100
    assert draft.company_context.intensity == "standard"
    assert draft.model_dump(mode="json")["jd_text"]  # round-trips for storage


@pytest.mark.parametrize(
    "bad_nested",
    [
        {"pipeline": ["voice_screen", "assignment", "interview"]},  # bare strings
        {"pipeline": [{"stage_key": "vs", "stage_type": "voice_screen", "label": "V"}]},  # no position
        {"pipeline": [{"stage_key": "t", "stage_type": "technical_interview", "label": "T", "position": 0}]},  # bad enum
        {"evaluation_spec": {"dimensions": [
            {"key": "a", "label": "A", "weight": 30},
            {"key": "b", "label": "B", "weight": 30},
        ]}},  # weights sum 60
        {"company_context": {"intensity": "medium"}},  # bad literal
    ],
)
def test_role_draft_rejects_malformed_nested_shapes(bad_nested):
    """Documents WHY the prompt must spell out the exact shapes: any one
    malformed nested field fails the whole strict draft."""
    payload = {"title": "Backend Engineer", "jd_text": _JD, **bad_nested}
    with pytest.raises(ValidationError):
        RoleDraftContent.model_validate(payload)


def test_empty_pipeline_is_valid():
    """A draft with no pipeline still validates; apply seeds the default."""
    draft = RoleDraftContent.model_validate({"title": "X", "jd_text": _JD})
    assert draft.pipeline == []
