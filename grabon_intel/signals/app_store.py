"""Google Play Store signal collector.

Uses the `google-play-scraper` package (free, no API key) to discover
brands with mobile apps. Emits `app.google_play` signals with ratings,
installs, and recent update frequency — useful for ASO gap detection.

pip install google-play-scraper
"""
from __future__ import annotations

import asyncio
import datetime as dt
import re
from collections.abc import AsyncIterator
from typing import Any

from ..logging import get_logger
from .base import Collector, SignalEvent

log = get_logger(__name__)


class AppStoreCollector(Collector):
    """Discover brands via Google Play app listings.

    Parameters:
        search_terms: app search query (e.g. "shopping india", "beauty"). REQUIRED.
        max_results: max apps to scan (default 30).
        country: 2-letter code (default "in").
        language: language code (default "en").
    """

    name = "app_store"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        search_terms = (self.params.get("search_terms") or "").strip()
        if not search_terms:
            log.warning("app_store.no_search_terms")
            return

        max_results = int(self.params.get("max_results") or 30)
        country = (self.params.get("country") or "in").lower()
        language = self.params.get("language") or "en"

        try:
            from google_play_scraper import search as gp_search
        except ImportError:
            log.warning("app_store.google_play_scraper_not_installed")
            return

        try:
            results = await asyncio.to_thread(
                gp_search,
                search_terms,
                n_hits=max_results,
                lang=language,
                country=country,
            )
        except Exception as exc:
            log.warning("app_store.search_failed", error=str(exc))
            return

        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()

        for app in results or []:
            if not app or not isinstance(app, dict):
                continue
            app_id = app.get("appId", "")
            title = app.get("title", "")
            developer = app.get("developer", "")
            if not title:
                continue

            score = app.get("score") or 0
            installs_text = app.get("installs", "0")
            installs = _parse_installs(installs_text)
            domain = _infer_domain(app.get("developerWebsite") or "")

            brand_name = developer or title.split("-")[0].strip().split("–")[0].strip()

            yield SignalEvent(
                type="app.google_play",
                source=self.name,
                observed_at=now,
                brand_name=brand_name,
                brand_domain=domain,
                value_num=float(installs),
                value_text=f"★{score:.1f} · {installs_text}",
                payload={
                    "app_id": app_id,
                    "title": title,
                    "developer": developer,
                    "score": score,
                    "installs": installs,
                    "installs_text": installs_text,
                    "genre": app.get("genre"),
                    "price": app.get("price", 0),
                    "free": app.get("free", True),
                    "developer_website": app.get("developerWebsite"),
                    "icon": app.get("icon"),
                    "updated": app.get("updated"),
                    "search_terms": search_terms,
                    "country": country,
                },
                dedupe_key=f"gplay:{app_id}:{day}",
            )


def _parse_installs(text: str) -> int:
    if not text:
        return 0
    nums = re.sub(r"[^0-9]", "", str(text))
    return int(nums) if nums else 0


def _infer_domain(url: str) -> str | None:
    if not url:
        return None
    url = url.strip().lower()
    url = url.removeprefix("http://").removeprefix("https://").removeprefix("www.")
    url = url.split("/", 1)[0]
    if "." in url and not url.endswith(".") and " " not in url:
        return url
    return None
