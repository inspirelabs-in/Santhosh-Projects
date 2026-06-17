import re
import sys
import random
import logging
import asyncio
import json
import time
import httpx
from urllib.parse import quote_plus

_HEADLESS = "virtual" if sys.platform != "win32" else True
from camoufox.async_api import AsyncCamoufox
from app.models import ScrapeResult
from app.config import get_settings
from app.routes.auth import (
    load_google_cookies, load_chatgpt_cookies, load_chatgpt_storage_state,
    load_gemini_storage_state, load_claude_storage_state,
    load_perplexity_storage_state,
)
from app.agent.smart_extract import (
    find_text_input, snapshot_page_text, type_and_submit,
    wait_and_extract_response, clean_scraped_text,
)

log = logging.getLogger("geo.scraper")

ENGINES = {
    "google_aio": {
        "url_template": "https://www.google.com/search?q={query}",
        "response_selectors": [
            "div.wDYxhc",
            "div[data-attrid='wa:/description']",
            "div[data-attrid]",
            "div.IZ6rdc",
            "div[data-sgrd]",
            "div.xpdopen div.LGOjhe",
            "div.kp-wholepage div[data-md]",
        ],
        "needs_input": False,
    },
    "google_ai_mode": {
        "url_template": "https://www.google.com/search?q={query}&udm=50",
        "response_selectors": [],
        "needs_input": False,
    },
    "perplexity": {
        "url": "https://www.perplexity.ai",
        "input_selector": "textarea",
        "response_selectors": [
            "div.prose",
            "div[class*='prose']",
            "div.markdown",
        ],
        "needs_input": True,
    },
}

ALL_ENGINES = ["google_aio", "google_ai_mode", "perplexity", "gemini", "chatgpt", "claude"]
VISIBLE_ENGINES = ALL_ENGINES


_engine_proxy_cache: dict[str, dict] = {}
_engine_proxy_cache_ts: dict[str, float] = {}

_serp_proxy_cache: dict | None = None
_serp_proxy_cache_ts: float = 0

_proxy_list_cache: list = []
_proxy_list_cache_ts: float = 0

AUTH_ENGINES = {"chatgpt", "claude", "gemini", "perplexity"}
UNAUTH_ENGINES = {"google_aio", "google_ai_mode", "google_serp"}

_CAPTCHA_SIGNALS = [
    "unusual traffic",
    "detected unusual traffic",
    "not a robot",
    "captcha",
    "sorry/index",
    "automated queries",
    "please show you're not a robot",
    "systems have detected",
]


def _is_captcha_page(text: str) -> bool:
    lower = text.lower()
    return any(sig in lower for sig in _CAPTCHA_SIGNALS)


def _is_paa_content(text: str) -> bool:
    """Detect People Also Ask content that leaked into a response."""
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    if not lines:
        return False
    question_lines = [l for l in lines if l.rstrip().endswith("?")]
    if len(lines) <= 6 and len(question_lines) == len(lines):
        return True
    if len(text) < 300 and len(question_lines) >= 3:
        ratio = len(question_lines) / len(lines)
        if ratio > 0.7:
            return True
    return False


_FEATURED_SNIPPET_SIGNALS = [
    "find your items and add them to",
    "fill out the", "verification form",
    "you must select a loved one",
    "all vouchers linked to your number",
    "how to use", "step 1", "step 2",
]


def _is_featured_snippet(text: str) -> bool:
    """Detect instructional featured snippets that aren't AI Overview content.
    Only rejects obvious how-to/instructional snippets without AIO header.
    Short real answers (coupon codes, prices) are kept."""
    if "AI Overview" in text:
        return False
    lower = text.lower()
    hits = sum(1 for sig in _FEATURED_SNIPPET_SIGNALS if sig in lower)
    if hits >= 1 and len(text) < 350:
        return True
    return False


_GOOGLE_MAPS_SIGNALS = [
    "choose area", "closes ", "opens ", "directions", "open now",
    "supermarket", "cash and carry", "reviews)",
]


_UI_NOISE_SIGNALS = [
    "all chats", "free plan", "pro plan", "upgrade", "new chat",
    "message limit", "out of free messages",
]


_RATE_LIMIT_VALIDATE_SIGNALS = [
    "out of free messages", "message limit", "hit your limit",
    "limits will reset", "upgrade for higher limits",
    "5-hour message limit", "you've hit your limit",
    "rate limit", "too many requests", "usage limit",
    "limit exhausted",
]


def _validate_response(text: str, query: str, engine_name: str) -> str | None:
    """Check if scraped response is valid. Returns error reason or None if valid."""
    if _is_captcha_page(text):
        return "captcha_detected"
    if len(text) < 30:
        return None

    lower = text.lower()
    rate_hits = sum(1 for sig in _RATE_LIMIT_VALIDATE_SIGNALS if sig in lower)
    if rate_hits >= 2:
        log.warning(f"[{engine_name}] Rate limit text detected in response ({rate_hits} signals)")
        return "rate_limited"

    if engine_name in ("google_aio", "google_ai_mode"):
        if _is_paa_content(text):
            log.warning(f"[{engine_name}] PAA content detected, rejecting: {text[:100]}")
            return "paa_content"

        if engine_name == "google_aio" and _is_featured_snippet(text):
            log.warning(f"[{engine_name}] Featured snippet detected (no AIO header), rejecting: {text[:100]}")
            return "featured_snippet"

        maps_hits = sum(1 for sig in _GOOGLE_MAPS_SIGNALS if sig in lower)
        if maps_hits >= 2 and len(text) < 800:
            log.warning(f"[{engine_name}] Google Maps/Places content detected, rejecting: {text[:100]}")
            return "maps_content"

    if engine_name in ("claude", "chatgpt", "gemini", "perplexity"):
        noise_hits = sum(1 for sig in _UI_NOISE_SIGNALS if sig in lower)
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        short_lines = sum(1 for l in lines if len(l) < 30)
        if noise_hits >= 3 and len(text) < 500:
            log.warning(f"[{engine_name}] UI noise detected ({noise_hits} signals), rejecting: {text[:100]}")
            return "ui_noise"
        if len(lines) > 5 and short_lines / len(lines) > 0.8 and noise_hits >= 2:
            log.warning(f"[{engine_name}] Mostly short UI noise lines, rejecting: {text[:100]}")
            return "ui_noise"

    query_words = [w.lower() for w in query.split() if len(w) > 3]
    if not query_words:
        return None
    matches = sum(1 for w in query_words if w in lower)
    relevance = matches / len(query_words)
    if relevance < 0.15 and len(text) > 100:
        if engine_name in ("google_aio", "google_ai_mode"):
            log.warning(f"[{engine_name}] Low relevance ({relevance:.0%}) for query: {query[:60]}")
    return None

_browser_sem: asyncio.Semaphore | None = None
_login_gate: asyncio.Event | None = None

def _get_browser_sem() -> asyncio.Semaphore:
    global _browser_sem
    if _browser_sem is None:
        _browser_sem = asyncio.Semaphore(5)
    return _browser_sem

def _get_login_gate() -> asyncio.Event:
    global _login_gate
    if _login_gate is None:
        _login_gate = asyncio.Event()
        _login_gate.set()
    return _login_gate

def pause_scraping():
    _get_login_gate().clear()
    log.info("[scraper] Scraping paused for multi-login")

def resume_scraping():
    _get_login_gate().set()
    log.info("[scraper] Scraping resumed after multi-login")


async def _fetch_proxy_list() -> list[dict]:
    settings = get_settings()
    if not settings.cloudproxy_url:
        return []
    url = f"{settings.cloudproxy_url.rstrip('/')}/"
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=10, verify=False) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    log.warning(f"CloudProxy returned {resp.status_code} (attempt {attempt+1}/3)")
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                data = resp.json()
                proxies = data.get("proxies", data.get("proxy", data.get("ips", [])))
                if isinstance(proxies, list):
                    return proxies
                return [proxies] if proxies else []
        except Exception as e:
            log.warning(f"CloudProxy fetch failed (attempt {attempt+1}/3): {e}")
            await asyncio.sleep(2 * (attempt + 1))
    log.error("CloudProxy unavailable after 3 attempts")
    return []


