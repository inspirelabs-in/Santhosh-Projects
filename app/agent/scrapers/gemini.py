import json
import logging

from app.models import ScrapeResult
from app.agent.scrapers.base import (
    _HEADLESS, _run_browser_scrape,
    load_gemini_storage_state,
    find_text_input, snapshot_page_text, type_and_submit,
    wait_and_extract_response, clean_scraped_text,
)

log = logging.getLogger("geo.scraper")


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
            "locale": "en-IN",
            "enable_cache": True,
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
