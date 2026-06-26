import asyncio
import logging
import time

from app.models import ScrapeResult
from app.engines import ALL_ENGINES, AUTH_ENGINES
from app.agent.scrapers.base import (
    ENGINES,
    _get_proxy, _get_cached_proxy_list, _build_proxy_config,
    _is_proxy_error, _engine_proxy_fail_count, _PROXY_FAIL_DIRECT_THRESHOLD,
    rotate_engine_proxy, rotate_serp_proxy, reset_proxy_fails,
    _engine_cooldown_active, _engine_cooldown_remaining,
    _engine_record_captcha, _engine_record_success,
    _ip_available, _ip_wait_time, _ip_record_use, _ip_record_captcha,
    _ip_daily_count, _IP_DAILY_MAX, _DIRECT_IP_KEY,
    get_ip_stats, pause_scraping, resume_scraping,
    _validate_response,
    load_google_cookies,
)
from app.agent.scrapers.google import scrape_google_aio, scrape_google_ai_mode
from app.agent.scrapers.perplexity import scrape_perplexity
from app.agent.scrapers.chatgpt import scrape_chatgpt
from app.agent.scrapers.gemini import scrape_gemini_web
from app.agent.scrapers.claude_engine import scrape_claude

log = logging.getLogger("geo.scraper")

ENGINE_SCRAPERS = {
    "google_aio": scrape_google_aio,
    "google_ai_mode": scrape_google_ai_mode,
    "perplexity": scrape_perplexity,
    "gemini": scrape_gemini_web,
    "chatgpt": scrape_chatgpt,
    "claude": scrape_claude,
}


async def run_scrape(engine_name: str, query: str, retry_count: int = 0) -> ScrapeResult:
    """Main entry - routes to correct scraper with proxy rotation and direct fallback."""
    scraper_fn = ENGINE_SCRAPERS.get(engine_name)
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
    """Direct-first scraping for Google AIO/AI Mode."""
    if _engine_cooldown_active(engine_name):
        remaining = _engine_cooldown_remaining(engine_name)
        log.info(f"[{engine_name}] Engine in global captcha cooldown ({remaining}s remaining), skipping")
        return ScrapeResult(
            raw_response_text="",
            raw_html_payload="",
            error_log=f"Engine captcha cooldown ({remaining}s remaining)",
        )

    cookies_list = await load_google_cookies()

    ip_slots = []
    ip_slots.append((_DIRECT_IP_KEY, None))

    proxies_raw = await _get_cached_proxy_list()
    for p in proxies_raw:
        config = _build_proxy_config(p)
        ip_key = config["server"].split("//")[1] if "//" in config["server"] else config["server"]
        ip_slots.append((ip_key, config))

    available_slots = [(k, p) for k, p in ip_slots if _ip_available(k)]

    if not available_slots:
        direct_wait = _ip_wait_time(_DIRECT_IP_KEY)
        if direct_wait <= 300:
            log.info(f"[{engine_name}] All IPs busy, waiting {direct_wait:.0f}s for direct IP to recover")
            await asyncio.sleep(direct_wait + 1)
            available_slots = [(k, p) for k, p in ip_slots if _ip_available(k)]

        if not available_slots:
            wait_times = {k: _ip_wait_time(k) for k, _ in ip_slots}
            shortest_key = min(wait_times, key=wait_times.get)
            shortest_wait = wait_times[shortest_key]
            if shortest_wait <= 300:
                log.info(f"[{engine_name}] Waiting {shortest_wait:.0f}s for {shortest_key}")
                await asyncio.sleep(shortest_wait + 1)
                available_slots = [(k, p) for k, p in ip_slots if _ip_available(k)]

    if not available_slots:
        log.warning(f"[{engine_name}] No IPs available after wait — all at daily limit or captcha'd")
        return ScrapeResult(
            raw_response_text="",
            raw_html_payload="",
            error_log="All IPs exhausted (daily limit or captcha penalty)",
        )

    direct_first = [(k, p) for k, p in available_slots if k == _DIRECT_IP_KEY]
    proxies_sorted = sorted(
        [(k, p) for k, p in available_slots if k != _DIRECT_IP_KEY],
        key=lambda x: _ip_wait_time(x[0]),
    )
    ordered_slots = direct_first + proxies_sorted

    last_result = None
    tried = 0
    max_tries = min(len(ordered_slots) + 1, 4)

    for ip_key, proxy_config in ordered_slots[:max_tries]:
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
            _engine_record_success(engine_name)
            log.info(f"[{engine_name}] IP '{strategy_label}' succeeded: {len(result.raw_response_text)} chars")
            return result

        if result.raw_response_text and (
            result.raw_response_text.startswith("[No AI Overview]")
            or result.raw_response_text.startswith("[No AI Mode]")
        ):
            log.info(f"[{engine_name}] No AI content for this query (confirmed via '{strategy_label}')")
            return result

        last_result = result
        err = (result.error_log or "").lower()

        if "captcha" in err or "bot detection" in err:
            _ip_record_captcha(ip_key)
            _engine_record_captcha(engine_name)
            log.warning(f"[{engine_name}] Captcha on '{strategy_label}' — penalized, trying next IP")
            if _engine_cooldown_active(engine_name):
                log.warning(f"[{engine_name}] Global engine cooldown triggered — stopping IP rotation")
                break
            continue

        if _is_proxy_error(result.error_log or ""):
            log.warning(f"[{engine_name}] Proxy error on '{strategy_label}', trying next")
            continue

        log.warning(f"[{engine_name}] IP '{strategy_label}' failed: {result.error_log}")
        await asyncio.sleep(1)

    if tried == max_tries and last_result and last_result.error_log:
        direct_wait = _ip_wait_time(_DIRECT_IP_KEY)
        if direct_wait <= 200:
            log.info(f"[{engine_name}] Last resort: waiting {direct_wait:.0f}s for direct + cookie refresh")
            if direct_wait > 0:
                await asyncio.sleep(direct_wait)
            try:
                from app.routes.auth import refresh_google_cookies
                await refresh_google_cookies()
            except Exception as e:
                log.warning(f"[{engine_name}] Cookie refresh failed: {e}")
            _ip_record_use(_DIRECT_IP_KEY)
            result = await scraper_fn(query, None)
            if result.raw_response_text and not result.error_log:
                return result
            if result.raw_response_text and (
                result.raw_response_text.startswith("[No AI Overview]")
                or result.raw_response_text.startswith("[No AI Mode]")
            ):
                return result
            last_result = result

    log.error(f"[{engine_name}] All {tried} IPs exhausted for: {query[:60]}")
    return last_result or ScrapeResult(
        raw_response_text="",
        raw_html_payload="",
        error_log=f"All {tried} IPs exhausted for Google scrape",
    )
