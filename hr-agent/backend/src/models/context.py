"""Role-tuned company context — generated at JD time, grounds every later stage.

Distinct from ``org.hiring_persona`` (the static, org-wide worldview): this is
the *role-specific* grounding generated when the JD is drafted, from the persona
+ the role. Its **intensity scales with the role** so the bar and the depth of
context tighten for higher-stakes roles (a Staff/critical hire gets a deeper,
more demanding context than a junior one).

Stored on ``roles.company_context`` (JSONB) and fed to fit, screening, voice,
assignment, and interview-analysis prompts so their judgment is grounded in the
company + this specific role.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RoleContext(BaseModel):
    intensity: Literal["light", "standard", "high", "critical"] = "standard"
    summary: str | None = None  # grounding narrative the LLM reads before judging
    what_matters_here: list[str] = Field(default_factory=list)  # role-specific signals
    hiring_bar: str | None = None  # what clearing the bar looks like for this role
    generated_from_persona_version: int | None = None
    generated_at: datetime | None = None