def _build_proxy_config(proxy) -> dict:
    if isinstance(proxy, str):
        from urllib.parse import urlparse
        parsed = urlparse(proxy)
        config = {"server": f"http://{parsed.hostname}:{parsed.port or 8899}"}
        if parsed.username:
            config["username"] = parsed.username
            config["password"] = parsed.password or ""
        return config

    ip = proxy.get("ip", proxy.get("host", ""))
    port = proxy.get("port", 8899)
    config = {"server": f"http://{ip}:{port}"}
    if proxy.get("username"):
        config["username"] = proxy["username"]
        config["password"] = proxy.get("password", "")
    return config


async def _get_cached_proxy_list() -> list:
    """Fetch proxy list with 5-min cache to avoid hammering CloudProxy API."""
    global _proxy_list_cache, _proxy_list_cache_ts
    if _proxy_list_cache and (time.time() - _proxy_list_cache_ts) < 300:
        return _proxy_list_cache
    old_servers = {_build_proxy_config(p)["server"] for p in _proxy_list_cache} if _proxy_list_cache else set()
    _proxy_list_cache = await _fetch_proxy_list()
    _proxy_list_cache_ts = time.time()
    if _proxy_list_cache and old_servers:
        new_servers = {_build_proxy_config(p)["server"] for p in _proxy_list_cache}
        if new_servers != old_servers:
            log.info(f"Proxy pool changed: {old_servers} -> {new_servers}, clearing engine proxy caches")
            _engine_proxy_cache.clear()
            _engine_proxy_cache_ts.clear()
            _engine_proxy_fail_count.clear()
    return _proxy_list_cache


async def _get_proxy(engine_name: str = "") -> dict | None:
    """Get proxy. Auth engines go DIRECT (no proxy). Only unauth/SERP engines use proxy."""
    global _serp_proxy_cache, _serp_proxy_cache_ts

    settings = get_settings()
    if not settings.cloudproxy_url:
        return None

    if engine_name in AUTH_ENGINES:
        return None

    if engine_name == "google_serp":
        if _serp_proxy_cache and (time.time() - _serp_proxy_cache_ts) < 1800:
            return _serp_proxy_cache

        proxies = await _get_cached_proxy_list()
        if not proxies:
            return None
        proxy = proxies[0] if len(proxies) == 1 else proxies[hash(str(int(time.time() / 1800))) % len(proxies)]
        _serp_proxy_cache = _build_proxy_config(proxy)
        _serp_proxy_cache_ts = time.time()
        log.info(f"SERP sticky proxy (30min): {_serp_proxy_cache['server']}")
        return _serp_proxy_cache

    proxies = await _get_cached_proxy_list()
    if not proxies:
        return None
    proxy = random.choice(proxies)
    config = _build_proxy_config(proxy)
    return config


def rotate_serp_proxy():
    """Force SERP proxy rotation on next request."""
    global _serp_proxy_cache_ts
    _serp_proxy_cache_ts = 0


def rotate_engine_proxy(engine_name: str):
    """Force proxy rotation for a specific engine on next request."""
    _engine_proxy_cache_ts[engine_name] = 0
    _engine_proxy_cache.pop(engine_name, None)
    log.info(f"[{engine_name}] Proxy rotated, will pick new IP on next request")


_BROWSER_SCRAPE_TIMEOUT = 120

async def _run_browser_scrape(camo_kwargs: dict, page_fn, use_google_cookies: bool = False) -> tuple[str, str, list[str]]:
    """Run a browser scrape with global concurrency limit, hard timeout, and error handling."""
    try:
        return await asyncio.wait_for(
            _run_browser_scrape_inner(camo_kwargs, page_fn, use_google_cookies),
            timeout=_BROWSER_SCRAPE_TIMEOUT + 120,
        )
    except asyncio.TimeoutError:
        log.error(f"Browser scrape hard-killed after {_BROWSER_SCRAPE_TIMEOUT + 120}s (including semaphore wait)")
        return "", "", []


async def _run_browser_scrape_inner(camo_kwargs: dict, page_fn, use_google_cookies: bool = False) -> tuple[str, str, list[str]]:
    """Inner browser scrape with concurrency limit."""
    raw_text, raw_html, cited_urls = "", "", []
    await _get_login_gate().wait()
    sem = _get_browser_sem()
    async with sem:
        try:
            async with asyncio.timeout(_BROWSER_SCRAPE_TIMEOUT):
                async with AsyncCamoufox(**camo_kwargs) as browser:
                    page = await browser.new_page()
                    page.on("pageerror", lambda _: None)
                    try:
                        page.context.on("weberror", lambda _: None)
                    except Exception:
                        pass
                    try:
                        page.context.on("page", lambda p: p.on("pageerror", lambda _: None))
                    except Exception:
                        pass
                    if use_google_cookies:
                        cookies = await load_google_cookies()
                        if cookies:
                            await page.context.add_cookies(cookies)
                            log.info(f"Loaded {len(cookies)} Google cookies for authenticated scrape")
                    raw_text, raw_html, cited_urls = await page_fn(page)
        except TimeoutError:
            log.error(f"Browser scrape timed out after {_BROWSER_SCRAPE_TIMEOUT}s (inside semaphore)")
        except Exception as e:
            err = str(e)
            if "WriteUnixTransport" in err or "handler is closed" in err or "has been closed" in err:
                log.debug("Ignoring Browser.close transport error (data already captured)")
            elif "Cannot read properties of undefined" in err or "Node.js" in err:
                log.debug("Ignoring Playwright internal page error (non-fatal)")
            elif "Target page, context or browser has been closed" in err:
                log.debug("Browser closed mid-scrape (non-fatal)")
            elif raw_text:
                log.debug(f"Browser close error after data capture: {err}")
            else:
                import traceback
                log.error(f"Browser scrape exception: {type(e).__name__}: {e}\n{''.join(traceback.format_tb(e.__traceback__))}")
                raise
    return raw_text, raw_html, cited_urls


