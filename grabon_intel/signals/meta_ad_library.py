"""Meta Ad Library collector.

Pulls active ads matching a search term from the public Ads Archive API.
Each unique advertiser becomes an `ad_spend.meta.active` signal carrying
ad count + earliest start date. High signal-to-noise for D2C brands:
running active Meta ads = real spend + intent.

Docs: https://www.facebook.com/ads/library/api/

Endpoint: GET https://graph.facebook.com/v21.0/ads_archive
Required params: search_terms, ad_reached_countries, access_token
Useful fields: id, page_id, page_name, ad_delivery_start_time,
ad_delivery_stop_time, ad_snapshot_url, ad_creative_link_captions,
publisher_platforms, impressions, spend.

Note: requires a Meta developer access token w/ ads_archive permission.
For now the collector degrades gracefully when the token is missing — it
logs a warning and yields nothing, so the runner can still smoke-test
the rest of the pipeline.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from ..config import get_settings
from ..logging import get_logger
from .base import Collector, SignalEvent

log = get_logger(__name__)


class MetaAdLibraryCollector(Collector):
    """Collect Meta active-ad signals for a search term + country.

    Parameters:
        search_terms: required, freeform query (e.g. "skincare", "Mamaearth")
        country: ISO-2 country code (default "IN")
        ad_type: "ALL" | "POLITICAL_AND_ISSUE_ADS" (default "ALL")
        active_status: "ACTIVE" | "INACTIVE" | "ALL" (default "ACTIVE")
        limit: max pages of advertisers to emit (default 50)
        page_size: per-page from API (default 100, max 5000)
        publisher_platforms: list[str] filter (default all)
    """

    name = "meta_ad_library"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        s = get_settings()
        token = s.meta_ad_library_token.get_secret_value()
        if not token:
            log.warning("meta_ad_library.no_token — yielding nothing")
            return

        search_terms = self.params.get("search_terms") or ""
        if not search_terms:
            log.warning("meta_ad_library.no_search_terms")
            return

        country = (self.params.get("country") or "IN").upper()
        ad_type = self.params.get("ad_type") or "ALL"
        active_status = self.params.get("active_status") or "ACTIVE"
        page_size = min(int(self.params.get("page_size") or 100), 5000)
        max_pages = int(self.params.get("limit") or 50)

        fields = ",".join(
            [
                "id",
                "page_id",
                "page_name",
                "ad_delivery_start_time",
                "ad_delivery_stop_time",
                "ad_snapshot_url",
                "ad_creative_link_captions",
                "publisher_platforms",
                "languages",
            ]
        )

        params: dict[str, Any] = {
            "search_terms": search_terms,
            "ad_reached_countries": f"['{country}']",
            "ad_type": ad_type,
            "ad_active_status": active_status,
            "fields": fields,
            "limit": page_size,
            "access_token": token,
        }

        url = s.meta_ad_library_base
        page_count = 0
        # Aggregate per advertiser across pages — one signal per page_id.
        by_page: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "page_name": None,
                "ad_count": 0,
                "earliest_start": None,
                "latest_start": None,
                "platforms": set(),
                "creative_captions": set(),
                "sample_snapshot": None,
            }
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            next_url: str | None = url
            next_params: dict[str, Any] | None = params

            while next_url and page_count < max_pages:
                resp = await _request_with_retries(client, next_url, next_params)
                if resp is None:
                    break
                data = resp.json()
                rows = data.get("data") or []
                for row in rows:
                    pid = str(row.get("page_id") or "")
                    if not pid:
                        continue
                    agg = by_page[pid]
                    agg["page_name"] = agg["page_name"] or row.get("page_name")
                    agg["ad_count"] += 1
                    start = row.get("ad_delivery_start_time")
                    if start:
                        if agg["earliest_start"] is None or start < agg["earliest_start"]:
                            agg["earliest_start"] = start
                        if agg["latest_start"] is None or start > agg["latest_start"]:
                            agg["latest_start"] = start
                    for plat in row.get("publisher_platforms") or []:
                        agg["platforms"].add(plat)
                    for cap in row.get("ad_creative_link_captions") or []:
                        if cap:
                            agg["creative_captions"].add(cap)
                    if agg["sample_snapshot"] is None:
                        agg["sample_snapshot"] = row.get("ad_snapshot_url")

                page_count += 1
                next_url = (data.get("paging") or {}).get("next")
                next_params = None  # next URL already carries cursor + token

        now = dt.datetime.now(dt.timezone.utc)
        for pid, agg in by_page.items():
            domain = _domain_from_captions(agg["creative_captions"])
            yield SignalEvent(
                type="ad_spend.meta.active",
                source=self.name,
                observed_at=now,
                brand_name=agg["page_name"],
                brand_domain=domain,
                value_num=float(agg["ad_count"]),
                value_text=country,
                payload={
                    "page_id": pid,
                    "country": country,
                    "ad_count": agg["ad_count"],
                    "earliest_start": agg["earliest_start"],
                    "latest_start": agg["latest_start"],
                    "platforms": sorted(agg["platforms"]),
                    "creative_captions": sorted(agg["creative_captions"])[:20],
                    "sample_snapshot": agg["sample_snapshot"],
                    "search_terms": search_terms,
                },
                # Bucket by (page_id, country, day) so re-runs within a day are deduped.
                dedupe_key=f"meta:{pid}:{country}:{now.date().isoformat()}",
            )


def _domain_from_captions(captions: set[str]) -> str | None:
    """Pick the most common-looking root domain out of ad link captions."""
    from collections import Counter

    candidates: list[str] = []
    for c in captions:
        c = c.strip().lower()
        if not c or " " in c:
            continue
        c = c.removeprefix("http://").removeprefix("https://").removeprefix("www.")
        c = c.split("/", 1)[0]
        if "." in c and not c.endswith("."):
            candidates.append(c)
    if not candidates:
        return None
    return Counter(candidates).most_common(1)[0][0]


async def _request_with_retries(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any] | None,
) -> httpx.Response | None:
    """GET with exponential backoff. Treats 4xx (except 429) as terminal."""
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(4),
        wait=wait_exponential_jitter(initial=1, max=30),
        retry=retry_if_exception_type((httpx.HTTPError,)),
        reraise=True,
    ):
        with attempt:
            resp = await client.get(url, params=params)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
            if resp.status_code >= 400:
                log.error("meta_ad_library.http_error", status=resp.status_code, body=resp.text[:400])
                return None
            return resp
    return None
