"""Brand-hint resolver.

Phase-1 stub: maps a freeform hint (page name, domain, etc.) to an
existing `brands.id` via root_domain match, else inserts a new brand row
and returns its id. Embedding-based fuzzy match comes later.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_DOMAIN_RE = re.compile(r"^(?:https?://)?(?:www\.)?([^/\s]+)", re.I)

# Platform hosts where subdomains are not real brand domains
_PLATFORM_HOSTS = {
    "myshopify.com", "shopify.com", "shopify.in", "shopifypreview.com",
    "bigcartel.com",
    "wixsite.com", "squarespace.com", "webflow.io",
    "godaddysites.com", "carrd.co",
    "netlify.app", "vercel.app", "herokuapp.com",
}

# Auto-generated junk patterns (hex slugs, numeric store IDs)
_JUNK_PREFIX_RE = re.compile(
    r"^(\d{2,}-\d{2,}[a-z]*-|[0-9a-f]{8,}-|store-?\d{4,}|shop-?\d{4,}|test-?site|demo-?store)",
    re.I,
)


def normalize_domain(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().lower()
    if "://" not in v and "." in v and " " not in v:
        v = "http://" + v
    if "://" in v:
        host = urlparse(v).hostname or ""
    else:
        m = _DOMAIN_RE.match(v)
        host = m.group(1).lower() if m else ""
    host = host.removeprefix("www.")
    if not host or "." not in host:
        return None
    # Reject platform subdomains — real brands use custom domains
    for ph in _PLATFORM_HOSTS:
        if host.endswith("." + ph):
            return None
    # Reject auto-generated junk domain prefixes
    if _JUNK_PREFIX_RE.match(host):
        return None
    return host


async def resolve_or_create(
    session: AsyncSession,
    *,
    name: str | None,
    domain: str | None,
    enforce_veto: bool = True,
) -> int | None:
    """Return brand_id for hint. Creates a row if no match and name is provided.

    When `enforce_veto` (default), the brand_blocklist is consulted *before*
    insertion. `block` decisions return None — the caller decides whether to
    log the rejection. `existing_client` decisions still resolve (we want
    visibility for CSMs); the brand row will carry status='existing_client'.
    """
    from .veto import evaluate as _veto_eval

    rd = normalize_domain(domain)
    if enforce_veto:
        decision = await _veto_eval(session, name=name, domain=rd)
        if decision.decision == "blocked":
            return None
    if rd:
        row = (
            await session.execute(
                text("SELECT id FROM brands WHERE root_domain = :rd LIMIT 1"),
                {"rd": rd},
            )
        ).first()
        if row:
            return int(row[0])
    if not name and not rd:
        return None
    if not rd and name:
        row = (
            await session.execute(
                text("SELECT id FROM brands WHERE LOWER(name) = LOWER(:n) LIMIT 1"),
                {"n": name},
            )
        ).first()
        if row:
            return int(row[0])
        # Create brand from name alone — domain will be enriched later by research graph
        status_name = "existing_client" if (enforce_veto and decision.decision == "existing_client") else "new"
        try:
            row = (
                await session.execute(
                    text("INSERT INTO brands (name, domain, status) VALUES (:n, '', :st) RETURNING id"),
                    {"n": name, "st": status_name},
                )
            ).first()
            return int(row[0]) if row else None
        except Exception:
            return None
    if not rd:
        return None

    # Before inserting a new domain-bearing brand, check if a name-only twin
    # already exists (domain='').  If so, adopt it — update domain in place.
    if name:
        twin = (
            await session.execute(
                text(
                    "SELECT id FROM brands WHERE domain = '' AND LOWER(name) = LOWER(:n) LIMIT 1"
                ),
                {"n": name},
            )
        ).first()
        if twin:
            await session.execute(
                text("UPDATE brands SET domain = :d WHERE id = :id"),
                {"d": rd, "id": twin[0]},
            )
            return int(twin[0])

    status_initial = "existing_client" if (enforce_veto and decision.decision == "existing_client") else "new"
    # Insert. Use ON CONFLICT (root_domain) DO UPDATE to be idempotent on race.
    insert_sql = text(
        "INSERT INTO brands (name, domain, status) VALUES (:n, :d, :st) "
        "ON CONFLICT (root_domain) WHERE root_domain <> '' "
        "DO UPDATE SET name = COALESCE(brands.name, EXCLUDED.name) "
        "RETURNING id"
    )
    try:
        row = (
            await session.execute(insert_sql, {"n": name or rd, "d": rd or "", "st": status_initial})
        ).first()
        return int(row[0]) if row else None
    except Exception:
        # ON CONFLICT clause requires a unique constraint matching; if the
        # legacy schema lacks one on root_domain (older DBs), fall back to plain insert.
        row = (
            await session.execute(
                text("INSERT INTO brands (name, domain, status) VALUES (:n, :d, :st) RETURNING id"),
                {"n": name or rd, "d": rd or "", "st": status_initial},
            )
        ).first()
        return int(row[0]) if row else None