async def scrape_google_aio(query: str, proxy: dict | None = None) -> ScrapeResult:
    config = ENGINES["google_aio"]
    url = config["url_template"].format(query=quote_plus(query)) + "&hl=en&gl=in"

    try:
        camo_kwargs = {"headless": _HEADLESS, "humanize": True, "block_webrtc": True, "os": "windows"}
        if proxy:
            camo_kwargs["proxy"] = proxy
            camo_kwargs["geoip"] = True

        async def _page_fn(page):
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            # Dismiss Google consent banner if present
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

            # Wait for AIO to appear (it lazy-loads after initial SERP render)
            aio_appeared = False
            for tick in range(25):
                has_aio = await page.evaluate("""() => {
                    const body = document.body.innerText;
                    if (body.includes('AI Overview')) return true;
                    if (document.querySelector('div[data-async-type="editableDirectAnswer"]')) return true;
                    if (document.querySelector('div.wDYxhc[data-bsrd]')) return true;
                    if (document.querySelector('div.M8OgIe')) return true;
                    if (document.querySelector('div.kp-wholepage div[data-md]')) return true;
                    // data-sgrd: check for non-PAA content at lower threshold
                    const sgrd = document.querySelector('div[data-sgrd]');
                    if (sgrd) {
                        const text = sgrd.innerText || '';
                        const qCount = (text.match(/\\?/g) || []).length;
                        const lines = text.trim().split('\\n').filter(l => l.trim());
                        if (text.length > 80 && qCount < lines.length * 0.6) return true;
                    }
                    return false;
                }""")
                if has_aio:
                    aio_appeared = True
                    await page.wait_for_timeout(2000)
                    log.info(f"AIO detected after {tick + 1}s of polling")
                    break
                await page.wait_for_timeout(1000)

            # Second chance: scroll down and check once more
            if not aio_appeared:
                try:
                    await page.evaluate("window.scrollBy(0, 600)")
                    await page.wait_for_timeout(2000)
                    has_aio_after_scroll = await page.evaluate("""() => {
                        return document.body.innerText.includes('AI Overview')
                            || !!document.querySelector('div[data-async-type="editableDirectAnswer"]')
                            || !!document.querySelector('div.wDYxhc[data-bsrd]')
                            || !!document.querySelector('div.M8OgIe');
                    }""")
                    if has_aio_after_scroll:
                        aio_appeared = True
                        await page.wait_for_timeout(2000)
                        log.info("AIO detected after scroll-down second chance")
                    else:
                        await page.evaluate("window.scrollTo(0, 0)")
                except Exception:
                    pass

            if not aio_appeared:
                page_text = await page.evaluate("() => document.body.innerText.substring(0, 500)")
                log.warning(f"AIO not detected after 35s polling. Page preview: {page_text[:300]}")

            # Try clicking "Show more" to expand collapsed AIO
            if aio_appeared:
                try:
                    expanded = await page.evaluate("""() => {
                        // First look for "Show more" near the AIO heading
                        const aioH = [...document.querySelectorAll('h2, div[role="heading"], span[role="heading"]')]
                            .find(h => h.textContent.includes('AI Overview'));
                        const aioTop = aioH ? aioH.getBoundingClientRect().top : 0;
                        const buttons = document.querySelectorAll('div[role="button"], button, span[role="button"]');
                        for (const b of buttons) {
                            const t = b.textContent.trim().toLowerCase();
                            if (t === 'show more' || t === 'show all') {
                                const rect = b.getBoundingClientRect();
                                // Click if within 1200px of page top or near the AIO heading
                                if (rect.top < 1200 || (aioTop > 0 && Math.abs(rect.top - aioTop) < 600)) {
                                    b.click(); return true;
                                }
                            }
                        }
                        return false;
                    }""")
                    if expanded:
                        log.info("Clicked 'Show more' to expand AIO")
                        await page.wait_for_timeout(3000)
                except Exception:
                    pass

            # Wait for content to stabilize
            prev_len = 0
            stable = 0
            for _ in range(15):
                cur_len = await page.evaluate("() => document.body.innerText.length")
                if cur_len > 500 and cur_len == prev_len:
                    stable += 1
                    if stable >= 2:
                        break
                else:
                    stable = 0
                    prev_len = cur_len
                await page.wait_for_timeout(1000)

            aio_text = await page.evaluate("""() => {
                function isPAA(text) {
                    if (text.includes('People also ask')) return true;
                    const lines = text.trim().split('\\n').filter(l => l.trim());
                    if (lines.length < 6 && lines.every(l => l.endsWith('?'))) return true;
                    const qCount = (text.match(/\\?/g) || []).length;
                    if (qCount >= 3 && text.length < 300) return true;
                    return false;
                }

                // Strategy 1: Find "AI Overview" heading → grab parent container
                const headings = document.querySelectorAll('h2, div[role="heading"], span[role="heading"]');
                for (const h of headings) {
                    if (h.textContent.includes('AI Overview')) {
                        let container = h.closest('div[data-sgrd]')
                            || h.closest('div[data-bsrd]')
                            || h.closest('div[jscontroller]')
                            || h.closest('div[jsname]')
                            || h.closest('div.M8OgIe')
                            || h.closest('div.kp-wholepage');
                        if (!container) {
                            let el = h;
                            for (let i = 0; i < 10; i++) {
                                el = el.parentElement;
                                if (!el) break;
                                if (el.innerText && el.innerText.length > 50) {
                                    container = el;
                                    if (el.parentElement && el.parentElement.innerText.length > el.innerText.length * 1.3) {
                                        container = el.parentElement;
                                    }
                                    break;
                                }
                            }
                        }
                        if (container) {
                            const text = container.innerText;
                            if (text.length > 30 && !isPAA(text)) return '1:' + text;
                        }
                    }
                }

                // Strategy 2: data-sgrd containers
                const sgrd = document.querySelectorAll('div[data-sgrd]');
                for (const el of sgrd) {
                    const text = el.innerText;
                    if (text.length > 80 && !isPAA(text)) return '2:' + text;
                }

                // Strategy 3: Known AIO wrapper selectors
                const selectors = [
                    'div[data-async-type="editableDirectAnswer"]',
                    'div.wDYxhc[data-bsrd]',
                    'div.M8OgIe',
                    'div[data-attrid="wa:/description"]',
                    'div.xpdopen div.LGOjhe',
                    'div.kp-wholepage div[data-md]',
                    'div.wDYxhc',
                ];
                for (const sel of selectors) {
                    const els = document.querySelectorAll(sel);
                    for (const el of els) {
                        const text = el.innerText;
                        if (text.length > 80 && !isPAA(text) && text.includes('AI Overview')) return '3:' + text;
                    }
                }
                // Strategy 3b: same selectors but without requiring "AI Overview" text
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el) {
                        const text = el.innerText;
                        if (text.length > 150 && !isPAA(text)) return '3:' + text;
                    }
                }

                // Strategy 4: Text-based extraction from body
                const body = document.body.innerText;
                const idx = body.indexOf('AI Overview');
                if (idx >= 0) {
                    const chunk = body.substring(idx, idx + 5000);
                    const endMarkers = ['People also ask', 'Related searches', 'Images', 'Videos',
                                        'Web results', 'More results', 'Feedback', 'About this result',
                                        'Search Results', 'Sponsored', 'People also search for',
                                        'See results about', 'Top stories', 'Discussions and forums'];
                    let end = chunk.length;
                    for (const m of endMarkers) {
                        const mi = chunk.indexOf(m, 20);
                        if (mi > 0 && mi < end) end = mi;
                    }
                    const result = chunk.substring(0, end).trim();
                    if (result.length > 50 && !isPAA(result)) return '4:' + result;
                }

                // Diagnostic: collect what each strategy found
                const diag = [];
                const headingCount = document.querySelectorAll('h2, div[role="heading"], span[role="heading"]').length;
                const aioHeadings = [...document.querySelectorAll('h2, div[role="heading"], span[role="heading"]')]
                    .filter(h => h.textContent.includes('AI Overview'));
                diag.push('h:' + headingCount + '/aio:' + aioHeadings.length);
                const sgrdEls = document.querySelectorAll('div[data-sgrd]');
                const sgrdLens = [...sgrdEls].map(e => e.innerText.length);
                diag.push('sgrd:' + sgrdEls.length + '/' + JSON.stringify(sgrdLens));
                const bodyHasAIO = document.body.innerText.includes('AI Overview');
                diag.push('bodyAIO:' + bodyHasAIO);
                // Sample sgrd content for debugging
                if (sgrdEls.length > 0) {
                    diag.push('sgrd0:' + sgrdEls[0].innerText.substring(0, 100).replace(/\\n/g, ' '));
                }
                // If AIO heading exists, grab nearest large parent text
                if (aioHeadings.length > 0) {
                    let el = aioHeadings[0];
                    for (let i = 0; i < 10; i++) {
                        el = el.parentElement;
                        if (!el) break;
                        if (el.innerText && el.innerText.length > 50) {
                            diag.push('aioParent:' + el.innerText.length + '/' + el.innerText.substring(0, 80).replace(/\\n/g, ' '));
                            break;
                        }
                    }
                }
                return 'DIAG:' + diag.join('|');
            }""")

            if aio_text and aio_text.startswith('DIAG:'):
                log.warning(f"AIO extraction failed - diagnostics: {aio_text[5:]}")
                aio_text = ""

            if aio_text and ':' in aio_text[:3]:
                strategy = aio_text[:2]
                aio_text = aio_text[2:]
                log.info(f"AIO extraction matched strategy {strategy}, {len(aio_text)} chars")

            if not aio_text or len(aio_text) < 50:
                log.warning("No AIO found via JS strategies, trying full body fallback")
                try:
                    full = await page.evaluate("() => document.body ? document.body.innerText : ''")
                    full = (full or "").strip()
                except Exception as fb_err:
                    log.error(f"Body fallback evaluate failed: {fb_err}")
                    full = ""
                log.info(f"Full body text length: {len(full)}, contains 'AI Overview': {'AI Overview' in full}")
                if full and "AI Overview" in full:
                    idx = full.index("AI Overview")
                    chunk = full[idx:idx + 5000]
                    end_markers = ["People also ask", "Related searches", "Images", "Videos",
                                   "Web results", "More results", "Feedback", "About this result",
                                   "Search Results", "Sponsored", "People also search for",
                                   "See results about", "Top stories", "Discussions and forums"]
                    end = len(chunk)
                    for m in end_markers:
                        mi = chunk.find(m, 20)
                        if 0 < mi < end:
                            end = mi
                    aio_text = chunk[:end].strip()
                    log.info(f"Body fallback extracted {len(aio_text)} chars")
                elif full and len(full) > 200 and aio_appeared:
                    log.info(f"No 'AI Overview' marker in body despite detection signal - treating as no AIO (likely PAA false positive)")

            cited_urls = []
            if aio_text:
                links = await page.query_selector_all("a[href^='http']")
                for link in links:
                    href = await link.get_attribute("href")
                    if href and "google.com" not in href and "gstatic.com" not in href:
                        cited_urls.append(href)
                cited_urls = list(dict.fromkeys(cited_urls))[:20]

            html = ""
            try:
                body_el = await page.query_selector("body")
                if body_el:
                    html = await body_el.inner_html()
            except Exception:
                pass

            return aio_text or "", html, cited_urls

        raw_text, raw_html, cited_urls = await _run_browser_scrape(camo_kwargs, _page_fn, use_google_cookies=True)

        if _is_captcha_page(raw_text or raw_html or ""):
            log.warning(f"Google AIO captcha/bot detection triggered")
            return ScrapeResult(
                raw_response_text="",
                raw_html_payload=raw_html,
                error_log="Google captcha/bot detection - IP may be blocked",
            )

        if not raw_text:
            return ScrapeResult(
                raw_response_text="[No AI Overview] Google did not display an AI Overview for this query.",
                raw_html_payload=raw_html,
            )

        log.info(f"Google AIO scraped: {len(raw_text)} chars, {len(cited_urls)} cited URLs")
        return ScrapeResult(raw_response_text=raw_text, raw_html_payload=raw_html, cited_urls=cited_urls)

    except Exception as e:
        err_msg = str(e) or f"{type(e).__name__} (no message)"
        log.error(f"Google AIO scrape failed: {err_msg}")
        return ScrapeResult(raw_response_text="", raw_html_payload="", error_log=err_msg)


