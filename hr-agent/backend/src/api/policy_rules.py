"""Policy rules API: list, read, and update pipeline decision thresholds.

HR/admin uses these endpoints to configure per-role thresholds for
CTC tolerance, scoring weights, confidence gates, stall timers, etc.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from src.api.auth import require_recruiter, require_viewer
from src.db.base import PolicyRule
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.policy import _CACHE

router = APIRouter(prefix="/admin/policies", tags=["policies"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class PolicyRuleItem(BaseModel):
    id: str
    key: str
    role_id: str | None
    value: Any
    value_type: str
    description: str | None
    updated_by: str | None
    version: int
    is_active: bool
    created_at: str


class PolicyRuleListResponse(BaseModel):
    rules: list[PolicyRuleItem]
    total: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=PolicyRuleListResponse)
async def list_policies(
    _: Annotated[str, Depends(require_viewer)],
    key_prefix: str | None = Query(None, description="Filter by key prefix"),
    role_id: UUID | None = Query(None),
    active_only: bool = Query(True),
) -> PolicyRuleListResponse:
    """List all policy rules, optionally filtered."""
    async with session_scope() as session:
        stmt = select(PolicyRule).order_by(PolicyRule.key)
        if key_prefix:
            stmt = stmt.where(PolicyRule.key.startswith(key_prefix))
        if role_id:
            stmt = stmt.where(PolicyRule.role_id == role_id)
        if active_only:
            stmt = stmt.where(PolicyRule.is_active.is_(True))
        rows = (await session.execute(stmt)).scalars().all()

    return PolicyRuleListResponse(
        rules=[
            PolicyRuleItem(
                id=str(r.id),
                key=r.key,
                role_id=str(r.role_id) if r.role_id else None,
                value=r.value,
                value_type=r.value_type,
                description=r.description,
                updated_by=r.updated_by,
                version=r.version,
                is_active=r.is_active,
                created_at=str(r.created_at),
            )
            for r in rows
        ],
        total=len(rows),
    )


class PolicyRuleUpdate(BaseModel):
    value: Any
    description: str | None = None


@router.patch("/{rule_id}")
async def update_policy(
    rule_id: UUID,
    body: PolicyRuleUpdate,
    actor: Annotated[str, Depends(require_recruiter)],
) -> PolicyRuleItem:
    """Update a policy rule value. Bumps version and clears cache."""
    async with session_scope() as session:
        rule = await session.get(PolicyRule, rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="policy rule not found")

        old_value = rule.value
        rule.value = body.value
        if body.description is not None:
            rule.description = body.description
        rule.version += 1
        rule.updated_by = actor

        _CACHE.clear()

        await log_audit(
            session,
            action="policy_rule_updated",
            actor=actor,
            details={
                "rule_id": str(rule_id),
                "key": rule.key,
                "old_value": old_value,
                "new_value": body.value,
                "version": rule.version,
            },
        )

    return PolicyRuleItem(
        id=str(rule.id),
        key=rule.key,
        role_id=str(rule.role_id) if rule.role_id else None,
        value=rule.value,
        value_type=rule.value_type,
        description=rule.description,
        updated_by=rule.updated_by,
        version=rule.version,
        is_active=rule.is_active,
        created_at=str(rule.created_at),
    )


class PolicyRuleCreate(BaseModel):
    key: str
    value: Any
    value_type: str = "float"
    role_id: str | None = None
    description: str | None = None


@router.post("", status_code=201)
async def create_policy(
    body: PolicyRuleCreate,
    actor: Annotated[str, Depends(require_recruiter)],
) -> PolicyRuleItem:
    """Create a new policy rule (typically a per-role override)."""
    async with session_scope() as session:
        rule = PolicyRule(
            key=body.key,
            role_id=UUID(body.role_id) if body.role_id else None,
            value=body.value,
            value_type=body.value_type,
            description=body.description,
            updated_by=actor,
            version=1,
            is_active=True,
        )
        session.add(rule)
        await session.flush()

        _CACHE.clear()

        await log_audit(
            session,
            action="policy_rule_created",
            actor=actor,
            details={
                "rule_id": str(rule.id),
                "key": rule.key,
                "value": body.value,
            },
        )

    return PolicyRuleItem(
        id=str(rule.id),
        key=rule.key,
        role_id=str(rule.role_id) if rule.role_id else None,
        value=rule.value,
        value_type=rule.value_type,
        description=rule.description,
        updated_by=rule.updated_by,
        version=rule.version,
        is_active=rule.is_active,
        created_at=str(rule.created_at),
    )
