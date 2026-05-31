import re
import random
import logging
import asyncio
import json
import httpx
from urllib.parse import quote_plus
from camoufox.async_api import AsyncCamoufox
from app.models import ScrapeResult
from app.config import get_settings
from app.routes.auth import (
    load_google_cookies, load_chatgpt_cookies, load_chatgpt_storage_state,
    load_gemini_storage_state, load_claude_storage_state, load_grok_storage_state,
    load_perplexity_storage_state,
)
from app.agent.smart_extract import (
    find_text_input, snapshot_page_text, type_and_submit,
    wait_and_extract_response,
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

ALL_ENGINES = ["google_aio", "google_ai_mode", "perplexity", "gemini", "chatgpt", "claude", "grok"]
HIDDEN_ENGINES = {"grok"}
VISIBLE_ENGINES = [e for e in ALL_ENGINES if e not in HIDDEN_ENGINES]


_proxy_cache: dict | None = None
_proxy_cache_ts: float = 0

AUTH_ENGINES = {"chatgpt", "claude", "grok", "gemini", "perplexity"}
UNAUTH_ENGINES = {"google_aio", "google_ai_mode"}


async def _fetch_proxy_list() -> list[dict]:
    settings = get_settings()
    if not settings.cloudproxy_url:
        return []
    try:
        async with httpx.AsyncClient(timeout=10, verify=False) as client:
            resp = await client.get(f"{settings.cloudproxy_url.rstrip('/')}/")
            data = resp.json()
            proxies = data.get("proxies", data.get("proxy", []))
            return proxies if isinstance(proxies, list) else [proxies] if proxies else []
    except Exception as e:
        log.warning(f"CloudProxy fetch failed: {e}")
        return []


def _build_proxy_config(proxy: dict) -> dict:
    ip = proxy.get("ip", proxy.get("host", ""))
    port = proxy.get("port", 8899)
    config = {"server": f"http://{ip}:{port}"}
    if proxy.get("username"):
        config["username"] = proxy["username"]
        config["password"] = proxy.get("password", "")
    return config


async def _get_proxy(engine_name: str = "") -> dict | None:
    """Get proxy. Auth'd engines get a sticky IP; unauthenticated engines rotate."""
    import time
    global _proxy_cache, _proxy_cache_ts

    settings = get_settings()
    if not settings.cloudproxy_url:
        return None

    if engine_name in AUTH_ENGINES:
        if _proxy_cache and (time.time() - _proxy_cache_ts) < 3600:
            log.info(f"Using sticky proxy for {engine_name}: {_proxy_cache['server']}")
            return _proxy_cache

        proxies = await _fetch_proxy_list()
        if not proxies:
            return None
        proxy = random.choice(proxies)
        _proxy_cache = _build_proxy_config(proxy)
        _proxy_cache_ts = time.time()
        log.info(f"Pinned sticky proxy for auth'd engines: {_proxy_cache['server']}")
        return _proxy_cache

    proxies = await _fetch_proxy_list()
    if not proxies:
        return None
    proxy = random.choice(proxies)
    config = _build_proxy_config(proxy)
    log.info(f"Rotating proxy for {engine_name}: {config['server']}")
    return config


async def _run_browser_scrape(camo_kwargs: dict, page_fn, use_google_cookies: bool = False) -> tuple[str, str, list[str]]:
    """Run a browser scrape, handling the Camoufox Browser.close transport error."""
    raw_text, raw_html, cited_urls = "", "", []
    try:
        async with AsyncCamoufox(**camo_kwargs) as browser:
            page = await browser.new_page()
            if use_google_cookies:
                cookies = await load_google_cookies()
                if cookies:
                    await page.context.add_cookies(cookies)
                    log.info(f"Loaded {len(cookies)} Google cookies for authenticated scrape")
            raw_text, raw_html, cited_urls = await page_fn(page)
    except Exception as e:
        err = str(e)
        if "WriteUnixTransport" in err or "handler is closed" in err:
            log.debug(f"Ignoring Browser.close transport error (data already captured)")
        elif raw_text:
            log.debug(f"Browser close error after data capture: {err}")
        else:
            raise
    return raw_text, raw_html, cited_urls


async def scrape_google_aio(query: str, proxy: dict | None = None) -> ScrapeResult:
    config = ENGINES["google_aio"]
    url = config["url_template"].format(query=quote_plus(query))

    try:
        camo_kwargs = {"headless": True}
        if proxy:
            camo_kwargs["proxy"] = proxy
            camo_kwargs["geoip"] = True

        async def _page_fn(page):
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            # Wait for AIO to appear (it lazy-loads after initial SERP render)
            aio_appeared = False
            for tick in range(25):
                has_aio = await page.evaluate("""() => {
                    const body = document.body.innerText;
                    if (body.includes('AI Overview')) return true;
                    // Check for AIO containers even without heading text
                    if (document.querySelector('div[data-sgrd]')) return true;
                    if (document.querySelector('div[data-async-type="editableDirectAnswer"]')) return true;
                    if (document.querySelector('div.wDYxhc[data-bsrd]')) return true;
                    return false;
                }""")
                if has_aio:
                    aio_appeared = True
                    await page.wait_for_timeout(2000)
                    log.info(f"AIO detected after {tick + 1}s of polling")
                    break
                await page.wait_for_timeout(1000)

            if not aio_appeared:
                page_text = await page.evaluate("() => document.body.innerText.substring(0, 500)")
                log.warning(f"AIO not detected after 25s polling. Page preview: {page_text[:300]}")

            # Try clicking "Show more" to expand collapsed AIO
            if aio_appeared:
                try:
                    expanded = await page.evaluate("""() => {
                        const buttons = document.querySelectorAll('div[role="button"], button, span[role="button"]');
                        for (const b of buttons) {
                            const t = b.textContent.trim().toLowerCase();
                            if (t === 'show more' || t === 'show all' || t.includes('more')) {
                                // Only click if near AIO section
                                const rect = b.getBoundingClientRect();
                                if (rect.top < 800) { b.click(); return true; }
                            }
                        }
                        return false;
                    }""")
                    if expanded:
                        log.info("Clicked 'Show more' to expand AIO")
                        await page.wait_for_timeout(2000)
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
                            || h.closest('div[jscontroller]')
                            || h.closest('div[jsname]')
                            || h.closest('div.M8OgIe');
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
                            // Skip isPAA check — we know this is the AIO section from the heading
                            if (text.length > 30) return '1:' + text;
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
                                        'Web results', 'More results', 'Feedback', 'About this result'];
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
                log.warning(f"AIO extraction failed — diagnostics: {aio_text[5:]}")
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
                                   "Search Results", "Web results"]
                    end = len(chunk)
                    for m in end_markers:
                        mi = chunk.find(m, 20)
                        if 0 < mi < end:
                            end = mi
                    aio_text = chunk[:end].strip()
                    log.info(f"Body fallback extracted {len(aio_text)} chars")
                elif full and len(full) > 200 and aio_appeared:
                    aio_text = full[:5000]
                    log.info(f"No 'AI Overview' marker but AIO was detected — using body text ({len(aio_text)} chars)")

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

        if not raw_text:
            return ScrapeResult(
                raw_response_text="[No AI Overview] Google did not display an AI Overview for this query.",
                raw_html_payload=raw_html,
            )

        log.info(f"Google AIO scraped: {len(raw_text)} chars, {len(cited_urls)} cited URLs")
        return ScrapeResult(raw_response_text=raw_text, raw_html_payload=raw_html, cited_urls=cited_urls)

    except Exception as e:
        log.error(f"Google AIO scrape failed: {e}")
        return ScrapeResult(raw_response_text="", raw_html_payload="", error_log=str(e))


