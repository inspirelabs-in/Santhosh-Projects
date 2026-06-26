import re
import sys
import random
import logging
import asyncio
import json
import time
import httpx
from urllib.parse import quote_plus

from camoufox.async_api import AsyncCamoufox
from app.models import ScrapeResult
from app.config import get_settings
from app.engines import ALL_ENGINES, VISIBLE_ENGINES, AUTH_ENGINES, UNAUTH_ENGINES
from app.routes.auth import (
    load_google_cookies, load_google_storage_state,
    load_chatgpt_storage_state,
    load_gemini_storage_state, load_claude_storage_state,
    load_perplexity_storage_state,
)
from app.agent.smart_extract import (
    find_text_input, snapshot_page_text, type_and_submit,
    wait_and_extract_response, clean_scraped_text,
)

log = logging.getLogger("geo.scraper")

_HEADLESS = "virtual" if sys.platform != "win32" else True

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


# ── Proxy caching ─────────────────────────────────────────────

_engine_proxy_cache: dict[str, dict] = {}
_engine_proxy_cache_ts: dict[str, float] = {}

_serp_proxy_cache: dict | None = None
_serp_proxy_cache_ts: float = 0

_proxy_list_cache: list = []
_proxy_list_cache_ts: float = 0

_engine_proxy_fail_count: dict[str, int] = {}
_PROXY_FAIL_DIRECT_THRESHOLD = 3


# ── Captcha / validation signals ─────────────────────────────

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
    matched = [sig for sig in _CAPTCHA_SIGNALS if sig in lower]
    if matched:
        log.debug(f"Captcha signals matched: {matched} in text ({len(text)} chars): {text[:200]}")
    return bool(matched)


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
    "sign in", "you said",
]

_HOME_PAGE_FINGERPRINTS = {
    "gemini": [
        "about gemini", "get gemini app", "subscriptions", "for business",
        "gemini is ai and can make mistakes",
    ],
    "chatgpt": [
        "write a whatsapp message", "help me prepare for my exams",
        "turn photo into profile pic", "create a study plan",
        "summarize this article", "plan a trip",
    ],
    "claude": [
        "start a new chat", "how can i help you today",
    ],
    "perplexity": [
        "trending searches", "popular searches",
    ],
}


_RATE_LIMIT_VALIDATE_SIGNALS = [
    "out of free messages", "message limit", "hit your limit",
    "limits will reset", "upgrade for higher limits",
    "5-hour message limit", "you've hit your limit",
    "rate limit", "too many requests", "usage limit",
    "limit exhausted",
]


_QUERY_STOPWORDS = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all", "can",
    "had", "her", "was", "one", "our", "out", "has", "its", "let",
    "any", "few", "get", "got", "him", "his", "may", "new", "now",
    "old", "see", "way", "who", "did", "use", "say", "she", "two",
    "how", "top", "too", "own", "try", "set", "put", "yet", "run",
    "what", "which", "where", "when", "who", "whom", "whose", "how",
    "why", "does", "will", "would", "could", "should", "shall", "might",
    "that", "this", "these", "those", "there", "here",
    "with", "from", "into", "about", "between", "through", "during",
    "before", "after", "above", "below", "under", "over", "around",
    "best", "good", "most", "more", "very", "really", "much",
    "some", "many", "each", "every", "other", "also", "just", "only",
    "been", "being", "have", "having", "were", "your", "their", "they",
    "them", "than", "then", "both", "same", "such", "like", "well",
    "make", "made", "find", "give", "tell", "know", "think", "want",
    "need", "help", "come", "take", "keep", "still", "show",
    "available", "today", "currently", "right",
})


