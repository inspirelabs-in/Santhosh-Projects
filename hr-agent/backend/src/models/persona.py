"""Company hiring persona — the org's worldview that seeds role eval specs.

Stored in ``organizations.hiring_persona`` (JSONB). Static but editable in
Settings. It is **not** a rubric: it's the seed the role-eval-spec generator
reasons from, so a coupon-editor role and an engineer role get different
dimensions from the *same* persona. Org-scoped, so each tenant gets its own
worldview.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PersonaValue(BaseModel):
    name: str
    description: str | None = None


class HiringPersona(BaseModel):
    company_name: str | None = None
    mission: str | None = None
    domain_context: str | None = None
    values: list[PersonaValue] = Field(default_factory=list)
    what_good_looks_like: list[str] = Field(default_factory=list)
    anti_patterns: list[str] = Field(default_factory=list)
    hiring_philosophy: str | None = None
    tone: str | None = None
    version: int = 1
    updated_at: datetime | None = None