async def scrape_google_ai_mode(query: str, proxy: dict | None = None) -> ScrapeResult:
    """Google AI Mode — same as AIO but with &udm=50 parameter."""
    config = ENGINES["google_ai_mode"]
    url = config["url_template"].format(query=quote_plus(query))

    try:
        camo_kwargs = {"headless": True}
        if proxy:
            camo_kwargs["proxy"] = proxy
            camo_kwargs["geoip"] = True

        async def _page_fn(page):
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)

            previous_length = 0
            stable_ticks = 0
            for _ in range(40):
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
                             "Short videos", "Sign in", "Skip to")
            for line in lines:
                s = line.strip()
                if not s:
                    continue
                if s in skip_prefixes or s.startswith(skip_prefixes):
                    continue
                if len(s) < 5:
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

        if not raw_text:
            return ScrapeResult(
                raw_response_text="",
                raw_html_payload="",
                error_log="No AI Mode response found for this query",
            )

        log.info(f"Google AI Mode scraped: {len(raw_text)} chars, {len(cited_urls)} cited URLs")
        return ScrapeResult(raw_response_text=raw_text, raw_html_payload=raw_html, cited_urls=cited_urls)

    except Exception as e:
        log.error(f"Google AI Mode scrape failed: {e}")
        return ScrapeResult(raw_response_text="", raw_html_payload="", error_log=str(e))


