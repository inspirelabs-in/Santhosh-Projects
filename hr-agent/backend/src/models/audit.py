"""Append-only audit log and DPDP consent artifact schemas."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from ipaddress import IPv4Address, IPv6Address
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConsentType(StrEnum):
    HIRING = "hiring"
    TALENT_POOL = "talent_pool"
    ANALYTICS = "analytics"


class ConsentChannel(StrEnum):
    FORM = "form"
    EMAIL = "email"
    WHATSAPP = "whatsapp"


class ConsentArtifact(BaseModel):
    """Row from `consent_artifacts` table. Immutable once captured (except revoked_at)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    candidate_id: UUID
    consent_type: ConsentType = ConsentType.HIRING
    consent_text: str
    channel: ConsentChannel
    ip_address: IPv4Address | IPv6Address | None = None
    captured_at: datetime | None = None
    revoked_at: datetime | None = None


class AuditActor(StrEnum):
    AGENT = "agent"
    SYSTEM = "system"
    # HR users are stored by email (free string), not via this enum.


class AuditEntry(BaseModel):
    """Row from `audit_log` table. Append-only, never updated.

    Any state transition, LLM call, HR override, or outbound message must produce
    one of these rows. `langfuse_trace_id` links to the observability layer.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    candidate_id: UUID | None = None
    application_id: UUID | None = None
    action: str  # e.g. "classified", "fit_scored", "hr_override", "rejection_sent"
    actor: str  # "agent" | "system" | HR email
    details: dict[str, Any] = Field(default_factory=dict)
    model_version: str | None = None
    prompt_version: str | None = None
    langfuse_trace_id: str | None = None
    created_at: datetime | None = None
