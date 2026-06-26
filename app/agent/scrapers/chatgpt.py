import json
import logging

from app.models import ScrapeResult
from app.agent.scrapers.base import (
    _HEADLESS, _run_browser_scrape,
    load_chatgpt_storage_state,
    find_text_input, snapshot_page_text, type_and_submit,
    wait_and_extract_response, clean_scraped_text,
)

log = logging.getLogger("geo.scraper")


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
            "locale": "en-IN",
            "enable_cache": True,
        }
        if proxy:
            camo_kwargs["proxy"] = proxy
            camo_kwargs["geoip"] = True

        raw_text, raw_html, cited_urls, input_err = "", "", [], None

        async def _page_fn(page):
            nonlocal input_err

            page.on("pageerror", lambda _: None)
            await page.context.add_cookies(cookies)

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

            await page.evaluate("""() => {
                const dismiss_texts = ['Accept', 'Accept all', 'Got it', 'Okay', 'OK', 'Close', 'Dismiss', 'Stay logged out', 'Continue'];
                document.querySelectorAll('button, [role="button"]').forEach(el => {
                    const t = (el.textContent || '').trim();
                    if (dismiss_texts.some(d => t.includes(d)) && el.offsetParent !== null) {
                        try { el.click(); } catch(e) {}
                    }
                });
                document.querySelectorAll('[role="dialog"], [data-testid*="modal"]').forEach(el => {
                    try { el.remove(); } catch(e) {}
                });
            }""")
            await page.wait_for_timeout(1000)

            url = page.url
            log.info(f"ChatGPT page URL: {url}")

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