def _extract_content_words(query: str, engine_name: str) -> list[str]:
    """Extract content words from query — strips stopwords and engine names."""
    engine_aliases = {engine_name, engine_name.replace("_", " ")}
    if engine_name == "google_aio":
        engine_aliases.update({"google", "aio"})
    elif engine_name == "google_ai_mode":
        engine_aliases.update({"google", "mode"})
    words = []
    for w in re.findall(r"[a-z0-9]+", query.lower()):
        if len(w) < 3:
            continue
        if w in _QUERY_STOPWORDS:
            continue
        if w in engine_aliases:
            continue
        words.append(w)
    return words


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
        fingerprints = _HOME_PAGE_FINGERPRINTS.get(engine_name, [])
        fp_hits = sum(1 for fp in fingerprints if fp in lower)
        if fp_hits >= 2:
            log.warning(f"[{engine_name}] Home page fingerprint detected ({fp_hits} hits), rejecting: {text[:100]}")
            return "ui_noise"

        noise_hits = sum(1 for sig in _UI_NOISE_SIGNALS if sig in lower)
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        short_lines = sum(1 for l in lines if len(l) < 30)
        if noise_hits >= 2 and len(text) < 500:
            log.warning(f"[{engine_name}] UI noise detected ({noise_hits} signals), rejecting: {text[:100]}")
            return "ui_noise"
        if len(lines) > 5 and short_lines / len(lines) > 0.8 and noise_hits >= 1:
            log.warning(f"[{engine_name}] Mostly short UI noise lines, rejecting: {text[:100]}")
            return "ui_noise"

    content_words = _extract_content_words(query, engine_name)
    if not content_words:
        return None
    matches = sum(1 for w in content_words if w in lower)
    relevance = matches / len(content_words)

    if engine_name in ("chatgpt", "claude", "gemini", "perplexity"):
        if relevance == 0 and len(text) < 600:
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            short = sum(1 for l in lines if len(l) < 50)
            if len(lines) >= 3 and short / len(lines) > 0.7:
                log.warning(f"[{engine_name}] Zero relevance + short disconnected lines, rejecting: {text[:100]}")
                return "ui_noise"
        if relevance < 0.15 and len(text) > 100:
            log.warning(f"[{engine_name}] Low relevance ({relevance:.0%}), rejecting: {text[:100]}")
            return "ui_noise"
    elif engine_name in ("google_aio", "google_ai_mode"):
        if relevance < 0.15 and len(text) > 100:
            log.warning(f"[{engine_name}] Low relevance ({relevance:.0%}) for query: {query[:60]}")
    return None


# ── Browser concurrency / login gate ─────────────────────────

_browser_sem: asyncio.Semaphore | None = None
_login_gate: asyncio.Event | None = None

def _get_browser_sem() -> asyncio.Semaphore:
    global _browser_sem
    if _browser_sem is None:
        _browser_sem = asyncio.Semaphore(3)
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


# ── Proxy helpers ─────────────────────────────────────────────

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


# ── Browser scrape runner ─────────────────────────────────────

_BROWSER_SCRAPE_TIMEOUT = 120

async def _run_browser_scrape(camo_kwargs: dict, page_fn, use_google_cookies: bool = False, timeout_override: int | None = None) -> tuple[str, str, list[str]]:
    """Run a browser scrape with global concurrency limit, hard timeout, and error handling."""
    t = timeout_override or _BROWSER_SCRAPE_TIMEOUT
    try:
        return await asyncio.wait_for(
            _run_browser_scrape_inner(camo_kwargs, page_fn, use_google_cookies, timeout_override=t),
            timeout=t + 120,
        )
    except asyncio.TimeoutError:
        log.error(f"Browser scrape hard-killed after {t + 120}s (including semaphore wait)")
        return "", "", []


async def _run_browser_scrape_inner(camo_kwargs: dict, page_fn, use_google_cookies: bool = False, timeout_override: int | None = None) -> tuple[str, str, list[str]]:
    """Inner browser scrape with concurrency limit and automatic fallback on NotImplementedError."""
    raw_text, raw_html, cited_urls = "", "", []
    await _get_login_gate().wait()
    sem = _get_browser_sem()

    _t = timeout_override or _BROWSER_SCRAPE_TIMEOUT

    async def _attempt(kwargs: dict) -> tuple[str, str, list[str]]:
        async with asyncio.timeout(_t):
            _result = ("", "", [])
            try:
                async with AsyncCamoufox(**kwargs) as browser:
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
                        state = await load_google_storage_state()
                        if state and isinstance(state, dict):
                            cookies = state.get("cookies", [])
                            if cookies:
                                await page.context.add_cookies(cookies)
                            origins = state.get("origins", [])
                            for origin_data in origins:
                                ls_items = origin_data.get("localStorage", [])
                                if ls_items:
                                    ls_dict = {item["name"]: item["value"] for item in ls_items}
                                    json_data = json.dumps(ls_dict)
                                    await page.add_init_script(
                                        f"try {{ var d = {json_data}; for (var k in d) localStorage.setItem(k, d[k]); }} catch(e) {{}}"
                                    )
                            log.info(f"Loaded {len(cookies)} cookies + {sum(len(o.get('localStorage',[])) for o in origins)} localStorage items")
                    _result = await page_fn(page)
            except Exception as close_err:
                close_str = str(close_err)
                if _result[0] and ("Connection closed" in close_str or "has been closed" in close_str
                                   or "reading from the driver" in close_str):
                    log.debug(f"Browser crashed during close but data was captured ({len(_result[0])} chars)")
                else:
                    raise
            return _result

    async with sem:
        try:
            raw_text, raw_html, cited_urls = await _attempt(camo_kwargs)
        except NotImplementedError:
            fallback_kwargs = {k: v for k, v in camo_kwargs.items()
                               if k not in ("humanize", "block_webrtc", "geoip")}
            log.warning(f"NotImplementedError from Camoufox — retrying without humanize/block_webrtc/geoip")
            try:
                raw_text, raw_html, cited_urls = await _attempt(fallback_kwargs)
            except NotImplementedError:
                log.error("NotImplementedError persists even with simplified kwargs")
                raise
        except TimeoutError:
            log.error(f"Browser scrape timed out after {_BROWSER_SCRAPE_TIMEOUT}s (inside semaphore)")
            raise
        except Exception as e:
            err = str(e)
            if "WriteUnixTransport" in err or "handler is closed" in err or "has been closed" in err:
                log.debug("Ignoring Browser.close transport error (data already captured)")
            elif "Cannot read properties of undefined" in err or "Node.js" in err:
                log.debug("Ignoring Playwright internal page error (non-fatal)")
            elif "Target page, context or browser has been closed" in err:
                log.debug("Browser closed mid-scrape (non-fatal)")
            elif "Connection closed" in err and ("reading from the driver" in err or "Browser.close" in err):
                log.debug(f"Browser process crashed during close (Playwright bug): {err[:120]}")
            elif raw_text:
                log.debug(f"Browser close error after data capture: {err}")
            else:
                import traceback
                log.error(f"Browser scrape exception: {type(e).__name__}: {e}\n{''.join(traceback.format_tb(e.__traceback__))}")
                raise
    return raw_text, raw_html, cited_urls


