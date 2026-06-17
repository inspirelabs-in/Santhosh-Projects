"""
Dedicated SERP crawl loop.
Primary: curl_cffi with Chrome TLS fingerprint via CloudProxy
Fallback: Camoufox browser
Option D: GEO pipeline already captures SERP inline from google_aio/ai_mode -- this loop
only targets keywords NOT covered by inline parsing.
"""
import asyncio
import logging
import random
import sys
import time
from urllib.parse import quote_plus

_HEADLESS = "virtual" if sys.platform != "win32" else True

from app.agent.scraper import (
    _get_proxy, _run_browser_scrape, load_google_cookies, rotate_serp_proxy,
)
from app.agent.serp_parser import parse_serp_html
from app.config import get_settings
from app.database import run_db
from app.events import broadcast
from app.models import PromptItem

log = logging.getLogger("geo.serp_scheduler")

_serp_running = False

_stats = {"curl_ok": 0, "curl_fail": 0, "browser_ok": 0, "browser_fail": 0, "start_time": 0}
_consecutive_429 = 0

GOOGLE_SEARCH_URL = "https://www.google.com/search?q={query}&hl=en&gl=in&num=20"

CURL_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "accept-language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
    "accept-encoding": "gzip, deflate, br",
    "upgrade-insecure-requests": "1",
    "sec-ch-ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "dnt": "1",
}


def _playwright_cookies_to_curl(cookies: list[dict]) -> dict[str, str]:
    result = {}
    for c in cookies:
        if "google" in c.get("domain", ""):
            result[c["name"]] = c["value"]
    return result


def _build_curl_proxies(proxy_config: dict | None) -> dict | None:
    if not proxy_config:
        return None
    proxy_url = proxy_config.get("server", "")
    if proxy_config.get("username"):
        proto, rest = proxy_url.split("://", 1)
        proxy_url = f"{proto}://{proxy_config['username']}:{proxy_config.get('password', '')}@{rest}"
    return {"http": proxy_url, "https": proxy_url}


async def _curl_attempt(session, url, cookies, proxies, label):
    """Single curl attempt. Returns HTML or None."""
    try:
        resp = await session.get(
            url,
            headers=CURL_HEADERS,
            cookies=cookies,
            proxies=proxies,
            allow_redirects=True,
        )

        if resp.status_code == 429:
            log.warning(f"SERP 429 {'(proxy)' if proxies else '(direct)'}: {label}")
            return None
        if resp.status_code != 200:
            log.warning(f"SERP {resp.status_code}: {label}")
            return None

        html = resp.text
        if not html or len(html) < 500:
            return None

        lower = html[:3000].lower()
        if "captcha" in lower or "unusual traffic" in lower or "sorry/index" in lower:
            log.warning(f"SERP captcha: {label}")
            return None

        return html

    except Exception as e:
        err = str(e)
        if "CONNECT" in err or "connect to" in err or "Connection refused" in err:
            log.warning(f"SERP proxy dead, rotating: {err[:80]}")
            rotate_serp_proxy()
        else:
            log.warning(f"SERP curl error: {err[:80]}")
        return None


async def _scrape_serp_curl(query: str) -> str | None:
    """SERP scrape via curl_cffi. Proxy first, rotate on failure, retry with new proxy."""
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None

    cookies_list = await load_google_cookies()
    cookies = _playwright_cookies_to_curl(cookies_list) if cookies_list else {}
    url = GOOGLE_SEARCH_URL.format(query=quote_plus(query))
    label = query[:40]

    for attempt in range(2):
        proxy_config = await _get_proxy("google_serp")
        proxies = _build_curl_proxies(proxy_config)

        async with AsyncSession(impersonate="chrome136", timeout=25) as session:
            html = await _curl_attempt(session, url, cookies, proxies, label)
            if html:
                return html

        if attempt == 0:
            rotate_serp_proxy()
            await asyncio.sleep(2)

    return None


async def _scrape_serp_browser(query: str) -> str | None:
    """Fallback: Camoufox browser scrape. No proxy (direct) to avoid dead proxy blocking."""
    camo_kwargs = {"headless": _HEADLESS, "humanize": True, "block_webrtc": True, "os": "windows"}

    url = GOOGLE_SEARCH_URL.format(query=quote_plus(query))

    async def _page_fn(page):
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        try:
            await page.evaluate("""() => {
                const btns = document.querySelectorAll('button, div[role="button"]');
                for (const b of btns) {
                    const t = b.textContent.trim().toLowerCase();
                    if (t === 'accept all' || t === 'i agree' || t === 'reject all') {
                        b.click(); return true;
                    }
                }
                return false;
            }""")
        except Exception:
            pass

        prev_len = 0
        stable = 0
        for _ in range(10):
            cur_len = await page.evaluate("() => document.body.innerText.length")
            if cur_len > 500 and cur_len == prev_len:
                stable += 1
                if stable >= 2:
                    break
            else:
                stable = 0
                prev_len = cur_len
            await page.wait_for_timeout(1000)

        html = ""
        try:
            body_el = await page.query_selector("body")
            if body_el:
                html = await body_el.inner_html()
        except Exception:
            pass

        return html, html, []

    raw_text, raw_html, _ = await _run_browser_scrape(camo_kwargs, _page_fn, use_google_cookies=True)
    return raw_html if raw_html and len(raw_html) > 200 else None


