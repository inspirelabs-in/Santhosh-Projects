"""Social media presence collector — Instagram, Facebook, Twitter/X detection.

Complements youtube_social by checking the other major social platforms.
Detects brand presence, follower counts (where public), and influencer
collaboration signals.

Approach:
1. Fetch brand homepage → extract social links from HTML/meta tags
2. For each discovered social profile, fetch public page to get follower counts
3. Detect influencer collaboration patterns (collab posts, brand ambassador pages)

Free — just HTTP fetches. No API keys needed.
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

import httpx

from ..logging import get_logger
from .base import Collector, SignalEvent

log = get_logger(__name__)

_SOCIAL_LINK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("instagram", re.compile(r'(?:https?://)?(?:www\.)?instagram\.com/([a-zA-Z0-9_.]{2,30})(?:[/"\'\s?#>]|$)', re.I)),
    ("facebook", re.compile(r'(?:https?://)?(?:www\.)?facebook\.com/([a-zA-Z0-9_.]{2,50})(?:[/"\'\s?#>]|$)', re.I)),
    ("twitter", re.compile(r'(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/([a-zA-Z0-9_]{2,30})(?:[/"\'\s?#>]|$)', re.I)),
    ("linkedin", re.compile(r'(?:https?://)?(?:www\.)?linkedin\.com/company/([a-zA-Z0-9_-]{2,50})(?:[/"\'\s?#>]|$)', re.I)),
    ("pinterest", re.compile(r'(?:https?://)?(?:www\.)?pinterest\.com/([a-zA-Z0-9_]{2,30})(?:[/"\'\s?#>]|$)', re.I)),
    ("youtube", re.compile(r'(?:https?://)?(?:www\.)?youtube\.com/(?:@|channel/|c/)([a-zA-Z0-9_-]{2,50})(?:[/"\'\s?#>]|$)', re.I)),
]

_SOCIAL_META_PATTERNS: list[tuple[str, str]] = [
    ("instagram", "instagram.com"),
    ("facebook", "facebook.com"),
    ("twitter", "twitter.com"),
    ("twitter", "x.com"),
    ("linkedin", "linkedin.com"),
    ("pinterest", "pinterest.com"),
    ("youtube", "youtube.com"),
]

_INFLUENCER_KEYWORDS = re.compile(
    r"influencer|brand\s+ambassador|collab(?:oration)?s?\b|"
    r"creator\s+program|creator\s+community|"
    r"partner\s+with\s+creators?|work\s+with\s+influencer|"
    r"#(?:ad|sponsored|collab|partner|gifted)|"
    r"ugc|user[\s-]generated\s+content",
    re.I,
)

_INFLUENCER_PAGES = [
    "/influencer",
    "/influencers",
    "/creators",
    "/creator-program",
    "/brand-ambassador",
    "/ambassador",
    "/ambassadors",
    "/collab",
    "/collaborations",
    "/ugc",
]

_NOISE_HANDLES = {
    "share", "sharer", "intent", "home", "login", "signup",
    "help", "about", "privacy", "terms", "hashtag", "search",
    "settings", "explore", "pages", "groups", "events",
    "tr", "en_US", "plugins", "dialog", "connect.facebook.net",
    "platform", "watch", "feed", "channel", "results",
}


class SocialPresenceCollector(Collector):
    """Detect social media presence and influencer signals for target domains.

    Parameters:
        domains: list[str] of domains to check. REQUIRED.
    """

    name = "social_presence"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        domains = self.params.get("domains") or []
        if not domains:
            log.warning("social_presence.no_domains")
            return

        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()

        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
        ) as client:
            for domain in domains:
                domain = domain.strip().lower().removeprefix("www.")
                if not domain:
                    continue

                try:
                    result = await _analyze_social(client, domain)
                    if result is None:
                        continue

                    # Emit social presence signal
                    platforms_found = len(result["profiles"])
                    yield SignalEvent(
                        type="social.presence",
                        source=self.name,
                        observed_at=now,
                        brand_domain=domain,
                        value_num=float(platforms_found),
                        value_text=", ".join(result["platforms_active"]) or "none",
                        payload=result,
                        dedupe_key=f"social:{domain}:{day}",
                    )

                    # Emit separate influencer signal if detected
                    if result["has_influencer_program"]:
                        yield SignalEvent(
                            type="social.influencer_program",
                            source=self.name,
                            observed_at=now,
                            brand_domain=domain,
                            value_num=1.0,
                            value_text="has_influencer_program",
                            payload={
                                "domain": domain,
                                "influencer_pages": result["influencer_pages"],
                                "influencer_signals": result["influencer_signals"],
                            },
                            dedupe_key=f"influencer:{domain}:{day}",
                        )
                except Exception as exc:
                    log.debug("social_presence.failed", domain=domain, error=str(exc))


async def _analyze_social(
    client: httpx.AsyncClient, domain: str
) -> dict[str, Any] | None:
    """Analyze social presence for a domain."""
    base_url = f"https://{domain}"

    # 1. Fetch homepage
    try:
        resp = await client.get(base_url)
        if resp.status_code >= 400:
            return None
        html = resp.text[:300_000]
    except httpx.HTTPError:
        return None

    # 2. Extract social links
    profiles: dict[str, dict[str, Any]] = {}
    for platform, pattern in _SOCIAL_LINK_PATTERNS:
        matches = pattern.findall(html)
        for handle in matches:
            handle = handle.strip().strip("/").split("?")[0].split("#")[0]
            if not handle or handle.lower() in _NOISE_HANDLES or len(handle) < 2:
                continue
            if platform not in profiles:
                profiles[platform] = {
                    "platform": platform,
                    "handle": handle,
                    "url": _build_social_url(platform, handle),
                    "followers": None,
                    "verified": None,
                }

    # 3. Check meta tags for social links not found via href
    for sel_platform, sel_domain in _SOCIAL_META_PATTERNS:
        if sel_platform in profiles:
            continue
        meta_match = re.search(
            rf'content=["\'](?:https?://)?(?:www\.)?{re.escape(sel_domain)}/([a-zA-Z0-9_./-]+)["\']',
            html, re.I,
        )
        if meta_match:
            handle = meta_match.group(1).strip("/").split("?")[0]
            if handle and handle.lower() not in _NOISE_HANDLES and len(handle) >= 2:
                profiles[sel_platform] = {
                    "platform": sel_platform,
                    "handle": handle,
                    "url": _build_social_url(sel_platform, handle),
                    "followers": None,
                    "verified": None,
                }

    # 4. Try to get Instagram follower count
    if "instagram" in profiles:
        ig_handle = profiles["instagram"]["handle"]
        ig_data = await _fetch_instagram_meta(client, ig_handle)
        if not ig_data:
            ig_data = await _fetch_instagram_via_searxng(ig_handle)
        if ig_data:
            profiles["instagram"].update(ig_data)

    # 4b. Try to get YouTube subscriber count via API
    if "youtube" in profiles:
        yt_handle = profiles["youtube"]["handle"]
        yt_data = await _fetch_youtube_stats(client, yt_handle)
        if yt_data:
            profiles["youtube"].update(yt_data)

    # 5. Check for influencer/creator program
    has_influencer = False
    influencer_pages: list[str] = []
    influencer_signals: list[str] = []

    # Check homepage for influencer keywords
    if _INFLUENCER_KEYWORDS.search(html):
        influencer_signals.append("homepage_mentions_influencers")

    # Check dedicated influencer pages
    for path in _INFLUENCER_PAGES:
        try:
            resp = await client.get(f"{base_url}{path}", follow_redirects=True)
            if resp.status_code == 200:
                page_text = resp.text[:50_000]
                if _INFLUENCER_KEYWORDS.search(page_text):
                    influencer_pages.append(path)
                    has_influencer = True
                    break
        except httpx.HTTPError:
            continue

    # Check footer links for influencer/creator references
    influencer_link = re.search(
        r'href=["\']([^"\']*(?:influencer|creator|ambassador|collab)[^"\']*)["\']',
        html, re.I,
    )
    if influencer_link:
        influencer_signals.append(f"link_found: {influencer_link.group(1)[:100]}")
        has_influencer = True

    platforms_active = sorted(profiles.keys())
    social_score = _compute_social_score(profiles, has_influencer)

    return {
        "domain": domain,
        "platforms_active": platforms_active,
        "platform_count": len(platforms_active),
        "profiles": profiles,
        "has_influencer_program": has_influencer,
        "influencer_pages": influencer_pages,
        "influencer_signals": influencer_signals,
        "social_score": social_score,
    }


async def _fetch_instagram_meta(
    client: httpx.AsyncClient, handle: str
) -> dict[str, Any] | None:
    """Try to get Instagram follower count from public profile page meta tags."""
    try:
        resp = await client.get(
            f"https://www.instagram.com/{handle}/",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
            },
        )
        if resp.status_code != 200:
            return None
        text = resp.text[:100_000]

        # Try og:description which often contains "X Followers"
        desc_match = re.search(
            r'content=["\']([^"\']*?[\d,.]+[KMB]?\s+Followers[^"\']*)["\']',
            text, re.I,
        )
        if desc_match:
            followers_text = desc_match.group(1)
            count = _parse_follower_count(followers_text)
            return {"followers": count, "followers_text": followers_text[:100]}

        # Fallback: look for follower count in JSON-LD or script data
        follower_match = re.search(r'"edge_followed_by":\s*\{"count":\s*(\d+)\}', text)
        if follower_match:
            return {"followers": int(follower_match.group(1))}

        return None
    except httpx.HTTPError:
        return None


async def _fetch_instagram_via_searxng(handle: str) -> dict[str, Any] | None:
    """Fallback: search SearXNG for Instagram follower count when direct scrape fails."""
    try:
        from ..tools.searxng import SearXNGTool
        searxng = SearXNGTool()
        if not searxng.available:
            return None
        result = await searxng.safe(query=f'instagram.com/{handle} followers', num=5)
        if result.degraded:
            return None
        for item in (result.data or {}).get("results", []):
            url = (item.get("url") or "").lower()
            if f"instagram.com/{handle.lower()}" not in url:
                continue
            snippet = item.get("snippet") or item.get("content") or ""
            title = item.get("title") or ""
            combined = f"{title} {snippet}"
            count = _parse_follower_count(combined)
            if count and count >= 10:
                return {"followers": count, "followers_text": f"{count} (via search)"}
    except Exception:
        pass
    return None


async def _fetch_youtube_stats(
    client: httpx.AsyncClient, handle: str
) -> dict[str, Any] | None:
    """Fetch YouTube channel stats via Data API v3."""
    import os
    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        return None

    try:
        handle_clean = handle.lstrip("@")

        params: dict[str, Any] = {
            "part": "statistics,snippet",
            "key": api_key,
        }
        if handle_clean.startswith("UC") and len(handle_clean) == 24:
            params["id"] = handle_clean
        else:
            params["forHandle"] = handle_clean

        resp = await client.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params=params,
        )
        if resp.status_code != 200:
            return None

        data = resp.json()
        items = data.get("items", [])
        if not items:
            if "forHandle" in params:
                params.pop("forHandle")
                params["forUsername"] = handle_clean
                resp = await client.get(
                    "https://www.googleapis.com/youtube/v3/channels",
                    params=params,
                )
                if resp.status_code != 200:
                    return None
                data = resp.json()
                items = data.get("items", [])
            if not items:
                return None

        stats = items[0].get("statistics", {})
        subs = stats.get("subscriberCount")
        views = stats.get("viewCount")
        videos = stats.get("videoCount")

        result: dict[str, Any] = {}
        if subs:
            result["subscribers"] = int(subs)
            result["followers"] = int(subs)
        if views:
            result["total_views"] = int(views)
        if videos:
            result["video_count"] = int(videos)
        return result if result else None
    except Exception:
        return None


def _parse_follower_count(text: str) -> int | None:
    """Parse follower count from text like '1.2M Followers' or '50,234 Followers'."""
    match = re.search(r'([\d,.]+)\s*([KMB])?\s*Followers', text, re.I)
    if not match:
        return None
    num_str = match.group(1).replace(",", "")
    try:
        num = float(num_str)
    except ValueError:
        return None
    suffix = (match.group(2) or "").upper()
    multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    return int(num * multipliers.get(suffix, 1))


def _build_social_url(platform: str, handle: str) -> str:
    urls = {
        "instagram": f"https://instagram.com/{handle}",
        "facebook": f"https://facebook.com/{handle}",
        "twitter": f"https://x.com/{handle}",
        "linkedin": f"https://linkedin.com/company/{handle}",
        "pinterest": f"https://pinterest.com/{handle}",
        "youtube": f"https://youtube.com/@{handle}",
    }
    return urls.get(platform, handle)


def _compute_social_score(
    profiles: dict[str, dict[str, Any]],
    has_influencer: bool,
) -> int:
    """Social maturity score 0-100. LOW score = opportunity for Grabon."""
    score = 0
    score += min(len(profiles) * 15, 45)  # up to 45 for 3+ platforms
    ig = profiles.get("instagram", {})
    followers = ig.get("followers")
    if isinstance(followers, int):
        if followers > 100_000:
            score += 30
        elif followers > 10_000:
            score += 20
        elif followers > 1_000:
            score += 10
    if has_influencer:
        score += 15
    return min(score, 100)
