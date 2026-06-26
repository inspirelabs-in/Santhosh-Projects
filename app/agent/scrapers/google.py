import re
import logging
from urllib.parse import quote_plus
from html.parser import HTMLParser

from app.models import ScrapeResult
from app.agent.scrapers.base import (
    ENGINES, _HEADLESS, _CURL_HEADERS, _cookies_to_dict,
    _run_browser_scrape, _is_captcha_page,
    load_google_cookies,
    log as _base_log,
)

log = logging.getLogger("geo.scraper")


def _parse_aio_from_html(html: str) -> str:
    """Extract AI Overview text from raw Google search HTML."""

    if "AI Overview" not in html and "ai-overview" not in html.lower():
        return ""

    idx = html.find("AI Overview")
    if idx < 0:
        return ""

    chunk = html[idx:idx + 20000]

    class _TagStripper(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts: list[str] = []
            self._skip = False

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "noscript"):
                self._skip = True

        def handle_endtag(self, tag):
            if tag in ("script", "style", "noscript"):
                self._skip = False

        def handle_data(self, data):
            if not self._skip:
                t = data.strip()
                if t:
                    self.parts.append(t)

    stripper = _TagStripper()
    stripper.feed(chunk)
    text = "\n".join(stripper.parts)

    start = text.find("AI Overview")
    if start >= 0:
        text = text[start:]

    end_markers = [
        "People also ask", "Related searches", "Images", "Videos",
        "Web results", "More results", "Feedback", "About this result",
        "Search Results", "Sponsored", "People also search for",
        "See results about", "Top stories", "Discussions and forums",
    ]
    end = len(text)
    for m in end_markers:
        mi = text.find(m, 20)
        if 0 < mi < end:
            end = mi

    result = text[:end].strip()
    return result if len(result) >= 60 else ""


def _parse_ai_mode_from_html(html: str, query: str) -> str:
    """Extract AI Mode response text from raw HTML."""

    class _TagStripper(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts: list[str] = []
            self._skip = False

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "noscript"):
                self._skip = True

        def handle_endtag(self, tag):
            if tag in ("script", "style", "noscript"):
                self._skip = False

        def handle_data(self, data):
            if not self._skip:
                t = data.strip()
                if t:
                    self.parts.append(t)

    stripper = _TagStripper()
    stripper.feed(html)

    skip_prefixes = (
        "AI Mode", "All", "Images", "Videos", "News", "More",
        "Search Results", "You said:", "Tools", "Shopping",
        "Short videos", "Sign in", "Skip to",
        "Accessibility help", "Accessibility", "Quick results from the web",
        "About", "Feedback", "Privacy", "Terms", "Settings",
    )
    query_lower = query.lower().strip()
    filtered = []
    for s in stripper.parts:
        if len(s) < 5:
            continue
        if s in skip_prefixes or s.startswith(skip_prefixes):
            continue
        if s.lower() == query_lower:
            continue
        filtered.append(s)

    result = "\n".join(filtered)
    if not result or len(result) <= 100:
        return ""
    _low = result.lower()
    if "not redirected within" in _low or "please click" in _low:
        return ""
    return result


async def _curl_google_aio(query: str, cookies_list: list[dict] | None = None) -> ScrapeResult | None:
    """Fast curl_cffi scrape for Google AIO. Returns ScrapeResult or None on failure."""
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None

    url = f"https://www.google.com/search?q={quote_plus(query)}&hl=en&gl=in"
    cookies = _cookies_to_dict(cookies_list)

    try:
        async with AsyncSession(impersonate="chrome136", timeout=25) as session:
            resp = await session.get(url, headers=_CURL_HEADERS, cookies=cookies, allow_redirects=True)

            if resp.status_code == 429:
                log.warning("Google AIO curl: 429 rate limited")
                return None
            if resp.status_code != 200:
                log.warning(f"Google AIO curl: HTTP {resp.status_code}")
                return None

            html = resp.text
            if not html or len(html) < 500:
                return None

            low = html[:5000].lower()
            if "captcha" in low or "unusual traffic" in low or "/sorry/" in low:
                log.warning("Google AIO curl: captcha detected")
                return None

            aio_text = _parse_aio_from_html(html)

            if not aio_text:
                return ScrapeResult(
                    raw_response_text="[No AI Overview] Google did not display an AI Overview for this query.",
                    raw_html_payload=html[:10000],
                )

            cited_urls = []
            for m in re.finditer(r'href="(https?://[^"]+)"', html):
                href = m.group(1)
                if "google.com" not in href and "gstatic.com" not in href and href not in cited_urls:
                    cited_urls.append(href)
                    if len(cited_urls) >= 20:
                        break

            log.info(f"Google AIO curl OK: {len(aio_text)} chars, {len(cited_urls)} URLs")
            return ScrapeResult(
                raw_response_text=aio_text,
                raw_html_payload=html[:10000],
                cited_urls=cited_urls,
            )
    except Exception as e:
        log.warning(f"Google AIO curl failed: {e}")
        return None


