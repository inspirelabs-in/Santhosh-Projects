"""Website fetch + extract — title, meta, og tags, headings, links.

No browser — plain httpx + selectolax. Skips JS-rendered sites; we have
Playwright in the scraper/ pkg for those if needed later.
"""
from __future__ import annotations

import httpx
from selectolax.parser import HTMLParser

from .base import Tool, ToolError, ToolResult

_UA = (
    "Mozilla/5.0 (compatible; GrabonIntelBot/0.1; +https://grabon.in/bots) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class WebsiteFetchTool(Tool):
    name = "website_fetch"

    def __init__(self) -> None:
        super().__init__(rate=4, period=1.0)

    async def call(self, *, url: str, max_bytes: int = 1_500_000) -> ToolResult:
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
            html = r.text[:max_bytes]

        tree = HTMLParser(html)

        def _meta(name: str) -> str | None:
            for sel in (f'meta[name="{name}"]', f'meta[property="{name}"]'):
                node = tree.css_first(sel)
                if node:
                    v = node.attributes.get("content")
                    if v:
                        return v.strip()
            return None

        title = (tree.css_first("title").text() if tree.css_first("title") else "").strip()
        h1s = [n.text(strip=True) for n in tree.css("h1")][:5]
        h2s = [n.text(strip=True) for n in tree.css("h2")][:10]
        links = []
        for a in tree.css("a[href]")[:50]:
            href = a.attributes.get("href") or ""
            text = a.text(strip=True)[:80]
            if href:
                links.append({"href": href, "text": text})

        data = {
            "url": str(r.url),
            "status": r.status_code,
            "title": title,
            "description": _meta("description") or _meta("og:description"),
            "og_title": _meta("og:title"),
            "og_image": _meta("og:image"),
            "h1": h1s,
            "h2": h2s,
            "links": links,
            "bytes": len(html),
        }
        return ToolResult(name=self.name, cost_cents=0, data=data)
