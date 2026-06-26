"""
Universal browser extraction helpers.

These use content-diffing and structural analysis instead of
hardcoded CSS selectors, making scrapers resilient to UI redesigns.
"""
import logging
import random
import re

log = logging.getLogger("geo.smart")

# Nav/sidebar phrases to filter out when extracting response text
SIDEBAR_PHRASES = frozenset([
    "new chat", "recents", "settings", "upgrade", "history",
    "projects", "artifacts", "customize", "sign in", "log in",
    "trending", "explore", "notifications",
    "home", "premium", "sign up",
    "continue your conversation",
    "sign up for free", "sign up to continue", "imagine",
    # Cookie consent / privacy wall phrases
    "privacy preference", "cookie preference", "your privacy",
    "strictly necessary", "performance cookies", "functional cookies",
    "targeting cookies", "confirm my choices", "accept all cookies",
    "accept all", "privacy policy", "cookie policy", "manage cookies",
    "cookie settings", "consent", "onetrust",
    # Claude.ai UI noise
    "all chats", "free plan", "pro plan", "team plan",
    "claude fable", "claude sonnet", "claude haiku", "claude opus",
    "is currently unavailable", "learn more", "opens in new tab",
    "you are out of free messages", "you've hit your limit",
    "you hit your 5-hour message limit", "message limit",
    "limits will reset", "for higher limits",
    "explore our pro plan", "upgrade for higher limits",
    "cowork", "hand off tasks", "build, debug, and ship",
    "microsoft office", "analyze data and build slides",
    "chrome", "navigates, clicks buttons",
    "not now", "plus, get more ways to use claude",
    "returns!", "starred",
    # ChatGPT UI noise
    "chatgpt plus", "upgrade to plus", "get gpt-4",
    "temporary chat", "today", "yesterday", "previous 7 days",
    "search chats", "my gpts", "explore gpts",
    "write a whatsapp message", "help me prepare for my exams",
    "turn photo into profile pic", "create a study plan",
    "summarize this article", "plan a trip",
    # Perplexity UI noise
    "perplexity pro", "try pro", "copilot",
    "trending searches", "popular searches",
    # Gemini UI noise
    "gemini advanced", "try gemini advanced",
    "google apps", "search your chats",
    "about gemini", "get gemini app", "subscriptions",
    "for business", "gemini is ai and can make mistakes",
])


async def find_text_input(page, extra_selectors: list[str] | None = None, timeout: int = 8000):
    """Find a text input on the page using progressive detection.

    Tries specific selectors first, then falls back to a universal DOM scan.
    Returns the element or None.
    """
    selectors = [
        *(extra_selectors or []),
        "textarea:visible",
        "div[contenteditable='true']:visible",
        "div.ProseMirror:visible",
        "input[type='text']:visible",
    ]

    for sel in selectors:
        try:
            el = await page.wait_for_selector(sel, timeout=min(timeout, 3000))
            if el:
                is_visible = await el.is_visible()
                if is_visible:
                    log.debug(f"Input found via selector: {sel}")
                    return el
        except Exception:
            continue

    # Fallback: DOM scan for any focusable text input
    try:
        el = await page.evaluate_handle("""() => {
            // Check contenteditable divs
            const editables = document.querySelectorAll('[contenteditable="true"]');
            for (const e of editables) {
                const rect = e.getBoundingClientRect();
                if (rect.width > 100 && rect.height > 20 && rect.top > 0) return e;
            }
            // Check textareas
            const areas = document.querySelectorAll('textarea');
            for (const a of areas) {
                const rect = a.getBoundingClientRect();
                if (rect.width > 100 && rect.height > 20 && rect.top > 0) return a;
            }
            // Check text inputs
            const inputs = document.querySelectorAll('input[type="text"], input:not([type])');
            for (const i of inputs) {
                const rect = i.getBoundingClientRect();
                if (rect.width > 100 && rect.top > 0) return i;
            }
            return null;
        }""")
        if el:
            as_el = el.as_element()
            if as_el:
                log.info("Input found via DOM scan fallback")
                return as_el
    except Exception as e:
        log.debug(f"DOM scan input detection failed: {e}")

    return None


