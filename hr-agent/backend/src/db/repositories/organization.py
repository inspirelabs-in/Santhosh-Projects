"""Organization repository: the tenant anchor + hiring-persona access.

One org today (GrabOn); tenant-ready. Callers pass an explicit ``org_id`` where
they have one, else ``get_default`` resolves the single org during the
single-tenant phase.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
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


async def company_persona_block(
    session: AsyncSession, org_id: UUID | None = None
) -> tuple[str, str]:
    """Render the org's company persona as a plain-text block for prompt
    injection (assignment generation, etc.), plus the company name.

    Sources, in order of preference, from ``organizations``:
      * ``name`` -> the company name used in candidate-facing text.
      * ``settings.org_context.summary`` -> the grounding paragraph.
      * ``hiring_persona.hiring_philosophy`` / ``.tone`` -> the culture line.

    Returns ``(persona_block, company_name)``. Never raises: when the org or its
    context is unset, returns a minimal block so the prompt still compiles.
    Resolves the default org when ``org_id`` is omitted (single-tenant phase).
    """
    org = (
        await session.get(Organization, org_id)
        if org_id is not None
        else await get_default(session)
    )
    if org is None:
        block = (
            "# Company\n\n"
            "Use the company name in all candidate-facing text. "
            "Never invent fictional company names."
        )
        return block, "the company"

    name = (org.name or "the company").strip()
    settings = org.settings if isinstance(org.settings, dict) else {}
    ctx = settings.get("org_context") if isinstance(settings, dict) else None
    summary = (ctx.get("summary") if isinstance(ctx, dict) else None) or ""
    persona = org.hiring_persona if isinstance(org.hiring_persona, dict) else {}
    culture = (persona.get("hiring_philosophy") or persona.get("tone") or "").strip()

    lines = [f"# Company: {name}", ""]
    if summary.strip():
        lines.append(summary.strip())
        lines.append("")
    if culture:
        lines.append(f"Culture: {culture}")
        lines.append("")
    lines.append(
        f'Use "{name}" as the company name in all candidate-facing text. '
        "Never invent fictional company names."
    )
    return "\n".join(lines), name


async def set_persona(
    session: AsyncSession, org_id: UUID, persona: dict
) -> Organization | None:
    org = await session.get(Organization, org_id)
    if org is not None:
        org.hiring_persona = persona
    return org


async def get_org_overview(
    session: AsyncSession, org_id: UUID | None = None
) -> dict[str, Any] | None:
    """Compact org snapshot for the onboarding read tool.

    Returns ``{org_id, org_name, persona, org_context_summary}`` or ``None`` when
    no org exists. ``persona`` is the raw ``hiring_persona`` dict (``{}`` if
    unset). Resolves the default org when ``org_id`` is omitted.
    """
    org = (
        await session.get(Organization, org_id)
        if org_id is not None
        else await get_default(session)
    )
    if org is None:
        return None
    persona = org.hiring_persona if isinstance(org.hiring_persona, dict) else {}
    settings = org.settings if isinstance(org.settings, dict) else {}
    ctx = settings.get("org_context") if isinstance(settings, dict) else None
    summary = ctx.get("summary") if isinstance(ctx, dict) else None
    return {
        "org_id": str(org.id),
        "org_name": org.name,
        "persona": persona,
        "org_context_summary": summary,
    }


async def merge_persona(
    session: AsyncSession,
    org_id: UUID | None,
    patch: dict[str, Any],
    *,
    replace_lists: bool = False,
    org_context_summary: str | None = None,
) -> tuple[list[str], int] | None:
    """Deep-merge ``patch`` into the org's hiring_persona (never a blind replace).

    Scalars overwrite-if-present; list fields append+dedupe (or overwrite when
    ``replace_lists``). Bumps ``version``, stamps ``updated_at``, validates the
    result against ``HiringPersona`` before persisting, and optionally sets
    ``settings.org_context.summary``. Returns ``(changed_fields, new_version)``
    or ``None`` if the org doesn't exist. Resolves the default org when
    ``org_id`` is omitted.
    """
    # Imported here to avoid a heavy import at module load.
    from src.models.persona import HiringPersona
    from src.recruiter_agent.org_onboarding import merge_persona_patch

    org = (
        await session.get(Organization, org_id)
        if org_id is not None
        else await get_default(session)
    )
    if org is None:
        return None

    current = org.hiring_persona if isinstance(org.hiring_persona, dict) else {}
    merged, changed = merge_persona_patch(current, patch, replace_lists=replace_lists)

    summary_changed = False
    if isinstance(org_context_summary, str) and org_context_summary.strip():
        summary_changed = True

    if not changed and not summary_changed:
        return [], int(current.get("version", 1) or 1)

    new_version = int(current.get("version", 1) or 1) + 1
    merged["version"] = new_version
    merged["updated_at"] = datetime.now(UTC).isoformat()

    # Pydantic is the rail — raises if the merged shape is invalid.
    HiringPersona(**merged)

    # Reassign fresh dicts so SQLAlchemy detects the JSONB mutation.
    org.hiring_persona = dict(merged)
    if summary_changed:
        settings = dict(org.settings or {})
        ctx = dict(settings.get("org_context") or {})
        ctx["summary"] = org_context_summary.strip()
        settings["org_context"] = ctx
        org.settings = settings
        changed = [*changed, "org_context_summary"]

    return changed, new_version


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