async def scrape_perplexity(query: str, proxy: dict | None = None) -> ScrapeResult:
    config = ENGINES["perplexity"]

    try:
        camo_kwargs = {"headless": True}
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

            input_el = await find_text_input(page, ["textarea", "div[contenteditable='true']"])
            if not input_el:
                _input_error = "Perplexity input field not found — UI may have changed"
                return "", "", []

            pre_snapshot = await snapshot_page_text(page)
            await type_and_submit(page, input_el, query)
            await page.wait_for_timeout(5000)

            combined, url_list = await wait_and_extract_response(
                page, pre_snapshot, max_wait=45, min_response_chars=80,
                primary_selectors=config["response_selectors"],
            )
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

        log.info(f"Perplexity scraped: {len(raw_text)} chars, {len(cited_urls)} cited URLs")
        return ScrapeResult(raw_response_text=raw_text, raw_html_payload=raw_html, cited_urls=cited_urls)

    except Exception as e:
        log.error(f"Perplexity scrape failed: {e}")
        return ScrapeResult(raw_response_text="", raw_html_payload="", error_log=str(e))


async def scrape_gemini_api(query: str, proxy: dict | None = None) -> ScrapeResult:
    """Direct Gemini API call — no browser needed."""
    settings = get_settings()
    if not settings.gemini_api_key:
        return ScrapeResult(raw_response_text="", raw_html_payload="", error_log="GEMINI_API_KEY not configured")

    try:
        from google import genai

        client = genai.Client(api_key=settings.gemini_api_key)

        def _call():
            return client.models.generate_content(
                model="gemini-2.5-flash",
                contents=query,
            )

        response = await asyncio.to_thread(_call)
        text = response.text or ""

        if not text:
            return ScrapeResult(raw_response_text="", raw_html_payload="", error_log="Gemini returned empty response")

        url_pattern = re.compile(r'https?://[^\s\)\]\"\'<>]+')
        cited_urls = list(dict.fromkeys(url_pattern.findall(text)))

        log.info(f"Gemini API response: {len(text)} chars, {len(cited_urls)} URLs found")
        return ScrapeResult(raw_response_text=text, raw_html_payload="", cited_urls=cited_urls)

    except Exception as e:
        log.error(f"Gemini API scrape failed: {e}")
        return ScrapeResult(raw_response_text="", raw_html_payload="", error_log=str(e))


async def scrape_chatgpt(query: str, proxy: dict | None = None) -> ScrapeResult:
    """ChatGPT via Camoufox with cookies + localStorage injection."""
    storage_state = await load_chatgpt_storage_state()
    if not storage_state:
        return ScrapeResult(
            raw_response_text="",
            raw_html_payload="",
            error_log="ChatGPT requires login — go to /chatgpt-login to authenticate",
        )

    cookies = storage_state.get("cookies", [])
    origins = storage_state.get("origins", [])

    try:
        camo_kwargs = {"headless": True}
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
            await page.wait_for_timeout(6000)

            url = page.url
            log.info(f"ChatGPT page URL: {url}")

            # Check for login redirect (auth.openai.com) or explicit login page
            if "auth.openai.com" in url or "auth0.openai.com" in url:
                input_err = "ChatGPT session expired — re-login at /chatgpt-login"
                return "", "", []

            input_el = await find_text_input(page, ["#prompt-textarea", "div[contenteditable='true']#prompt-textarea"])
            if not input_el:
                input_err = "ChatGPT input not found — UI may have changed or session expired"
                return "", "", []

            pre_snapshot = await snapshot_page_text(page)
            await type_and_submit(page, input_el, query)
            await page.wait_for_timeout(5000)

            combined, url_list = await wait_and_extract_response(
                page, pre_snapshot, max_wait=50, min_response_chars=80,
                primary_selectors=[
                    "div[data-message-author-role='assistant']",
                    "div.markdown",
                    "div.prose",
                    "div[class*='markdown']",
                ],
            )

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

        log.info(f"ChatGPT scraped: {len(raw_text)} chars, {len(cited_urls)} cited URLs")
        return ScrapeResult(raw_response_text=raw_text, raw_html_payload=raw_html, cited_urls=cited_urls)

    except Exception as e:
        log.error(f"ChatGPT scrape failed: {e}")
        return ScrapeResult(raw_response_text="", raw_html_payload="", error_log=str(e))


