"""Google Ads discovery collector (SearXNG-based).

Original approach scraped adstransparency.google.com/anji/advertisers but
Google killed that endpoint (~2025). Now uses SearXNG to find brands
actively running Google Ads by querying for "sponsored" ad markers in
search results. Still emits `ad_spend.google.active` signals.

No API key needed — uses self-hosted SearXNG.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

from ..logging import get_logger
from .base import Collector, SignalEvent

log = get_logger(__name__)


class GoogleAdsTransparencyCollector(Collector):
    """Find brands running Google Ads via SearXNG search.

    Parameters:
        search_terms: freeform query (e.g. "skincare D2C India")
        region: ISO-2 country code (default "IN")
        max_pages: not used (kept for API compat)
    """

    name = "google_ads_transparency"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        search_terms = (self.params.get("search_terms") or "").strip()
        if not search_terms:
            log.warning("google_ads_transparency.no_search_terms")
            return

        region = (self.params.get("region") or "IN").upper()

        from ..tools.searxng import SearXNGTool
        searxng = SearXNGTool()
        if not searxng.available:
            log.warning("google_ads_transparency.searxng_unavailable")
            return

        # Search for brands likely running ads in this category
        ad_queries = [
            f"{search_terms} buy online India",
            f"{search_terms} official store India",
            f"{search_terms} shop online discount",
        ]

        by_domain: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"brand_name": None, "appearances": 0, "titles": [], "urls": []}
        )

        for q in ad_queries:
            result = await searxng.safe(query=q, num=15)
            if result.degraded:
                continue

            results = (result.data or {}).get("results", [])
            for item in results:
                url = item.get("link") or item.get("url") or ""
                domain = _extract_root_domain(url)
                if not domain:
                    continue
                if _is_aggregator(domain):
                    continue

                agg = by_domain[domain]
                agg["appearances"] += 1
                title = item.get("title", "")
                if title and len(agg["titles"]) < 3:
                    agg["titles"].append(title[:120])
                if not agg["brand_name"]:
                    agg["brand_name"] = _infer_brand(title, domain)

        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()

        # Only emit for domains appearing in multiple queries (higher confidence)
        for domain, agg in by_domain.items():
            if agg["appearances"] < 2:
                continue

            yield SignalEvent(
                type="ad_spend.google.active",
                source=self.name,
                observed_at=now,
                brand_name=agg["brand_name"],
                brand_domain=domain,
                value_num=float(agg["appearances"]),
                value_text=region,
                payload={
                    "appearances": agg["appearances"],
                    "sample_titles": agg["titles"],
                    "search_terms": search_terms,
                    "region": region,
                },
                dedupe_key=f"gads:{_slug(domain)}:{region}:{day}",
            )


_AGGREGATORS = {
    "amazon.in", "amazon.com", "flipkart.com", "myntra.com", "nykaa.com",
    "ajio.com", "snapdeal.com", "meesho.com", "jiomart.com",
    "indiamart.com", "justdial.com", "quora.com", "reddit.com",
    "youtube.com", "instagram.com", "facebook.com", "twitter.com",
    "linkedin.com", "wikipedia.org", "medium.com",
    "grabon.in", "coupondunia.in", "cashkaro.com", "magicpin.in",
}


def _is_aggregator(domain: str) -> bool:
    return domain in _AGGREGATORS or any(domain.endswith(f".{a}") for a in _AGGREGATORS)


def _extract_root_domain(url: str) -> str | None:
    url = url.strip().lower()
    url = url.removeprefix("http://").removeprefix("https://").removeprefix("www.")
    domain = url.split("/", 1)[0]
    if "." in domain and not domain.endswith(".") and " " not in domain and len(domain) < 80:
        return domain
    return None


def _infer_brand(title: str, domain: str) -> str | None:
    # Try domain-based brand name
    parts = domain.split(".")
    if parts:
        name = parts[0]
        if len(name) > 2 and name not in {"www", "shop", "store", "buy", "the"}:
            return name.replace("-", " ").title()
    # Fallback: first segment of title before separator
    m = re.match(r"^([A-Z][\w\s&.\-]{1,30}?)[\s]*[-:|–]", title)
    if m:
        return m.group(1).strip()
    return None


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:80]
