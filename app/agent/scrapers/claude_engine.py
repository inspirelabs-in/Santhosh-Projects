import json
import logging

from app.models import ScrapeResult
from app.agent.scrapers.base import (
    _HEADLESS, _run_browser_scrape,
    load_claude_storage_state,
    find_text_input, snapshot_page_text, type_and_submit,
    wait_and_extract_response, clean_scraped_text,
)

log = logging.getLogger("geo.scraper")


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

            try:
                early_page_text = await page.evaluate("() => document.body ? document.body.innerText.substring(0, 2000) : ''")
                _early_lower = (early_page_text or "").lower()
                _early_rate_signals = ["out of free messages", "hit your limit", "message limit",
                                       "5-hour message limit", "you've hit your limit", "limits will reset"]
                if any(sig in _early_lower for sig in _early_rate_signals):
                    log.warning("Claude: rate limit detected on page load (before query submission)")
                    input_err = "limit_exhausted: Claude free plan rate limit hit"
                    return "", "", []
            except Exception:
                pass

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

            if combined and len(combined) < 1500:
                _upgrade_noise = ["explore our pro plan", "cowork", "hand off tasks",
                                  "claude code", "build, debug", "chrome", "navigates, clicks",
                                  "microsoft office", "analyze data"]
                _noise_count = sum(1 for sig in _upgrade_noise if sig in _combined_lower)
                if _noise_count >= 3:
                    log.warning("Claude: response is rate limit upgrade UI, not real content")
                    input_err = "limit_exhausted: Claude rate limit UI captured instead of response"
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