async def scrape_gemini_web(query: str, proxy: dict | None = None) -> ScrapeResult:
    """Gemini via browser with Google cookies."""
    storage_state = await load_gemini_storage_state()
    if not storage_state:
        return ScrapeResult(
            raw_response_text="",
            raw_html_payload="",
            error_log="Gemini requires login — go to /gemini-login to authenticate",
        )

    cookies = storage_state.get("cookies", [])
    origins = storage_state.get("origins", [])

    try:
        camo_kwargs = {"headless": True}
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
            await page.wait_for_timeout(5000)

            url = page.url
            if "accounts.google.com" in url:
                input_err = "Gemini session expired — re-login at /gemini-login"
                return "", "", []

            input_el = await find_text_input(page, ["rich-textarea .ql-editor"])
            if not input_el:
                input_err = "Gemini input field not found — UI may have changed"
                return "", "", []

            pre_snapshot = await snapshot_page_text(page)
            await type_and_submit(page, input_el, query)
            await page.wait_for_timeout(5000)

            combined, url_list = await wait_and_extract_response(
                page, pre_snapshot, max_wait=50, min_response_chars=80,
                primary_selectors=[
                    "div.model-response-text",
                    "div.response-container",
                    "div.markdown",
                    "message-content",
                ],
            )

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

        log.info(f"Gemini Web scraped: {len(raw_text)} chars, {len(cited_urls)} cited URLs")
        return ScrapeResult(raw_response_text=raw_text, raw_html_payload=raw_html, cited_urls=cited_urls)

    except Exception as e:
        log.error(f"Gemini Web scrape failed: {e}")
        return ScrapeResult(raw_response_text="", raw_html_payload="", error_log=str(e))


async def scrape_claude(query: str, proxy: dict | None = None) -> ScrapeResult:
    """Claude.ai via Camoufox with cookies + localStorage injection."""
    storage_state = await load_claude_storage_state()
    if not storage_state:
        return ScrapeResult(
            raw_response_text="",
            raw_html_payload="",
            error_log="Claude requires login — go to /claude-login to authenticate",
        )

    cookies = storage_state.get("cookies", [])
    origins = storage_state.get("origins", [])

    try:
        camo_kwargs = {"headless": True}
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
            except Exception as nav_err:
                log.warning(f"Claude navigation issue (continuing): {nav_err}")
            await page.wait_for_timeout(5000)

            url = page.url
            if "/login" in url or "accounts.google.com" in url:
                input_err = "Claude session expired — re-login at /claude-login"
                return "", "", []

            input_el = await find_text_input(page, ["div[contenteditable='true']", "div.ProseMirror"])
            if not input_el:
                input_err = "Claude input field not found — UI may have changed"
                return "", "", []

            pre_snapshot = await snapshot_page_text(page)
            await type_and_submit(page, input_el, query)
            log.info(f"Claude: query submitted, waiting for response...")
            await page.wait_for_timeout(5000)

            combined, url_list = await wait_and_extract_response(
                page, pre_snapshot, max_wait=60, min_response_chars=80,
                primary_selectors=[
                    "div[data-is-streaming]",
                    "div.font-claude-message",
                    "div[class*='message']",
                    "div.prose",
                    "div.markdown",
                ],
            )

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

        log.info(f"Claude scraped: {len(raw_text)} chars, {len(cited_urls)} cited URLs")
        return ScrapeResult(raw_response_text=raw_text, raw_html_payload=raw_html, cited_urls=cited_urls)

    except Exception as e:
        log.error(f"Claude scrape failed: {e}")
        return ScrapeResult(raw_response_text="", raw_html_payload="", error_log=str(e))