async def _curl_google_ai_mode(query: str, cookies_list: list[dict] | None = None) -> ScrapeResult | None:
    """Fast curl_cffi scrape for Google AI Mode (&udm=50). Returns ScrapeResult or None."""
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None

    url = f"https://www.google.com/search?q={quote_plus(query)}&udm=50&hl=en&gl=in"
    cookies = _cookies_to_dict(cookies_list)

    try:
        async with AsyncSession(impersonate="chrome136", timeout=25) as session:
            resp = await session.get(url, headers=_CURL_HEADERS, cookies=cookies, allow_redirects=True)

            if resp.status_code == 429:
                log.warning("Google AI Mode curl: 429 rate limited")
                return None
            if resp.status_code != 200:
                log.warning(f"Google AI Mode curl: HTTP {resp.status_code}")
                return None

            html = resp.text
            if not html or len(html) < 500:
                return None

            low = html[:5000].lower()
            if "captcha" in low or "unusual traffic" in low or "/sorry/" in low:
                log.warning("Google AI Mode curl: captcha detected")
                return None

            ai_text = _parse_ai_mode_from_html(html, query)

            if not ai_text:
                return ScrapeResult(
                    raw_response_text="[No AI Mode] Google did not generate an AI Mode response for this query.",
                    raw_html_payload=html[:10000],
                )

            cited_urls = []
            for m in re.finditer(r'href="(https?://[^"]+)"', html):
                href = m.group(1)
                if "google.com" not in href and "gstatic.com" not in href and href not in cited_urls:
                    cited_urls.append(href)
                    if len(cited_urls) >= 20:
                        break

            log.info(f"Google AI Mode curl OK: {len(ai_text)} chars, {len(cited_urls)} URLs")
            return ScrapeResult(
                raw_response_text=ai_text,
                raw_html_payload=html[:10000],
                cited_urls=cited_urls,
            )
    except Exception as e:
        log.warning(f"Google AI Mode curl failed: {e}")
        return None


