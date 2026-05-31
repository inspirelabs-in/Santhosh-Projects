"""Google News polling collector.

Polls Google News RSS across a list of ICP-aligned queries on a cron and
emits `news.funding` / `news.growth` / `news.generic` signals. Uses the
GoogleNewsTool from `..tools.news`.

Brand resolution: best-effort domain extraction from each item title
(brand mentions in title); resolver promotes a name-only hint into a
new brand row when no domain present.
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import AsyncIterator

from ..logging import get_logger
from ..tools.news import GoogleNewsTool
from .base import Collector, SignalEvent

log = get_logger(__name__)


_FUNDING_RE = re.compile(
    r"(raises|raised|funding|series\s+[a-h]|seed\s+round|valuation|valued at|ipo|files? for ipo)", re.I
)
_GROWTH_RE = re.compile(r"(expand|launches|enters|hires|appoints|crosses|milestone|growth)", re.I)
_LAYOFF_RE = re.compile(r"(layoff|lays off|cuts? jobs|reduces? headcount|workforce reduction)", re.I)
_EXEC_RE = re.compile(
    r"(appoints?|steps down|resigns?|exits?|new ceo|new cmo|new cfo|new coo|"
    r"chief marketing officer|chief revenue officer)",
    re.I,
)
_LEGAL_RE = re.compile(r"(lawsuit|sues|investigation|fined|penalt|regulatory action)", re.I)


class NewsPollingCollector(Collector):
    """Poll Google News across a list of queries.

    Parameters:
        queries: list[str] of seed queries (e.g. ICP-aligned). REQUIRED.
        country: 2-letter geo, default "IN".
        max_items_per_query: default 10.
    """

    name = "news_polling"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        queries = self.params.get("queries") or []
        country = self.params.get("country") or "IN"
        per_q = int(self.params.get("max_items_per_query") or 10)
        if not queries:
            log.warning("news_polling.no_queries")
            return

        tool = GoogleNewsTool()
        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()

        for q in queries:
            r = await tool.safe(query=q, max_items=per_q, hl=f"en-{country}", gl=country)
            if r.degraded:
                continue
            for item in (r.data or {}).get("items", []):
                title = (item.get("title") or "").strip()
                if not title:
                    continue
                brand_name = _extract_brand(title)
                kind = "news.generic"
                if _LAYOFF_RE.search(title):
                    kind = "news.layoff"
                elif _LEGAL_RE.search(title):
                    kind = "news.legal"
                elif _EXEC_RE.search(title):
                    kind = "news.exec_change"
                elif _FUNDING_RE.search(title):
                    kind = "news.funding"
                elif _GROWTH_RE.search(title):
                    kind = "news.growth"
                yield SignalEvent(
                    type=kind,
                    source=self.name,
                    observed_at=_parse_or_now(item.get("published"), now),
                    brand_name=brand_name,
                    value_text=title[:200],
                    payload={
                        "title": title,
                        "link": item.get("link"),
                        "source": item.get("source"),
                        "summary": item.get("summary"),
                        "query": q,
                    },
                    dedupe_key=f"news:{_slug(q)}:{_slug(title)[:80]}:{day}",
                )


_REJECT_BRANDS = re.compile(
    r"^(top\s+\d+|best\s+\w+|how\s+to|why\s+|what\s+|the\s+|india|indian|"
    r"e-?commerce|retail|budget|cracking|stitching|fortune|news|"
    r"ecommerce business|d2c|new\s+|case study|newsletter|inside\s+(the|india|indian)|"
    r"layoffs|funding\s+wrap|breaking\s+views|people[\s,]+jobs|"
    r"linkedin\s+|isbr\s+|amazon$|mamaearth\s+case)",
    re.I,
)


def _extract_brand(title: str) -> str | None:
    # Heuristic: take the substring before the first verb-ish keyword.
    m = re.match(
        r"^(?P<brand>[A-Z][\w&.\- ]{1,40}?)\s+(raises|raised|launches|expands|hires|appoints|crosses|secures|partners|to\s+enter|enters)",
        title,
    )
    if m:
        candidate = m.group("brand").strip()
        if not _REJECT_BRANDS.match(candidate) and len(candidate.split()) <= 4:
            return candidate
    # Fall back to first capitalised run before a colon/dash.
    m = re.match(r"^(?P<brand>[A-Z][\w&.\- ]{1,40}?)\s*[:\-–]", title)
    if m:
        candidate = m.group("brand").strip()
        if not _REJECT_BRANDS.match(candidate) and len(candidate.split()) <= 3:
            return candidate
    return None


def _parse_or_now(value: str | None, fallback: dt.datetime) -> dt.datetime:
    if not value:
        return fallback
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            return dt.datetime.strptime(value, fmt)
        except ValueError:
            continue
    return fallback


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:80]