async def _dismiss_grok_popups(page, wait_for_popup=False):
    """Dismiss age confirmation + notification popups on Grok."""
    # Strategy 1: Wait for Continue button to appear in DOM (up to 10s)
    # Popup may take seconds to hydrate into DOM via React portal
    try:
        timeout = 10000 if wait_for_popup else 3000
        cont_locator = page.locator("text=Continue").first
        await cont_locator.click(timeout=timeout)
        log.info("Grok: age popup dismissed via locator click")
        await page.wait_for_timeout(1500)
        # Dismiss notification popups
        try:
            await page.locator("text=Dismiss").first.click(timeout=3000)
            log.info("Grok: notification dismissed via locator")
        except Exception:
            pass
        return True
    except Exception:
        log.info("Grok: no age popup found via locator")

    # Strategy 2: Keyboard Tab+Enter (popup might have focus)
    try:
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(200)
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(200)
        await page.keyboard.press("Enter")
        log.info("Grok: age popup — attempted keyboard Tab+Enter")
        await page.wait_for_timeout(1500)
    except Exception:
        pass

    # Strategy 3: Coordinate clicks at viewport center
    vp = page.viewport_size or {"width": 1024, "height": 640}
    center_x = vp["width"] // 2
    for y_offset in [300, 280, 320, 260]:
        await page.mouse.click(center_x, y_offset)
        await page.wait_for_timeout(500)
    log.info(f"Grok: age popup — attempted coordinate clicks at x={center_x}")
    await page.wait_for_timeout(1000)

    # Dismiss notification popups via DOM
    await page.evaluate("""() => {
        const buttons = document.querySelectorAll('button, [role="button"]');
        for (const el of buttons) {
            const t = el.textContent.trim();
            if ((t === 'Dismiss' || t === 'Not now' || t === 'Maybe later') && el.offsetParent !== null) {
                el.click();
            }
        }
    }""")
    return False


