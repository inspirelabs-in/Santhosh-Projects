"""Content/blog maturity collector — detects content marketing presence.

Checks:
1. Blog/content page existence (/blog, /articles, /resources, etc.)
2. Post count and publishing frequency
3. Last publish date (stale blog = opportunity)
4. Content categories and topics

Brands with no blog or dead blog = content marketing opportunity.

Free — just HTTP fetches via httpx. No API key needed.
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..logging import get_logger
from .base import Collector, SignalEvent

log = get_logger(__name__)

_BLOG_PATHS = [
    "/blog",
    "/blogs",
    "/articles",
    "/resources",
    "/news",
    "/stories",
    "/journal",
    "/magazine",
    "/insights",
    "/learn",
    "/knowledge",
    "/content",
    "/library",
]

_POST_DATE_PATTERNS = [
    re.compile(r"(\d{4}-\d{2}-\d{2})"),  # 2025-03-15
    re.compile(r"(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})", re.I),  # 15 March 2025
    re.compile(r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})", re.I),  # March 15, 2025
    re.compile(r'datetime=["\'](\d{4}-\d{2}-\d{2})', re.I),  # datetime="2025-03-15"
    re.compile(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})', re.I),  # JSON-LD datePublished
    re.compile(r'"dateModified"\s*:\s*"(\d{4}-\d{2}-\d{2})', re.I),  # JSON-LD dateModified
]

_POST_LINK_PATTERN = re.compile(
    r'<a[^>]*href=["\']([^"\']*(?:/blog/|/articles?/|/posts?/|/stories/|/resources/)[^"\']*)["\']',
    re.I,
)

_ARTICLE_TAG_PATTERN = re.compile(
    r"<article[\s>]",
    re.I,
)

_RSS_FEED_PATTERN = re.compile(
    r'<link[^>]*type=["\']application/(?:rss\+xml|atom\+xml)["\'][^>]*href=["\']([^"\']+)["\']',
    re.I,
)


class ContentBlogCollector(Collector):
    """Detect content marketing / blog presence for target domains.

    Parameters:
        domains: list[str] of domains to check. REQUIRED.
    """

    name = "content_blog"

    async def produce(self) -> AsyncIterator[SignalEvent]:
        domains = self.params.get("domains") or []
        if not domains:
            log.warning("content_blog.no_domains")
            return

        now = dt.datetime.now(dt.timezone.utc)
        day = now.date().isoformat()

        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; GrabonIntelBot/0.2)"},
        ) as client:
            for domain in domains:
                domain = domain.strip().lower().removeprefix("www.")
                if not domain:
                    continue

                try:
                    result = await _analyze_content(client, domain)
                    if result is None:
                        continue

                    yield SignalEvent(
                        type="content.blog",
                        source=self.name,
                        observed_at=now,
                        brand_domain=domain,
                        value_num=float(result["post_count_estimate"]),
                        value_text=result["maturity"],
                        payload=result,
                        dedupe_key=f"content:{domain}:{day}",
                    )
                except Exception as exc:
                    log.debug("content_blog.failed", domain=domain, error=str(exc))


async def _analyze_content(
    client: httpx.AsyncClient, domain: str
) -> dict[str, Any] | None:
    """Analyze content/blog presence for a domain."""
    base_url = f"https://{domain}"

    # 1. Fetch homepage — check for blog link and RSS feed
    homepage_html: str | None = None
    has_blog_link = False
    rss_url: str | None = None

    try:
        resp = await client.get(base_url)
        if resp.status_code < 400:
            homepage_html = resp.text[:300_000]
            # Check for blog links in navigation
            blog_link = re.search(
                r'<a[^>]*href=["\']([^"\']*(?:/blog|/articles|/resources|/stories|/journal)[^"\']*)["\'][^>]*>',
                homepage_html, re.I,
            )
            if blog_link:
                has_blog_link = True
            # Check for RSS feed
            rss_match = _RSS_FEED_PATTERN.search(homepage_html)
            if rss_match:
                rss_url = rss_match.group(1)
    except httpx.HTTPError:
        return None

    # 2. Find the blog page
    blog_path: str | None = None
    blog_html: str | None = None
    blog_url: str | None = None

    for path in _BLOG_PATHS:
        try:
            resp = await client.get(f"{base_url}{path}", follow_redirects=True)
            if resp.status_code == 200:
                text = resp.text[:300_000]
                # Verify it's actually a blog page (has article links or article tags)
                post_links = _POST_LINK_PATTERN.findall(text)
                article_tags = _ARTICLE_TAG_PATTERN.findall(text)
                if post_links or len(article_tags) >= 2:
                    blog_path = path
                    blog_html = text
                    blog_url = str(resp.url)
                    break
        except httpx.HTTPError:
            continue

    # 3. If no standard blog path worked, check for Shopify-style blogs
    if blog_html is None:
        for path in ["/blogs/news", "/blogs/journal", "/blogs/stories", "/blogs/all"]:
            try:
                resp = await client.get(f"{base_url}{path}", follow_redirects=True)
                if resp.status_code == 200:
                    text = resp.text[:300_000]
                    article_tags = _ARTICLE_TAG_PATTERN.findall(text)
                    if len(article_tags) >= 2:
                        blog_path = path
                        blog_html = text
                        blog_url = str(resp.url)
                        break
            except httpx.HTTPError:
                continue

    # 4. Analyze blog content
    post_count = 0
    post_links_found: list[str] = []
    dates_found: list[str] = []
    latest_date: str | None = None
    days_since_last_post: int | None = None
    categories: list[str] = []

    if blog_html:
        # Count post links
        post_links_found = _POST_LINK_PATTERN.findall(blog_html)
        unique_posts = set()
        for link in post_links_found:
            cleaned = link.split("?")[0].split("#")[0].rstrip("/")
            unique_posts.add(cleaned)
        post_count = len(unique_posts)

        # Count article tags as fallback
        article_count = len(_ARTICLE_TAG_PATTERN.findall(blog_html))
        if article_count > post_count:
            post_count = article_count

        # Extract dates
        for pattern in _POST_DATE_PATTERNS:
            matches = pattern.findall(blog_html)
            dates_found.extend(matches[:20])

        # Parse dates to find latest
        if dates_found:
            parsed = _parse_dates(dates_found)
            if parsed:
                latest = max(parsed)
                latest_date = latest.isoformat()
                days_since_last_post = (dt.date.today() - latest).days

        # Extract categories from links
        cat_pattern = re.compile(
            r'/(?:blog|articles?)/(?:category|tag|topic)/([a-z0-9-]+)',
            re.I,
        )
        cats = cat_pattern.findall(blog_html)
        categories = sorted(set(c.replace("-", " ").title() for c in cats))[:10]

    # 5. Determine maturity level
    maturity = _classify_maturity(
        has_blog=blog_html is not None,
        has_blog_link=has_blog_link,
        post_count=post_count,
        days_since_last=days_since_last_post,
        has_rss=rss_url is not None,
    )

    return {
        "domain": domain,
        "has_blog": blog_html is not None,
        "has_blog_link_on_homepage": has_blog_link,
        "blog_path": blog_path,
        "blog_url": blog_url,
        "has_rss_feed": rss_url is not None,
        "rss_url": rss_url,
        "post_count_estimate": post_count,
        "latest_post_date": latest_date,
        "days_since_last_post": days_since_last_post,
        "categories": categories,
        "maturity": maturity,
    }


def _parse_dates(date_strings: list[str]) -> list[dt.date]:
    """Parse various date formats into date objects."""
    import calendar

    results: list[dt.date] = []
    month_map = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
    month_abbr_map = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}

    for ds in date_strings:
        ds = ds.strip()
        try:
            # ISO format: 2025-03-15
            if re.match(r"\d{4}-\d{2}-\d{2}", ds):
                results.append(dt.date.fromisoformat(ds[:10]))
                continue
            # "15 March 2025" or "March 15, 2025"
            parts = re.split(r"[\s,]+", ds)
            if len(parts) >= 3:
                for i, p in enumerate(parts):
                    p_lower = p.lower().rstrip(".,")
                    month = month_map.get(p_lower) or month_abbr_map.get(p_lower[:3])
                    if month:
                        day_parts = [x for x in parts if x != p and re.match(r"\d+", x)]
                        if len(day_parts) >= 2:
                            nums = sorted([int(x.rstrip(".,")) for x in day_parts])
                            if nums[-1] > 31:  # year
                                results.append(dt.date(nums[-1], month, min(nums[0], 28)))
                        break
        except (ValueError, IndexError):
            continue
    return results


def _classify_maturity(
    has_blog: bool,
    has_blog_link: bool,
    post_count: int,
    days_since_last: int | None,
    has_rss: bool,
) -> str:
    """Content maturity: none / dead / basic / active / strong."""
    if not has_blog and not has_blog_link:
        return "none"
    if not has_blog:
        return "none"
    if post_count == 0:
        return "dead"
    if days_since_last is not None and days_since_last > 180:
        return "dead"
    if days_since_last is not None and days_since_last > 60:
        return "basic"
    if post_count < 5:
        return "basic"
    if post_count >= 20 and has_rss:
        return "strong"
    if post_count >= 10:
        return "active"
    return "basic"
