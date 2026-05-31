"""PageSpeed Insights (Lighthouse) — free, no key.

Endpoint: https://www.googleapis.com/pagespeedonline/v5/runPagespeed
Optional `PAGESPEED_API_KEY` (env) raises quota. Without a key the
service still works at ~25k requests/day shared anonymous quota.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from .base import Tool, ToolError, ToolResult


class LighthouseTool(Tool):
    name = "lighthouse"

    def __init__(self) -> None:
        super().__init__(rate=2, period=1.0)

    @property
    def available(self) -> bool:
        return True  # public endpoint

    async def call(self, *, url: str, strategy: str = "mobile") -> ToolResult:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        params: dict[str, Any] = {
            "url": url,
            "strategy": strategy,
            "category": ["performance", "accessibility", "best-practices", "seo"],
        }
        api_key = os.getenv("PAGESPEED_API_KEY")
        if api_key:
            params["key"] = api_key
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get("https://www.googleapis.com/pagespeedonline/v5/runPagespeed", params=params)
            if r.status_code >= 400:
                raise ToolError(f"PSI {r.status_code}: {r.text[:200]}")
            data = r.json()
        lhr = data.get("lighthouseResult") or {}
        cats = lhr.get("categories") or {}
        audits = lhr.get("audits") or {}

        def _score(key: str) -> float | None:
            c = cats.get(key)
            return float(c["score"] * 100) if c and isinstance(c.get("score"), (int, float)) else None

        def _audit_num(key: str) -> float | None:
            a = audits.get(key)
            return float(a["numericValue"]) if a and isinstance(a.get("numericValue"), (int, float)) else None

        compact = {
            "url": url,
            "strategy": strategy,
            "perf": _score("performance"),
            "accessibility": _score("accessibility"),
            "best_practices": _score("best-practices"),
            "seo": _score("seo"),
            "lcp_ms": _audit_num("largest-contentful-paint"),
            "cls": _audit_num("cumulative-layout-shift"),
            "tbt_ms": _audit_num("total-blocking-time"),
            "fcp_ms": _audit_num("first-contentful-paint"),
            "tti_ms": _audit_num("interactive"),
        }
        return ToolResult(name=self.name, cost_cents=0, data=compact)
