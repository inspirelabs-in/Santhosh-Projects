"""Append-only audit log repository."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import AuditLog


async def log_audit(
    session: AsyncSession,
    *,
    action: str,
    actor: str = "agent",
    candidate_id: UUID | None = None,
    application_id: UUID | None = None,
    details: dict[str, Any] | None = None,
    model_version: str | None = None,
    prompt_version: str | None = None,
    langfuse_trace_id: str | None = None,
) -> AuditLog:
    row = AuditLog(
        action=action,
        actor=actor,
        candidate_id=candidate_id,
        application_id=application_id,
        details=details or {},
        model_version=model_version,
        prompt_version=prompt_version,
        langfuse_trace_id=langfuse_trace_id,
    )
    session.add(row)
    await session.flush()
    return row
