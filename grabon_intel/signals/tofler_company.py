"""Tofler.in company data collector — Indian MCA filings via SearXNG.

Searches Tofler.in for company profiles and parses SERP snippets for:
- Revenue / turnover from annual filings
- Employee count
- Directors / founders
- Incorporation date
- Authorized capital / paid-up capital

Uses LLM (CHEAP tier) to extract structured data from noisy SERP snippets
instead of fragile regex parsing.

Free — uses SearXNG to search public MCA filing data on Tofler.in.
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

_CIN_PATTERN = re.compile(r"\b[UL]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b")

_COMMON_PREFIXES = ("the", "my", "get", "go", "try", "use", "hey", "its", "buy")


def _strip_common_prefixes(stem: str) -> str:
    lower = stem.lower()
    for prefix in _COMMON_PREFIXES:
        if lower.startswith(prefix) and len(lower) > len(prefix) + 2:
            return lower[len(prefix):]
    return lower


class ToflerCompanyCollector(Collector):
    """Discover Indian company financial data via Tofler.in SERP snippets.

    Parameters:
        domains: list[str] of domains to research. REQUIRED.
        brand_names: list[str] optional brand names for better search.
    """

    name = "tofler_company"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        domains = self.params.get("domains") or []
        if not domains:
            log.warning("tofler_company.no_domains")
            return

        from ..tools.searxng import SearXNGTool

        searxng = SearXNGTool()
        if not searxng.available:
            log.warning("tofler_company.searxng_unavailable")
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
                queries.append(f"site:tofler.in {term}")
            queries.append(f'"{search_terms[0]}" tofler.in company revenue')
            queries.append(f'"{search_terms[0]}" zaubacorp company')

            snippets: list[str] = []
            tofler_url: str | None = None

            for q in queries:
                result = await searxng.safe(query=q, num=5)
                if result.degraded:
                    continue
                for item in (result.data or {}).get("results", []):
                    url = item.get("link") or item.get("url") or ""
                    if "tofler.in" in url.lower() or "zaubacorp" in url.lower():
                        if not tofler_url:
                            tofler_url = url
                        snippet = item.get("snippet") or item.get("content") or ""
                        title = item.get("title") or ""
                        if snippet:
                            snippets.append(f"{title}. {snippet}")

            if not snippets:
                continue

            combined = " ".join(snippets)

            cin_match = _CIN_PATTERN.search(combined)
            cin = cin_match.group(0) if cin_match else None

            extracted = await _llm_extract_company(
                combined, brand_name or clean_stem, domain
            )

            rev = extracted.get("revenue")
            if rev:
                yield SignalEvent(
                    type="company.revenue_filing",
                    source=self.name,
                    observed_at=now,
                    brand_domain=domain,
                    value_num=rev.get("amount_inr"),
                    value_text=rev.get("amount_text", ""),
                    payload={
                        "cin": cin or extracted.get("cin"),
                        "tofler_url": tofler_url,
                        "source_type": "mca_filing",
                        "fiscal_year": rev.get("fiscal_year"),
                    },
                    dedupe_key=f"tofler-rev:{brand_stem}:{day}",
                )

            cap = extracted.get("capital")
            if cap:
                yield SignalEvent(
                    type="company.capital",
                    source=self.name,
                    observed_at=now,
                    brand_domain=domain,
                    value_num=cap.get("paid_up_inr"),
                    value_text=cap.get("text", ""),
                    payload={
                        "cin": cin or extracted.get("cin"),
                        "tofler_url": tofler_url,
                        "authorized_inr": cap.get("authorized_inr"),
                    },
                    dedupe_key=f"tofler-cap:{brand_stem}:{day}",
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
                    payload={
                        "cin": cin or extracted.get("cin"),
                        "tofler_url": tofler_url,
                    },
                    dedupe_key=f"tofler-emp:{brand_stem}:{day}",
                )

            inc = extracted.get("incorporation_date")
            if inc:
                yield SignalEvent(
                    type="company.incorporated",
                    source=self.name,
                    observed_at=now,
                    brand_domain=domain,
                    value_num=None,
                    value_text=inc,
                    payload={
                        "cin": cin or extracted.get("cin"),
                        "tofler_url": tofler_url,
                    },
                    dedupe_key=f"tofler-inc:{brand_stem}:{day}",
                )

            directors = extracted.get("directors") or []
            if directors:
                yield SignalEvent(
                    type="company.directors",
                    source=self.name,
                    observed_at=now,
                    brand_domain=domain,
                    value_num=float(len(directors)),
                    value_text=", ".join(directors[:3]),
                    payload={
                        "directors": directors,
                        "cin": cin or extracted.get("cin"),
                        "tofler_url": tofler_url,
                    },
                    dedupe_key=f"tofler-dir:{brand_stem}:{day}",
                )

            if not rev and not emp and not directors and (cin or extracted.get("cin")):
                yield SignalEvent(
                    type="company.mca_profile",
                    source=self.name,
                    observed_at=now,
                    brand_domain=domain,
                    value_num=None,
                    value_text=f"CIN: {cin or extracted.get('cin', 'unknown')}",
                    payload={
                        "cin": cin or extracted.get("cin"),
                        "tofler_url": tofler_url,
                        "company_name": extracted.get("registered_name"),
                        "snippet_count": len(snippets),
                    },
                    dedupe_key=f"tofler-profile:{brand_stem}:{day}",
                )


async def _llm_extract_company(
    snippets_text: str, brand_name: str, domain: str
) -> dict[str, Any]:
    """Use CHEAP LLM to extract structured company data from Tofler/Zaubacorp SERP snippets."""
    from ..llm import Tier, complete

    prompt = (
        f"Extract Indian company (MCA/ROC) data for '{brand_name}' ({domain}) "
        f"from these Tofler.in / Zaubacorp SERP snippets. Snippets are noisy and may "
        f"contain data about OTHER companies — only extract data clearly about '{brand_name}'.\n\n"
        f"Snippets:\n{snippets_text[:3000]}\n\n"
        "Return JSON with:\n"
        "- registered_name: official company name or null\n"
        "- cin: Company Identification Number (21-char alphanumeric starting with U or L) or null\n"
        "- revenue: {amount_text (exact text e.g. 'Rs 5.2 Crore'), amount_inr (numeric in INR), "
        "fiscal_year (e.g. '2023-24')} or null\n"
        "- capital: {text (e.g. 'Authorized: 10 Lakh, Paid-up: 5 Lakh'), "
        "authorized_inr (numeric), paid_up_inr (numeric)} or null\n"
        "- employee_count: integer or null\n"
        "- incorporation_date: string (DD/MM/YYYY or YYYY) or null\n"
        "- directors: [list of director/founder names] or []\n\n"
        "RULES:\n"
        "- Only include data clearly about THIS company, not other companies\n"
        "- Convert amounts to raw INR (1 Crore = 10000000, 1 Lakh = 100000)\n"
        "- If amount is ambiguous or from different company, set to null\n"
        "- If no data found, return nulls/empty arrays"
    )
    try:
        res = await complete(
            tier=Tier.CHEAP, prompt=prompt, json_mode=True, max_output_tokens=500
        )
        parsed = _json.loads(res.content)
        if isinstance(parsed, dict):
            return parsed
    except Exception as exc:
        log.debug("tofler_company.llm_extract_failed", error=str(exc))
    return {}
