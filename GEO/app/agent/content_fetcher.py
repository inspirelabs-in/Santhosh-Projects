"""
Fetch and extract structured content from cited URLs.
Uses httpx for HTTP requests, BeautifulSoup for parsing.
Caches results in citation_pages table (24h TTL).
"""
import asyncio
import json
import logging
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.database import run_db
from app.models import PageContent

log = logging.getLogger("geo.content_fetcher")

_FETCH_TIMEOUT = 15
_MAX_TEXT_PREVIEW = 5000
_CACHE_HOURS = 24
_PER_DOMAIN_DELAY = 2.0
_domain_last_fetch: dict[str, float] = {}
_domain_locks: dict[str, asyncio.Lock] = {}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

SKIP_DOMAINS = {
    "google.com", "gstatic.com", "googleapis.com",
    "youtube.com", "facebook.com", "twitter.com", "x.com",
    "instagram.com", "linkedin.com", "tiktok.com",
}


def _extract_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc or ""
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def _should_skip(url: str) -> bool:
    domain = _extract_domain(url)
    return any(skip in domain for skip in SKIP_DOMAINS)


async def _respect_domain_delay(domain: str):
    import time
    if domain not in _domain_locks:
        _domain_locks[domain] = asyncio.Lock()
    async with _domain_locks[domain]:
        last = _domain_last_fetch.get(domain, 0)
        elapsed = time.time() - last
        if elapsed < _PER_DOMAIN_DELAY:
            await asyncio.sleep(_PER_DOMAIN_DELAY - elapsed)
        _domain_last_fetch[domain] = time.time()


async def _check_cache(url: str) -> PageContent | None:
    """Return cached page content if fetched within CACHE_HOURS."""
    def _q(conn):
        return conn.execute(
            """SELECT url, domain, title, meta_description, h1_tags, word_count,
                      main_text_preview, schema_types, canonical_url, fetch_status, fetched_at
               FROM citation_pages
               WHERE url = %s
                 AND fetched_at >= NOW() - make_interval(hours => %s)""",
            (url, _CACHE_HOURS),
        ).fetchone()

    row = await run_db(_q)
    if not row:
        return None

    return _row_to_page_content(row)


def _row_to_page_content(row) -> PageContent:
    return PageContent(
        url=row["url"],
        domain=row["domain"],
        title=row["title"] or "",
        meta_description=row["meta_description"] or "",
        h1_tags=row["h1_tags"] or [],
        word_count=row["word_count"] or 0,
        main_text_preview=row["main_text_preview"] or "",
        schema_types=row["schema_types"] or [],
        canonical_url=row["canonical_url"] or "",
        fetch_status=row["fetch_status"],
    )


async def get_cached_page(url: str) -> PageContent | None:
    """Return cached page content (any age). No live fetching."""
    def _q(conn):
        return conn.execute(
            """SELECT url, domain, title, meta_description, h1_tags, word_count,
                      main_text_preview, schema_types, canonical_url, fetch_status
               FROM citation_pages
               WHERE url = %s AND fetch_status = 200""",
            (url,),
        ).fetchone()
    row = await run_db(_q)
    return _row_to_page_content(row) if row else None


async def _save_to_cache(pc: PageContent):
    def _upsert(conn):
        conn.execute(
            """INSERT INTO citation_pages
               (url, domain, title, meta_description, h1_tags, word_count,
                main_text_preview, schema_types, canonical_url, fetch_status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (url) DO UPDATE SET
                 title = EXCLUDED.title,
                 meta_description = EXCLUDED.meta_description,
                 h1_tags = EXCLUDED.h1_tags,
                 word_count = EXCLUDED.word_count,
                 main_text_preview = EXCLUDED.main_text_preview,
                 schema_types = EXCLUDED.schema_types,
                 canonical_url = EXCLUDED.canonical_url,
                 fetch_status = EXCLUDED.fetch_status,
                 fetched_at = NOW()""",
            (pc.url, pc.domain, pc.title, pc.meta_description,
             pc.h1_tags, pc.word_count, pc.main_text_preview,
             pc.schema_types, pc.canonical_url, pc.fetch_status),
        )
        conn.commit()
    try:
        await run_db(_upsert)
    except Exception as e:
        log.warning(f"Cache save failed for {pc.url}: {e}")


