"""Organization repository: the tenant anchor + hiring-persona access.

One org today (GrabOn); tenant-ready. Callers pass an explicit ``org_id`` where
they have one, else ``get_default`` resolves the single org during the
single-tenant phase.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import Organization


async def get_org(session: AsyncSession, org_id: UUID) -> Organization | None:
    return await session.get(Organization, org_id)


async def get_by_slug(session: AsyncSession, slug: str) -> Organization | None:
    return await session.scalar(select(Organization).where(Organization.slug == slug))


async def get_default(session: AsyncSession) -> Organization | None:
    """The single org during the single-tenant phase (earliest created)."""
    return await session.scalar(
        select(Organization).order_by(Organization.created_at).limit(1)
    )


async def create_org(
    session: AsyncSession,
    *,
    name: str,
    slug: str,
    hiring_persona: dict | None = None,
    settings: dict | None = None,
) -> Organization:
    org = Organization(
        name=name,
        slug=slug,
        hiring_persona=hiring_persona or {},
        settings=settings or {},
    )
    session.add(org)
    await session.flush()
    return org


async def set_persona(
    session: AsyncSession, org_id: UUID, persona: dict
) -> Organization | None:
    org = await session.get(Organization, org_id)
    if org is not None:
        org.hiring_persona = persona
    return org
