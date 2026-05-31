"""Google Trends signal collector.

Uses `pytrends` (free, no API key) to detect brands with rising
search interest via related queries for shopping categories.

pip install pytrends
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

_NOISE_WORDS = re.compile(
    r"\b(statistics|demandsage|www\.|\.com|\.in|online shopping|"
    r"how to|what is|vs |best |top \d+)\b",
    re.I,
)

_PURE_NOISE = re.compile(
    r"^(online shopping|shopping|india|app|website|"
    r"amazon|flipkart|myntra|ajio|meesho|coupon|discount|offer|"
    r"review|price|buy|sale|deal|free|download)$",
    re.I,
)


class GoogleTrendsCollector(Collector):
    """Discover rising brands via Google Trends related queries.

    Parameters:
        search_terms: category keyword (e.g. "online shopping"). REQUIRED.
        geo: 2-letter country code (default "IN").
        timeframe: trends timeframe (default "today 3-m").
    """

    name = "google_trends"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        search_terms = (self.params.get("search_terms") or "").strip()
        if not search_terms:
            log.warning("google_trends.no_search_terms")
            return

        geo = (self.params.get("geo") or "IN").upper()
        timeframe = self.params.get("timeframe") or "today 3-m"

        try:
            from pytrends.request import TrendReq
        except ImportError:
            log.warning("google_trends.pytrends_not_installed")
            return

        try:
            related = await asyncio.to_thread(
                _fetch_rising_queries, search_terms, geo, timeframe
            )
        except Exception as exc:
            log.warning("google_trends.fetch_failed", error=str(exc))
            return

        if not related:
            return

        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()

        for item in related:
            query_text = item.get("query", "").strip()
            rise_value = item.get("value", 0)
            if not query_text or len(query_text) < 3:
                continue

            brand_name = _extract_brand(query_text)
            if not brand_name:
                continue

            yield SignalEvent(
                type="trends.rising",
                source=self.name,
                observed_at=now,
                brand_name=brand_name,
                value_num=float(rise_value),
                value_text=f"rising {rise_value}%",
                payload={
                    "query": query_text,
                    "rise_value": rise_value,
                    "geo": geo,
                    "timeframe": timeframe,
                    "search_category": search_terms,
                },
                dedupe_key=f"trends:{query_text.lower()}:{geo}:{day}",
            )


def _fetch_rising_queries(
    keyword: str, geo: str, timeframe: str
) -> list[dict[str, Any]]:
    from pytrends.request import TrendReq

    pytrends = TrendReq(hl="en-US", tz=330)
    pytrends.build_payload([keyword], timeframe=timeframe, geo=geo)
    related = pytrends.related_queries()

    results = []
    if keyword in related:
        for table in ("rising", "top"):
            df = related[keyword].get(table)
            if df is None:
                continue
            for _, row in df.head(15).iterrows():
                results.append({"query": str(row["query"]), "value": int(row["value"])})

    return results


def _extract_brand(query: str) -> str | None:
    """Extract brand name from a trending query."""
    if not query or len(query) > 80:
        return None

    cleaned = _NOISE_WORDS.sub("", query).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if len(cleaned) < 2 or len(cleaned) > 50:
        return None

    if _PURE_NOISE.match(cleaned):
        return None

    return cleaned