def _parse_page(html: str, url: str) -> PageContent:
    """Extract structured content from raw HTML."""
    domain = _extract_domain(url)
    soup = BeautifulSoup(html, "lxml")

    title = ""
    title_el = soup.find("title")
    if title_el:
        title = title_el.get_text(strip=True)

    meta_desc = ""
    meta_el = soup.find("meta", attrs={"name": "description"})
    if meta_el:
        meta_desc = meta_el.get("content", "")

    h1_tags = []
    for h1 in soup.find_all("h1"):
        text = h1.get_text(strip=True)
        if text:
            h1_tags.append(text)

    canonical = ""
    can_el = soup.find("link", attrs={"rel": "canonical"})
    if can_el:
        canonical = can_el.get("href", "")

    schema_types = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict) and "@type" in data:
                schema_types.append(data["@type"])
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "@type" in item:
                        schema_types.append(item["@type"])
        except (json.JSONDecodeError, TypeError):
            continue

    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        tag.decompose()

    main_el = soup.find("main") or soup.find("article") or soup.find("body")
    main_text = ""
    if main_el:
        main_text = main_el.get_text(separator="\n", strip=True)

    main_text = re.sub(r"\n{3,}", "\n\n", main_text)
    word_count = len(main_text.split())
    preview = main_text[:_MAX_TEXT_PREVIEW]

    return PageContent(
        url=url,
        domain=domain,
        title=title,
        meta_description=meta_desc,
        h1_tags=h1_tags,
        word_count=word_count,
        main_text_preview=preview,
        schema_types=schema_types,
        canonical_url=canonical,
        fetch_status=200,
    )


async def fetch_page_content(url: str) -> PageContent | None:
    """Fetch and parse a URL. Returns cached result if available."""
    if _should_skip(url):
        log.debug(f"Skipping blocked domain: {url}")
        return None

    cached = await _check_cache(url)
    if cached:
        log.debug(f"Cache hit: {url}")
        return cached

    domain = _extract_domain(url)
    await _respect_domain_delay(domain)

    try:
        async with httpx.AsyncClient(
            headers=_HEADERS,
            timeout=_FETCH_TIMEOUT,
            follow_redirects=True,
            max_redirects=5,
        ) as client:
            resp = await client.get(url)

            if resp.status_code != 200:
                pc = PageContent(
                    url=url, domain=domain, fetch_status=resp.status_code,
                )
                await _save_to_cache(pc)
                return pc

            content_type = resp.headers.get("content-type", "")
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                log.debug(f"Non-HTML content-type for {url}: {content_type}")
                pc = PageContent(url=url, domain=domain, fetch_status=415)
                await _save_to_cache(pc)
                return pc

            pc = _parse_page(resp.text, url)
            await _save_to_cache(pc)
            log.info(f"Fetched: {url} - {pc.word_count} words, {len(pc.schema_types)} schemas")
            return pc

    except httpx.TimeoutException:
        log.warning(f"Timeout fetching {url}")
        pc = PageContent(url=url, domain=domain, fetch_status=408)
        await _save_to_cache(pc)
        return pc
    except Exception as e:
        log.warning(f"Fetch failed for {url}: {e}")
        pc = PageContent(url=url, domain=domain, fetch_status=0)
        await _save_to_cache(pc)
        return pc


async def fetch_multiple(urls: list[str], concurrency: int = 3) -> dict[str, PageContent]:
    """Fetch multiple URLs with concurrency limit. Returns url->PageContent map."""
    results: dict[str, PageContent] = {}
    sem = asyncio.Semaphore(concurrency)

    async def _fetch_one(url: str):
        async with sem:
            pc = await fetch_page_content(url)
            if pc:
                results[url] = pc

    await asyncio.gather(*[_fetch_one(u) for u in urls], return_exceptions=True)
    return results