async def scrape_google_aio(query: str, proxy: dict | None = None) -> ScrapeResult:
    config = ENGINES["google_aio"]
    url = config["url_template"].format(query=quote_plus(query)) + "&hl=en&gl=in"

    try:
        camo_kwargs = {
            "headless": _HEADLESS, "humanize": True, "block_webrtc": True,
            "os": "windows", "locale": "en-IN", "enable_cache": True,
        }
        if proxy:
            camo_kwargs["proxy"] = proxy
            camo_kwargs["geoip"] = True
        else:
            camo_kwargs["geoip"] = True

        async def _page_fn(page):
            _CAPTCHA_URL_SIGS = ("sorry/index", "consent.google.com", "/sorry", "ipv4.google.com/sorry")
            _CAPTCHA_TEXT_SIGS = ("unusual traffic", "not a robot", "automated queries", "systems have detected")

            def _is_captcha_url(u: str) -> bool:
                return any(sig in u for sig in _CAPTCHA_URL_SIGS)

            def _is_captcha_text(t: str) -> bool:
                tl = t.lower()
                return any(sig in tl for sig in _CAPTCHA_TEXT_SIGS)

            async def _safe_evaluate(p, script, fallback=None):
                try:
                    return await p.evaluate(script)
                except Exception as ev_err:
                    err_str = str(ev_err).lower()
                    if "context was destroyed" in err_str or "navigation" in err_str:
                        log.warning(f"[AIO] Evaluate failed (navigation): waiting for page to settle")
                        try:
                            await p.wait_for_load_state("domcontentloaded", timeout=10000)
                        except Exception:
                            pass
                        await p.wait_for_timeout(2000)
                        if _is_captcha_url(p.url):
                            return "__CAPTCHA__"
                        try:
                            return await p.evaluate(script)
                        except Exception:
                            return fallback
                    return fallback

            await page.goto(url, wait_until="commit", timeout=45000)
            await page.wait_for_timeout(4000)

            if _is_captcha_url(page.url):
                log.warning(f"[AIO] Google redirected to captcha: {page.url[:120]}")
                html = ""
                try:
                    body_el = await page.query_selector("body")
                    if body_el:
                        html = await body_el.inner_html()
                except Exception:
                    pass
                return "__CAPTCHA__", html, []

            try:
                consent_clicked = await page.evaluate("""() => {
                    const btns = document.querySelectorAll('button, div[role="button"]');
                    for (const b of btns) {
                        const t = b.textContent.trim().toLowerCase();
                        if (t === 'accept all' || t === 'i agree' || t === 'reject all') {
                            b.click(); return true;
                        }
                    }
                    return false;
                }""")
                if consent_clicked:
                    log.info("[AIO] Consent banner clicked, waiting for navigation")
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=10000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(3000)
                    if _is_captcha_url(page.url):
                        log.warning(f"[AIO] Redirected to captcha after consent: {page.url[:120]}")
                        return "__CAPTCHA__", "", []
            except Exception as consent_err:
                if "context was destroyed" in str(consent_err).lower():
                    log.warning("[AIO] Navigation during consent click, waiting to settle")
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=10000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(3000)
                    if _is_captcha_url(page.url):
                        log.warning(f"[AIO] Captcha after consent navigation: {page.url[:120]}")
                        return "__CAPTCHA__", "", []

            if _is_captcha_url(page.url):
                log.warning(f"[AIO] Captcha detected at URL: {page.url[:120]}")
                return "__CAPTCHA__", "", []

            body_preview = await _safe_evaluate(page, "() => document.body ? document.body.innerText.substring(0, 500) : ''", "")
            if body_preview == "__CAPTCHA__" or (body_preview and _is_captcha_text(body_preview)):
                log.warning(f"[AIO] Captcha text in page body")
                return "__CAPTCHA__", "", []

            aio_appeared = False
            for tick in range(25):
                has_aio = await _safe_evaluate(page, """() => {
                    const body = document.body.innerText;
                    if (body.includes('AI Overview')) return true;
                    if (document.querySelector('div[data-async-type="editableDirectAnswer"]')) return true;
                    if (document.querySelector('div.wDYxhc[data-bsrd]')) return true;
                    if (document.querySelector('div.M8OgIe')) return true;
                    if (document.querySelector('div.kp-wholepage div[data-md]')) return true;
                    const sgrd = document.querySelector('div[data-sgrd]');
                    if (sgrd) {
                        const text = sgrd.innerText || '';
                        const qCount = (text.match(/\\?/g) || []).length;
                        const lines = text.trim().split('\\n').filter(l => l.trim());
                        if (text.length > 80 && qCount < lines.length * 0.6) return true;
                    }
                    return false;
                }""", False)
                if has_aio == "__CAPTCHA__":
                    log.warning("[AIO] Captcha detected during polling")
                    return "__CAPTCHA__", "", []
                if has_aio:
                    aio_appeared = True
                    await page.wait_for_timeout(2000)
                    log.info(f"[AIO] AIO detected after {tick + 1}s of polling")
                    break
                await page.wait_for_timeout(1000)

            if not aio_appeared:
                try:
                    await _safe_evaluate(page, "window.scrollBy(0, 600)")
                    await page.wait_for_timeout(2000)
                    has_aio_after_scroll = await _safe_evaluate(page, """() => {
                        return document.body.innerText.includes('AI Overview')
                            || !!document.querySelector('div[data-async-type="editableDirectAnswer"]')
                            || !!document.querySelector('div.wDYxhc[data-bsrd]')
                            || !!document.querySelector('div.M8OgIe');
                    }""", False)
                    if has_aio_after_scroll and has_aio_after_scroll != "__CAPTCHA__":
                        aio_appeared = True
                        await page.wait_for_timeout(2000)
                        log.info("[AIO] AIO detected after scroll-down second chance")
                    elif not aio_appeared:
                        await _safe_evaluate(page, "window.scrollTo(0, 0)")
                except Exception:
                    pass

            if not aio_appeared:
                try:
                    page_text = await _safe_evaluate(page, "() => document.body.innerText.substring(0, 500)", "")
                    log.warning(f"[AIO] Not detected after 25s polling. URL: {page.url[:80]} Preview: {(page_text or '')[:200]}")
                except Exception:
                    log.warning(f"[AIO] Not detected after 25s polling. URL: {page.url[:80]}")

            if aio_appeared:
                try:
                    expanded = await _safe_evaluate(page, """() => {
                        const aioH = [...document.querySelectorAll('h2, div[role="heading"], span[role="heading"]')]
                            .find(h => h.textContent.includes('AI Overview'));
                        const aioTop = aioH ? aioH.getBoundingClientRect().top : 0;
                        const buttons = document.querySelectorAll('div[role="button"], button, span[role="button"]');
                        for (const b of buttons) {
                            const t = b.textContent.trim().toLowerCase();
                            if (t === 'show more' || t === 'show all') {
                                const rect = b.getBoundingClientRect();
                                if (rect.top < 1200 || (aioTop > 0 && Math.abs(rect.top - aioTop) < 600)) {
                                    b.click(); return true;
                                }
                            }
                        }
                        return false;
                    }""", False)
                    if expanded:
                        log.info("[AIO] Clicked 'Show more' to expand AIO")
                        await page.wait_for_timeout(3000)
                except Exception:
                    pass

            prev_len = 0
            stable = 0
            for _ in range(10):
                cur_len = await _safe_evaluate(page, "() => document.body.innerText.length", 0)
                if cur_len and cur_len > 500 and cur_len == prev_len:
                    stable += 1
                    if stable >= 2:
                        break
                else:
                    stable = 0
                    prev_len = cur_len
                await page.wait_for_timeout(1000)

            aio_text = await _safe_evaluate(page, """() => {
                function isPAA(text) {
                    if (text.includes('People also ask')) return true;
                    const lines = text.trim().split('\\n').filter(l => l.trim());
                    if (lines.length < 6 && lines.every(l => l.endsWith('?'))) return true;
                    const qCount = (text.match(/\\?/g) || []).length;
                    if (qCount >= 3 && text.length < 300) return true;
                    return false;
                }

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

                const sgrd = document.querySelectorAll('div[data-sgrd]');
                for (const el of sgrd) {
                    const text = el.innerText;
                    if (text.length > 80 && !isPAA(text)) return '2:' + text;
                }

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
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el) {
                        const text = el.innerText;
                        if (text.length > 150 && !isPAA(text)) return '3:' + text;
                    }
                }

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
                if (sgrdEls.length > 0) {
                    diag.push('sgrd0:' + sgrdEls[0].innerText.substring(0, 100).replace(/\\n/g, ' '));
                }
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
            }""", "")

            if aio_text == "__CAPTCHA__":
                return "__CAPTCHA__", "", []

            if aio_text and aio_text.startswith('DIAG:'):
                log.warning(f"[AIO] Extraction diagnostics: {aio_text[5:]}")
                aio_text = ""

            if aio_text and ':' in aio_text[:3]:
                strategy = aio_text[:2]
                aio_text = aio_text[2:]
                log.info(f"[AIO] Extraction matched strategy {strategy}, {len(aio_text)} chars")

            if not aio_text or len(aio_text) < 50:
                log.warning("[AIO] No AIO via JS strategies, trying body fallback")
                try:
                    full = await _safe_evaluate(page, "() => document.body ? document.body.innerText : ''", "")
                    full = (full or "").strip() if full != "__CAPTCHA__" else ""
                except Exception as fb_err:
                    log.error(f"[AIO] Body fallback failed: {fb_err}")
                    full = ""
                log.info(f"[AIO] Body text: {len(full)} chars, has 'AI Overview': {'AI Overview' in full}")
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
                    log.info(f"[AIO] Body fallback extracted {len(aio_text)} chars")
                elif full and len(full) > 200 and aio_appeared:
                    log.info("[AIO] No 'AI Overview' marker in body despite detection signal (likely PAA false positive)")

            cited_urls = []
            if aio_text:
                try:
                    links = await page.query_selector_all("a[href^='http']")
                    for link in links:
                        href = await link.get_attribute("href")
                        if href and "google.com" not in href and "gstatic.com" not in href:
                            cited_urls.append(href)
                    cited_urls = list(dict.fromkeys(cited_urls))[:20]
                except Exception:
                    pass

            html = ""
            try:
                body_el = await page.query_selector("body")
                if body_el:
                    html = await body_el.inner_html()
            except Exception:
                pass

            return aio_text or "", html, cited_urls

        raw_text, raw_html, cited_urls = await _run_browser_scrape(camo_kwargs, _page_fn, use_google_cookies=True)

        if raw_text == "__CAPTCHA__" or _is_captcha_page(raw_text or raw_html or ""):
            log.warning("[AIO] Captcha/bot detection triggered")
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
    """Google AI Mode - robust scraping. Strategy: load regular search, then navigate to AI Mode tab."""

    try:
        camo_kwargs = {
            "headless": _HEADLESS, "humanize": True, "block_webrtc": True,
            "os": "windows", "locale": "en-IN", "enable_cache": True,
        }
        if proxy:
            camo_kwargs["proxy"] = proxy
            camo_kwargs["geoip"] = True
        else:
            camo_kwargs["geoip"] = True

        async def _page_fn(page):
            regular_url = f"https://www.google.com/search?q={quote_plus(query)}&hl=en&gl=in"
            await page.goto(regular_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(2000)

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
                await page.wait_for_timeout(1000)
            except Exception:
                pass

            is_real_captcha = await page.evaluate("""() => {
                if (document.querySelector('iframe[src*="recaptcha"]')) return 'recaptcha_iframe';
                if (document.querySelector('#captcha-form')) return 'captcha_form';
                if (document.querySelector('form[action*="/sorry/"]')) return 'sorry_form';
                if (window.location.href.includes('/sorry/')) return 'sorry_url';
                const body = document.body ? document.body.innerText.toLowerCase() : '';
                if (body.length < 500 && (body.includes('unusual traffic') || body.includes('not a robot')))
                    return 'short_captcha_text';
                return '';
            }""")

            if is_real_captcha:
                log.warning(f"Google AI Mode real captcha on search page: {is_real_captcha}")
                return "__CAPTCHA__", "", []

            ai_mode_clicked = await page.evaluate("""() => {
                const links = document.querySelectorAll('a');
                for (const a of links) {
                    const text = (a.textContent || '').trim().toLowerCase();
                    const href = (a.href || '').toLowerCase();
                    if (text === 'ai mode' || href.includes('udm=50')) {
                        a.click();
                        return 'clicked_link';
                    }
                }
                const tabs = document.querySelectorAll('div[role="tab"], div[role="listitem"], div.hdtb-mitem');
                for (const tab of tabs) {
                    const text = (tab.textContent || '').trim().toLowerCase();
                    if (text === 'ai mode') {
                        tab.click();
                        return 'clicked_tab';
                    }
                }
                return '';
            }""")

            if ai_mode_clicked:
                log.info(f"Google AI Mode: navigated via tab click ({ai_mode_clicked})")
                await page.wait_for_timeout(3000)
            else:
                log.info("Google AI Mode: no tab found, navigating directly to udm=50")
                ai_mode_url = f"https://www.google.com/search?q={quote_plus(query)}&udm=50&hl=en&gl=in"
                await page.goto(ai_mode_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)

            post_captcha = await page.evaluate("""() => {
                if (document.querySelector('iframe[src*="recaptcha"]')) return true;
                if (document.querySelector('form[action*="/sorry/"]')) return true;
                if (window.location.href.includes('/sorry/')) return true;
                const body = document.body ? document.body.innerText.toLowerCase() : '';
                if (body.length < 500 && (body.includes('unusual traffic') || body.includes('not a robot')))
                    return true;
                return false;
            }""")
            if post_captcha:
                log.warning("Google AI Mode captcha after navigation to AI Mode")
                return "__CAPTCHA__", "", []

            ai_mode_selectors = [
                "div[data-ai-mode-response]",
                "div.ai-mode-response",
                "#ai-mode-container",
                "div[jsname] div[data-content-feature='1']",
                "div.wIBFpe",
                "div.MasOCd",
                "div.GOrczb",
                "div.CYJI5e",
            ]

            ai_content_el = None
            for sel in ai_mode_selectors:
                try:
                    ai_content_el = await page.wait_for_selector(sel, timeout=5000)
                    if ai_content_el:
                        log.debug(f"AI Mode selector matched: {sel}")
                        break
                except Exception:
                    continue

            previous_length = 0
            stable_ticks = 0
            for _ in range(30):
                body_el = await page.query_selector("body")
                text = (await body_el.inner_text()).strip() if body_el else ""
                current_len = len(text)
                if current_len > 300 and current_len == previous_length:
                    stable_ticks += 1
                    if stable_ticks >= 3:
                        break
                else:
                    stable_ticks = 0
                    previous_length = current_len
                await page.wait_for_timeout(1000)

            try:
                show_more = await page.query_selector_all('button, div[role="button"], span[role="button"]')
                for btn in show_more:
                    try:
                        btn_text = (await btn.inner_text()).strip().lower()
                        if btn_text in ("show more", "see more", "continue reading", "show all"):
                            await btn.click()
                            await page.wait_for_timeout(2000)
                    except Exception:
                        continue
            except Exception:
                pass

            ai_text = ""
            html = ""

            for sel in ai_mode_selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        candidate = (await el.inner_text()).strip()
                        if len(candidate) > 100:
                            ai_text = candidate
                            html = await el.inner_html()
                            break
                except Exception:
                    continue

            if not ai_text or len(ai_text) < 100:
                try:
                    result = await page.evaluate("""() => {
                        const candidates = [];
                        const divs = document.querySelectorAll('div');
                        for (const d of divs) {
                            const t = d.innerText || '';
                            if (t.length > 200 && t.length < 50000) {
                                const rect = d.getBoundingClientRect();
                                if (rect.width > 300 && rect.height > 100) {
                                    candidates.push({text: t, len: t.length, tag: d.tagName, cls: d.className.substring(0, 100)});
                                }
                            }
                        }
                        candidates.sort((a, b) => b.len - a.len);
                        return candidates.slice(0, 3);
                    }""")
                    if result:
                        best = max(result, key=lambda c: c["len"])
                        if best["len"] > 200:
                            ai_text = best["text"]
                except Exception:
                    pass

            if not ai_text or len(ai_text) < 100:
                body_el = await page.query_selector("body")
                raw_text = (await body_el.inner_text()).strip() if body_el else ""
                html = (await body_el.inner_html()) if body_el else ""

                lines = raw_text.split("\n")
                filtered = []
                skip_prefixes = ("AI Mode", "All", "Images", "Videos", "News", "More",
                                 "Search Results", "You said:", "Tools", "Shopping",
                                 "Short videos", "Sign in", "Skip to",
                                 "Accessibility help", "Accessibility", "Quick results from the web",
                                 "About", "Feedback", "Privacy", "Terms", "Settings")
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
                ai_text = "\n".join(filtered)

            cited_urls = []
            try:
                links = await page.query_selector_all("a[href^='http']")
                for link in links[:50]:
                    href = await link.get_attribute("href")
                    if href and "google.com" not in href and "gstatic.com" not in href:
                        cited_urls.append(href)
                cited_urls = list(dict.fromkeys(cited_urls))
            except Exception:
                pass

            return ai_text or "", html, cited_urls

        raw_text, raw_html, cited_urls = await _run_browser_scrape(camo_kwargs, _page_fn, use_google_cookies=True)

        if raw_text == "__CAPTCHA__":
            log.warning("Google AI Mode captcha/bot detection triggered")
            return ScrapeResult(
                raw_response_text="",
                raw_html_payload=raw_html,
                error_log="Google captcha/bot detection - IP may be blocked",
            )

        _check_text = raw_text or raw_html or ""
        if _is_captcha_page(_check_text) and len(_check_text) < 1000:
            log.warning(f"Google AI Mode captcha in final text ({len(_check_text)} chars): {_check_text[:200]}")
            return ScrapeResult(
                raw_response_text="",
                raw_html_payload=raw_html,
                error_log="Google captcha/bot detection - IP may be blocked",
            )

        if not raw_text:
            return ScrapeResult(
                raw_response_text="[No AI Mode] Google did not generate an AI Mode response for this query.",
                raw_html_payload=raw_html,
            )

        _lower = raw_text.lower()
        if "not redirected within" in _lower or ("please click" in _lower and len(raw_text) < 300):
            log.warning("Google AI Mode got redirect/interstitial page instead of content")
            return ScrapeResult(
                raw_response_text="",
                raw_html_payload=raw_html,
                error_log="Google AI Mode redirect page - retry needed",
            )
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