async def scrape_google_ai_mode(query: str, proxy: dict | None = None) -> ScrapeResult:
    """Google AI Mode - same as AIO but with &udm=50 parameter."""
    config = ENGINES["google_ai_mode"]
    url = config["url_template"].format(query=quote_plus(query))

    try:
        camo_kwargs = {"headless": _HEADLESS, "humanize": True}
        if proxy:
            camo_kwargs["proxy"] = proxy
            camo_kwargs["geoip"] = True

        async def _page_fn(page):
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            previous_length = 0
            stable_ticks = 0
            for _ in range(30):
                body = await page.query_selector("body")
                text = (await body.inner_text()).strip() if body else ""
                if len(text) > 200 and len(text) == previous_length:
                    stable_ticks += 1
                    if stable_ticks >= 2:
                        break
                else:
                    stable_ticks = 0
                    previous_length = len(text)
                await page.wait_for_timeout(1000)

            body = await page.query_selector("body")
            raw_text = (await body.inner_text()).strip() if body else ""
            raw_html = (await body.inner_html()) if body else ""

            lines = raw_text.split("\n")
            filtered = []
            skip_prefixes = ("AI Mode", "All", "Images", "Videos", "News", "More",
                             "Search Results", "You said:", "Tools", "Shopping",
                             "Short videos", "Sign in", "Skip to",
                             "Accessibility help", "Accessibility", "Quick results from the web")
            query_lower = query.lower().strip()
            for line in lines:
                s = line.strip()
                if not s:
                    continue
                if s in skip_prefixes or s.startswith(skip_prefixes):
                    continue
                if len(s) < 5:
                    continue
                if s.lower() == query_lower:
                    continue
                filtered.append(s)
            clean_text = "\n".join(filtered)

            cited_urls = []
            links = await page.query_selector_all("a[href^='http']")
            for link in links:
                href = await link.get_attribute("href")
                if href and "google.com" not in href and "gstatic.com" not in href:
                    cited_urls.append(href)
            cited_urls = list(dict.fromkeys(cited_urls))

            return clean_text, raw_html, cited_urls

        raw_text, raw_html, cited_urls = await _run_browser_scrape(camo_kwargs, _page_fn, use_google_cookies=True)

        if _is_captcha_page(raw_text or raw_html or ""):
            log.warning(f"Google AI Mode captcha/bot detection triggered")
            return ScrapeResult(
                raw_response_text="",
                raw_html_payload=raw_html,
                error_log="Google captcha/bot detection - IP may be blocked",
            )

        if not raw_text:
            return ScrapeResult(
                raw_response_text="",
                raw_html_payload="",
                error_log="No AI Mode response found for this query",
            )

        _lower = raw_text.lower()
        if "something went wrong" in _lower or "content wasn't generated" in _lower:
            log.warning(f"Google AI Mode returned error page: {raw_text[:200]}")
            return ScrapeResult(
                raw_response_text="",
                raw_html_payload=raw_html,
                error_log=f"Google AI Mode error: {raw_text[:300]}",
            )

        log.info(f"Google AI Mode scraped: {len(raw_text)} chars, {len(cited_urls)} cited URLs")
        return ScrapeResult(raw_response_text=raw_text, raw_html_payload=raw_html, cited_urls=cited_urls)

    except Exception as e:
        err_msg = str(e) or f"{type(e).__name__} (no message)"
        log.error(f"Google AI Mode scrape failed: {err_msg}")
        return ScrapeResult(raw_response_text="", raw_html_payload="", error_log=err_msg)


