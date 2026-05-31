"""Tracxn funding collector — discovers funding and company data via SearXNG.

Searches site:tracxn.com for company profiles and parses SERP snippets
for funding rounds, revenue estimates, and employee counts.

Uses LLM (CHEAP tier) to extract structured data from noisy SERP snippets
instead of fragile regex parsing.

No API key needed — uses self-hosted SearXNG to search Tracxn.
"""
from __future__ import annotations

import datetime as dt
import json as _json
import re
from collections.abc import AsyncIterator
from typing import Any

from ..logging import get_logger
from .base import Collector, SignalEvent

log = get_logger(__name__)

_EMPLOYEE_PATTERN = re.compile(
    r"(\d[\d,]*)\s*(?:\+\s*)?employees?|employees?\s*:?\s*(\d[\d,]*)",
    re.I,
)


class TracxnFundingCollector(Collector):
    """Discover funding and company data via Tracxn SERP snippets.

    Parameters:
        domains: list[str] of domains to research. REQUIRED.
    """

    name = "tracxn_funding"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        domains = self.params.get("domains") or []
        if not domains:
            log.warning("tracxn_funding.no_domains")
            return

        from ..tools.searxng import SearXNGTool

        searxng = SearXNGTool()
        if not searxng.available:
            log.warning("tracxn_funding.searxng_unavailable")
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
                queries.append(f"site:tracxn.com {term}")
                queries.append(f"site:tracxn.com/d {term} funding")
            queries.append(f'"{search_terms[0]}" tracxn funding revenue')

            snippets: list[str] = []
            tracxn_url: str | None = None

            for q in queries:
                result = await searxng.safe(query=q, num=5)
                if result.degraded:
                    continue
                for item in (result.data or {}).get("results", []):
                    url = item.get("link") or item.get("url") or ""
                    if "tracxn.com" in url.lower():
                        if not tracxn_url:
                            tracxn_url = url
                        snippet = item.get("snippet") or item.get("content") or ""
                        title = item.get("title") or ""
                        if snippet:
                            snippets.append(f"{title}. {snippet}")

            if not snippets:
                continue

            combined = " ".join(snippets)

            # Use LLM to extract structured funding data from noisy snippets
            extracted = await _llm_extract_funding(
                combined, brand_name or clean_stem, domain
            )

            rounds = extracted.get("funding_rounds", [])
            for i, r in enumerate(rounds):
                round_name = r.get("round_type", "unknown")
                yield SignalEvent(
                    type="funding.round",
                    source=self.name,
                    observed_at=now,
                    brand_domain=domain,
                    value_num=r.get("amount_usd"),
                    value_text=_format_round_text(r),
                    payload={
                        "round": round_name,
                        "amount": r.get("amount"),
                        "amount_usd": r.get("amount_usd"),
                        "date": r.get("date"),
                        "investors": r.get("investors", []),
                        "tracxn_url": tracxn_url,
                    },
                    dedupe_key=f"tx-fund:{brand_stem}:{round_name}:{i}:{day}",
                )

            total = extracted.get("total_funding")
            if total:
                yield SignalEvent(
                    type="funding.total",
                    source=self.name,
                    observed_at=now,
                    brand_domain=domain,
                    value_num=total.get("amount_usd"),
                    value_text=total.get("amount_text", ""),
                    payload={"tracxn_url": tracxn_url},
                    dedupe_key=f"tx-total:{brand_stem}:{day}",
                )

            rev = extracted.get("revenue_estimate")
            if rev:
                yield SignalEvent(
                    type="revenue.estimate",
                    source=self.name,
                    observed_at=now,
                    brand_domain=domain,
                    value_num=rev.get("amount_usd"),
                    value_text=rev.get("amount_text", ""),
                    payload={"tracxn_url": tracxn_url},
                    dedupe_key=f"tx-rev:{brand_stem}:{day}",
                )

            emp = extracted.get("employee_count")
            if emp:
                yield SignalEvent(
                    type="company.employees",
                    source=self.name,
                    observed_at=now,
                    brand_domain=domain,
                    value_num=float(emp),
                    value_text=f"{emp} employees",
                    payload={"tracxn_url": tracxn_url},
                    dedupe_key=f"tx-emp:{brand_stem}:{day}",
                )

            # Fallback: regex employee extraction if LLM missed it
            if not emp:
                emp_match = _EMPLOYEE_PATTERN.search(combined)
                if emp_match:
                    emp_str = emp_match.group(1) or emp_match.group(2)
                    try:
                        emp_count = int(emp_str.replace(",", ""))
                    except ValueError:
                        emp_count = None
                    if emp_count:
                        yield SignalEvent(
                            type="company.employees",
                            source=self.name,
                            observed_at=now,
                            brand_domain=domain,
                            value_num=float(emp_count),
                            value_text=f"{emp_count} employees",
                            payload={"tracxn_url": tracxn_url},
                            dedupe_key=f"tx-emp:{brand_stem}:{day}",
                        )

            if not rounds and not rev and not emp and snippets:
                yield SignalEvent(
                    type="funding.profile",
                    source=self.name,
                    observed_at=now,
                    brand_domain=domain,
                    value_num=None,
                    value_text=combined[:300],
                    payload={
                        "tracxn_url": tracxn_url,
                        "snippet_count": len(snippets),
                    },
                    dedupe_key=f"tx-profile:{brand_stem}:{day}",
                )


