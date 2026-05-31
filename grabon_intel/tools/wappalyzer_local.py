"""python-Wappalyzer — local tech-stack detection.

OSS replacement for paid BuiltWith. Fetches the target's HTML + headers
via the existing `WebsiteFetchTool`, then runs python-Wappalyzer over
the response. Returns category-grouped technology list (same shape as
the old BuiltWith result so callers don't change).
"""
from __future__ import annotations

from typing import Any

import httpx

from ..logging import get_logger
from .base import Tool, ToolError, ToolResult

log = get_logger(__name__)

_UA = (
    "Mozilla/5.0 (compatible; GrabonIntelBot/0.2; +https://grabon.in/bots) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class WappalyzerLocalTool(Tool):
    name = "wappalyzer_local"

    def __init__(self) -> None:
        super().__init__(rate=4, period=1.0)

    @property
    def available(self) -> bool:
        # python-Wappalyzer is bundled; available is True unless import fails.
        try:
            import Wappalyzer  # noqa: F401

            return True
        except Exception:
            return False

    async def call(self, *, domain: str) -> ToolResult:
        try:
            from Wappalyzer import Wappalyzer, WebPage  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"python-Wappalyzer unavailable: {exc}") from exc

        url = domain.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
        ) as c:
            r = await c.get(url)
            if r.status_code >= 400:
                raise ToolError(f"http {r.status_code} on {url}")

        # WebPage.new_from_response is sync; small payloads, fine to call inline.
        page = WebPage(
            url=str(r.url),
            html=r.text,
            headers={k.lower(): v for k, v in r.headers.items()},
        )
        wap = Wappalyzer.latest()
        techs = wap.analyze_with_categories(page) or {}
        # Pivot to {category: [names]} for parity with the legacy BuiltWith shape.
        groups: dict[str, list[str]] = {}
        for tech_name, info in techs.items():
            cats = info.get("categories") or ["uncategorized"]
            for cat in cats:
                groups.setdefault(cat, []).append(tech_name)
        return ToolResult(
            name=self.name,
            cost_cents=0,
            data={
                "domain": domain,
                "url": str(r.url),
                "status": r.status_code,
                "categories": groups,
            },
        )