async def scrape_perplexity(query: str, proxy: dict | None = None) -> ScrapeResult:
    config = ENGINES["perplexity"]

    try:
        camo_kwargs = {
            "headless": _HEADLESS,
            "humanize": True,
            "block_webrtc": True,
            "os": "windows",
        }
        if proxy:
            camo_kwargs["proxy"] = proxy
            camo_kwargs["geoip"] = True

        storage_state = await load_perplexity_storage_state()
        _input_error = None

        async def _page_fn(page):
            nonlocal _input_error

            # Load Perplexity cookies if available (avoids rate limits + CAPTCHA)
            if storage_state:
                cookies = storage_state.get("cookies", [])
                if cookies:
                    await page.context.add_cookies(cookies)
                    log.info(f"Loaded {len(cookies)} Perplexity cookies")
                for origin_data in storage_state.get("origins", []):
                    if "perplexity" in origin_data.get("origin", ""):
                        ls_items = origin_data.get("localStorage", [])
                        if ls_items:
                            ls_dict = {item["name"]: item["value"] for item in ls_items}
                            json_data = json.dumps(ls_dict)
                            await page.add_init_script(
                                f"try {{ var d = {json_data}; for (var k in d) localStorage.setItem(k, d[k]); }} catch(e) {{}}"
                            )

            await page.goto(config["url"], wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)

            # Dismiss cookie consent / modal overlays
            await page.evaluate("""() => {
                const dismiss_texts = ['Accept', 'Got it', 'Close', 'Dismiss', 'OK', 'Continue'];
                document.querySelectorAll('button, [role="button"]').forEach(el => {
                    const t = (el.textContent || '').trim();
                    if (dismiss_texts.some(d => t.includes(d)) && el.offsetParent !== null) {
                        try { el.click(); } catch(e) {}
                    }
                });
                document.querySelectorAll('[role="dialog"]').forEach(el => {
                    try { el.remove(); } catch(e) {}
                });
            }""")
            await page.wait_for_timeout(500)

            input_el = await find_text_input(page, ["textarea", "div[contenteditable='true']"])
            if not input_el:
                log.info("Perplexity: input not found on first try, waiting 5s and retrying...")
                await page.wait_for_timeout(5000)
                input_el = await find_text_input(page, ["textarea", "div[contenteditable='true']"], timeout=10000)
            if not input_el:
                page_text = await page.evaluate("() => document.body ? document.body.innerText.substring(0, 400) : ''")
                log.warning(f"Perplexity: input not found after retry. URL: {page.url}, page: {page_text[:300]}")
                _pt_lower = page_text.lower()
                if "proxy" in _pt_lower and ("refusing" in _pt_lower or "connection" in _pt_lower):
                    _input_error = "Proxy connection refused - proxy server is down or overloaded"
                elif "internal error" in _pt_lower or "return home" in _pt_lower:
                    _input_error = "Perplexity internal error - transient server error, retryable"
                elif "sign" in _pt_lower or "log in" in _pt_lower or "create" in _pt_lower:
                    _input_error = "Perplexity session expired - needs re-login"
                else:
                    _input_error = "Perplexity input field not found - UI may have changed"
                return "", "", []

            pre_snapshot = await snapshot_page_text(page)
            await type_and_submit(page, input_el, query)
            await page.wait_for_timeout(4000)

            combined, url_list = await wait_and_extract_response(
                page, pre_snapshot, max_wait=55, min_response_chars=80,
                primary_selectors=config["response_selectors"],
            )
            _perp_rate_signals = [
                "rate limit", "too many requests", "try again later",
                "sign up to continue", "create an account",
            ]
            _combined_lower = (combined or "").lower()
            if any(sig in _combined_lower for sig in _perp_rate_signals):
                _input_error = "limit_exhausted: Perplexity rate limit hit"
                return "", "", []

            combined = clean_scraped_text(combined, "perplexity")

            if not combined or len(combined) < 30:
                page_text = await page.evaluate("() => document.body ? document.body.innerText.substring(0, 3000) : ''")
                _pt_lower = (page_text or "").lower()
                if any(sig in _pt_lower for sig in _perp_rate_signals):
                    _input_error = "limit_exhausted: Perplexity rate limit hit"
                    return "", "", []

            return combined, "", url_list

        raw_text, raw_html, cited_urls = await _run_browser_scrape(camo_kwargs, _page_fn)

        if _input_error:
            return ScrapeResult(raw_response_text="", raw_html_payload="", error_log=_input_error)

        if not raw_text:
            return ScrapeResult(
                raw_response_text="",
                raw_html_payload="",
                error_log="Perplexity response container empty after timeout",
            )

        raw_text = clean_scraped_text(raw_text, "perplexity")
        log.info(f"Perplexity scraped: {len(raw_text)} chars, {len(cited_urls)} cited URLs")
        return ScrapeResult(raw_response_text=raw_text, raw_html_payload=raw_html, cited_urls=cited_urls)

    except Exception as e:
        err_msg = str(e) or f"{type(e).__name__} (no message)"
        log.error(f"Perplexity scrape failed: {err_msg}")
        return ScrapeResult(raw_response_text="", raw_html_payload="", error_log=err_msg)


async def scrape_chatgpt(query: str, proxy: dict | None = None) -> ScrapeResult:
    """ChatGPT via Camoufox with cookies + localStorage injection."""
    storage_state = await load_chatgpt_storage_state()
    if not storage_state:
        return ScrapeResult(
            raw_response_text="",
            raw_html_payload="",
            error_log="ChatGPT requires login - go to /chatgpt-login to authenticate",
        )

    cookies = storage_state.get("cookies", [])
    origins = storage_state.get("origins", [])

    try:
        camo_kwargs = {
            "headless": _HEADLESS,
            "humanize": True,
            "block_webrtc": True,
            "os": "windows",
        }
        if proxy:
            camo_kwargs["proxy"] = proxy
            camo_kwargs["geoip"] = True

        raw_text, raw_html, cited_urls, input_err = "", "", [], None

        async def _page_fn(page):
            nonlocal input_err

            page.on("pageerror", lambda _: None)
            await page.context.add_cookies(cookies)

            # Build localStorage injection script
            ls_items = []
            for origin_data in origins:
                if "chatgpt" in origin_data.get("origin", "") or "openai" in origin_data.get("origin", ""):
                    ls_items = origin_data.get("localStorage", [])
                    break

            if ls_items:
                ls_dict = {item["name"]: item["value"] for item in ls_items}
                json_data = json.dumps(ls_dict)
                script = f"try {{ var d = {json_data}; for (var k in d) localStorage.setItem(k, d[k]); }} catch(e) {{}}"
                await page.add_init_script(script)
                log.info(f"Prepared {len(ls_items)} localStorage items for ChatGPT")

            try:
                await page.goto("https://chatgpt.com", wait_until="domcontentloaded", timeout=45000)
            except Exception as nav_err:
                log.warning(f"ChatGPT navigation issue (continuing): {nav_err}")
            await page.wait_for_timeout(4000)

            # Dismiss any overlays (cookie consent, welcome modal, etc.)
            await page.evaluate("""() => {
                const dismiss_texts = ['Accept', 'Accept all', 'Got it', 'Okay', 'OK', 'Close', 'Dismiss', 'Stay logged out', 'Continue'];
                document.querySelectorAll('button, [role="button"]').forEach(el => {
                    const t = (el.textContent || '').trim();
                    if (dismiss_texts.some(d => t.includes(d)) && el.offsetParent !== null) {
                        try { el.click(); } catch(e) {}
                    }
                });
                // Remove modal overlays
                document.querySelectorAll('[role="dialog"], [data-testid*="modal"]').forEach(el => {
                    try { el.remove(); } catch(e) {}
                });
            }""")
            await page.wait_for_timeout(1000)

            url = page.url
            log.info(f"ChatGPT page URL: {url}")

            # Check for login redirect (auth.openai.com) or explicit login page
            if "auth.openai.com" in url or "auth0.openai.com" in url:
                input_err = "ChatGPT session expired - re-login at /chatgpt-login"
                return "", "", []

            input_el = await find_text_input(page, ["#prompt-textarea", "div[contenteditable='true']#prompt-textarea"], timeout=10000)
            if not input_el:
                log.info("ChatGPT: input not found on first try, waiting 5s and retrying...")
                await page.wait_for_timeout(5000)
                input_el = await find_text_input(page, ["#prompt-textarea", "div[contenteditable='true']#prompt-textarea"], timeout=10000)
            if not input_el:
                page_text = await page.evaluate("() => document.body ? document.body.innerText.substring(0, 400) : ''")
                if "proxy" in page_text.lower() and ("refusing" in page_text.lower() or "connection" in page_text.lower()):
                    input_err = "Proxy connection refused - proxy server is down or overloaded"
                else:
                    input_err = "ChatGPT input not found - UI may have changed or session expired"
                return "", "", []

            pre_snapshot = await snapshot_page_text(page)
            await type_and_submit(page, input_el, query)
            await page.wait_for_timeout(4000)

            combined, url_list = await wait_and_extract_response(
                page, pre_snapshot, max_wait=55, min_response_chars=80,
                primary_selectors=[
                    "div[data-message-author-role='assistant']",
                    "div.markdown",
                    "div.prose",
                    "div[class*='markdown']",
                ],
            )

            _chatgpt_rate_signals = [
                "usage cap", "message cap", "limit reached",
                "you've reached", "too many messages",
                "upgrade to plus", "rate limit",
            ]
            _comb_lower = (combined or "").lower()
            if any(sig in _comb_lower for sig in _chatgpt_rate_signals):
                input_err = "limit_exhausted: ChatGPT rate limit hit"
                return "", "", []

            combined = clean_scraped_text(combined, "chatgpt")

            html = ""
            try:
                body_el = await page.query_selector("body")
                if body_el:
                    html = await body_el.inner_html()
            except Exception:
                pass

            return combined, html, url_list

        raw_text, raw_html, cited_urls = await _run_browser_scrape(camo_kwargs, _page_fn)

        if input_err:
            return ScrapeResult(raw_response_text="", raw_html_payload="", error_log=input_err)

        if not raw_text:
            return ScrapeResult(
                raw_response_text="",
                raw_html_payload="",
                error_log="ChatGPT returned empty response after timeout",
            )

        raw_text = clean_scraped_text(raw_text, "chatgpt")
        log.info(f"ChatGPT scraped: {len(raw_text)} chars, {len(cited_urls)} cited URLs")
        return ScrapeResult(raw_response_text=raw_text, raw_html_payload=raw_html, cited_urls=cited_urls)

    except Exception as e:
        err_msg = str(e) or f"{type(e).__name__} (no message)"
        log.error(f"ChatGPT scrape failed: {err_msg}")
        return ScrapeResult(raw_response_text="", raw_html_payload="", error_log=err_msg)