_COMMON_PREFIXES = ("the", "my", "get", "go", "try", "use", "hey", "its", "buy")


def _strip_common_prefixes(stem: str) -> str:
    lower = stem.lower()
    for prefix in _COMMON_PREFIXES:
        if lower.startswith(prefix) and len(lower) > len(prefix) + 2:
            return lower[len(prefix):]
    return lower


async def _llm_extract_funding(
    snippets_text: str, brand_name: str, domain: str
) -> dict[str, Any]:
    """Use CHEAP LLM to extract structured funding data from noisy SERP snippets."""
    from ..llm import Tier, complete

    prompt = (
        f"Extract funding information for '{brand_name}' ({domain}) from these "
        f"Tracxn SERP snippets. The snippets are noisy and may contain data about "
        f"OTHER companies — only extract data that clearly belongs to '{brand_name}'.\n\n"
        f"Snippets:\n{snippets_text[:3000]}\n\n"
        "Return JSON with:\n"
        "- funding_rounds: array of distinct rounds. Each: "
        "{round_type (Seed/Angel/Series A/B/C/Pre-Series A/Venture/etc), "
        "amount (exact text e.g. '$2.6M'), amount_usd (numeric in USD, null if unknown), "
        "date (YYYY or YYYY-MM or null), investors: [names]}\n"
        "- total_funding: {amount_text, amount_usd} or null\n"
        "- revenue_estimate: {amount_text, amount_usd} or null\n"
        "- employee_count: integer or null\n\n"
        "RULES:\n"
        "- Only include rounds clearly about THIS company, not other companies in snippets\n"
        "- Deduplicate: same round mentioned twice = one entry\n"
        "- If amount is ambiguous or from a different company, set to null\n"
        "- If no funding info found for this company, return empty arrays/nulls"
    )
    try:
        res = await complete(
            tier=Tier.CHEAP, prompt=prompt, json_mode=True, max_output_tokens=600
        )
        parsed = _json.loads(res.content)
        if isinstance(parsed, dict):
            return parsed
    except Exception as exc:
        log.debug("tracxn_funding.llm_extract_failed", error=str(exc))
    return {}


def _format_round_text(r: dict) -> str:
    parts = []
    if r.get("round_type"):
        parts.append(r["round_type"])
    if r.get("amount"):
        parts.append(r["amount"])
    if r.get("date"):
        parts.append(f"({r['date']})")
    return " ".join(parts) if parts else "funding round detected"
