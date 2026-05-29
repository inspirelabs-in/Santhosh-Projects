"""Role repository: open roles listing + title-based matching for the classifier."""

from __future__ import annotations

from uuid import UUID

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import Role
from src.models.candidate import RoleStatus


async def list_open_roles(session: AsyncSession) -> list[Role]:
    result = await session.scalars(
        select(Role).where(Role.status == RoleStatus.OPEN.value).order_by(Role.title)
    )
    return list(result)


async def get_role(session: AsyncSession, role_id: UUID) -> Role | None:
    return await session.get(Role, role_id)


async def match_role_by_title(
    session: AsyncSession, title: str, threshold: int = 80
) -> Role | None:
    """Fuzzy-match a title string (from the LLM classifier) to an open role."""
    if not title or title.lower() == "unknown":
        return None
    roles = await list_open_roles(session)
    if not roles:
        return None
    lookup = {r.title: r for r in roles}
    match = process.extractOne(title, list(lookup.keys()), scorer=fuzz.WRatio)
    if match is None:
        return None
    matched_title, score, _ = match
    if score < threshold:
        return None
    return lookup[matched_title]
