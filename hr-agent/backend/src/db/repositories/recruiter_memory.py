"""Per-recruiter long-term memory repository."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import RecruiterMemory


async def upsert(
    session: AsyncSession,
    *,
    actor_hash: str,
    key: str,
    value: Any,
    scope: str = "self",
) -> RecruiterMemory:
    stmt = (
        pg_insert(RecruiterMemory)
        .values(actor_hash=actor_hash, scope=scope, key=key, value=value)
        .on_conflict_do_update(
            index_elements=["actor_hash", "scope", "key"],
            set_={"value": value, "updated_at": datetime.now(tz=UTC)},
        )
    )
    await session.execute(stmt)
    row = (
        await session.execute(
            select(RecruiterMemory).where(
                RecruiterMemory.actor_hash == actor_hash,
                RecruiterMemory.scope == scope,
                RecruiterMemory.key == key,
            )
        )
    ).scalar_one()
    return row


async def list_by_actor(
    session: AsyncSession,
    *,
    actor_hash: str,
    prefix: str | None = None,
    limit: int = 50,
) -> list[RecruiterMemory]:
    q = (
        select(RecruiterMemory)
        .where(RecruiterMemory.actor_hash == actor_hash)
        .order_by(desc(RecruiterMemory.updated_at))
        .limit(max(1, min(int(limit), 200)))
    )
    if prefix:
        q = q.where(RecruiterMemory.key.like(f"{prefix}%"))
    return list((await session.execute(q)).scalars().all())


async def snapshot_for_prompt(
    session: AsyncSession,
    *,
    actor_hash: str,
    max_chars: int = 1500,
) -> str:
    """Render the recruiter's memory as a compact text block to inject into
    the system prompt. Most-recent first, capped to ``max_chars``.
    """
    rows = await list_by_actor(session, actor_hash=actor_hash, limit=80)
    if not rows:
        return ""
    lines: list[str] = []
    used = 0
    for r in rows:
        line = f"- {r.key} = {r.value}"
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)
