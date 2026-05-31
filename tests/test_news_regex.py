"""Extended news regex covers brand-event triggers."""
from __future__ import annotations

import pytest

from grabon_intel.signals.news_polling import NewsPollingCollector
from grabon_intel.tools.base import ToolResult


@pytest.mark.asyncio
async def test_layoff_legal_exec_kinds(monkeypatch) -> None:
    items = {
        "items": [
            {"title": "Brand X lays off 200 employees", "link": "x", "published": None, "summary": ""},
            {"title": "Brand Y appoints new CMO", "link": "y", "published": None, "summary": ""},
            {"title": "Brand Z faces SEC investigation", "link": "z", "published": None, "summary": ""},
            {"title": "Mamaearth raises $50M Series F", "link": "m", "published": None, "summary": ""},
        ]
    }

    async def fake_safe(self, **_):
        return ToolResult(name="google_news", data=items, degraded=False)

    from grabon_intel.tools.news import GoogleNewsTool

    monkeypatch.setattr(GoogleNewsTool, "safe", fake_safe)

    coll = NewsPollingCollector(queries=["test"], country="IN")
    types = sorted([e.type async for e in coll.produce()])
    assert "news.layoff" in types
    assert "news.exec_change" in types
    assert "news.legal" in types
    assert "news.funding" in types