def _is_proxy_error(error_log: str) -> bool:
    err = error_log.lower()
    if "reading from the driver" in err or "browser.close" in err:
        return False
    if "proxy" in err and ("refusing" in err or "connection" in err or "failed" in err or "timeout" in err):
        return True
    if "sec_error_unknown_issuer" in err:
        return True
    if "ssl" in err and ("certificate" in err or "interception" in err or "handshake" in err):
        return True
    if "ns_error_proxy" in err:
        return True
    if "err_tunnel_connection_failed" in err:
        return True
    if "connection" in err and ("refused" in err or "reset" in err or "closed" in err):
        return True
    return False


def reset_proxy_fails(engine_name: str = ""):
    """Reset proxy fail counter (called when proxy pool refreshes)."""
    if engine_name:
        _engine_proxy_fail_count.pop(engine_name, None)
    else:
        _engine_proxy_fail_count.clear()


# ── Engine-level captcha cooldown ─────────────────────────────

_ENGINE_CAPTCHA_COOLDOWN = 1800
_ENGINE_CAPTCHA_THRESHOLD = 3
_ENGINE_CAPTCHA_WINDOW = 600

_engine_captcha_times: dict[str, list[float]] = {}
_engine_cooldown_until: dict[str, float] = {}


def _engine_record_captcha(engine_name: str):
    """Record a captcha event for this engine. Triggers global cooldown after threshold."""
    now = time.time()
    times = _engine_captcha_times.setdefault(engine_name, [])
    times.append(now)
    cutoff = now - _ENGINE_CAPTCHA_WINDOW
    _engine_captcha_times[engine_name] = [t for t in times if t > cutoff]

    if len(_engine_captcha_times[engine_name]) >= _ENGINE_CAPTCHA_THRESHOLD:
        _engine_cooldown_until[engine_name] = now + _ENGINE_CAPTCHA_COOLDOWN
        log.warning(
            f"[{engine_name}] {len(_engine_captcha_times[engine_name])} captchas in "
            f"{_ENGINE_CAPTCHA_WINDOW}s — engine on {_ENGINE_CAPTCHA_COOLDOWN}s global cooldown"
        )
        _engine_captcha_times[engine_name] = []


def _engine_cooldown_active(engine_name: str) -> bool:
    """Check if this engine is in global captcha cooldown."""
    until = _engine_cooldown_until.get(engine_name, 0)
    if until <= time.time():
        return False
    return True


def _engine_cooldown_remaining(engine_name: str) -> int:
    """Seconds remaining on global engine cooldown (0 = not in cooldown)."""
    return max(0, int(_engine_cooldown_until.get(engine_name, 0) - time.time()))


def _engine_record_success(engine_name: str):
    """Reset captcha counter after successful scrape."""
    _engine_captcha_times.pop(engine_name, None)


# ── IP Rate Tracker for Google scraping ───────────────────────

_IP_MIN_GAP_SECONDS = 90
_IP_CAPTCHA_PENALTY_PROXY = 1800
_IP_CAPTCHA_PENALTY_DIRECT = 300
_IP_DAILY_MAX = 200

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
    stats["_engine_cooldowns"] = {
        eng: _engine_cooldown_remaining(eng)
        for eng in _engine_cooldown_until
        if _engine_cooldown_remaining(eng) > 0
    }
    return stats


# ── Curl helpers (shared by Google scrapers) ──────────────────

_CURL_HEADERS = {
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


def _cookies_to_dict(cookies_list: list[dict] | None) -> dict[str, str]:
    if not cookies_list:
        return {}
    result = {}
    for c in cookies_list:
        if "google" in c.get("domain", ""):
            result[c["name"]] = c["value"]
    return result
