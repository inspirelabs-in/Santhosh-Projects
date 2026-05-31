"""Common Crawl CDX discovery collector.

Queries Common Crawl's CDX index to bulk-discover ecommerce stores
by URL pattern (e.g. *.myshopify.com). Finds brands that have been
crawled by Common Crawl across 300B+ pages.

Free — no API key, no signup. Public dataset.
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import AsyncIterator

import httpx

from ..logging import get_logger
from .base import Collector, SignalEvent

log = get_logger(__name__)

_DEFAULT_PATTERNS = [
    "*.myshopify.com",
    "*.shopifypreview.com",
]

_NOISE = re.compile(
    r"(cdn\.|assets\.|checkout\.|admin\.|apps\.|"
    r"accounts\.|community\.|help\.|support\.|status\.)",
    re.I,
)

_JUNK_SHOPIFY = re.compile(
    r"^([0-9a-f]{4,}(-[a-z0-9]{1,3})?|[a-z0-9]{1,3}-[0-9a-f]+)\.myshopify\.com$",
    re.I,
)


class CommonCrawlCollector(Collector):
    """Discover ecommerce stores from Common Crawl index.

    Parameters:
        url_patterns: list of URL patterns to search (default: Shopify patterns).
        index: CC index name (default: latest). e.g. "CC-MAIN-2026-17".
        max_results: max domains to extract per pattern (default 50).
    """

    name = "common_crawl"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        patterns = self.params.get("url_patterns") or _DEFAULT_PATTERNS
        index = self.params.get("index") or ""
        max_results = self.params.get("max_results", 50)

        if not index:
            index = await _get_latest_index()
            if not index:
                log.warning("common_crawl.no_index_found")
                return

        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()

        for pattern in patterns:
            try:
                domains = await _search_cdx(index, pattern, max_results)
            except Exception as exc:
                log.warning("common_crawl.search_failed", pattern=pattern, error=str(exc))
                continue

            for domain in domains:
                if domain in ("myshopify.com", "shopifypreview.com"):
                    continue
                brand_name = _domain_to_brand(domain, pattern)
                if not brand_name or len(brand_name) < 3:
                    continue

                yield SignalEvent(
                    type="crawl.store_found",
                    source=self.name,
                    observed_at=now,
                    brand_domain=domain if "." in domain else None,
                    brand_name=brand_name,
                    value_text=f"Found in Common Crawl ({pattern})",
                    payload={
                        "domain": domain,
                        "source_pattern": pattern,
                        "index": index,
                    },
                    dedupe_key=f"cc:{domain}:{day}",
                )


async def _get_latest_index() -> str | None:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://index.commoncrawl.org/collinfo.json"
            )
            if resp.status_code != 200:
                return None
            indexes = resp.json()
            if indexes:
                return indexes[0].get("id", "")
    except Exception:
        return None
    return None


async def _search_cdx(
    index: str, url_pattern: str, max_results: int
) -> list[str]:
    url = f"https://index.commoncrawl.org/{index}-index"
    params = {
        "url": url_pattern,
        "output": "json",
        "fl": "url",
        "limit": str(max_results * 3),
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url, params=params)
        if resp.status_code != 200:
            return []

        seen: set[str] = set()
        domains: list[str] = []

        for line in resp.text.strip().split("\n"):
            if not line.strip():
                continue
            try:
                import json
                record = json.loads(line)
                raw_url = record.get("url", "")
            except (ValueError, KeyError):
                continue

            domain = _extract_domain(raw_url)
            if domain and domain not in seen and not _NOISE.search(domain) and not _JUNK_SHOPIFY.match(domain):
                seen.add(domain)
                domains.append(domain)
                if len(domains) >= max_results:
                    break

        return domains


def _extract_domain(url: str) -> str | None:
    url = url.lower().strip()
    url = re.sub(r"^https?://", "", url)
    domain = url.split("/")[0].split("?")[0].split("#")[0]
    domain = domain.removeprefix("www.")
    if not domain or "." not in domain:
        return None
    return domain


def _domain_to_brand(domain: str, pattern: str) -> str:
    if "myshopify.com" in domain:
        name = domain.replace(".myshopify.com", "")
    elif "shopifypreview.com" in domain:
        name = domain.replace(".shopifypreview.com", "")
    else:
        name = domain.split(".")[0]

    name = re.sub(r"[-_]", " ", name)
    return name.strip().title() if name else ""