async def scrape_gemini_web(query: str, proxy: dict | None = None) -> ScrapeResult:
    """Gemini via browser with Google cookies."""
    storage_state = await load_gemini_storage_state()
    if not storage_state:
        return ScrapeResult(
            raw_response_text="",
            raw_html_payload="",
            error_log="Gemini requires login - go to /gemini-login to authenticate",
        )

    cookies = storage_state.get("cookies", [])
    origins = storage_state.get("origins", [])

    try:
        camo_kwargs = {
            "headless": _HEADLESS,
            "humanize": True,
            "block_webrtc": True,
            "os": "windows",
        }
        if proxy:
            camo_kwargs["proxy"] = proxy
            camo_kwargs["geoip"] = True

        input_err = None

        async def _page_fn(page):
            nonlocal input_err
            page.on("pageerror", lambda _: None)
            await page.context.add_cookies(cookies)

            for origin_data in origins:
                if "google" in origin_data.get("origin", ""):
                    ls_items = origin_data.get("localStorage", [])
                    if ls_items:
                        ls_dict = {item["name"]: item["value"] for item in ls_items}
                        json_data = json.dumps(ls_dict)
                        await page.add_init_script(
                            f"try {{ var d = {json_data}; for (var k in d) localStorage.setItem(k, d[k]); }} catch(e) {{}}"
                        )

            try:
                await page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=45000)
            except Exception as nav_err:
                log.warning(f"Gemini navigation issue (continuing): {nav_err}")
            await page.wait_for_timeout(3000)

            url = page.url
            if "accounts.google.com" in url:
                input_err = "Gemini session expired - re-login at /gemini-login"
                return "", "", []

            gemini_input_selectors = [
                "rich-textarea .ql-editor",
                "div.ql-editor[contenteditable='true']",
                "rich-textarea",
                "div[contenteditable='true'][aria-label*='prompt']",
                "div[contenteditable='true'][aria-label*='Enter']",
                "textarea[aria-label*='prompt']",
                "div.input-area [contenteditable='true']",
                "div[contenteditable='true'][role='textbox']",
            ]
            input_el = await find_text_input(page, gemini_input_selectors)
            if not input_el:
                log.info("Gemini: input not found on first try, waiting 5s and retrying...")
                await page.wait_for_timeout(5000)
                input_el = await find_text_input(page, gemini_input_selectors, timeout=10000)
            if not input_el:
                page_text = await page.evaluate("() => document.body ? document.body.innerText.substring(0, 400) : 'NO BODY'")
                log.warning(f"Gemini: input not found after retry. URL: {page.url}, page: {page_text[:300]}")
                if "proxy" in page_text.lower() and ("refusing" in page_text.lower() or "connection" in page_text.lower()):
                    input_err = "Proxy connection refused - proxy server is down or overloaded"
                else:
                    input_err = "Gemini input field not found - UI may have changed"
                return "", "", []

            pre_snapshot = await snapshot_page_text(page)
            await type_and_submit(page, input_el, query)
            await page.wait_for_timeout(4000)

            combined, url_list = await wait_and_extract_response(
                page, pre_snapshot, max_wait=55, min_response_chars=80,
                primary_selectors=[
                    "div.model-response-text",
                    "div.response-container",
                    "div.markdown",
                    "message-content",
                    "div[class*='response']",
                    "div[class*='message-content']",
                    "div.conversation-container",
                ],
            )

            _gemini_rate_signals = [
                "usage limit", "rate limit", "too many requests",
                "quota exceeded", "resource_exhausted",
            ]
            _comb_lower = (combined or "").lower()
            if any(sig in _comb_lower for sig in _gemini_rate_signals):
                input_err = "limit_exhausted: Gemini rate limit hit"
                return "", "", []

            combined = clean_scraped_text(combined, "gemini")

            html = ""
            try:
                body_el = await page.query_selector("body")
                if body_el:
                    html = await body_el.inner_html()
            except Exception:
                pass

            return combined, html, url_list

        raw_text, raw_html, cited_urls = await _run_browser_scrape(camo_kwargs, _page_fn)

        if input_err:
            return ScrapeResult(raw_response_text="", raw_html_payload="", error_log=input_err)

        if not raw_text:
            return ScrapeResult(
                raw_response_text="",
                raw_html_payload="",
                error_log="Gemini returned empty response after timeout",
            )

        raw_text = clean_scraped_text(raw_text, "gemini")
        log.info(f"Gemini Web scraped: {len(raw_text)} chars, {len(cited_urls)} cited URLs")
        return ScrapeResult(raw_response_text=raw_text, raw_html_payload=raw_html, cited_urls=cited_urls)

    except Exception as e:
        err_msg = str(e) or f"{type(e).__name__} (no message)"
        log.error(f"Gemini Web scrape failed: {err_msg}")
        return ScrapeResult(raw_response_text="", raw_html_payload="", error_log=err_msg)


