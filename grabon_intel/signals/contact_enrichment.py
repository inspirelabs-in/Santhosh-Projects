"""Contact enrichment collector — finds decision-maker contacts via SearXNG.

Free alternative to Apollo/ZoomInfo contact databases.
Uses SearXNG to search for founders, C-suite, and marketing leaders.
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..config import get_settings
from ..logging import get_logger
from .base import Collector, SignalEvent

log = get_logger(__name__)

_LINKEDIN_PROFILE_RE = re.compile(
    r"linkedin\.com/in/([a-zA-Z0-9_-]+)", re.I
)

_TITLE_PATTERNS = [
    r"(?:co-?)?founder",
    r"CEO|CTO|CMO|COO|CFO|CXO",
    r"chief\s+(?:executive|technology|marketing|operating|financial)\s+officer",
    r"head\s+of\s+(?:marketing|growth|digital|ecommerce|e-commerce)",
    r"VP\s+(?:of\s+)?(?:marketing|growth|sales|business)",
    r"director\s+(?:of\s+)?(?:marketing|growth|digital|ecommerce)",
    r"managing\s+director",
    r"general\s+manager",
]
_TITLE_RE = re.compile("|".join(_TITLE_PATTERNS), re.I)

_EMAIL_PATTERNS = [
    "{first}@{domain}",
    "{first}.{last}@{domain}",
    "{first}{last}@{domain}",
    "{f}{last}@{domain}",
]

_NAME_RE = re.compile(r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})$")


class ContactEnrichmentCollector(Collector):
    """Find decision-maker contacts for brands via SearXNG search."""

    name = "contact_enrichment"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        domains = self.params.get("domains") or []
        if not domains:
            return

        searxng_url = get_settings().searxng_url
        if not searxng_url:
            log.warning("contact_enrichment.no_searxng_url")
            return

        brand_names = self.params.get("brand_names") or []
        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()

        async with httpx.AsyncClient(timeout=30.0) as client:
            for idx, domain in enumerate(domains):
                domain = domain.strip().lower().removeprefix("www.")
                if not domain:
                    continue

                brand_name = brand_names[idx] if idx < len(brand_names) else domain.split(".")[0]

                _SEARCH_QUERIES = [
                    f'"{brand_name}" founder CEO site:linkedin.com/in',
                    f'"{brand_name}" "head of marketing" OR CMO OR "VP marketing" site:linkedin.com/in',
                    f'"{brand_name}" founder OR CEO OR "managing director"',
                    f'"{brand_name}" {domain} team leadership about',
                ]

                contacts_found: list[dict[str, Any]] = []
                seen_names: set[str] = set()

                for query in _SEARCH_QUERIES:
                    try:
                        resp = await client.get(
                            f"{searxng_url}/search",
                            params={
                                "q": query,
                                "format": "json",
                                "engines": "google,bing",
                                "language": "en",
                                "safesearch": "1",
                            },
                        )
                        if resp.status_code != 200:
                            continue
                        results = resp.json().get("results", [])
                    except Exception:
                        continue

                    for r in results[:8]:
                        title = r.get("title", "")
                        url = r.get("url", "")
                        content = r.get("content", "")

                        person = _extract_person(title, url, content, brand_name, domain)
                        if person and person["name"].lower() not in seen_names:
                            seen_names.add(person["name"].lower())
                            contacts_found.append(person)

                for contact in contacts_found[:10]:
                    email_guesses = _guess_emails(contact["name"], domain)
                    yield SignalEvent(
                        type="contact.decision_maker",
                        source=self.name,
                        observed_at=now,
                        brand_domain=domain,
                        value_num=None,
                        value_text=f"{contact['name']} — {contact.get('title', 'Unknown')}",
                        payload={
                            "person_name": contact["name"],
                            "person_title": contact.get("title"),
                            "linkedin_url": contact.get("linkedin_url"),
                            "email_guesses": email_guesses,
                            "source_url": contact.get("source_url"),
                            "confidence": contact.get("confidence", 0.5),
                        },
                        dedupe_key=f"contact:{domain}:{contact['name'].lower().replace(' ', '-')}:{day}",
                    )


def _extract_person(
    title: str, url: str, content: str, brand_name: str, domain: str
) -> dict[str, Any] | None:
    """Try to extract a person's name, title, and LinkedIn from a search result."""
    combined = f"{title} {content}"
    brand_lower = brand_name.lower()

    if brand_lower not in combined.lower() and domain not in combined.lower():
        return None

    linkedin_match = _LINKEDIN_PROFILE_RE.search(url)
    linkedin_url = f"https://linkedin.com/in/{linkedin_match.group(1)}" if linkedin_match else None

    person_name = None
    person_title = None

    if linkedin_url:
        parts = title.split(" - ")
        if len(parts) >= 2:
            person_name = parts[0].strip()
            for p in parts[1:]:
                if _TITLE_RE.search(p):
                    person_title = p.strip()
                    break

    title_match = _TITLE_RE.search(combined)
    if title_match and not person_title:
        person_title = title_match.group(0).strip()

    if not person_name:
        for segment in combined.split(","):
            segment = segment.strip()
            if _NAME_RE.match(segment):
                person_name = segment
                break

    if not person_name or len(person_name) < 3 or len(person_name) > 60:
        return None

    noise = {"linkedin", "about", "team", brand_lower, domain}
    if person_name.lower() in noise:
        return None

    confidence = 0.3
    if linkedin_url:
        confidence += 0.3
    if person_title:
        confidence += 0.2
    if brand_lower in combined.lower():
        confidence += 0.1

    return {
        "name": person_name,
        "title": person_title,
        "linkedin_url": linkedin_url,
        "source_url": url,
        "confidence": min(confidence, 1.0),
    }


def _guess_emails(name: str, domain: str) -> list[str]:
    """Generate likely email patterns from name + domain."""
    parts = name.lower().strip().split()
    if len(parts) < 2:
        return [f"{parts[0]}@{domain}"]
    first = parts[0]
    last = parts[-1]
    f = first[0]
    emails = []
    for pattern in _EMAIL_PATTERNS:
        email = pattern.format(first=first, last=last, f=f, domain=domain)
        email = re.sub(r"[^a-z0-9@._-]", "", email)
        emails.append(email)
    return emails
