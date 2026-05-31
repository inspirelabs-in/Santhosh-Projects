"""urlscan.io search collector.

Searches urlscan.io's public index of previously scanned websites
to discover ecommerce brands and extract tech/payment stack data
without crawling. 1K searches/day free with API key.

Free tier — API key required (free signup at urlscan.io).
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

_SEARCH_URL = "https://urlscan.io/api/v1/search/"

_NOISE_DOMAINS = {
    "google.com", "youtube.com", "facebook.com", "twitter.com",
    "instagram.com", "linkedin.com", "reddit.com", "wikipedia.org",
    "amazon.com", "amazon.in", "flipkart.com", "myntra.com",
    "github.com", "stackoverflow.com", "medium.com",
}

_ECOMMERCE_TAGS = {"shopify", "woocommerce", "magento", "razorpay", "cashfree", "paytm"}


class URLScanSearchCollector(Collector):
    """Discover brands from urlscan.io's scanned site index.

    Parameters:
        search_query: urlscan search query (e.g. 'page.domain:*.in AND tags:shopify'). REQUIRED.
        api_key: urlscan.io API key (free signup). Optional for basic search.
        max_results: max results to process (default 50).
    """

    name = "urlscan_search"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        query = (self.params.get("search_query") or "").strip()
        if not query:
            log.warning("urlscan_search.no_query")
            return

        api_key = self.params.get("api_key") or ""
        max_results = self.params.get("max_results", 50)

        try:
            results = await _search_urlscan(query, api_key, max_results)
        except Exception as exc:
            log.warning("urlscan_search.fetch_failed", error=str(exc))
            return

        if not results:
            return

        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()
        seen_domains: set[str] = set()

        for result in results:
            page = result.get("page", {})
            domain = (page.get("domain") or "").lower().removeprefix("www.")

            if not domain or domain in seen_domains or domain in _NOISE_DOMAINS:
                continue

            seen_domains.add(domain)
            brand_name = _domain_to_brand(domain)
            tags = result.get("tags", [])
            has_ecommerce = bool(set(t.lower() for t in tags) & _ECOMMERCE_TAGS)

            yield SignalEvent(
                type="scan.site_found",
                source=self.name,
                observed_at=now,
                brand_domain=domain,
                brand_name=brand_name,
                value_text=f"urlscan: {domain} tags={tags[:5]}",
                payload={
                    "domain": domain,
                    "ip": page.get("ip", ""),
                    "server": page.get("server", ""),
                    "title": page.get("title", "")[:120],
                    "tags": tags[:10],
                    "has_ecommerce_tag": has_ecommerce,
                    "country": page.get("country", ""),
                    "scan_id": result.get("_id", ""),
                    "scan_date": result.get("indexedAt", ""),
                },
                dedupe_key=f"uscan:{domain}:{day}",
            )


async def _search_urlscan(
    query: str, api_key: str, max_results: int
) -> list[dict[str, Any]]:
    headers: dict[str, str] = {}
    if api_key:
        headers["API-Key"] = api_key

    params = {
        "q": query,
        "size": str(min(max_results, 100)),
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(_SEARCH_URL, params=params, headers=headers)
        if resp.status_code == 429:
            log.warning("urlscan_search.rate_limited")
            return []
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data.get("results", [])


def _domain_to_brand(domain: str) -> str:
    name = domain.split(".")[0]
    name = re.sub(r"[-_]", " ", name)
    return name.strip().title()