async def snapshot_page_text(page) -> str:
    """Capture current page text for diffing."""
    try:
        return await page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:
        return ""


async def type_and_submit(page, input_el, query: str, submit_key: str = "Enter"):
    """Type query into input with human-like delays and submit."""
    try:
        await input_el.click(timeout=8000)
    except Exception:
        log.info("Normal click failed, trying force click + JS focus")
        try:
            await input_el.click(force=True, timeout=5000)
        except Exception:
            await page.evaluate("(el) => { el.focus(); el.click(); }", input_el)
    await page.wait_for_timeout(300)

    for char in query:
        await page.keyboard.type(char)
        await page.wait_for_timeout(random.randint(15, 45))

    await page.wait_for_timeout(500)
    await page.keyboard.press(submit_key)


async def wait_and_extract_response(
    page,
    pre_snapshot: str,
    max_wait: int = 60,
    min_response_chars: int = 80,
    primary_selectors: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Wait for a response and extract it using multiple strategies.

    Strategy priority:
    1. Primary selectors (if provided - legacy compatibility)
    2. Content-diff: new text that appeared after pre_snapshot
    3. DOM scan: largest new text block on page
    4. Body text growth detection

    Returns (response_text, cited_urls).
    """
    pre_lines = set(pre_snapshot.strip().split('\n'))
    pre_len = len(pre_snapshot)

    # Phase 1: Poll for response content
    best_text = ""
    stable_ticks = 0
    prev_len = 0
    grace_extended = False
    effective_max = max_wait

    for tick in range(max_wait + 15):
        if tick >= effective_max:
            break

        # Strategy A: Try primary selectors
        if primary_selectors:
            texts = []
            for sel in primary_selectors:
                try:
                    elements = await page.query_selector_all(sel)
                    for el in elements:
                        t = await el.inner_text()
                        if t and len(t.strip()) > 20:
                            texts.append(t.strip())
                except Exception:
                    continue
            combined = "\n\n".join(texts)
            if len(combined) > min_response_chars:
                if len(combined) == prev_len:
                    stable_ticks += 1
                    if stable_ticks >= 3:
                        best_text = combined
                        break
                else:
                    stable_ticks = 0
                    prev_len = len(combined)
                    best_text = combined
                    if tick >= max_wait - 1 and not grace_extended:
                        grace_extended = True
                        effective_max = max_wait + 15
                        log.info("Response still growing at max_wait, extending 15s")
                await page.wait_for_timeout(1000)
                continue

        # Strategy B: Content growth detection
        current = await snapshot_page_text(page)
        current_len = len(current)

        if current_len > pre_len + min_response_chars:
            new_text = _extract_diff(pre_snapshot, current)
            if len(new_text) > min_response_chars:
                if len(new_text) == prev_len:
                    stable_ticks += 1
                    if stable_ticks >= 3:
                        best_text = new_text
                        break
                else:
                    stable_ticks = 0
                    prev_len = len(new_text)
                    best_text = new_text
                    if tick >= max_wait - 1 and not grace_extended:
                        grace_extended = True
                        effective_max = max_wait + 15
                        log.info("Response still growing at max_wait, extending 15s")

        await page.wait_for_timeout(1000)

    # Phase 2: If polling found nothing, try DOM scan
    if not best_text or len(best_text) < min_response_chars:
        best_text = await _dom_scan_response(page, pre_snapshot)

    # Phase 3: Last resort - full body diff
    if not best_text or len(best_text) < min_response_chars:
        current = await snapshot_page_text(page)
        diff = _extract_diff(pre_snapshot, current)
        if len(diff) > min_response_chars:
            best_text = diff
            log.info(f"Body diff fallback extracted {len(best_text)} chars")

    # Extract URLs
    urls = await _extract_cited_urls(page)

    return best_text, urls


def _extract_diff(pre: str, post: str) -> str:
    """Extract new text content that appeared between snapshots."""
    pre_lines = set(pre.strip().split('\n'))
    post_lines = post.strip().split('\n')

    new_lines = []
    for line in post_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped not in pre_lines and len(stripped) > 2:
            # Skip sidebar/nav items
            lower = stripped.lower()
            if any(phrase in lower for phrase in SIDEBAR_PHRASES) and len(stripped) < 100:
                continue
            new_lines.append(stripped)

    return '\n'.join(new_lines)


async def _dom_scan_response(page, pre_snapshot: str) -> str:
    """Scan DOM for the largest new text block (response content)."""
    try:
        result = await page.evaluate("""(preLen) => {
            const candidates = [];
            const allEls = document.querySelectorAll('div, article, section, main');

            for (const el of allEls) {
                const text = el.innerText || '';
                if (text.length < 80 || text.length > 50000) continue;

                // Skip if clearly nav/sidebar
                const lower = text.toLowerCase();
                if (lower.includes('new chat') && lower.includes('recents') && text.length < 2000) continue;
                if (lower.includes('customize') && lower.includes('projects') && text.length < 2000) continue;

                // Prefer elements with links (cited sources)
                const links = el.querySelectorAll('a[href^="http"]').length;

                // Score: longer text with links ranks higher, but penalize if too similar to pre-snapshot length
                const score = text.length + (links * 200);
                candidates.push({ text, score, tag: el.tagName, cls: (el.className || '').substring(0, 40) });
            }

            // Sort by score descending
            candidates.sort((a, b) => b.score - a.score);

            // Return best candidate that's substantially different from pre-snapshot
            for (const c of candidates) {
                // Check it's not just the full page body
                if (c.tag === 'BODY' || c.tag === 'HTML') continue;
                if (c.text.length > preLen * 0.9 && c.text.length < preLen * 1.1) continue;
                return c.text;
            }
            return '';
        }""", len(pre_snapshot))

        if result and len(result) > 80:
            clean = _clean_response(result, pre_snapshot)
            if len(clean) > 50:
                log.info(f"DOM scan extracted {len(clean)} chars")
                return clean
    except Exception as e:
        log.debug(f"DOM scan failed: {e}")

    return ""


def _clean_response(text: str, pre_snapshot: str) -> str:
    """Remove sidebar/nav content from extracted text."""
    pre_lines = set(pre_snapshot.strip().split('\n'))
    lines = text.strip().split('\n')

    clean = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in pre_lines:
            continue
        lower = stripped.lower()
        if any(phrase in lower for phrase in SIDEBAR_PHRASES) and len(stripped) < 100:
            continue
        clean.append(stripped)

    return '\n'.join(clean)


_TOOL_CALLS_RE = re.compile(r"<tool_calls>.*?</tool_calls>", re.DOTALL)
_SEARCH_WEB_RE = re.compile(r"<search_web>.*?</search_web>", re.DOTALL)
_XML_TAG_BLOCK_RE = re.compile(r"<(?:tool_call|function_call|search|thinking|reflection)s?>.*?</(?:tool_call|function_call|search|thinking|reflection)s?>", re.DOTALL)

_RATE_LIMIT_PATTERNS = [
    re.compile(r"you(?:'ve| have)?\s+(?:hit|reached|exceeded)\s+(?:your|the)\s+.*?(?:message|usage|rate)\s*limit", re.IGNORECASE),
    re.compile(r"you\s+(?:are\s+)?out\s+of\s+free\s+messages", re.IGNORECASE),
    re.compile(r"limits?\s+will\s+reset", re.IGNORECASE),
    re.compile(r"upgrade\s+(?:to|for)\s+(?:pro|plus|premium|higher)\s+(?:plan|limits)", re.IGNORECASE),
    re.compile(r"(?:free|starter|basic)\s+plan\s+(?:includes?|allows?|has)", re.IGNORECASE),
]

_CLAUDE_UI_NOISE_PATTERNS = [
    re.compile(r"^(?:sonnet|opus|haiku|fable|claude)\s+\d", re.IGNORECASE),
    re.compile(r"^(?:model|plan)\s*:", re.IGNORECASE),
    re.compile(r"^(?:all chats|starred|projects?|artifacts?)$", re.IGNORECASE),
    re.compile(r"(?:opens?\s+in\s+new\s+tab)", re.IGNORECASE),
]

_CHATGPT_UI_NOISE_PATTERNS = [
    re.compile(r"^(?:gpt-4|gpt-3|chatgpt|temporary chat)$", re.IGNORECASE),
    re.compile(r"^(?:today|yesterday|previous\s+\d+\s+days?)$", re.IGNORECASE),
]

_PERPLEXITY_UI_NOISE_PATTERNS = [
    re.compile(r"^(?:home|discover|library|threads?|collections?)$", re.IGNORECASE),
]


def clean_scraped_text(text: str, engine_name: str = "") -> str:
    """Post-extraction cleaning to remove UI noise, XML tool calls, rate limits, and duplicates."""
    if not text or len(text) < 10:
        return text

    text = _TOOL_CALLS_RE.sub("", text)
    text = _SEARCH_WEB_RE.sub("", text)
    text = _XML_TAG_BLOCK_RE.sub("", text)

    lines = text.split("\n")
    cleaned = []
    seen_lines = set()

    noise_patterns = list(_RATE_LIMIT_PATTERNS)
    if engine_name == "claude":
        noise_patterns.extend(_CLAUDE_UI_NOISE_PATTERNS)
    elif engine_name == "chatgpt":
        noise_patterns.extend(_CHATGPT_UI_NOISE_PATTERNS)
    elif engine_name == "perplexity":
        noise_patterns.extend(_PERPLEXITY_UI_NOISE_PATTERNS)

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if stripped in seen_lines and len(stripped) < 120:
            continue

        lower = stripped.lower()
        if any(phrase in lower for phrase in SIDEBAR_PHRASES) and len(stripped) < 80:
            continue

        if any(pat.search(stripped) for pat in noise_patterns):
            continue

        seen_lines.add(stripped)
        cleaned.append(stripped)

    result = "\n".join(cleaned).strip()

    if _is_mostly_noise(result):
        log.warning(f"[{engine_name}] Response appears to be mostly UI noise ({len(result)} chars), rejecting")
        return ""

    return result


def _is_mostly_noise(text: str) -> bool:
    """Detect when extracted text is mostly UI noise rather than a real response."""
    if not text:
        return True
    if len(text) > 500:
        return False
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return True
    if len(lines) < 3 and len(text) < 100:
        return True
    short_lines = sum(1 for l in lines if len(l) < 30)
    if len(lines) > 5 and short_lines / len(lines) > 0.8:
        return True
    unique_lines = set(lines)
    if len(lines) > 5 and len(unique_lines) / len(lines) < 0.4:
        return True
    return False


async def _extract_cited_urls(page, exclude_domains: list[str] | None = None) -> list[str]:
    """Extract cited URLs from the page, filtering out site-internal links."""
    default_exclude = [
        "google.com", "gstatic.com", "claude.ai", "anthropic.com",
        "chatgpt.com", "openai.com", "perplexity.ai", "gemini.google.com",
        "x.com", "twitter.com",
    ]
    exclude = set(default_exclude + (exclude_domains or []))

    try:
        links = await page.query_selector_all("a[href^='http']")
        urls = []
        for link in links:
            href = await link.get_attribute("href")
            if href and not any(d in href for d in exclude):
                urls.append(href)
        return list(dict.fromkeys(urls))[:20]
    except Exception:
        return []
