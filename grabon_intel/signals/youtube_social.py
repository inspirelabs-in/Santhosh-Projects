"""YouTube social velocity collector.

Uses the YouTube Data API v3 (free, 10K units/day) to measure brand
channel growth velocity: subscriber count, view count, recent video
frequency. Emits `social.youtube` signals.

Requires YOUTUBE_API_KEY env var (free from Google Cloud Console).
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..config import get_settings
from ..logging import get_logger
from .base import Collector, SignalEvent

log = get_logger(__name__)

_YT_SEARCH = "https://www.googleapis.com/youtube/v3/search"
_YT_CHANNELS = "https://www.googleapis.com/youtube/v3/channels"
_YT_ACTIVITIES = "https://www.googleapis.com/youtube/v3/activities"


class YouTubeSocialCollector(Collector):
    """Measure YouTube channel velocity for brand discovery.

    Parameters:
        search_terms: brand/category search query. REQUIRED.
        max_channels: max channels to analyze (default 15).
    """

    name = "youtube_social"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        s = get_settings()
        api_key = getattr(s, "youtube_api_key", "") or ""
        if not api_key:
            log.warning("youtube_social.no_api_key — YouTube Data API key not set")
            return

        search_terms = (self.params.get("search_terms") or self.params.get("keywords") or "").strip()
        if not search_terms:
            log.warning("youtube_social.no_search_terms")
            return

        max_channels = int(self.params.get("max_channels") or 15)
        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()

        async with httpx.AsyncClient(timeout=15.0) as client:
            # Search for channels
            search_resp = await client.get(
                _YT_SEARCH,
                params={
                    "part": "snippet",
                    "q": search_terms,
                    "type": "channel",
                    "maxResults": min(max_channels, 50),
                    "key": api_key,
                },
            )
            if search_resp.status_code != 200:
                log.warning("youtube_social.search_failed", status=search_resp.status_code)
                return

            items = search_resp.json().get("items", [])
            channel_ids = [
                item["snippet"]["channelId"]
                for item in items
                if item.get("snippet", {}).get("channelId")
            ]
            if not channel_ids:
                return

            # Get channel statistics in batch
            stats_resp = await client.get(
                _YT_CHANNELS,
                params={
                    "part": "snippet,statistics,brandingSettings",
                    "id": ",".join(channel_ids[:50]),
                    "key": api_key,
                },
            )
            if stats_resp.status_code != 200:
                log.warning("youtube_social.stats_failed", status=stats_resp.status_code)
                return

            channels = stats_resp.json().get("items", [])

        for ch in channels:
            snippet = ch.get("snippet", {})
            stats = ch.get("statistics", {})
            branding = ch.get("brandingSettings", {}).get("channel", {})

            channel_name = snippet.get("title", "")
            if not channel_name:
                continue

            subs = int(stats.get("subscriberCount", 0))
            views = int(stats.get("viewCount", 0))
            video_count = int(stats.get("videoCount", 0))
            domain = _extract_domain_from_description(
                snippet.get("description", "") + " " + branding.get("unsubscribedTrailer", "")
            )

            yield SignalEvent(
                type="social.youtube",
                source=self.name,
                observed_at=now,
                brand_name=channel_name,
                brand_domain=domain,
                value_num=float(subs),
                value_text=f"{video_count} videos, {_human_number(views)} views",
                payload={
                    "channel_id": ch.get("id"),
                    "channel_name": channel_name,
                    "subscribers": subs,
                    "total_views": views,
                    "video_count": video_count,
                    "country": snippet.get("country"),
                    "custom_url": snippet.get("customUrl"),
                    "published_at": snippet.get("publishedAt"),
                    "domain": domain,
                    "search_terms": search_terms,
                },
                dedupe_key=f"yt:{ch.get('id', '')}:{day}",
            )


def _extract_domain_from_description(text: str) -> str | None:
    urls = re.findall(r"https?://(?:www\.)?([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", text)
    for url in urls:
        url = url.lower()
        if url not in {"youtube.com", "youtu.be", "instagram.com", "facebook.com", "twitter.com", "x.com", "linkedin.com"}:
            return url
    return None


def _human_number(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)