async def scrape_claude(query: str, proxy: dict | None = None) -> ScrapeResult:
    """Claude.ai via Camoufox with cookies + localStorage injection."""
    storage_state = await load_claude_storage_state()
    if not storage_state:
        return ScrapeResult(
            raw_response_text="",
            raw_html_payload="",
            error_log="Claude requires login - go to /claude-login to authenticate",
        )

    cookies = storage_state.get("cookies", [])
    origins = storage_state.get("origins", [])

    try:
        camo_kwargs = {
            "headless": _HEADLESS,
            "humanize": True,
            "block_webrtc": True,
            "os": "windows",
        }
        if proxy:
            camo_kwargs["proxy"] = proxy
            camo_kwargs["geoip"] = True

        input_err = None

        async def _page_fn(page):
            nonlocal input_err
            page.on("pageerror", lambda _: None)
            await page.context.add_cookies(cookies)

            for origin_data in origins:
                if "claude" in origin_data.get("origin", "") or "anthropic" in origin_data.get("origin", ""):
                    ls_items = origin_data.get("localStorage", [])
                    if ls_items:
                        ls_dict = {item["name"]: item["value"] for item in ls_items}
                        json_data = json.dumps(ls_dict)
                        await page.add_init_script(
                            f"try {{ var d = {json_data}; for (var k in d) localStorage.setItem(k, d[k]); }} catch(e) {{}}"
                        )

            try:
                await page.goto("https://claude.ai/new", wait_until="domcontentloaded", timeout=45000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
            except Exception as nav_err:
                log.warning(f"Claude navigation issue (continuing): {nav_err}")
            await page.wait_for_timeout(3000)

            url = page.url
            if "/login" in url or "accounts.google.com" in url:
                input_err = "Claude session expired - re-login at /claude-login"
                return "", "", []

            # Dismiss any overlays (cookie consent, welcome modal)
            await page.evaluate("""() => {
                const dismiss_texts = ['Accept', 'Got it', 'Close', 'Dismiss', 'OK', 'Continue', 'Skip'];
                document.querySelectorAll('button, [role="button"]').forEach(el => {
                    const t = (el.textContent || '').trim();
                    if (dismiss_texts.some(d => t.includes(d)) && el.offsetParent !== null) {
                        try { el.click(); } catch(e) {}
                    }
                });
                document.querySelectorAll('[role="dialog"]').forEach(el => {
                    try { el.remove(); } catch(e) {}
                });
            }""")
            await page.wait_for_timeout(1000)

            claude_input_selectors = [
                "div.ProseMirror[contenteditable='true']",
                "div[contenteditable='true']",
                "div.ProseMirror",
                "div[contenteditable='true'][role='textbox']",
                "fieldset div[contenteditable='true']",
                "div[enterkeyhint='enter'][contenteditable='true']",
            ]
            input_el = await find_text_input(page, claude_input_selectors, timeout=10000)
            if not input_el:
                log.info("Claude: input not found on first try, waiting 5s and retrying...")
                await page.wait_for_timeout(5000)
                input_el = await find_text_input(page, claude_input_selectors, timeout=10000)
            if not input_el:
                page_text = await page.evaluate("() => document.body ? document.body.innerText.substring(0, 400) : 'NO BODY'")
                log.warning(f"Claude: input not found after retry. URL: {page.url}, page: {page_text[:300]}")
                if "proxy" in page_text.lower() and ("refusing" in page_text.lower() or "connection" in page_text.lower()):
                    input_err = "Proxy connection refused - proxy server is down or overloaded"
                else:
                    input_err = "Claude input field not found - UI may have changed"
                return "", "", []

            pre_snapshot = await snapshot_page_text(page)
            await type_and_submit(page, input_el, query)
            log.info(f"Claude: query submitted, waiting for response...")
            await page.wait_for_timeout(4000)

            _claude_primary_selectors = [
                    "div[data-is-streaming]",
                    "div.font-claude-message",
                    "div.prose",
                    "div.markdown",
            ]
            combined, url_list = await wait_and_extract_response(
                page, pre_snapshot, max_wait=55, min_response_chars=80,
                primary_selectors=_claude_primary_selectors,
            )

            _CLAUDE_NOISE_MARKERS = [
                "claude is responding", "searching the web",
                "want to be notified", "notify me when",
                "is thinking", "is writing",
            ]
            _combined_lower = (combined or "").lower()
            if any(marker in _combined_lower for marker in _CLAUDE_NOISE_MARKERS):
                log.info("Claude: detected loading-state noise in response, waiting 15s for real response...")
                await page.wait_for_timeout(15000)
                combined, url_list = await wait_and_extract_response(
                    page, pre_snapshot, max_wait=55, min_response_chars=80,
                    primary_selectors=_claude_primary_selectors,
                )
                _combined_lower = (combined or "").lower()
                if any(marker in _combined_lower for marker in _CLAUDE_NOISE_MARKERS):
                    log.warning("Claude: still showing loading state after extended wait")
                    input_err = "Claude response stuck in loading state"
                    return "", "", []

            _CLAUDE_CLARIFY_MARKERS = [
                "could you give me", "can you clarify", "more context",
                "could you provide", "what specifically", "which store",
                "which brand", "are you looking",
            ]
            if combined and len(combined) < 400 and any(m in _combined_lower for m in _CLAUDE_CLARIFY_MARKERS):
                log.info("Claude asked clarifying question instead of answering, not a real response")
                input_err = "Claude asked clarifying question - response not useful"
                return "", "", []

            _rate_limit_signals = [
                "out of free messages", "message limit", "hit your",
                "limits will reset", "upgrade for higher",
                "5-hour message limit", "you've hit your limit",
            ]
            if any(sig in _combined_lower for sig in _rate_limit_signals):
                input_err = "limit_exhausted: Claude free plan rate limit hit"
                return "", "", []

            page_text = await page.evaluate("() => document.body ? document.body.innerText.substring(0, 3000) : ''")
            _pt_lower = (page_text or "").lower()
            if any(sig in _pt_lower for sig in _rate_limit_signals):
                input_err = "limit_exhausted: Claude free plan rate limit hit"
                return "", "", []

            combined = clean_scraped_text(combined, "claude")

            html = ""
            try:
                body_el = await page.query_selector("body")
                if body_el:
                    html = await body_el.inner_html()
            except Exception:
                pass

            return combined, html, url_list

        raw_text, raw_html, cited_urls = await _run_browser_scrape(camo_kwargs, _page_fn)

        if input_err:
            return ScrapeResult(raw_response_text="", raw_html_payload="", error_log=input_err)

        if not raw_text:
            return ScrapeResult(
                raw_response_text="",
                raw_html_payload="",
                error_log="Claude returned empty response after timeout",
            )

        raw_text = clean_scraped_text(raw_text, "claude")
        log.info(f"Claude scraped: {len(raw_text)} chars, {len(cited_urls)} cited URLs")
        return ScrapeResult(raw_response_text=raw_text, raw_html_payload=raw_html, cited_urls=cited_urls)

    except Exception as e:
        err_msg = str(e) or f"{type(e).__name__} (no message)"
        log.error(f"Claude scrape failed: {err_msg}")
        return ScrapeResult(raw_response_text="", raw_html_payload="", error_log=err_msg)


_engine_proxy_fail_count: dict[str, int] = {}
_PROXY_FAIL_DIRECT_THRESHOLD = 3


# ── IP Rate Tracker for Google scraping ─────────────────────
# Distributes queries across direct IP + proxy IPs to avoid burnout.
# Tracks per-IP: request count, last-used time, captcha penalties.


_IP_MIN_GAP_SECONDS = 45
_IP_CAPTCHA_PENALTY_PROXY = 1800
_IP_CAPTCHA_PENALTY_DIRECT = 600
_IP_DAILY_MAX = 400

_ip_last_used: dict[str, float] = {}
_ip_daily_count: dict[str, int] = {}
_ip_daily_reset: float = 0
_ip_captcha_until: dict[str, float] = {}

_DIRECT_IP_KEY = "direct"


def _reset_daily_counts_if_needed():
    global _ip_daily_reset
    now = time.time()
    if now - _ip_daily_reset > 86400:
        _ip_daily_count.clear()
        _ip_daily_reset = now


def _ip_available(ip_key: str) -> bool:
    """Check if IP is available (not in captcha penalty, not over daily limit, gap elapsed)."""
    now = time.time()
    if _ip_captcha_until.get(ip_key, 0) > now:
        return False
    _reset_daily_counts_if_needed()
    if _ip_daily_count.get(ip_key, 0) >= _IP_DAILY_MAX:
        return False
    return True


def _ip_wait_time(ip_key: str) -> float:
    """Seconds until this IP can be used again (0 = ready now)."""
    now = time.time()
    captcha_wait = max(0, _ip_captcha_until.get(ip_key, 0) - now)
    if captcha_wait > 0:
        return captcha_wait
    gap_wait = max(0, (_ip_last_used.get(ip_key, 0) + _IP_MIN_GAP_SECONDS) - now)
    return gap_wait


def _ip_record_use(ip_key: str):
    """Record that this IP was just used."""
    _reset_daily_counts_if_needed()
    _ip_last_used[ip_key] = time.time()
    _ip_daily_count[ip_key] = _ip_daily_count.get(ip_key, 0) + 1


def _ip_record_captcha(ip_key: str):
    """Penalize IP after captcha — direct gets 10min, proxies get 30min."""
    penalty = _IP_CAPTCHA_PENALTY_DIRECT if ip_key == _DIRECT_IP_KEY else _IP_CAPTCHA_PENALTY_PROXY
    _ip_captcha_until[ip_key] = time.time() + penalty
    log.warning(f"[IP-tracker] {ip_key} captcha'd — penalized for {penalty}s")


def get_ip_stats() -> dict:
    """Return current IP usage stats for monitoring."""
    _reset_daily_counts_if_needed()
    now = time.time()
    stats = {}
    all_keys = set(_ip_daily_count.keys()) | set(_ip_last_used.keys()) | {_DIRECT_IP_KEY}
    for k in all_keys:
        stats[k] = {
            "daily_count": _ip_daily_count.get(k, 0),
            "last_used_ago": round(now - _ip_last_used[k], 1) if k in _ip_last_used else None,
            "captcha_remaining": max(0, round(_ip_captcha_until.get(k, 0) - now, 0)),
            "available": _ip_available(k),
            "wait_seconds": round(_ip_wait_time(k), 1),
        }
    return stats


async def run_scrape(engine_name: str, query: str, retry_count: int = 0) -> ScrapeResult:
    """Main entry - routes to correct scraper with proxy rotation and direct fallback.

    Google AIO/AI Mode get aggressive multi-strategy retry:
      1. Try with proxy
      2. On captcha/fail → rotate proxy and retry
      3. On continued fail → try direct (no proxy)
      4. Refresh Google cookies and retry with proxy
    """
    scrapers = {
        "google_aio": scrape_google_aio,
        "google_ai_mode": scrape_google_ai_mode,
        "perplexity": scrape_perplexity,
        "gemini": scrape_gemini_web,
        "chatgpt": scrape_chatgpt,
        "claude": scrape_claude,
    }

    scraper_fn = scrapers.get(engine_name)
    if not scraper_fn:
        return ScrapeResult(
            raw_response_text="",
            raw_html_payload="",
            error_log=f"Unknown engine: {engine_name}",
        )

    if engine_name in ("google_aio", "google_ai_mode"):
        return await _run_google_scrape_aggressive(engine_name, query, scraper_fn, retry_count)

    proxy_fails = _engine_proxy_fail_count.get(engine_name, 0)
    if proxy_fails >= _PROXY_FAIL_DIRECT_THRESHOLD:
        proxy = None
        if proxy_fails == _PROXY_FAIL_DIRECT_THRESHOLD:
            log.warning(f"[{engine_name}] {proxy_fails} proxy failures - falling back to direct connection")
    else:
        proxy = await _get_proxy(engine_name)

    result = await scraper_fn(query, proxy)

    if result.error_log and _is_proxy_error(result.error_log):
        _engine_proxy_fail_count[engine_name] = _engine_proxy_fail_count.get(engine_name, 0) + 1
        fails = _engine_proxy_fail_count[engine_name]
        if fails < _PROXY_FAIL_DIRECT_THRESHOLD:
            rotate_engine_proxy(engine_name)
            log.warning(f"[{engine_name}] Proxy error detected ({fails}/{_PROXY_FAIL_DIRECT_THRESHOLD}), rotated proxy")
    elif result.raw_response_text:
        _engine_proxy_fail_count[engine_name] = 0

    return result


async def _run_google_scrape_aggressive(engine_name: str, query: str, scraper_fn, retry_count: int) -> ScrapeResult:
    """Smart IP-distributed scraping for Google AIO/AI Mode.

    Distributes queries across direct IP + proxy IPs using round-robin.
    Each IP gets minimum 60s gap between requests, max 300/day.
    Captcha'd IPs get 30min penalty. Ensures 1000+ queries/day capacity
    across 4 IPs without burning any single IP.
    """
    ip_slots = []

    ip_slots.append((_DIRECT_IP_KEY, None))

    proxies_raw = await _get_cached_proxy_list()
    for p in proxies_raw:
        config = _build_proxy_config(p)
        ip_key = config["server"].split("//")[1] if "//" in config["server"] else config["server"]
        ip_slots.append((ip_key, config))

    available_slots = [(k, p) for k, p in ip_slots if _ip_available(k)]

    if not available_slots:
        wait_times = {k: _ip_wait_time(k) for k, _ in ip_slots}
        shortest_key = min(wait_times, key=wait_times.get)
        shortest_wait = wait_times[shortest_key]
        if shortest_wait <= 120:
            log.info(f"[{engine_name}] All IPs busy, waiting {shortest_wait:.0f}s for {shortest_key}")
            await asyncio.sleep(shortest_wait + 1)
            available_slots = [(k, p) for k, p in ip_slots if _ip_available(k)]

    if not available_slots:
        log.warning(f"[{engine_name}] No IPs available after wait — all at daily limit or captcha'd")
        return ScrapeResult(
            raw_response_text="",
            raw_html_payload="",
            error_log="All IPs exhausted (daily limit or captcha penalty)",
        )

    available_slots.sort(key=lambda x: _ip_wait_time(x[0]))

    last_result = None
    tried = 0
    max_tries = min(len(available_slots) + 1, 4)

    for ip_key, proxy_config in available_slots[:max_tries]:
        wait = _ip_wait_time(ip_key)
        if wait > 0:
            log.info(f"[{engine_name}] Waiting {wait:.0f}s for IP {ip_key} gap")
            await asyncio.sleep(wait)

        strategy_label = "direct" if ip_key == _DIRECT_IP_KEY else f"proxy_{ip_key}"
        log.info(f"[{engine_name}] IP '{strategy_label}' ({_ip_daily_count.get(ip_key, 0)}/{_IP_DAILY_MAX} today) for: {query[:50]}")

        _ip_record_use(ip_key)
        tried += 1

        result = await scraper_fn(query, proxy_config)

        if result.raw_response_text and not result.error_log:
            _engine_proxy_fail_count[engine_name] = 0
            log.info(f"[{engine_name}] IP '{strategy_label}' succeeded: {len(result.raw_response_text)} chars")
            return result

        if result.raw_response_text and result.raw_response_text.startswith("[No AI Overview]"):
            log.info(f"[{engine_name}] No AIO for this query (confirmed via '{strategy_label}')")
            return result

        last_result = result
        err = (result.error_log or "").lower()

        if "captcha" in err or "bot detection" in err:
            _ip_record_captcha(ip_key)
            log.warning(f"[{engine_name}] Captcha on '{strategy_label}' — penalized 30min, trying next IP")
            continue

        if _is_proxy_error(result.error_log or ""):
            log.warning(f"[{engine_name}] Proxy error on '{strategy_label}', trying next")
            continue

        log.warning(f"[{engine_name}] IP '{strategy_label}' failed: {result.error_log}")
        await asyncio.sleep(1)

    if tried == max_tries and last_result and last_result.error_log:
        direct_wait = _ip_wait_time(_DIRECT_IP_KEY)
        if direct_wait <= 30 and _ip_available(_DIRECT_IP_KEY):
            log.info(f"[{engine_name}] Last resort: cookie refresh + direct retry")
            try:
                from app.routes.auth import refresh_google_cookies
                await refresh_google_cookies()
            except Exception as e:
                log.warning(f"[{engine_name}] Cookie refresh failed: {e}")
            if direct_wait > 0:
                await asyncio.sleep(direct_wait)
            _ip_record_use(_DIRECT_IP_KEY)
            result = await scraper_fn(query, None)
            if result.raw_response_text and not result.error_log:
                return result
            last_result = result

    log.error(f"[{engine_name}] All {tried} IPs exhausted for: {query[:60]}")
    return last_result or ScrapeResult(
        raw_response_text="",
        raw_html_payload="",
        error_log=f"All {tried} IPs exhausted for Google scrape",
    )


def _is_proxy_error(error_log: str) -> bool:
    err = error_log.lower()
    if "proxy" in err and ("refusing" in err or "connection" in err):
        return True
    if "sec_error_unknown_issuer" in err:
        return True
    if "ssl" in err and ("certificate" in err or "interception" in err):
        return True
    if "ns_error_proxy" in err:
        return True
    return False


def reset_proxy_fails(engine_name: str = ""):
    """Reset proxy fail counter (called when proxy pool refreshes)."""
    if engine_name:
        _engine_proxy_fail_count.pop(engine_name, None)
    else:
        _engine_proxy_fail_count.clear()
