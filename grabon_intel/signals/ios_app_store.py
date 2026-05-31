"""iOS App Store signal collector.

Uses `app-store-scraper` (free, no API key) to discover brands
with iOS apps. Complements the existing Google Play collector.

pip install app-store-scraper
"""
from __future__ import annotations

import asyncio
import datetime as dt
import re
from collections.abc import AsyncIterator
from typing import Any

from ..logging import get_logger
from .base import Collector, SignalEvent

log = get_logger(__name__)


class IOSAppStoreCollector(Collector):
    """Discover brands via iOS App Store listings.

    Parameters:
        search_terms: app search query (e.g. "shopping india"). REQUIRED.
        max_results: max apps to scan (default 30).
        country: 2-letter code (default "in").
    """

    name = "ios_app_store"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        search_terms = (self.params.get("search_terms") or "").strip()
        if not search_terms:
            log.warning("ios_app_store.no_search_terms")
            return

        max_results = int(self.params.get("max_results") or 30)
        country = (self.params.get("country") or "in").lower()

        try:
            results = await asyncio.to_thread(
                _search_ios, search_terms, country, max_results
            )
        except Exception as exc:
            log.warning("ios_app_store.search_failed", error=str(exc))
            return

        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()

        for app in results:
            app_name = app.get("name", "")
            developer = app.get("developer", "")
            app_id = app.get("app_id", "")
            if not app_name:
                continue

            rating = app.get("rating", 0) or 0
            reviews = app.get("reviews", 0) or 0
            url = app.get("url", "")
            domain = _infer_domain(app.get("developer_url") or "")

            brand_name = developer or app_name.split("-")[0].strip().split("–")[0].strip()

            yield SignalEvent(
                type="app.ios",
                source=self.name,
                observed_at=now,
                brand_name=brand_name,
                brand_domain=domain,
                value_num=float(reviews),
                value_text=f"★{rating:.1f} · {reviews} reviews",
                payload={
                    "app_id": app_id,
                    "app_name": app_name,
                    "developer": developer,
                    "rating": rating,
                    "reviews": reviews,
                    "url": url,
                    "developer_url": app.get("developer_url"),
                    "price": app.get("price", "Free"),
                    "genre": app.get("genre"),
                    "search_terms": search_terms,
                    "country": country,
                },
                dedupe_key=f"ios:{app_id}:{day}",
            )


def _search_ios(search_terms: str, country: str, max_results: int) -> list[dict]:
    """Search iOS App Store. Runs in thread pool."""
    import json
    import urllib.request

    term = urllib.parse.quote(search_terms)
    url = (
        f"https://itunes.apple.com/search?term={term}"
        f"&country={country}&media=software&limit={min(max_results, 50)}"
    )

    req = urllib.request.Request(url, headers={"User-Agent": "GrabonIntelBot/0.2"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())

    results = []
    for item in data.get("results", []):
        results.append({
            "app_id": str(item.get("trackId", "")),
            "name": item.get("trackName", ""),
            "developer": item.get("artistName", ""),
            "rating": item.get("averageUserRating", 0),
            "reviews": item.get("userRatingCount", 0),
            "url": item.get("trackViewUrl", ""),
            "developer_url": item.get("sellerUrl", ""),
            "price": "Free" if item.get("price", 0) == 0 else str(item.get("price")),
            "genre": item.get("primaryGenreName", ""),
        })

    return results


def _infer_domain(url: str) -> str | None:
    if not url:
        return None
    url = url.strip().lower()
    url = url.removeprefix("http://").removeprefix("https://").removeprefix("www.")
    url = url.split("/", 1)[0]
    if "." in url and not url.endswith(".") and " " not in url:
        return url
    return None
