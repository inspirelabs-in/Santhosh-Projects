"""Conversation artifacts — structured, editable output the agent produces.

Instead of rendering tool proposals as inline "confirm-card" blocks in the chat
stream, the agent writes a typed **artifact** (structured JSON) into a collection
linked to the conversation. The frontend opens it in a side panel and renders it
as an **editable form**. Both the human (form edits) and the agent (on request)
can update the same artifact; an `apply` turns it into the real entity.

v1 ships one type — ``role_draft`` — which bundles the JD, the configurable
pipeline, and the persona-derived evaluation spec into one editable document.
More types (assignment_draft, linkedin_draft, email_draft) slot in the same way.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from src.models.context import RoleContext
from src.models.evaluation import EvaluationSpec
from src.models.pipeline import PipelineStageDef


class ArtifactType(StrEnum):
    ROLE_DRAFT = "role_draft"


class ArtifactStatus(StrEnum):
    DRAFT = "draft"        # being edited
    APPLIED = "applied"    # turned into the real entity (role created)
    DISMISSED = "dismissed"


class RoleDraftAssignment(BaseModel):
    """Assignment config carried in a role draft.

    ``brief`` / ``instructions`` carry the COMPANY'S OWN take-home, captured
    verbatim from the scoping conversation when the hiring manager described one
    or pasted their ideas. When ``brief`` is non-empty it is the source of truth:
    on apply it is persisted directly (text only; no PDF is rendered and the role
    stays in ``draft`` until the recruiter posts the assignment). Nothing is ever
    auto-generated: an empty ``brief`` simply means no assignment yet -- the
    recruiter uploads their own or explicitly asks Pulse to draft one.
    """

    enabled: bool = False
    brief: str = ""           # company-provided take-home (markdown); empty => none yet
    instructions: str = ""    # company-provided submission instructions (optional)
    n_problems: int = Field(default=2)
    time_budget_hours: int = Field(default=6)
    deadline_days: int = Field(default=7)

    @model_validator(mode="after")
    def _clamp(self) -> "RoleDraftAssignment":
        self.n_problems = max(1, min(8, self.n_problems))
        self.time_budget_hours = max(1, min(40, self.time_budget_hours))
        self.deadline_days = max(1, min(60, self.deadline_days))
        return self


class RoleDraftContent(BaseModel):
    """The editable content of a ``role_draft`` artifact. Composed from the
    Phase-0 contracts (PipelineStageDef, EvaluationSpec) so the same shapes flow
    straight through to role creation on apply."""

    title: str | None = None
    jd_text: str | None = None
    ctc_min_lpa: float | None = None
    ctc_max_lpa: float | None = None
    location: str | None = None
    remote_policy: str | None = None
    max_notice_days: int | None = None
    pipeline: list[PipelineStageDef] = Field(default_factory=list)
    evaluation_spec: EvaluationSpec = Field(default_factory=EvaluationSpec)
    company_context: RoleContext = Field(default_factory=RoleContext)
    assignment: RoleDraftAssignment = Field(default_factory=RoleDraftAssignment)
    notes: str | None = None


class Artifact(BaseModel):
    """Wire shape returned to the frontend (mirrors a recruiter_artifacts row)."""

    id: str
    conversation_id: str
    type: ArtifactType
    status: ArtifactStatus
    title: str | None = None
    content: dict
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
