"""Policy rule repository with TTL cache.

resolve_policy(key, role_id, fallback) is the single entry point for all
pipeline threshold reads. Per-role override > global default > hardcoded
fallback. Returns (value, policy_rule_id) so callers can cite the rule
in DecisionRecord.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import PolicyRule

_CACHE: dict[str, tuple[Any, UUID | None, float]] = {}
_CACHE_TTL_SECONDS = 60.0


def _cache_key(key: str, role_id: UUID | None) -> str:
    return f"{key}::{role_id or 'global'}"


def invalidate_policy_cache() -> None:
    _CACHE.clear()


async def resolve_policy(
    session: AsyncSession,
    key: str,
    role_id: UUID | None = None,
    fallback: Any = None,
) -> tuple[Any, UUID | None]:
    """Resolve a policy value: per-role override > global default > fallback.

    Returns (value, policy_rule_id). policy_rule_id is None when fallback is
    used, so callers know the decision wasn't backed by a DB-stored rule.
    """
    ck = _cache_key(key, role_id)
    now = time.monotonic()
    cached = _CACHE.get(ck)
    if cached and (now - cached[2]) < _CACHE_TTL_SECONDS:
        return cached[0], cached[1]

    value, rule_id = await _resolve_from_db(session, key, role_id)

    if value is not None:
        _CACHE[ck] = (value, rule_id, now)
        return value, rule_id

    _CACHE[ck] = (fallback, None, now)
    return fallback, None


async def _resolve_from_db(
    session: AsyncSession,
    key: str,
    role_id: UUID | None,
) -> tuple[Any | None, UUID | None]:
    if role_id:
        stmt = (
            select(PolicyRule)
            .where(PolicyRule.key == key)
            .where(PolicyRule.role_id == role_id)
            .where(PolicyRule.is_active.is_(True))
            .limit(1)
        )
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()
        if row:
            return _cast_value(row.value, row.value_type), row.id

    stmt = (
        select(PolicyRule)
        .where(PolicyRule.key == key)
        .where(PolicyRule.role_id.is_(None))
        .where(PolicyRule.is_active.is_(True))
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row:
        return _cast_value(row.value, row.value_type), row.id

    return None, None


def _cast_value(value: Any, value_type: str) -> Any:
    if value_type == "int":
        return int(value)
    if value_type == "float":
        return float(value)
    if value_type == "bool":
        return bool(value)
    return value


async def upsert_policy_rule(
    session: AsyncSession,
    *,
    key: str,
    value: Any,
    value_type: str,
    role_id: UUID | None = None,
    description: str | None = None,
    updated_by: str = "system",
) -> PolicyRule:
    stmt = (
        select(PolicyRule)
        .where(PolicyRule.key == key)
        .where(PolicyRule.role_id == role_id if role_id else PolicyRule.role_id.is_(None))
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()

    if row:
        row.value = value
        row.value_type = value_type
        row.version = row.version + 1
        row.updated_by = updated_by
        if description is not None:
            row.description = description
    else:
        row = PolicyRule(
            key=key,
            role_id=role_id,
            value=value,
            value_type=value_type,
            description=description,
            updated_by=updated_by,
        )
        session.add(row)

    await session.flush()
    invalidate_policy_cache()
    return row


async def list_policy_rules(
    session: AsyncSession,
    role_id: UUID | None = None,
    include_globals: bool = True,
) -> list[PolicyRule]:
    stmt = select(PolicyRule).where(PolicyRule.is_active.is_(True))

    if role_id and include_globals:
        stmt = stmt.where(
            (PolicyRule.role_id == role_id) | (PolicyRule.role_id.is_(None))
        )
    elif role_id:
        stmt = stmt.where(PolicyRule.role_id == role_id)
    else:
        stmt = stmt.where(PolicyRule.role_id.is_(None))

    stmt = stmt.order_by(PolicyRule.key)
    result = await session.execute(stmt)
    return list(result.scalars().all())
