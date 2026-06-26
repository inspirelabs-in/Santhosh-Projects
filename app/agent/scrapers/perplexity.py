import json
import logging

from app.models import ScrapeResult
from app.agent.scrapers.base import (
    ENGINES, _HEADLESS, _run_browser_scrape,
    load_perplexity_storage_state,
    find_text_input, snapshot_page_text, type_and_submit,
    wait_and_extract_response, clean_scraped_text,
)

log = logging.getLogger("geo.scraper")


async def scrape_perplexity(query: str, proxy: dict | None = None) -> ScrapeResult:
    config = ENGINES["perplexity"]

    try:
        camo_kwargs = {
            "headless": _HEADLESS,
            "humanize": True,
            "block_webrtc": True,
            "os": "windows",
            "locale": "en-IN",
            "enable_cache": True,
        }
        if proxy:
            camo_kwargs["proxy"] = proxy
            camo_kwargs["geoip"] = True

        storage_state = await load_perplexity_storage_state()
        _input_error = None

        async def _page_fn(page):
            nonlocal _input_error

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
