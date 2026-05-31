"""SEO rank tracking collector.

Uses SearXNG (self-hosted) to check SERP positions for target keywords.
Emits `seo.rank` signals per brand+keyword combo. Free — no API key.

Designed for cron: run weekly per ICP-keyword set, track position changes.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

from ..config import get_settings
from ..logging import get_logger
from ..tools.searxng import SearXNGTool
from .base import Collector, SignalEvent

log = get_logger(__name__)


class SEORankTrackingCollector(Collector):
    """Track SERP positions for keywords via SearXNG.

    Parameters:
        keywords: list[str] of target keywords to track. REQUIRED.
        max_results: results per keyword (default 20, determines rank depth).
        language: search language (default "en").
    """

    name = "seo_rank_tracking"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        keywords = self.params.get("keywords") or self.params.get("queries") or []
        if not keywords:
            log.warning("seo_rank_tracking.no_keywords")
            return

        max_results = int(self.params.get("max_results") or 20)
        language = self.params.get("language") or "en"

        searxng = SearXNGTool()
        if not searxng.available:
            log.warning("seo_rank_tracking.searxng_unavailable")
            return

        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()

        # Aggregate: domain → {keyword → rank}
        by_domain: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"brand_name": None, "rankings": {}, "total_appearances": 0}
        )

        for kw in keywords:
            result = await searxng.safe(query=kw, num=max_results)
            if result.degraded:
                continue

            results = (result.data or {}).get("results", [])
            for rank, item in enumerate(results, 1):
                url = item.get("link") or item.get("url") or ""
                domain = _extract_root_domain(url)
                if not domain or _is_non_brand(domain):
                    continue

                agg = by_domain[domain]
                agg["total_appearances"] += 1
                if not agg["brand_name"]:
                    title = item.get("title", "")
                    agg["brand_name"] = _infer_brand_from_title(title, domain)

                if kw not in agg["rankings"] or rank < agg["rankings"][kw]["rank"]:
                    agg["rankings"][kw] = {
                        "rank": rank,
                        "title": (item.get("title") or "")[:120],
                        "url": url[:300],
                    }

        for domain, agg in by_domain.items():
            if agg["total_appearances"] < 1:
                continue

            avg_rank = sum(r["rank"] for r in agg["rankings"].values()) / len(agg["rankings"])

            yield SignalEvent(
                type="seo.rank",
                source=self.name,
                observed_at=now,
                brand_name=agg["brand_name"],
                brand_domain=domain,
                value_num=round(avg_rank, 1),
                value_text=f"{len(agg['rankings'])} keywords tracked",
                payload={
                    "domain": domain,
                    "avg_rank": round(avg_rank, 1),
                    "keywords_found": len(agg["rankings"]),
                    "keywords_tracked": len(keywords),
                    "rankings": {
                        kw: r["rank"] for kw, r in agg["rankings"].items()
                    },
                    "total_appearances": agg["total_appearances"],
                },
                dedupe_key=f"seo:{_slug(domain)}:{day}",
            )


_SKIP_DOMAINS = {
    "amazon.in", "amazon.com", "flipkart.com", "myntra.com", "nykaa.com",
    "ajio.com", "snapdeal.com", "meesho.com", "jiomart.com",
    "indiamart.com", "justdial.com", "quora.com", "reddit.com",
    "youtube.com", "instagram.com", "facebook.com", "twitter.com",
    "linkedin.com", "wikipedia.org", "medium.com", "inc42.com",
    "yourstory.com", "entrackr.com", "moneycontrol.com", "livemint.com",
    "economictimes.com", "businessinsider.com", "forbes.com",
    "answers.microsoft.com", "forum.lowyat.net", "techcrunch.com",
    "grabon.in", "coupondunia.in", "cashkaro.com", "magicpin.in",
    "ndtv.com", "indianexpress.com", "aajtak.in", "news18.com",
    "thehindu.com", "hindustantimes.com", "zeenews.india.com",
    "britannica.com", "salesforce.com", "hubspot.com", "zoho.com",
    "freshworks.com", "pinterest.com", "x.com", "tiktok.com",
    "mycouponsnexus.com", "couponzguru.com", "blogspot.com",
    "wordpress.com", "d2cstory.com", "discoveringbrands.com",
    "d2csale.com", "towardsbusiness.com", "businessoutreach.in",
}

_SKIP_DOMAIN_RE = re.compile(r"\.(gov\.\w+|gov|mil|edu|ac\.in|nic\.in|bank\.in|org\.in)$")


def _is_non_brand(domain: str) -> bool:
    if domain in _SKIP_DOMAINS:
        return True
    if _SKIP_DOMAIN_RE.search(domain):
        return True
    for skip in _SKIP_DOMAINS:
        if domain.endswith(f".{skip}"):
            return True
    return False


def _extract_root_domain(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        host = host.removeprefix("www.")
        if "." in host and len(host) > 3:
            return host
    except Exception:
        pass
    return None


def _infer_brand_from_title(title: str, domain: str) -> str | None:
    parts = re.split(r"\s*[|\-–—:]\s*", title)
    if parts:
        candidate = parts[-1].strip()
        if len(candidate) > 2 and len(candidate) < 50:
            return candidate
    name = domain.split(".")[0]
    return name.capitalize() if name else None


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:80]
