"""News polling collector — classification + brand extraction."""
from __future__ import annotations

import datetime as dt

import pytest

from grabon_intel.signals.news_polling import NewsPollingCollector, _extract_brand
from grabon_intel.tools.base import ToolResult


def test_extract_brand_funding_title() -> None:
    assert _extract_brand("Mamaearth raises $50M in Series E") == "Mamaearth"


def test_extract_brand_dash_separator() -> None:
    assert _extract_brand("Nykaa: Q4 revenue grows 24%") == "Nykaa"


def test_extract_brand_none() -> None:
    assert _extract_brand("this is a generic headline") is None


@pytest.mark.asyncio
async def test_collector_classifies_kinds(monkeypatch) -> None:
    fake_items = {
        "items": [
            {
                "title": "Mamaearth raises $50M Series F",
                "link": "https://x/1",
                "source": "Inc42",
                "summary": "...",
                "published": "2026-05-10T08:00:00+0000",
            },
            {
                "title": "boAt expands to Southeast Asia",
                "link": "https://x/2",
                "source": "Moneycontrol",
                "summary": "...",
                "published": "2026-05-11T08:00:00+0000",
            },
            {
                "title": "Random unrelated news",
                "link": "https://x/3",
                "source": "Other",
                "summary": "...",
                "published": None,
            },
        ]
    }

    async def fake_safe(self, **_):
        return ToolResult(name="google_news", data=fake_items, degraded=False)

    from grabon_intel.tools.news import GoogleNewsTool

    monkeypatch.setattr(GoogleNewsTool, "safe", fake_safe)

    coll = NewsPollingCollector(queries=["beauty d2c"], country="IN", max_items_per_query=10)
    events = [ev async for ev in coll.produce()]
    kinds = sorted([e.type for e in events])
    assert "news.funding" in kinds
    assert "news.growth" in kinds
    # Always >= one generic (the random one).
    assert any(k == "news.generic" for k in kinds)
    # observed_at should parse the funding title's date.
    funding = next(e for e in events if e.type == "news.funding")
    assert isinstance(funding.observed_at, dt.datetime)