async def _commit_serp_to_db(prompt_id: str, serp_data):
    def _insert(conn):
        with conn.transaction():
            cur = conn.execute(
                """INSERT INTO serp_results (prompt_id, device, results_count, raw_html_hash)
                   VALUES (%s::uuid, %s, %s, %s) RETURNING id""",
                (prompt_id, "desktop", serp_data.results_count, serp_data.raw_html_hash),
            )
            serp_id = cur.fetchone()["id"]

            for r in serp_data.organic_results:
                conn.execute(
                    """INSERT INTO serp_organic_entries
                       (serp_id, rank_position, title, snippet, url, domain, has_table, is_target)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (serp_id, r.rank_position, r.title, r.snippet, r.url,
                     r.domain, r.has_table, r.is_target),
                )

            for ad in serp_data.ads:
                conn.execute(
                    """INSERT INTO serp_features
                       (serp_id, feature_type, rank_position, title, snippet, url, domain)
                       VALUES (%s, 'ad', %s, %s, %s, %s, %s)""",
                    (serp_id, ad.rank_position, ad.title, ad.snippet, ad.url, ad.domain),
                )

            for ab in serp_data.answer_box:
                conn.execute(
                    """INSERT INTO serp_features
                       (serp_id, feature_type, title, url, domain)
                       VALUES (%s, 'answer_box', %s, %s, %s)""",
                    (serp_id, ab.title, ab.url, ab.domain),
                )

            for q in serp_data.people_also_ask:
                conn.execute(
                    """INSERT INTO serp_features
                       (serp_id, feature_type, question_text)
                       VALUES (%s, 'paa', %s)""",
                    (serp_id, q),
                )

            for kw in serp_data.related_keywords:
                conn.execute(
                    """INSERT INTO serp_features
                       (serp_id, feature_type, title)
                       VALUES (%s, 'related_keyword', %s)""",
                    (serp_id, kw),
                )

            conn.execute(
                "UPDATE prompts SET last_serp_at = NOW() WHERE id = %s::uuid",
                (prompt_id,),
            )

            return serp_id

    try:
        serp_id = await run_db(_insert)
        log.info(f"SERP committed: serp_id={serp_id}, organic={len(serp_data.organic_results)}")
        return serp_id
    except Exception as e:
        log.error(f"SERP DB commit failed: {e}")
        return None


async def run_serp_for_prompt(prompt: PromptItem) -> bool:
    """Run single SERP crawl. Proxy-first curl, then browser fallback."""
    global _consecutive_429
    settings = get_settings()
    use_curl = settings.serp_use_curl
    method = "curl"

    if _consecutive_429 >= 3:
        pause = min(60 * _consecutive_429, 300)
        log.warning(f"SERP: {_consecutive_429} consecutive 429s, pausing {pause}s and rotating proxy")
        broadcast("serp_start", prompt=f"Rate limited, pausing {pause}s...")
        rotate_serp_proxy()
        await asyncio.sleep(pause)
        _consecutive_429 = max(0, _consecutive_429 - 2)

    try:
        log.info(f"SERP crawl: {prompt.text[:60]}")
        broadcast("serp_start", prompt=prompt.text[:80])

        html = None
        if use_curl:
            html = await _scrape_serp_curl(prompt.text)
            if html:
                _stats["curl_ok"] += 1
                _consecutive_429 = 0
            else:
                _stats["curl_fail"] += 1
                _consecutive_429 += 1
                from app.agent.scraper import _get_browser_sem
                sem = _get_browser_sem()
                if sem._value >= 2:
                    method = "browser"
                    log.info(f"SERP curl failed, trying browser: {prompt.text[:40]}")
                    html = await _scrape_serp_browser(prompt.text)
                    if html:
                        _stats["browser_ok"] += 1
                        _consecutive_429 = 0
                    else:
                        _stats["browser_fail"] += 1
                else:
                    log.info(f"SERP curl failed, skipping browser fallback (browser slots busy): {prompt.text[:40]}")
                    _stats["browser_fail"] += 1
        else:
            method = "browser"
            html = await _scrape_serp_browser(prompt.text)
            if html:
                _stats["browser_ok"] += 1
                _consecutive_429 = 0
            else:
                _stats["browser_fail"] += 1

        if not html or len(html) < 200:
            log.warning(f"SERP no HTML: {prompt.text[:60]} ({method})")
            return False

        serp_data = parse_serp_html(html, prompt.text, settings.target_domain)

        if not serp_data.organic_results:
            log.warning(f"SERP no organic results: {prompt.text[:60]} ({method})")
            return False

        await _commit_serp_to_db(prompt.id, serp_data)
        broadcast("serp_done", prompt=prompt.text[:80],
                  organic=len(serp_data.organic_results), ads=len(serp_data.ads),
                  method=method)
        return True

    except Exception as e:
        log.error(f"SERP crawl failed for {prompt.text[:60]}: {e}")
        return False


def get_serp_stats() -> dict:
    elapsed = time.time() - _stats["start_time"] if _stats["start_time"] else 0
    total_ok = _stats["curl_ok"] + _stats["browser_ok"]
    rate = (total_ok / (elapsed / 3600)) if elapsed > 60 else 0
    return {
        "curl_ok": _stats["curl_ok"],
        "curl_fail": _stats["curl_fail"],
        "browser_ok": _stats["browser_ok"],
        "browser_fail": _stats["browser_fail"],
        "total_success": total_ok,
        "elapsed_hours": round(elapsed / 3600, 2),
        "rate_per_hour": round(rate, 1),
    }


async def run_serp_loop():
    """Continuous SERP crawl loop.

    Option D: Skips keywords that already have fresh SERP data from
    GEO pipeline inline parsing (google_aio/ai_mode). Only crawls
    keywords with stale or missing SERP data.
    """
    global _serp_running
    if _serp_running:
        log.warning("SERP loop already running")
        return
    _serp_running = True
    _stats["start_time"] = time.time()

    settings = get_settings()
    batch_size = settings.serp_batch_size
    delay_min = settings.serp_delay_min
    delay_max = settings.serp_delay_max

    concurrency = settings.serp_concurrency

    log.info(f"SERP loop started: batch={batch_size}, delay={delay_min}-{delay_max}s, "
             f"concurrency={concurrency}, proxy=sticky")
    broadcast("serp_mode", mode="continuous", method="curl_cffi+proxy")

    _serp_sem = asyncio.Semaphore(concurrency)
    consecutive_blocks = 0

    while _serp_running:
        try:
            rows = await run_db(lambda conn: conn.execute(
                """SELECT id, text, merchant_category, intent_type
                   FROM prompts
                   WHERE intent_type <> 'GEO'
                     AND (last_serp_at IS NULL
                          OR last_serp_at < NOW() - INTERVAL '12 hours')
                   ORDER BY last_serp_at ASC NULLS FIRST
                   LIMIT %s""",
                (batch_size,),
            ).fetchall())

            if not rows:
                log.info("All SERP keywords fresh. Waiting 10m...")
                await asyncio.sleep(600)
                continue

            log.info(f"SERP batch: {len(rows)} keywords (concurrency={concurrency})")
            success_count = 0
            fail_count = 0

            async def _worker(row):
                nonlocal success_count, fail_count
                async with _serp_sem:
                    if not _serp_running:
                        return
                    prompt = PromptItem(
                        id=str(row["id"]),
                        text=row["text"],
                        merchant_category=row["merchant_category"],
                        intent_type=row["intent_type"],
                    )
                    ok = await run_serp_for_prompt(prompt)
                    if ok:
                        success_count += 1
                    else:
                        fail_count += 1
                    delay = random.uniform(delay_min, delay_max)
                    jitter = random.uniform(0, delay * 0.3)
                    await asyncio.sleep(delay + jitter)

            tasks = [asyncio.create_task(_worker(row)) for row in rows]
            await asyncio.gather(*tasks, return_exceptions=True)

            stats = get_serp_stats()
            log.info(f"SERP batch done: {success_count}/{len(rows)} ok | "
                     f"Rate: {stats['rate_per_hour']}/hr | "
                     f"curl={stats['curl_ok']}/{stats['curl_ok']+stats['curl_fail']} "
                     f"browser={stats['browser_ok']}/{stats['browser_ok']+stats['browser_fail']}")

            if fail_count > len(rows) * 0.5:
                consecutive_blocks += 1
                backoff = min(120 * consecutive_blocks, 900)
                log.warning(f"High fail rate ({fail_count}/{len(rows)}). "
                            f"Rotating proxy, backing off {backoff}s (streak={consecutive_blocks})")
                rotate_serp_proxy()
                await asyncio.sleep(backoff)
            else:
                consecutive_blocks = 0
                cooldown = random.uniform(15, 45)
                log.info(f"SERP cooldown {cooldown:.0f}s")
                await asyncio.sleep(cooldown)

        except Exception as e:
            log.error(f"SERP loop error: {e}")
            await asyncio.sleep(120)

    log.info("SERP loop stopped")
