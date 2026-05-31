"""PageSpeed Insights signal collector.

Uses Google's free PageSpeed Insights API (25K req/day, no key
required for basic usage). Site performance = brand investment proxy.

Serious D2C brands invest in fast sites. Low scores = opportunity
for GrabOn to offer CRO partnership.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..logging import get_logger
from .base import Collector, SignalEvent

log = get_logger(__name__)

_API_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


class PageSpeedCollector(Collector):
    """Check PageSpeed Insights for a list of domains.

    Parameters:
        domains: list[str] of domains to check. REQUIRED.
        strategy: "mobile" or "desktop" (default "mobile").
        api_key: optional Google API key for higher quota.
    """

    name = "pagespeed"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        domains = self.params.get("domains") or []
        if not domains:
            log.warning("pagespeed.no_domains")
            return

        strategy = (self.params.get("strategy") or "mobile").lower()
        api_key = self.params.get("api_key") or ""

        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()

        async with httpx.AsyncClient(timeout=90.0) as client:
            for domain in domains:
                domain = domain.strip().lower().removeprefix("www.")
                if not domain:
                    continue

                try:
                    result = await _fetch_pagespeed(client, domain, strategy, api_key)
                    if not result:
                        continue

                    perf_score = result["performance_score"]
                    level = _classify_performance(perf_score)

                    yield SignalEvent(
                        type="site.pagespeed",
                        source=self.name,
                        observed_at=now,
                        brand_domain=domain,
                        value_num=float(perf_score),
                        value_text=f"{level} ({perf_score}/100)",
                        payload={
                            "domain": domain,
                            "strategy": strategy,
                            "performance_score": perf_score,
                            "fcp_ms": result.get("fcp_ms"),
                            "lcp_ms": result.get("lcp_ms"),
                            "cls": result.get("cls"),
                            "tbt_ms": result.get("tbt_ms"),
                            "si_ms": result.get("si_ms"),
                            "level": level,
                        },
                        dedupe_key=f"psi:{domain}:{strategy}:{day}",
                    )
                except Exception as exc:
                    log.debug("pagespeed.check_failed", domain=domain, error=f"{type(exc).__name__}: {exc}")


async def _fetch_pagespeed(
    client: httpx.AsyncClient,
    domain: str,
    strategy: str,
    api_key: str,
) -> dict[str, Any] | None:
    params: dict[str, str] = {
        "url": f"https://{domain}",
        "strategy": strategy,
        "category": "performance",
    }
    if api_key:
        params["key"] = api_key

    resp = await client.get(_API_URL, params=params)
    if resp.status_code != 200:
        return None

    data = resp.json()
    lh = data.get("lighthouseResult", {})
    categories = lh.get("categories", {})
    audits = lh.get("audits", {})

    perf = categories.get("performance", {})
    score = int((perf.get("score") or 0) * 100)

    return {
        "performance_score": score,
        "fcp_ms": _audit_num(audits, "first-contentful-paint"),
        "lcp_ms": _audit_num(audits, "largest-contentful-paint"),
        "cls": _audit_num(audits, "cumulative-layout-shift"),
        "tbt_ms": _audit_num(audits, "total-blocking-time"),
        "si_ms": _audit_num(audits, "speed-index"),
    }


def _audit_num(audits: dict, key: str) -> float | None:
    audit = audits.get(key, {})
    return audit.get("numericValue")


def _classify_performance(score: int) -> str:
    if score >= 90:
        return "fast"
    if score >= 50:
        return "moderate"
    return "slow"
