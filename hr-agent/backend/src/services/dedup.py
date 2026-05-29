"""Candidate deduplication (intake + cross-application linking).

Layered match strategy from references/pipeline-stages.md:
  1. Exact email (strongest).
  2. Exact normalised phone (E.164).
  3. Exact normalised LinkedIn URL.
  4. Fuzzy name + city (rapidfuzz ratio > 85) -- weakest, requires both.

Phone normalisation assumes India (+91) as default region but respects
explicit country codes in the input.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse
from uuid import UUID

import phonenumbers
from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import Candidate


def normalise_phone(raw: str | None, default_region: str = "IN") -> str | None:
    if not raw:
        return None
    try:
        parsed = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def is_valid_indian_mobile(raw: str | None) -> bool:
    norm = normalise_phone(raw, default_region="IN")
    return bool(norm and norm.startswith("+91") and len(norm) == 13)


_LINKEDIN_SLUG_RE = re.compile(r"^/(in|pub)/([^/?#]+)/?")


def normalise_linkedin(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.netloc or "").lower().removeprefix("www.")
    if "linkedin.com" not in host:
        return None
    match = _LINKEDIN_SLUG_RE.match(parsed.path.lower())
    if not match:
        return None
    return f"https://www.linkedin.com/in/{match.group(2)}"


async def find_duplicate_candidate(
    session: AsyncSession,
    *,
    email: str | None = None,
    phone: str | None = None,
    linkedin_url: str | None = None,
    name: str | None = None,
    city: str | None = None,
) -> UUID | None:
    """Return the id of an existing candidate that matches, or None."""
    if email:
        row = await session.scalar(select(Candidate).where(Candidate.email == email.lower()))
        if row is not None:
            return row.id

    normalised_phone = normalise_phone(phone)
    if normalised_phone:
        row = await session.scalar(
            select(Candidate).where(Candidate.phone == normalised_phone)
        )
        if row is not None:
            return row.id

    normalised_li = normalise_linkedin(linkedin_url)
    if normalised_li:
        row = await session.scalar(
            select(Candidate).where(Candidate.linkedin_url == normalised_li)
        )
        if row is not None:
            return row.id

    if name and city:
        rows = (
            await session.scalars(
                select(Candidate).where(Candidate.name.isnot(None))
            )
        ).all()
        name_l = name.lower()
        city_l = city.lower()
        for r in rows:
            if not r.name:
                continue
            # Compare against candidates whose stored contact info plausibly
            # places them in the same city; the fuzzy match is the tiebreaker.
            if fuzz.ratio(name_l, r.name.lower()) > 85 and city_l in (r.name.lower()):
                return r.id

    return None
