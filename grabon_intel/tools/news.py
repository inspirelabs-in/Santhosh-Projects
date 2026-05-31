"""Google News RSS — free, no key. Use for funding/hiring/news signals."""
from __future__ import annotations

import datetime as dt
from urllib.parse import quote_plus

import feedparser
import httpx

from .base import Tool, ToolResult


class GoogleNewsTool(Tool):
    name = "google_news"

    def __init__(self) -> None:
        super().__init__(rate=10, period=1.0)

    @property
    def available(self) -> bool:
        return True

    async def call(self, *, query: str, max_items: int = 10, hl: str = "en-IN", gl: str = "IN") -> ToolResult:
        url = (
            f"https://news.google.com/rss/search?q={quote_plus(query)}"
            f"&hl={hl}&gl={gl}&ceid={gl}:en"
        )
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            xml = r.text
        feed = feedparser.parse(xml)
        items = []
        for e in feed.entries[:max_items]:
            published = e.get("published") or ""
            iso = _parse_dt(published)
            items.append(
                {
                    "title": e.get("title"),
                    "link": e.get("link"),
                    "source": (e.get("source") or {}).get("title") if isinstance(e.get("source"), dict) else None,
                    "published": iso,
                    "summary": (e.get("summary") or "")[:400],
                }
            )
        return ToolResult(name=self.name, cost_cents=0, data={"query": query, "items": items})


def _parse_dt(s: str) -> str | None:
    if not s:
        return None
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            return dt.datetime.strptime(s, fmt).isoformat()
        except ValueError:
            continue
    return s