async def scrape_grok(query: str, proxy: dict | None = None) -> ScrapeResult:
    """Grok (x.ai) via Camoufox with cookies + localStorage injection."""
    storage_state = await load_grok_storage_state()
    if not storage_state:
        return ScrapeResult(
            raw_response_text="",
            raw_html_payload="",
            error_log="Grok requires login — go to /grok-login to authenticate",
        )

    cookies = storage_state.get("cookies", [])
    origins = storage_state.get("origins", [])

    try:
        camo_kwargs = {
            "headless": "virtual",
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
                if "grok" in origin_data.get("origin", "") or "x.ai" in origin_data.get("origin", ""):
                    ls_items = origin_data.get("localStorage", [])
                    if ls_items:
                        ls_dict = {item["name"]: item["value"] for item in ls_items}
                        json_data = json.dumps(ls_dict)
                        await page.add_init_script(
                            f"try {{ var d = {json_data}; for (var k in d) localStorage.setItem(k, d[k]); }} catch(e) {{}}"
                        )

            try:
                await page.goto("https://grok.com", wait_until="domcontentloaded", timeout=45000)
            except Exception as nav_err:
                log.warning(f"Grok navigation issue (continuing): {nav_err}")
            await page.wait_for_timeout(5000)

            # Remove OneTrust
            await page.evaluate("""() => {
                const otAccept = document.querySelector('#onetrust-accept-btn-handler');
                if (otAccept) otAccept.click();
                const otBanner = document.querySelector('#onetrust-consent-sdk');
                if (otBanner) otBanner.remove();
            }""")

            # Log cookies for debugging
            ctx_cookies = await page.context.cookies()
            grok_cookies_count = len([c for c in ctx_cookies if 'grok' in c.get('domain', '')])
            x_cookies_count = len([c for c in ctx_cookies if 'x.com' in c.get('domain', '') or 'twitter' in c.get('domain', '')])
            log.info(f"Grok: loaded {grok_cookies_count} grok cookies, {x_cookies_count} x.com cookies")

            # Dismiss popups (Connectors, SuperGrok, etc)
            await page.evaluate("""() => {
                const buttons = document.querySelectorAll('button, [role="button"]');
                for (const el of buttons) {
                    const t = el.textContent.trim();
                    if ((t === 'Dismiss' || t === 'Not now' || t === 'Maybe later') && el.offsetParent !== null) {
                        el.click();
                    }
                }
            }""")
            await page.wait_for_timeout(1000)

            url = page.url
            if "/login" in url or "x.com/i/flow/login" in url:
                input_err = "Grok session expired — re-login at /grok-login"
                return "", "", []

            input_el = await find_text_input(page)
            if not input_el:
                input_err = "Grok input field not found — UI may have changed"
                return "", "", []

            log.info(f"Grok: found input element, submitting query...")
            pre_snapshot = await snapshot_page_text(page)
            try:
                await input_el.click(force=True, timeout=5000)
            except Exception as click_err:
                log.warning(f"Grok: input click failed: {click_err}, trying JS focus")
                await page.evaluate("() => { const el = document.querySelector('textarea') || document.querySelector('[contenteditable]'); if (el) { el.focus(); el.click(); } }")
                await page.wait_for_timeout(500)
            await page.wait_for_timeout(300)
            for char in query:
                await page.keyboard.type(char)
                await page.wait_for_timeout(random.randint(20, 50))

            await page.wait_for_timeout(500)
            await page.keyboard.press("Enter")
            log.info("Grok: query submitted, waiting for response...")
            await page.wait_for_timeout(3000)

            # Age popup may reappear — only use locator (no keyboard/clicks that interfere with response)
            try:
                cont_btn = page.locator("text=Continue").first
                await cont_btn.click(timeout=5000)
                log.info("Grok: post-submit age popup dismissed via locator")
                await page.wait_for_timeout(1000)
            except Exception:
                log.info("Grok: no post-submit age popup")

            # Wait for Grok response by polling for content or error
            grok_responded = False
            retries_used = 0
            max_retries = 3
            for wait_tick in range(30):  # up to ~90 seconds
                # Check if Grok produced a response
                has_response = await page.evaluate("""(q) => {
                    const els = document.querySelectorAll('div.prose, div.markdown, article, div[class*="response"], div[class*="answer"]');
                    for (const el of els) {
                        const text = (el.innerText || '').trim();
                        if (text.length > 50 && text.toLowerCase() !== q.toLowerCase()) return true;
                    }
                    return false;
                }""", query)
                if has_response:
                    log.info(f"Grok: response detected at tick {wait_tick}")
                    grok_responded = True
                    await page.wait_for_timeout(5000)
                    break

                # Check for error/retry state (limit retries)
                if retries_used < max_retries:
                    retry_btn = page.get_by_text("Retry", exact=True)
                    if await retry_btn.count() > 0:
                        log.info(f"Grok: error detected at tick {wait_tick}, clicking Retry ({retries_used + 1}/{max_retries})")
                        await retry_btn.first.click(timeout=3000)
                        retries_used += 1
                        await page.wait_for_timeout(10000)
                        continue

                await page.wait_for_timeout(3000)

            if not grok_responded:
                log.warning("Grok: no response after polling")

            # Wait a bit more for response to stabilize
            await page.wait_for_timeout(5000)

            try:
                await page.screenshot(path="/tmp/grok_debug.png", full_page=True)
            except Exception:
                pass

            # Extract using smart content diff + selector fallback
            combined, url_list = await wait_and_extract_response(
                page, pre_snapshot, max_wait=10, min_response_chars=80,
                primary_selectors=[
                    "div.prose", "div.markdown", "article",
                    "div[class*='response']", "div[class*='answer']",
                    "div[class*='message']", "div[data-testid*='message']",
                ],
            )

            if not combined or len(combined) < 30:
                log.warning(f"Grok: no valid response found")
                combined = ""
            else:
                log.info(f"Grok: extracted {len(combined)} chars")

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
                error_log="Grok returned empty response after timeout",
            )

        log.info(f"Grok scraped: {len(raw_text)} chars, {len(cited_urls)} cited URLs")
        return ScrapeResult(raw_response_text=raw_text, raw_html_payload=raw_html, cited_urls=cited_urls)

    except Exception as e:
        log.error(f"Grok scrape failed: {e}")
        return ScrapeResult(raw_response_text="", raw_html_payload="", error_log=str(e))


async def run_scrape(engine_name: str, query: str, retry_count: int = 0) -> ScrapeResult:
    """Main entry — routes to correct scraper with proxy rotation."""
    proxy = await _get_proxy(engine_name)

    scrapers = {
        "google_aio": scrape_google_aio,
        "google_ai_mode": scrape_google_ai_mode,
        "perplexity": scrape_perplexity,
        "gemini": scrape_gemini_web,
        "chatgpt": scrape_chatgpt,
        "claude": scrape_claude,
        "grok": scrape_grok,
    }

    scraper_fn = scrapers.get(engine_name)
    if not scraper_fn:
        return ScrapeResult(
            raw_response_text="",
            raw_html_payload="",
            error_log=f"Unknown engine: {engine_name}",
        )

    return await scraper_fn(query, proxy)
