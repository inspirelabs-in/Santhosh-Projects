"""Hacker News discovery via Algolia API.

Searches HN posts and comments for D2C brand mentions, "Show HN"
launches, and startup discussions. Brands that appear on HN are
typically in early growth phase — high-value leads.

Free — no API key, no signup. Public Algolia-powered API.
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

import httpx

from ..logging import get_logger
from .base import Collector, SignalEvent

log = get_logger(__name__)

_HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"

_NOISE_DOMAINS = {
    "github.com", "youtube.com", "twitter.com", "x.com",
    "reddit.com", "medium.com", "substack.com", "notion.so",
    "docs.google.com", "drive.google.com", "figma.com",
    "linkedin.com", "facebook.com", "instagram.com",
    "techcrunch.com", "producthunt.com", "ycombinator.com",
    "news.ycombinator.com", "wikipedia.org",
    "amazon.com", "amazon.in", "flipkart.com",
}


class HNDiscoveryCollector(Collector):
    """Discover brands mentioned on Hacker News.

    Parameters:
        search_terms: search query (e.g. "D2C India brand"). REQUIRED.
        max_results: max HN items to process (default 30).
        tags: HN post type filter — "story", "show_hn", "comment" (default "story").
    """

    name = "hn_discovery"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        search = (self.params.get("search_terms") or "").strip()
        if not search:
            log.warning("hn_discovery.no_search_terms")
            return

        max_results = self.params.get("max_results", 30)
        tags = self.params.get("tags", "story")

        try:
            hits = await _search_hn(search, tags, max_results)
        except Exception as exc:
            log.warning("hn_discovery.fetch_failed", error=str(exc))
            return

        if not hits:
            return

        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()
        seen_domains: set[str] = set()

        for hit in hits:
            url = hit.get("url") or ""
            title = hit.get("title") or ""
            points = hit.get("points") or 0

            domain = _extract_domain(url)
            if not domain or domain in seen_domains or domain in _NOISE_DOMAINS:
                continue

            seen_domains.add(domain)
            brand_name = _domain_to_brand(domain)
            is_show_hn = title.lower().startswith("show hn")

            yield SignalEvent(
                type="social.hn_mention",
                source=self.name,
                observed_at=now,
                brand_domain=domain,
                brand_name=brand_name,
                value_num=float(points),
                value_text=f"HN {'launch' if is_show_hn else 'mention'} ({points} pts)",
                payload={
                    "domain": domain,
                    "title": title[:200],
                    "points": points,
                    "url": url,
                    "hn_id": hit.get("objectID"),
                    "is_show_hn": is_show_hn,
                    "num_comments": hit.get("num_comments", 0),
                    "created_at": hit.get("created_at"),
                },
                dedupe_key=f"hn:{domain}:{day}",
            )


async def _search_hn(
    query: str, tags: str, max_results: int
) -> list[dict[str, Any]]:
    params = {
        "query": query,
        "tags": tags,
        "hitsPerPage": str(min(max_results, 50)),
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(_HN_SEARCH_URL, params=params)
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data.get("hits", [])


def _extract_domain(url: str) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url)
        domain = (parsed.hostname or "").lower().removeprefix("www.")
        if domain and "." in domain:
            return domain
    except Exception:
        pass
    return None


def _domain_to_brand(domain: str) -> str:
    name = domain.split(".")[0]
    name = re.sub(r"[-_]", " ", name)
    return name.strip().title()
