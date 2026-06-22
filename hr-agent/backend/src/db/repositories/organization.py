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


async def get_email_domains(
    session: AsyncSession, org_id: UUID | None = None
) -> list[str]:
    """The org's own email domains (``settings["email_domains"]``).

    Used by the inbound-mail funnel to tell internal mail from candidate mail.
    Stored as a JSON list on ``organizations.settings`` (no dedicated table).
    Resolves the default org when ``org_id`` is omitted (single-tenant phase).
    Always returns a clean, lower-cased, ``@``-stripped list (never raises).
    """
    org = (
        await session.get(Organization, org_id)
        if org_id is not None
        else await get_default(session)
    )
    if org is None:
        return []
    raw = (org.settings or {}).get("email_domains")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for d in raw:
        if not isinstance(d, str):
            continue
        cleaned = d.strip().lower().lstrip("@")
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


async def set_email_domains(
    session: AsyncSession, org_id: UUID, domains: list[str]
) -> Organization | None:
    """Replace the org's email-domain allowlist (merges into ``settings``)."""
    org = await session.get(Organization, org_id)
    if org is None:
        return None
    cleaned: list[str] = []
    for d in domains or []:
        if not isinstance(d, str):
            continue
        c = d.strip().lower().lstrip("@")
        if c and c not in cleaned:
            cleaned.append(c)
    # Reassign a new dict so SQLAlchemy detects the JSONB mutation.
    org.settings = {**(org.settings or {}), "email_domains": cleaned}
    return org
