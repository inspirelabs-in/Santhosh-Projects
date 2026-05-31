"""LinkedIn Jobs collector — hiring-signal proxy.

Uses the public jobs "guest API" endpoint that backs LinkedIn's logged-out
search results. No auth required, but rate-limited aggressively. We pull
job postings matching keyword + geo, group by company, and emit one
`hiring.signal` per company per day with the job count + recent titles.

A burst of marketing/growth roles is a strong leading indicator of
budget release.

Endpoint (undocumented but stable): GET
  https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search
  ?keywords=...&location=...&start=N

Returns HTML cards. We extract `data-entity-urn`, company name, title,
posted timestamp from each card.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

import httpx
from selectolax.parser import HTMLParser

from ..logging import get_logger
from .base import Collector, SignalEvent

log = get_logger(__name__)


_UA = "Mozilla/5.0 (compatible; GrabonIntelBot/0.1; +https://grabon.in/bots)"


class LinkedInJobsCollector(Collector):
    """Pull LinkedIn job postings for a keyword + geo.

    Parameters:
        keywords: e.g. "performance marketing manager"
        location: e.g. "India", "Bengaluru"
        max_pages: max paginated requests (each ~25 jobs). Default 4 = ~100 jobs.
        page_size: ignored (LinkedIn fixes ~25/page); kept for symmetry.
    """

    name = "linkedin_jobs"

    @property
    def available(self) -> bool:
        return True  # public endpoint, no key needed

    async def produce(self) -> AsyncIterator[SignalEvent]:
        keywords = (self.params.get("keywords") or "").strip()
        location = (self.params.get("location") or "India").strip()
        max_pages = int(self.params.get("max_pages") or 4)
        if not keywords:
            log.warning("linkedin_jobs.no_keywords")
            return

        by_company: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "titles": [], "earliest_posted": None, "sample_url": None}
        )

        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": _UA}, follow_redirects=True) as client:
            for page in range(max_pages):
                start = page * 25
                resp = await client.get(
                    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
                    params={"keywords": keywords, "location": location, "start": start},
                )
                if resp.status_code == 429:
                    log.warning("linkedin_jobs.rate_limited_stop", page=page)
                    break
                if resp.status_code >= 400:
                    log.warning("linkedin_jobs.http_error", status=resp.status_code)
                    break
                html = resp.text
                cards = _parse_cards(html)
                if not cards:
                    break
                for c in cards:
                    company = c.get("company")
                    if not company:
                        continue
                    agg = by_company[company]
                    agg["count"] += 1
                    if c.get("title"):
                        agg["titles"].append(c["title"])
                    posted = c.get("posted_at")
                    if posted and (agg["earliest_posted"] is None or posted < agg["earliest_posted"]):
                        agg["earliest_posted"] = posted
                    if not agg["sample_url"] and c.get("url"):
                        agg["sample_url"] = c["url"]

        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()
        for company, agg in by_company.items():
            yield SignalEvent(
                type="hiring.linkedin",
                source=self.name,
                observed_at=now,
                brand_name=company,
                value_num=float(agg["count"]),
                value_text=keywords,
                payload={
                    "count": agg["count"],
                    "titles": agg["titles"][:20],
                    "earliest_posted": agg["earliest_posted"],
                    "sample_url": agg["sample_url"],
                    "keywords": keywords,
                    "location": location,
                },
                dedupe_key=f"li-jobs:{_slug(company)}:{_slug(keywords)}:{day}",
            )


_TIME_RE = re.compile(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", re.I)


def _parse_cards(html: str) -> list[dict[str, Any]]:
    tree = HTMLParser(html)
    out: list[dict[str, Any]] = []
    for card in tree.css("li, div.base-card"):
        title_node = card.css_first("h3, h3.base-search-card__title")
        company_node = card.css_first("h4 a, h4.base-search-card__subtitle a, a.hidden-nested-link")
        url_node = card.css_first("a.base-card__full-link, a.hidden-nested-link")
        time_node = card.css_first("time")
        title = title_node.text(strip=True) if title_node else None
        company = company_node.text(strip=True) if company_node else None
        url = url_node.attributes.get("href") if url_node and url_node.attributes else None
        posted = None
        if time_node:
            dt_attr = time_node.attributes.get("datetime")
            if dt_attr:
                posted = dt_attr
            else:
                m = _TIME_RE.search(time_node.text())
                if m:
                    posted = f"{m.group(1)} {m.group(2)} ago"
        if title or company:
            out.append({"title": title, "company": company, "url": url, "posted_at": posted})
    # Dedup within-page on (title, company).
    seen: set[tuple] = set()
    uniq: list[dict[str, Any]] = []
    for o in out:
        key = (o.get("title") or "", o.get("company") or "")
        if key in seen:
            continue
        seen.add(key)
        uniq.append(o)
    return uniq


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:80]
