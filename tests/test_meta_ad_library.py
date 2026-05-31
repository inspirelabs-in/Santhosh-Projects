"""Smoke test for Meta Ad Library collector using a recorded fixture.

Verifies the parser aggregates rows per page_id and yields the expected
SignalEvent shape. Does not hit the real API.
"""
from __future__ import annotations

import datetime as dt

import httpx
import pytest

from grabon_intel.signals.meta_ad_library import MetaAdLibraryCollector, _domain_from_captions


_FIXTURE_PAGE_1 = {
    "data": [
        {
            "id": "ad1",
            "page_id": "111",
            "page_name": "Mamaearth",
            "ad_delivery_start_time": "2026-05-01T00:00:00+0000",
            "ad_creative_link_captions": ["mamaearth.in"],
            "publisher_platforms": ["facebook", "instagram"],
        },
        {
            "id": "ad2",
            "page_id": "111",
            "page_name": "Mamaearth",
            "ad_delivery_start_time": "2026-05-05T00:00:00+0000",
            "ad_creative_link_captions": ["mamaearth.in"],
            "publisher_platforms": ["instagram"],
        },
        {
            "id": "ad3",
            "page_id": "222",
            "page_name": "Boat",
            "ad_delivery_start_time": "2026-04-20T00:00:00+0000",
            "ad_creative_link_captions": ["boat-lifestyle.com"],
            "publisher_platforms": ["facebook"],
        },
    ],
    "paging": {"next": "https://graph.facebook.com/v21.0/ads_archive?after=cursor2"},
}

_FIXTURE_PAGE_2 = {
    "data": [
        {
            "id": "ad4",
            "page_id": "111",
            "page_name": "Mamaearth",
            "ad_delivery_start_time": "2026-04-15T00:00:00+0000",
            "ad_creative_link_captions": ["mamaearth.in"],
            "publisher_platforms": ["facebook"],
        }
    ],
    "paging": {},
}


def _mock_transport() -> httpx.MockTransport:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        body = _FIXTURE_PAGE_1 if calls["n"] == 1 else _FIXTURE_PAGE_2
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_collector_aggregates_per_page(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force a token so the collector doesn't short-circuit.
    monkeypatch.setenv("META_AD_LIBRARY_TOKEN", "test-token")
    from grabon_intel.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    # Patch httpx.AsyncClient to use the mock transport.
    real_init = httpx.AsyncClient.__init__

    def fake_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["transport"] = _mock_transport()
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", fake_init)

    coll = MetaAdLibraryCollector(search_terms="skincare", country="IN", limit=5, page_size=100)
    events = [ev async for ev in coll.produce()]

    by_page = {ev.payload["page_id"]: ev for ev in events}
    assert set(by_page) == {"111", "222"}

    mamaearth = by_page["111"]
    assert mamaearth.brand_name == "Mamaearth"
    assert mamaearth.brand_domain == "mamaearth.in"
    assert mamaearth.value_num == 3.0
    assert mamaearth.payload["earliest_start"] == "2026-04-15T00:00:00+0000"
    assert mamaearth.payload["latest_start"] == "2026-05-05T00:00:00+0000"
    assert set(mamaearth.payload["platforms"]) == {"facebook", "instagram"}
    assert mamaearth.type == "ad_spend.meta.active"
    assert isinstance(mamaearth.observed_at, dt.datetime)
    assert mamaearth.dedupe_key and mamaearth.dedupe_key.startswith("meta:111:IN:")


def test_domain_from_captions_picks_most_common() -> None:
    assert _domain_from_captions({"mamaearth.in", "Mamaearth.in", "shop.mamaearth.in"}) in {
        "mamaearth.in",
        "shop.mamaearth.in",
    }
    assert _domain_from_captions(set()) is None
    assert _domain_from_captions({"not a domain"}) is None


@pytest.mark.asyncio
async def test_no_token_yields_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("META_AD_LIBRARY_TOKEN", "")
    from grabon_intel.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    coll = MetaAdLibraryCollector(search_terms="skincare", country="IN")
    events = [ev async for ev in coll.produce()]
    assert events == []
