"""Crunchbase funding collector — discovers funding rounds via SearXNG.

Searches site:crunchbase.com for brand/company pages and parses SERP
snippets for funding round details: amount, round type, investors, date.

No API key needed — uses self-hosted SearXNG to search Crunchbase.
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import AsyncIterator
from typing import Any

from ..logging import get_logger
from .base import Collector, SignalEvent

log = get_logger(__name__)

_ROUND_PATTERN = re.compile(
    r"(pre[- ]?seed|seed|angel|series\s*[a-f]|venture|growth|debt|ipo|"
    r"grant|convertible|bridge|round\s*[a-f])",
    re.I,
)
_AMOUNT_PATTERN = re.compile(
    r"[\$₹€£]\s*([\d,.]+)\s*(million|mn|m|billion|bn|b|crore|cr|lakh|lac|k|thousand)?|"
    r"([\d,.]+)\s*(million|mn|m|billion|bn|b|crore|cr|lakh|lac)\s*(?:usd|inr|dollars?)?",
    re.I,
)
_DATE_PATTERN = re.compile(
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}|"
    r"\d{4}-\d{2}-\d{2}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}|"
    r"(?:Q[1-4]\s+\d{4})|"
    r"\b20[12]\d\b",
    re.I,
)
_INVESTOR_PATTERN = re.compile(
    r"(?:led by|from|investors?(?:\s+include)?|backed by|participated)\s+([^.;]{5,120})",
    re.I,
)
_TOTAL_FUNDING_PATTERN = re.compile(
    r"total\s+funding\s+(?:of\s+)?[\$₹]?\s*([\d,.]+)\s*(million|mn|m|billion|bn|b|crore|cr)?|"
    r"raised\s+(?:a\s+total\s+(?:of\s+)?)?[\$₹]?\s*([\d,.]+)\s*(million|mn|m|billion|bn|b|crore|cr)\s+"
    r"(?:in\s+total|across|over|to\s+date)",
    re.I,
)


class CrunchbaseFundingCollector(Collector):
    """Discover funding data via Crunchbase SERP snippets.

    Parameters:
        domains: list[str] of domains to research. REQUIRED.
    """

    name = "crunchbase_funding"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        domains = self.params.get("domains") or []
        if not domains:
            log.warning("crunchbase_funding.no_domains")
            return

        from ..tools.searxng import SearXNGTool

        searxng = SearXNGTool()
        if not searxng.available:
            log.warning("crunchbase_funding.searxng_unavailable")
            return

        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()

        brand_names = self.params.get("brand_names") or []

        for idx, domain in enumerate(domains):
            domain = domain.strip().lower().removeprefix("www.")
            if not domain:
                continue

            brand_stem = domain.split(".")[0]
            clean_stem = _strip_common_prefixes(brand_stem)
            brand_name = brand_names[idx] if idx < len(brand_names) else None

            search_terms = list(dict.fromkeys(filter(None, [
                brand_name,
                clean_stem if clean_stem != brand_stem else None,
                brand_stem,
            ])))

            queries = []
            for term in search_terms[:2]:
                queries.append(f"site:crunchbase.com/organization {term}")
                queries.append(f"site:crunchbase.com {term} funding")
            queries.append(f'"{search_terms[0]}" crunchbase funding investors')

            snippets: list[str] = []
            cb_url: str | None = None

            for q in queries:
                result = await searxng.safe(query=q, num=5)
                if result.degraded:
                    continue
                for item in (result.data or {}).get("results", []):
                    url = item.get("link") or item.get("url") or ""
                    if "crunchbase.com" in url.lower():
                        if not cb_url:
                            cb_url = url
                        snippet = item.get("snippet") or item.get("content") or ""
                        title = item.get("title") or ""
                        if snippet:
                            snippets.append(f"{title}. {snippet}")

            if not snippets:
                continue

            combined = " ".join(snippets)
            rounds = _extract_rounds(combined)
            total_funding = _extract_total_funding(combined)

            if rounds:
                for i, r in enumerate(rounds):
                    yield SignalEvent(
                        type="funding.round",
                        source=self.name,
                        observed_at=now,
                        brand_domain=domain,
                        value_num=_parse_amount_num(r.get("amount")) if r.get("amount") else None,
                        value_text=_format_round_text(r),
                        payload={
                            "round": r.get("round"),
                            "amount": r.get("amount"),
                            "date": r.get("date"),
                            "investors": r.get("investors", []),
                            "crunchbase_url": cb_url,
                        },
                        dedupe_key=f"cb-fund:{brand_stem}:{r.get('round', i)}:{day}",
                    )

            if total_funding:
                yield SignalEvent(
                    type="funding.total",
                    source=self.name,
                    observed_at=now,
                    brand_domain=domain,
                    value_num=_parse_amount_num(total_funding),
                    value_text=f"Total funding: {total_funding}",
                    payload={
                        "total_funding": total_funding,
                        "crunchbase_url": cb_url,
                        "rounds_found": len(rounds),
                    },
                    dedupe_key=f"cb-total:{brand_stem}:{day}",
                )

            if not rounds and not total_funding and snippets:
                yield SignalEvent(
                    type="funding.profile",
                    source=self.name,
                    observed_at=now,
                    brand_domain=domain,
                    value_num=None,
                    value_text=combined[:300],
                    payload={
                        "crunchbase_url": cb_url,
                        "snippet_count": len(snippets),
                    },
                    dedupe_key=f"cb-profile:{brand_stem}:{day}",
                )


_COMMON_PREFIXES = ("the", "my", "get", "go", "try", "use", "hey", "its", "buy")


def _strip_common_prefixes(stem: str) -> str:
    lower = stem.lower()
    for prefix in _COMMON_PREFIXES:
        if lower.startswith(prefix) and len(lower) > len(prefix) + 2:
            return lower[len(prefix):]
    return lower


def _extract_rounds(text: str) -> list[dict[str, Any]]:
    rounds: list[dict[str, Any]] = []
    sentences = re.split(r'[.;]', text)
    for sent in sentences:
        round_match = _ROUND_PATTERN.search(sent)
        amount_match = _AMOUNT_PATTERN.search(sent)
        if not round_match and not amount_match:
            continue
        r: dict[str, Any] = {}
        if round_match:
            r["round"] = round_match.group(1).strip().title()
        if amount_match:
            r["amount"] = amount_match.group(0).strip()
        date_match = _DATE_PATTERN.search(sent)
        if date_match:
            r["date"] = date_match.group(0).strip()
        inv_match = _INVESTOR_PATTERN.search(sent)
        if inv_match:
            raw_investors = inv_match.group(1)
            r["investors"] = [
                inv.strip() for inv in re.split(r',\s*(?:and\s+)?|(?:\s+and\s+)', raw_investors)
                if inv.strip() and len(inv.strip()) > 1
            ][:8]
        if r:
            rounds.append(r)
    return rounds


def _extract_total_funding(text: str) -> str | None:
    m = _TOTAL_FUNDING_PATTERN.search(text)
    if m:
        return m.group(0).strip()
    return None


def _parse_amount_num(amount_str: str | None) -> float | None:
    if not amount_str:
        return None
    m = _AMOUNT_PATTERN.search(amount_str)
    if not m:
        return None
    num_str = m.group(1) or m.group(3)
    multiplier_str = (m.group(2) or m.group(4) or "").lower()
    if not num_str:
        return None
    try:
        num = float(num_str.replace(",", ""))
    except ValueError:
        return None
    multipliers = {
        "million": 1_000_000, "mn": 1_000_000, "m": 1_000_000,
        "billion": 1_000_000_000, "bn": 1_000_000_000, "b": 1_000_000_000,
        "crore": 10_000_000, "cr": 10_000_000,
        "lakh": 100_000, "lac": 100_000,
        "k": 1_000, "thousand": 1_000,
    }
    return num * multipliers.get(multiplier_str, 1)


def _format_round_text(r: dict) -> str:
    parts = []
    if r.get("round"):
        parts.append(r["round"])
    if r.get("amount"):
        parts.append(r["amount"])
    if r.get("date"):
        parts.append(f"({r['date']})")
    return " ".join(parts) if parts else "funding round detected"
