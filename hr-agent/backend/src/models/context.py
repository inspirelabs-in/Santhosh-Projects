"""Role-tuned company context — generated at JD time, grounds every later stage.

Distinct from ``org.hiring_persona`` (the static, org-wide worldview): this is
the *role-specific* grounding generated when the JD is drafted from the persona
+ the role. Stored on ``roles.company_context`` (JSONB) and fed to fit,
screening, voice, assignment, and interview-analysis prompts.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RoleContext(BaseModel):
    summary: str | None = None  # grounding narrative the LLM reads before judging
    what_matters_here: list[str] = Field(default_factory=list)  # role-specific signals
    hiring_bar: str | None = None  # what clearing the bar looks like for this role
    generated_from_persona_version: int | None = None
    generated_at: datetime | None = None
