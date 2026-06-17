import asyncio
import base64
import json
import logging
import sys
import uuid
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from camoufox.async_api import AsyncCamoufox
from app.database import run_db
from app.config import get_settings

_HEADLESS = "virtual" if sys.platform != "win32" else True

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
log = logging.getLogger("geo.auth")

_sessions: dict[str, dict] = {}
_warm_camo = None
_warming_in_progress = False
_warm_camo_lock = asyncio.Lock()

VIEWPORT_W = 1280
VIEWPORT_H = 800


def _clear_engine_cooldown(engine_name: str):
    """Reset pipeline cooldown for engine after fresh cookies saved."""
    try:
        from app.agent.pipeline import clear_engine_cooldown
        clear_engine_cooldown(engine_name)
    except Exception:
        pass


async def _get_warm_camoufox():
    """Return pre-warmed Camoufox or launch fresh."""
    global _warm_camo
    async with _warm_camo_lock:
        browser = _warm_camo
        _warm_camo = None
    if browser:
        log.info("Using pre-warmed Camoufox")
        return browser
    log.info("Launching Camoufox (cold start)...")
    return await AsyncCamoufox(
        headless=_HEADLESS,
        humanize=True,
        block_webrtc=True,
        os="windows",
    ).__aenter__()


async def _warmup_camoufox():
    """Pre-launch one Camoufox in background. Serialized - only one warmup at a time."""
    global _warm_camo, _warming_in_progress
    async with _warm_camo_lock:
        if _warm_camo is not None or _warming_in_progress:
            return
        _warming_in_progress = True
    try:
        log.info("Pre-warming Camoufox...")
        browser = await AsyncCamoufox(
            headless=_HEADLESS,
            humanize=True,
            block_webrtc=True,
            os="windows",
        ).__aenter__()
        async with _warm_camo_lock:
            if _warm_camo is not None:
                await browser.__aexit__(None, None, None)
            else:
                _warm_camo = browser
                log.info("Camoufox pre-warmed and ready")
    except Exception as e:
        log.warning(f"Camoufox warm-up failed: {type(e).__name__}: {e}")
    finally:
        async with _warm_camo_lock:
            _warming_in_progress = False



LOGIN_PROVIDERS = {
    "google": {
        "name": "Google",
        "start_url": "https://accounts.google.com",
        "db_key": "google_cookies",
        "logged_in_signals": [
            "myaccount.google.com",
            "accounts.google.com/SignOutOptions",
            "accounts.google.com/b/",
            "mail.google.com",
            "drive.google.com",
            "google.com/preferences",
            "google.com/search",
            "google.com/?",
            "www.google.com",
        ],
        "cookie_domain_filter": "google",
    },
    "chatgpt": {
        "name": "ChatGPT",
        "start_url": "https://chatgpt.com",
        "db_key": "chatgpt_cookies",
        "logged_in_signals": [
            "chatgpt.com/?model=",
            "chatgpt.com/c/",
            "chatgpt.com/g/",
            "chatgpt.com/#702b0",
            "chatgpt.com/gpts",
            "chatgpt.com/settings",
        ],
        "cookie_domain_filter": "openai",
    },
    "perplexity": {
        "name": "Perplexity",
        "start_url": "https://www.perplexity.ai",
        "db_key": "perplexity_cookies",
        "logged_in_signals": [
            "perplexity.ai/search",
            "perplexity.ai/collections",
            "perplexity.ai/library",
            "perplexity.ai/settings",
            "perplexity.ai/profile",
            "perplexity.ai/home",
        ],
        "cookie_domain_filter": "perplexity",
    },
    "gemini": {
        "name": "Gemini",
        "start_url": "https://gemini.google.com/app",
        "db_key": "gemini_cookies",
        "logged_in_signals": [
            "gemini.google.com/app",
            "gemini.google.com/chat",
        ],
        "cookie_domain_filter": "google",
    },
    "claude": {
        "name": "Claude",
        "start_url": "https://claude.ai/login",
        "db_key": "claude_cookies",
        "logged_in_signals": [
            "claude.ai/new",
            "claude.ai/chat",
            "claude.ai/recents",
            "claude.ai/project",
            "claude.ai/settings",
        ],
        "cookie_domain_filter": "claude",
    },
}


class ClickInput(BaseModel):
    x: float
    y: float


class TypeInput(BaseModel):
    text: str


class KeyInput(BaseModel):
    key: str


class CredentialInput(BaseModel):
    provider: str
    email: str
    password: str
    slot_index: int | None = None


class OTPInput(BaseModel):
    code: str


# ── Credential Encryption ─────────────────────────────────
import hashlib
from cryptography.fernet import Fernet


def _get_fernet() -> Fernet:
    settings = get_settings()
    key = hashlib.sha256(settings.credentials_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _encrypt(text: str) -> str:
    return _get_fernet().encrypt(text.encode()).decode()


def _decrypt(token: str) -> str:
    return _get_fernet().decrypt(token.encode()).decode()


# ── Auto-Login State ──────────────────────────────────────
_auto_login_sessions: dict[str, dict] = {}
_otp_events: dict[str, asyncio.Event] = {}
_otp_values: dict[str, str] = {}


AUTO_LOGIN_FLOWS = {
    "google": {
        "url": "https://accounts.google.com/signin/v2/identifier",
        "covers": ["google", "gemini"],
        "steps": [
            {"action": "fill", "selector": "input[type='email'], input[name='identifier'], input[autocomplete='username'], input[aria-label='Email or phone']", "field": "email"},
            {"action": "click_next", "selectors": "#identifierNext, div#identifierNext, button:has-text('Next'), button[type='submit']"},
            {"action": "wait", "duration": 5000},
            {"action": "fill", "selector": "input[type='password'], input[name='Passwd'], input[name='password'], input[autocomplete='current-password']", "field": "password"},
            {"action": "click_next", "selectors": "#passwordNext, div#passwordNext, button:has-text('Next'), button[type='submit']"},
            {"action": "wait", "duration": 5000},
            {"action": "check_otp"},
        ],
    },
    "chatgpt": {
        "url": "https://chatgpt.com",
        "covers": ["chatgpt"],
        "preload_google_cookies": True,
        "steps": [
            {"action": "dismiss_cookies"},
            {"action": "click", "selector": "button[data-testid='login-button'], a[href*='/auth/login'], button:has-text('Log in'), a:has-text('Log in')"},
            {"action": "wait", "duration": 3000},
            {"action": "google_oauth"},
            {"action": "wait", "duration": 5000},
            {"action": "check_otp"},
        ],
    },
    "perplexity": {
        "url": "https://www.perplexity.ai",
        "covers": ["perplexity"],
        "preload_google_cookies": True,
        "steps": [
            {"action": "dismiss_cookies"},
            {"action": "click", "selector": "button:has-text('Sign In'), a:has-text('Sign In'), button:has-text('Sign in'), a:has-text('Sign in'), button:has-text('Log in'), a:has-text('Log in')"},
            {"action": "wait", "duration": 3000},
            {"action": "google_oauth"},
            {"action": "wait", "duration": 5000},
            {"action": "check_otp"},
        ],
    },
    "claude": {
        "url": "https://claude.ai/login",
        "covers": ["claude"],
        "preload_google_cookies": True,
        "steps": [
            {"action": "dismiss_cookies"},
            {"action": "wait", "duration": 2000},
            {"action": "google_oauth"},
            {"action": "wait", "duration": 5000},
            {"action": "check_otp"},
        ],
    },
}


# ── Pages ──────────────────────────────────────────────────

@router.get("/google-login", response_class=HTMLResponse)
async def google_login_page(request: Request):
    return await _render_login_page(request, "google")


@router.get("/chatgpt-login", response_class=HTMLResponse)
async def chatgpt_login_page(request: Request):
    return await _render_login_page(request, "chatgpt")


@router.get("/perplexity-login", response_class=HTMLResponse)
async def perplexity_login_page(request: Request):
    return await _render_login_page(request, "perplexity")


@router.get("/gemini-login", response_class=HTMLResponse)
async def gemini_login_page(request: Request):
    return await _render_login_page(request, "gemini")


@router.get("/claude-login", response_class=HTMLResponse)
async def claude_login_page(request: Request):
    return await _render_login_page(request, "claude")


async def _render_login_page(request: Request, provider: str):
    provider_statuses = {}
    for p in LOGIN_PROVIDERS:
        provider_statuses[p] = await _get_cookie_status(p)

    # Pre-warm Camoufox in background while user reads the page
    asyncio.create_task(_warmup_camoufox())

    visible_providers = LOGIN_PROVIDERS
    visible_statuses = provider_statuses

    return templates.TemplateResponse(request, "auth_login.html", {
        "active_provider": provider,
        "provider_statuses": visible_statuses,
        "providers": visible_providers,
    })


# ── Generic session endpoints ──────────────────────────────

@router.post("/api/auth/start/{provider}")
async def start_login(provider: str):
    if provider not in LOGIN_PROVIDERS:
        return JSONResponse({"error": f"Unknown provider: {provider}"}, status_code=400)

    for sid, sess in list(_sessions.items()):
        if sess.get("provider") == provider:
            await _cleanup_session(sid)

    config = LOGIN_PROVIDERS[provider]
    session_id = str(uuid.uuid4())[:8]

    try:
        camo_browser = await _get_warm_camoufox()
        page = await camo_browser.new_page()
        await page.set_viewport_size({"width": VIEWPORT_W, "height": VIEWPORT_H})

        # Block file chooser dialogs from opening OS picker
        page.on("filechooser", lambda fc: None)

        _sessions[session_id] = {
            "camo_browser": camo_browser,
            "page": page,
            "provider": provider,
            "created": asyncio.get_event_loop().time(),
            "navigating": True,
        }

        # Navigate in background - UI shows loading overlay meanwhile
        async def _nav():
            try:
                await page.goto(config["start_url"], wait_until="commit", timeout=25000)
                await _dismiss_cookie_popups(page)
                await _post_nav_actions(page, config, provider)
            except Exception as e:
                log.warning(f"[{provider}] Navigation slow/partial: {e}")
            finally:
                session = _sessions.get(session_id)
                if session:
                    session["navigating"] = False

        asyncio.create_task(_nav())
        # Pre-warm next Camoufox instance while user logs in
        asyncio.create_task(_warmup_camoufox())

        log.info(f"[{provider}] Login session started (camoufox): {session_id}")
        return JSONResponse({
            "session_id": session_id,
            "status": "started",
            "viewport": {"width": VIEWPORT_W, "height": VIEWPORT_H},
        })

    except Exception as e:
        log.error(f"[{provider}] Failed to start login session: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/auth/screenshot/{session_id}")
async def get_screenshot(session_id: str):
    session = _sessions.get(session_id)
    if not session:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    try:
        page = session["page"]
        provider = session["provider"]
        config = LOGIN_PROVIDERS[provider]

        if session.get("navigating"):
            return JSONResponse({
                "image": "",
                "url": "Loading...",
                "title": "Navigating to " + config["name"],
                "logged_in": False,
                "loading": True,
                "viewport": {"width": VIEWPORT_W, "height": VIEWPORT_H},
            })

        # Try dismissing cookie popups - reset check when URL changes
        current_url = page.url
        if session.get("_last_consent_url") != current_url:
            session["consent_dismissed"] = False
            session["_last_consent_url"] = current_url
        if not session.get("consent_dismissed"):
            dismissed = await _try_quick_dismiss(page)
            if dismissed:
                session["consent_dismissed"] = True

        screenshot = await page.screenshot(type="jpeg", quality=70, full_page=True)
        b64 = base64.b64encode(screenshot).decode()

        # Get actual page dimensions for accurate click mapping
        dimensions = await page.evaluate("""() => ({
            scrollWidth: document.documentElement.scrollWidth,
            scrollHeight: document.documentElement.scrollHeight,
            scrollTop: window.scrollY
        })""")

        url = page.url
        title = await page.title()

        logged_in = any(s in url for s in config["logged_in_signals"])
        if not logged_in:
            check_fn = {
                "chatgpt": _check_chatgpt_logged_in,
                "claude": _check_claude_logged_in,
                "perplexity": _check_perplexity_logged_in,
            }.get(provider)
            if check_fn:
                logged_in = await check_fn(page)

        return JSONResponse({
            "image": b64,
            "url": url,
            "title": title,
            "logged_in": logged_in,
            "viewport": {"width": VIEWPORT_W, "height": VIEWPORT_H},
            "page_height": dimensions["scrollHeight"],
            "page_width": dimensions["scrollWidth"],
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/auth/click/{session_id}")
async def send_click(session_id: str, data: ClickInput):
    session = _sessions.get(session_id)
    if not session:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    try:
        page = session["page"]
        x = max(0, data.x)
        y = max(0, data.y)
        scroll_y = await page.evaluate("window.scrollY")
        if y < scroll_y or y > scroll_y + VIEWPORT_H:
            scroll_to = max(0, y - VIEWPORT_H // 2)
            await page.evaluate(f"window.scrollTo(0, {scroll_to})")
            await page.wait_for_timeout(100)
            scroll_y = await page.evaluate("window.scrollY")
        viewport_y = y - scroll_y
        await page.mouse.click(x, viewport_y)
        await page.wait_for_timeout(150)
        return JSONResponse({"status": "clicked", "x": x, "y": y})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/auth/type/{session_id}")
async def send_type(session_id: str, data: TypeInput):
    session = _sessions.get(session_id)
    if not session:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    try:
        page = session["page"]
        await page.keyboard.type(data.text, delay=30)
        return JSONResponse({"status": "typed", "text": data.text})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/auth/key/{session_id}")
async def send_key(session_id: str, data: KeyInput):
    session = _sessions.get(session_id)
    if not session:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    try:
        page = session["page"]
        await page.keyboard.press(data.key)
        await page.wait_for_timeout(100)
        return JSONResponse({"status": "pressed", "key": data.key})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


class ScrollInput(BaseModel):
    direction: str  # "up" or "down"
    amount: int = 300


@router.post("/api/auth/scroll/{session_id}")
async def send_scroll(session_id: str, data: ScrollInput):
    session = _sessions.get(session_id)
    if not session:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    try:
        page = session["page"]
        delta = -data.amount if data.direction == "up" else data.amount
        await page.mouse.wheel(0, delta)
        return JSONResponse({"status": "scrolled", "direction": data.direction})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/auth/save/{session_id}")
async def save_cookies(session_id: str):
    session = _sessions.get(session_id)
    if not session:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    try:
        page = session["page"]
        provider = session["provider"]
        config = LOGIN_PROVIDERS[provider]

        state = await page.context.storage_state()
        cookies = state.get("cookies", [])

        if not cookies:
            return JSONResponse({"error": "No cookies captured"}, status_code=400)

        # Warn if key auth cookies are missing
        warning = None
        if provider == "chatgpt":
            cookie_names = {c.get("name", "") for c in cookies}
            has_session = any("session-token" in n or "auth-session" in n for n in cookie_names)
            has_clearance = any("cf_clearance" in n for n in cookie_names)
            if not has_session:
                warning = "Session token not found - make sure login is fully complete before saving"

        dumped = json.dumps(state)
        db_key = config["db_key"]

        def _save(conn):
            conn.execute("""
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (key)
                DO UPDATE SET value = %s::jsonb, updated_at = NOW()
            """, (db_key, dumped, dumped))
            conn.commit()

        await run_db(_save)
        log.info(f"[{provider}] Saved {len(cookies)} cookies from session {session_id}")

        _clear_engine_cooldown(provider)

        # Google cookies work for Gemini too (same Google account)
        shared = []
        if provider == "google":
            await _copy_cookies_to("gemini_cookies", dumped)
            shared.append("Gemini")
        elif provider == "gemini":
            await _copy_cookies_to("google_cookies", dumped)
            shared.append("Google")

        await _cleanup_session(session_id)

        resp = {
            "status": "saved",
            "cookie_count": len(cookies),
            "provider": provider,
        }
        if shared:
            resp["shared_with"] = shared
        if warning:
            resp["warning"] = warning
        return JSONResponse(resp)

    except Exception as e:
        log.error(f"Failed to save cookies: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/auth/cancel/{session_id}")
async def cancel_login(session_id: str):
    await _cleanup_session(session_id)
    return JSONResponse({"status": "cancelled"})


@router.get("/api/auth/status/{provider}")
async def cookie_status(provider: str):
    if provider not in LOGIN_PROVIDERS:
        return JSONResponse({"error": "Unknown provider"}, status_code=400)
    status = await _get_cookie_status(provider)
    return JSONResponse(status)


class ManualImportInput(BaseModel):
    cookies_json: str
    localstorage_json: str | None = None


@router.post("/api/auth/import/{provider}")
async def import_cookies(provider: str, data: ManualImportInput):
    if provider not in LOGIN_PROVIDERS:
        return JSONResponse({"error": f"Unknown provider: {provider}"}, status_code=400)

    try:
        raw = json.loads(data.cookies_json)
    except json.JSONDecodeError as e:
        return JSONResponse({"error": f"Invalid JSON: {e}"}, status_code=400)

    # Detect format: Cookie-Editor array vs Playwright storage_state
    if isinstance(raw, list):
        # Cookie-Editor format: [{name, value, domain, path, ...}, ...]
        pw_cookies = []
        for c in raw:
            if not isinstance(c, dict) or "name" not in c or "value" not in c:
                continue
            cookie = {
                "name": c["name"],
                "value": c["value"],
                "domain": c.get("domain", ""),
                "path": c.get("path", "/"),
                "httpOnly": c.get("httpOnly", False),
                "secure": c.get("secure", False),
                "sameSite": c.get("sameSite", "Lax"),
            }
            if "expirationDate" in c:
                cookie["expires"] = c["expirationDate"]
            elif "expires" in c:
                cookie["expires"] = c["expires"]
            pw_cookies.append(cookie)

        if not pw_cookies:
            return JSONResponse({"error": "No valid cookies found in array"}, status_code=400)

        state = {"cookies": pw_cookies, "origins": []}

    elif isinstance(raw, dict) and "cookies" in raw:
        # Already Playwright storage_state format
        state = raw
    else:
        return JSONResponse(
            {"error": "Unrecognized format. Paste Cookie-Editor JSON array or Playwright storage_state object."},
            status_code=400,
        )

    # Merge localStorage if provided (for ChatGPT/Claude)
    if data.localstorage_json:
        try:
            ls_raw = json.loads(data.localstorage_json)
        except json.JSONDecodeError:
            return JSONResponse({"error": "Invalid localStorage JSON"}, status_code=400)

        config = LOGIN_PROVIDERS[provider]
        origin_url = config["start_url"].rstrip("/")
        # Accept either {key: value} dict or [[key, value], ...] array
        if isinstance(ls_raw, dict):
            ls_items = [{"name": k, "value": v} for k, v in ls_raw.items()]
        elif isinstance(ls_raw, list):
            ls_items = [{"name": pair[0], "value": pair[1]} for pair in ls_raw if isinstance(pair, list) and len(pair) >= 2]
        else:
            ls_items = []

        if ls_items:
            existing_origins = state.get("origins", [])
            existing_origins.append({"origin": origin_url, "localStorage": ls_items})
            state["origins"] = existing_origins

    dumped = json.dumps(state)
    db_key = LOGIN_PROVIDERS[provider]["db_key"]
    cookie_count = len(state.get("cookies", []))

    def _save(conn):
        conn.execute("""
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (%s, %s::jsonb, NOW())
            ON CONFLICT (key)
            DO UPDATE SET value = %s::jsonb, updated_at = NOW()
        """, (db_key, dumped, dumped))
        conn.commit()

    await run_db(_save)
    log.info(f"[{provider}] Imported {cookie_count} cookies manually")

    _clear_engine_cooldown(provider)

    shared = []
    if provider == "google":
        await _copy_cookies_to("gemini_cookies", dumped)
        shared.append("Gemini")
        _clear_engine_cooldown("gemini")
    elif provider == "gemini":
        await _copy_cookies_to("google_cookies", dumped)
        shared.append("Google")

    resp = {"status": "imported", "cookie_count": cookie_count, "provider": provider}
    if shared:
        resp["shared_with"] = shared
    return JSONResponse(resp)


# ── Helpers ────────────────────────────────────────────────

# Cookie consent selectors (order matters - specific first, generic last)
_CONSENT_SELECTORS = [
    # OneTrust (ChatGPT, many others)
    "#onetrust-accept-btn-handler",
    "button.onetrust-close-btn-handler",
    # CookieYes / Osano / Klaro
    "button.cky-btn-accept",
    "button.osano-cm-accept-all",
    "button.cm-btn-accept",
    # Claude specific
    "button[data-testid='accept-cookies']",
    "button:has-text('Accept All Cookies')",
    "button:has-text('Accept all cookies')",
    # Generic consent buttons (specific text to avoid hitting login buttons)
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
    "button:has-text('Accept cookies')",
    "button:has-text('Allow all')",
    "button:has-text('Allow All')",
    "button:has-text('I agree')",
    "button:has-text('Got it')",
    # aria-label patterns
    "button[aria-label*='accept' i]",
    "button[aria-label*='consent' i]",
]


async def _dismiss_cookie_popups(page):
    """Dismiss cookie consent banners and Google One Tap popup after page load."""
    try:
        await page.wait_for_timeout(1500)

        # Kill Google One Tap iframe (appears on Claude, others)
        await _dismiss_google_one_tap(page)

        for selector in _CONSENT_SELECTORS:
            try:
                btn = await page.query_selector(selector)
                if btn and await btn.is_visible():
                    await btn.click()
                    log.info(f"Dismissed cookie popup via: {selector}")
                    await page.wait_for_timeout(500)
                    return
            except Exception:
                continue
    except Exception:
        pass


async def _dismiss_google_one_tap(page):
    """Remove Google One Tap popup only - leave Google Sign-In button intact."""
    try:
        await page.evaluate("""() => {
            // Only target One Tap credential picker elements
            const selectors = [
                '#credential_picker_container',
                '#credential_picker_iframe',
                '[id*="credential_picker"]',
            ];
            selectors.forEach(sel => {
                document.querySelectorAll(sel).forEach(el => el.remove());
            });
            // Hide One Tap popup via CSS (won't affect the sign-in button)
            if (!document.getElementById('_no_onetap')) {
                const style = document.createElement('style');
                style.id = '_no_onetap';
                style.textContent = `
                    #credential_picker_container,
                    [id*="credential_picker"] { display: none !important; }
                `;
                document.head.appendChild(style);
            }
        }""")
    except Exception:
        pass


async def _post_nav_actions(page, config: dict, provider: str):
    """Provider-specific actions after page loads."""
    try:
        post_nav = config.get("post_nav")

        if post_nav == "click_sign_in":
            await page.wait_for_timeout(2000)
            for sel in [
                "button:has-text('Sign In')",
                "button:has-text('Sign in')",
                "a:has-text('Sign In')",
                "a:has-text('Sign in')",
                "a:has-text('Log In')",
                "a:has-text('Log in')",
                "[role='button']:has-text('Sign In')",
                "[role='button']:has-text('Sign in')",
                "div:has-text('Sign In')",
                "span:has-text('Sign In')",
            ]:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        await btn.click()
                        log.info(f"[{provider}] Clicked sign-in button: {sel}")
                        await page.wait_for_timeout(3000)
                        await _dismiss_cookie_popups(page)
                        return
                except Exception:
                    continue
            # Fallback: JS-based click on any element containing "Sign in" text
            clicked = await page.evaluate("""() => {
                const els = document.querySelectorAll('a, button, [role="button"], div, span');
                for (const el of els) {
                    const t = el.textContent.trim();
                    if ((t === 'Sign In' || t === 'Sign in') && el.offsetParent !== null) {
                        el.click();
                        return true;
                    }
                }
                return false;
            }""")
            if clicked:
                log.info(f"[{provider}] Clicked sign-in via JS fallback")
                await page.wait_for_timeout(3000)
                await _dismiss_cookie_popups(page)

        if provider == "claude":
            # Only block the One Tap popup/iframe - NOT the Google Sign-In button
            await page.evaluate("""() => {
                const observer = new MutationObserver(muts => {
                    for (const m of muts) {
                        for (const n of m.addedNodes) {
                            // Only remove One Tap credential picker elements
                            if (n.id && n.id.includes('credential_picker')) {
                                n.remove();
                            }
                            // Remove One Tap iframes (but NOT oauth/consent iframes)
                            if (n.tagName === 'IFRAME' && n.src &&
                                n.src.includes('accounts.google.com/gsi/') &&
                                n.src.includes('credential')) {
                                n.remove();
                            }
                        }
                    }
                });
                observer.observe(document.documentElement, {childList: true, subtree: true});
            }""")
            await _dismiss_google_one_tap(page)

    except Exception as e:
        log.warning(f"[{provider}] Post-nav action failed: {e}")


async def _try_quick_dismiss(page) -> bool:
    """Fast check for cookie/consent popups - no waits, used during screenshot polling."""
    try:
        await _dismiss_google_one_tap(page)
        for selector in _CONSENT_SELECTORS:
            try:
                btn = await page.query_selector(selector)
                if btn and await btn.is_visible():
                    await btn.click()
                    log.info(f"Dismissed cookie popup (poll) via: {selector}")
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


async def _check_claude_logged_in(page) -> bool:
    try:
        for sel in [
            "div[contenteditable='true']",
            "div.ProseMirror",
            "button[data-testid='user-menu']",
            "img[alt*='Avatar']",
            "button:has-text('New task')",
            "button:has-text('New chat')",
            "a[href='/new']",
        ]:
            el = await page.query_selector(sel)
            if el:
                return True
    except Exception:
        pass
    return False


async def _check_chatgpt_logged_in(page) -> bool:
    try:
        for sel in [
            "textarea",
            "div[contenteditable='true'][id='prompt-textarea']",
            "button[aria-label*='User']",
            "img[alt*='User']",
        ]:
            el = await page.query_selector(sel)
            if el:
                return True
    except Exception:
        pass
    return False


async def _check_perplexity_logged_in(page) -> bool:
    try:
        for sel in [
            "textarea",
            "button[aria-label='Submit']",
            "a[href='/library']",
            "a[href='/settings']",
        ]:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                url = page.url
                if "perplexity.ai" in url and "/login" not in url and "/signup" not in url:
                    return True
    except Exception:
        pass
    return False


async def _cleanup_session(session_id: str):
    session = _sessions.pop(session_id, None)
    if session:
        try:
            camo = session.get("camo_browser")
            if camo:
                await camo.__aexit__(None, None, None)
        except Exception:
            pass
        log.info(f"Login session cleaned up: {session_id}")


async def _copy_cookies_to(target_db_key: str, dumped_json: str):
    """Copy cookies to another provider's DB key (e.g., Google → Gemini)."""
    def _save(conn):
        conn.execute("""
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (%s, %s::jsonb, NOW())
            ON CONFLICT (key)
            DO UPDATE SET value = %s::jsonb, updated_at = NOW()
        """, (target_db_key, dumped_json, dumped_json))
        conn.commit()

    await run_db(_save)
    log.info(f"Shared cookies to {target_db_key}")


async def _get_cookie_status(provider: str) -> dict:
    config = LOGIN_PROVIDERS.get(provider, {})
    db_key = config.get("db_key", f"{provider}_cookies")
    domain_filter = config.get("cookie_domain_filter", provider)

    def _check(conn):
        row = conn.execute(
            "SELECT value, updated_at::text as updated_at FROM app_settings WHERE key = %s",
            (db_key,)
        ).fetchone()
        if not row:
            return None
        return {"updated_at": row["updated_at"], "cookies": row["value"]}

    result = await run_db(_check)
    if not result:
        return {"status": "none", "message": f"No {config.get('name', provider)} cookies configured"}

    cookies = result["cookies"]
    cookie_list = cookies.get("cookies", []) if isinstance(cookies, dict) else []
    if isinstance(domain_filter, list):
        matched = [c for c in cookie_list if any(f in c.get("domain", "") for f in domain_filter)]
    else:
        matched = [c for c in cookie_list if domain_filter in c.get("domain", "")]

    return {
        "status": "configured",
        "updated_at": result["updated_at"],
        "cookie_count": len(matched),
        "total_cookies": len(cookie_list),
    }


async def _load_full_state(db_key: str) -> dict | None:
    def _fetch(conn):
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = %s", (db_key,)
        ).fetchone()
        if not row:
            return None
        return row["value"]

    state = await run_db(_fetch)
    if not state or not isinstance(state, dict):
        return None
    return state


async def _load_cookies_from_db(db_key: str) -> list[dict] | None:
    def _fetch(conn):
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = %s", (db_key,)
        ).fetchone()
        if not row:
            return None
        return row["value"]

    state = await run_db(_fetch)
    if not state:
        return None
    if isinstance(state, dict):
        return state.get("cookies", [])
    return None


# ── Public helpers for other modules ───────────────────────
# These now route through the account pool for multi-account rotation.
# The pool returns the next available slot, handling cooldowns automatically.
# Falls back to direct DB load if pool not initialized.

_last_used_db_key: dict[str, str] = {}


def get_last_used_db_key(engine_name: str) -> str | None:
    key = _last_used_db_key.get(engine_name)
    if not key and engine_name in ("google_aio", "google_ai_mode"):
        key = _last_used_db_key.get("google")
    return key


async def _pool_storage_state(engine_name: str) -> dict | None:
    try:
        from app.agent.account_pool import get_storage_state
        state, db_key = await get_storage_state(engine_name)
        if db_key:
            _last_used_db_key[engine_name] = db_key
        if state:
            return state
    except Exception:
        pass
    fallback_key = f"{engine_name}_cookies"
    _last_used_db_key[engine_name] = fallback_key
    return await _load_full_state(fallback_key)


async def _pool_cookies(engine_name: str) -> list[dict] | None:
    state = await _pool_storage_state(engine_name)
    if state and isinstance(state, dict):
        return state.get("cookies", [])
    return None


async def load_google_cookies() -> list[dict] | None:
    return await _pool_cookies("google")


async def load_chatgpt_cookies() -> list[dict] | None:
    return await _pool_cookies("chatgpt")


async def load_chatgpt_storage_state() -> dict | None:
    return await _pool_storage_state("chatgpt")


async def load_gemini_storage_state() -> dict | None:
    return await _pool_storage_state("gemini")


async def load_claude_storage_state() -> dict | None:
    return await _pool_storage_state("claude")


async def load_perplexity_storage_state() -> dict | None:
    return await _pool_storage_state("perplexity")


async def _refresh_all_slots(base_key: str, visit_url: str):
    """Refresh cookies for all existing slots of an engine."""
    def _find_keys(conn):
        rows = conn.execute(
            "SELECT key FROM app_settings WHERE key = %s OR key LIKE %s ORDER BY key",
            (base_key, f"{base_key}_%"),
        ).fetchall()
        return [r["key"] for r in rows]
    keys = await run_db(_find_keys)
    if not keys:
        keys = [base_key]
    for k in keys:
        await _refresh_cookies(k, visit_url)
        if len(keys) > 1:
            await asyncio.sleep(10)


async def refresh_google_cookies():
    await _refresh_all_slots("google_cookies", "https://www.google.com")


async def refresh_chatgpt_cookies():
    await _refresh_all_slots("chatgpt_cookies", "https://chatgpt.com")


async def refresh_gemini_cookies():
    await _refresh_all_slots("gemini_cookies", "https://gemini.google.com/app")


async def refresh_claude_cookies():
    await _refresh_all_slots("claude_cookies", "https://claude.ai")


async def refresh_perplexity_cookies():
    await _refresh_all_slots("perplexity_cookies", "https://www.perplexity.ai")


# ── Credential Management API ─────────────────────────────

@router.post("/api/auth/credentials")
async def save_credentials(cred: CredentialInput):
    """Save encrypted credentials for an engine. Supports multi-slot via slot_index."""
    if cred.provider not in LOGIN_PROVIDERS and cred.provider not in AUTO_LOGIN_FLOWS:
        return JSONResponse({"error": f"Unknown provider: {cred.provider}"}, status_code=400)
    encrypted_email = _encrypt(cred.email)
    encrypted_password = _encrypt(cred.password)
    payload = json.dumps({"email": encrypted_email, "password": encrypted_password})

    if cred.slot_index is not None:
        slot_idx = cred.slot_index
    else:
        slot_idx = await _next_free_cred_slot(cred.provider, cred.email)

    db_key = f"creds_{cred.provider}_{slot_idx}"

    def _save(conn):
        conn.execute("""
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (%s, %s::jsonb, NOW())
            ON CONFLICT (key) DO UPDATE SET value = %s::jsonb, updated_at = NOW()
        """, (db_key, payload, payload))
        conn.commit()
    await run_db(_save)
    log.info(f"Credentials saved for {cred.provider} slot {slot_idx}")
    return {"status": "saved", "provider": cred.provider, "slot_index": slot_idx}


async def _next_free_cred_slot(provider: str, email: str, max_slots: int = 10) -> int:
    """Find existing slot for this email, or next free slot index."""
    def _scan(conn):
        rows = conn.execute(
            "SELECT key, value FROM app_settings WHERE key LIKE %s ORDER BY key",
            (f"creds_{provider}_%",),
        ).fetchall()
        return rows

    rows = await run_db(_scan)

    for r in rows:
        try:
            data = r["value"] if isinstance(r["value"], dict) else json.loads(r["value"])
            existing_email = _decrypt(data["email"])
            if existing_email.lower() == email.lower():
                return int(r["key"].rsplit("_", 1)[-1])
        except Exception:
            continue

    used_indices = set()
    for r in rows:
        try:
            idx = int(r["key"].rsplit("_", 1)[-1])
            used_indices.add(idx)
        except ValueError:
            continue

    for i in range(max_slots):
        if i not in used_indices:
            return i
    return len(used_indices)


@router.get("/api/auth/credentials")
async def list_credentials():
    """List all credential slots per provider with masked emails."""
    def _list(conn):
        rows = conn.execute(
            "SELECT key, value, updated_at::text FROM app_settings WHERE key LIKE 'creds_%%' ORDER BY key"
        ).fetchall()
        return rows

    rows = await run_db(_list)
    result = {}
    for r in rows:
        key = r["key"]
        parts = key.split("_", 1)
        if len(parts) < 2:
            continue
        rest = parts[1]  # e.g. "google_0" or "chatgpt_2"

        # Parse provider and slot_index from key like creds_google_0, creds_chatgpt_1
        # Also handle legacy creds_google (no slot index)
        slot_parts = rest.rsplit("_", 1)
        try:
            slot_index = int(slot_parts[-1])
            provider = slot_parts[0] if len(slot_parts) > 1 else rest
        except ValueError:
            provider = rest
            slot_index = 0

        masked_email = ""
        try:
            data = r["value"] if isinstance(r["value"], dict) else json.loads(r["value"])
            email = _decrypt(data["email"])
            ep = email.split("@")
            if len(ep) == 2:
                name = ep[0]
                masked_email = name[:3] + "***@" + ep[1] if len(name) > 3 else name[0] + "***@" + ep[1]
            else:
                masked_email = email[:4] + "***"
        except Exception:
            masked_email = "***"

        if provider not in result:
            result[provider] = {"accounts": [], "has_credentials": True}
        result[provider]["accounts"].append({
            "slot_index": slot_index,
            "masked_email": masked_email,
            "updated_at": r["updated_at"],
            "db_key": key,
        })

    return {"credentials": result}


@router.delete("/api/auth/credentials/{provider}")
async def delete_credentials(provider: str, slot_index: int = 0):
    """Remove stored credentials for a specific slot."""
    db_key = f"creds_{provider}_{slot_index}"

    def _del(conn):
        conn.execute("DELETE FROM app_settings WHERE key = %s", (db_key,))
        conn.commit()
    await run_db(_del)
    return {"status": "deleted", "provider": provider, "slot_index": slot_index}


async def _load_credentials(provider: str, slot_index: int | None = None) -> dict | None:
    """Load and decrypt credentials. If slot_index given, load that slot.
    Otherwise load first available slot (creds_{provider}_0, _1, etc).
    Falls back to legacy creds_{provider} key."""
    if slot_index is not None:
        keys_to_try = [f"creds_{provider}_{slot_index}"]
    else:
        keys_to_try = [f"creds_{provider}_{i}" for i in range(10)]
    keys_to_try.append(f"creds_{provider}")

    def _load(conn):
        for k in keys_to_try:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = %s", (k,)
            ).fetchone()
            if row:
                return row["value"]
        return None
    raw = await run_db(_load)
    if not raw:
        return None
    try:
        data = raw if isinstance(raw, dict) else json.loads(raw)
        return {
            "email": _decrypt(data["email"]),
            "password": _decrypt(data["password"]),
        }
    except Exception as e:
        log.error(f"Failed to decrypt credentials for {provider}: {e}")
        return None


async def _load_all_credentials(provider: str) -> list[dict]:
    """Load all credential slots for a provider. Returns list of {email, password, slot_index}."""
    def _load_all(conn):
        rows = conn.execute(
            "SELECT key, value FROM app_settings WHERE key LIKE %s ORDER BY key",
            (f"creds_{provider}_%",),
        ).fetchall()
        # Also check legacy key
        legacy = conn.execute(
            "SELECT key, value FROM app_settings WHERE key = %s",
            (f"creds_{provider}",),
        ).fetchone()
        result = list(rows)
        if legacy:
            result.append(legacy)
        return result

    rows = await run_db(_load_all)
    creds_list = []
    seen_emails = set()
    for r in rows:
        try:
            data = r["value"] if isinstance(r["value"], dict) else json.loads(r["value"])
            email = _decrypt(data["email"])
            if email.lower() in seen_emails:
                continue
            seen_emails.add(email.lower())
            key = r["key"]
            try:
                slot_idx = int(key.rsplit("_", 1)[-1])
            except ValueError:
                slot_idx = 0
            creds_list.append({
                "email": email,
                "password": _decrypt(data["password"]),
                "slot_index": slot_idx,
            })
        except Exception:
            continue
    return creds_list


# ── Credential Migration (legacy creds_X → creds_X_0) ────

async def migrate_legacy_credentials():
    """Migrate legacy creds_X keys to creds_X_0 format. Idempotent."""
    providers = ["google", "chatgpt", "claude", "perplexity", "gemini"]

    def _migrate(conn):
        migrated = []
        for p in providers:
            old_key = f"creds_{p}"
            new_key = f"creds_{p}_0"
            old_row = conn.execute(
                "SELECT value FROM app_settings WHERE key = %s", (old_key,)
            ).fetchone()
            if not old_row:
                continue
            existing_new = conn.execute(
                "SELECT key FROM app_settings WHERE key = %s", (new_key,)
            ).fetchone()
            if existing_new:
                continue
            conn.execute("""
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (%s, %s, NOW())
            """, (new_key, json.dumps(old_row["value"]) if not isinstance(old_row["value"], str) else old_row["value"]))
            conn.execute("DELETE FROM app_settings WHERE key = %s", (old_key,))
            migrated.append(p)
        if migrated:
            conn.commit()
        return migrated

    migrated = await run_db(_migrate)
    if migrated:
        log.info(f"Migrated legacy credentials to slot format: {migrated}")


# ── Auto-Login Engine ─────────────────────────────────────

@router.post("/api/auth/auto-login/{provider}")
async def trigger_auto_login(provider: str, request: Request):
    """Start automated login for an engine. Accepts optional cred_slot_index and target_cookie_slot."""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    cred_slot = body.get("cred_slot_index")
    target_cookie_slot = body.get("target_cookie_slot")

    flow_key = provider
    if provider == "gemini":
        flow_key = "google"

    flow = AUTO_LOGIN_FLOWS.get(flow_key)
    if not flow:
        return JSONResponse({"error": f"No auto-login flow for: {provider}"}, status_code=400)

    cred_provider = flow_key
    if provider == "claude":
        cred_provider = "claude"
    creds = await _load_credentials(cred_provider, slot_index=cred_slot)
    if not creds and cred_provider != "google":
        creds = await _load_credentials("google", slot_index=cred_slot)
    if not creds:
        creds = await _load_credentials(provider, slot_index=cred_slot)
    if not creds:
        return JSONResponse({"error": f"No credentials stored for {cred_provider}. Save credentials first."}, status_code=400)

    session_id = str(uuid.uuid4())[:8]
    asyncio.create_task(_run_auto_login(session_id, provider, flow_key, flow, creds, target_cookie_slot=target_cookie_slot))

    return {"status": "started", "session_id": session_id, "provider": provider, "target_cookie_slot": target_cookie_slot}


@router.post("/api/auth/auto-login/{session_id}/otp")
async def submit_otp(session_id: str, otp: OTPInput):
    """Submit OTP code for a pending auto-login session."""
    if session_id not in _otp_events:
        return JSONResponse({"error": "No pending OTP request for this session"}, status_code=404)
    _otp_values[session_id] = otp.code
    _otp_events[session_id].set()
    return {"status": "otp_submitted"}


@router.get("/api/auth/auto-login/{session_id}/status")
async def auto_login_status(session_id: str):
    """Check status of an auto-login session."""
    session = _auto_login_sessions.get(session_id)
    if not session:
        return {"status": "unknown", "session_id": session_id}
    return {
        "status": session.get("status", "unknown"),
        "provider": session.get("provider"),
        "step": session.get("step", ""),
        "message": session.get("message", ""),
        "needs_otp": session.get("needs_otp", False),
    }


@router.post("/api/auth/multi-login")
async def trigger_multi_account_login(request: Request):
    """Login all engines using all available Google accounts, distributing across cookie slots.
    Staggers logins with delays to avoid IP burnout."""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    delay_between = body.get("delay_seconds", 30)

    google_creds = await _load_all_credentials("google")
    if not google_creds:
        return JSONResponse({"error": "No Google credentials stored. Add accounts first."}, status_code=400)

    engines_needing_google = ["google", "chatgpt", "claude", "perplexity"]
    plan = []
    for eng in engines_needing_google:
        flow_key = "google" if eng in ("google", "gemini") else eng
        flow = AUTO_LOGIN_FLOWS.get(flow_key)
        if not flow:
            continue
        for i, cred in enumerate(google_creds):
            plan.append({
                "engine": eng,
                "flow_key": flow_key,
                "cred": cred,
                "target_cookie_slot": i,
            })

    async def _execute_plan():
        from app.events import broadcast
        from app.agent.scraper import pause_scraping, resume_scraping
        pause_scraping()
        results = []
        try:
            for idx, item in enumerate(plan):
                if idx > 0:
                    wait_secs = delay_between
                    log.info(f"[multi-login] Waiting {wait_secs}s before next login ({item['engine']} slot {item['target_cookie_slot']})...")
                    await asyncio.sleep(wait_secs)

                session_id = f"multi_{item['engine']}_{item['target_cookie_slot']}_{str(uuid.uuid4())[:4]}"
                log.info(f"[multi-login] Starting {item['engine']} slot {item['target_cookie_slot']} with {item['cred']['email'][:4]}***")
                broadcast("multi_login", engine=item["engine"], slot=item["target_cookie_slot"], step="starting")

                await _run_auto_login(
                    session_id, item["engine"], item["flow_key"],
                    AUTO_LOGIN_FLOWS[item["flow_key"]], item["cred"],
                    target_cookie_slot=item["target_cookie_slot"],
                )

                session = _auto_login_sessions.get(session_id, {})
                status = session.get("status", "unknown")
                results.append({"engine": item["engine"], "slot": item["target_cookie_slot"], "status": status})
                _auto_login_sessions.pop(session_id, None)
        finally:
            resume_scraping()

        broadcast("multi_login_complete", results=results)
        log.info(f"[multi-login] Complete: {results}")

    asyncio.create_task(_execute_plan())

    return {
        "status": "started",
        "total_logins": len(plan),
        "accounts": len(google_creds),
        "engines": engines_needing_google,
        "estimated_time_minutes": round(len(plan) * delay_between / 60, 1),
    }


async def _detect_captcha(page) -> bool:
    """Check if page shows CAPTCHA or robot verification."""
    captcha_selectors = [
        "iframe[src*='recaptcha']", "iframe[src*='hcaptcha']", "iframe[title*='reCAPTCHA']",
        "div.g-recaptcha", "div.h-captcha", "#captcha", "div[class*='captcha']",
        "iframe[src*='challenges.cloudflare.com']", "div#challenge-running",
        "div[class*='cf-turnstile']", "iframe[src*='turnstile']",
    ]
    for sel in captcha_selectors:
        try:
            if await page.locator(sel).first.is_visible(timeout=500):
                return True
        except Exception:
            continue
    try:
        text = await page.inner_text("body")
        captcha_phrases = ["verify you are human", "robot", "captcha", "i'm not a robot",
                           "security check", "prove you're human", "challenge",
                           "type the text you hear or see"]
        if any(p in text.lower() for p in captcha_phrases):
            return True
    except Exception:
        pass
    return False


async def _detect_google_signin_captcha(page) -> bool:
    """Detect Google sign-in page text CAPTCHA ('Type the text you hear or see')."""
    try:
        text = await page.inner_text("body")
        return "type the text you hear or see" in text.lower()
    except Exception:
        return False


async def _solve_google_signin_captcha(page, max_attempts: int = 2) -> bool:
    """Solve Google sign-in text CAPTCHA using OpenAI Vision API.
    Returns True if solved successfully."""
    settings = get_settings()
    if not settings.openai_api_key:
        log.warning("[captcha-solver] No OpenAI API key — cannot solve CAPTCHA")
        return False

    for attempt in range(max_attempts):
        try:
            # Find the CAPTCHA image element
            captcha_img = None
            img_selectors = [
                "img[src*='captcha']", "img[alt*='captcha']",
                "img[src*='sorry']", "img[id*='captcha']",
                "canvas", "img[src*='accounts.google']",
            ]
            for sel in img_selectors:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=1500):
                        captcha_img = el
                        break
                except Exception:
                    continue

            if not captcha_img:
                # Fallback: screenshot the CAPTCHA area
                captcha_area = page.locator("div:has(> img):has(+ input), div:has(> img):has(~ input)").first
                try:
                    if await captcha_area.is_visible(timeout=1000):
                        captcha_img = captcha_area
                except Exception:
                    pass

            if not captcha_img:
                log.warning(f"[captcha-solver] Attempt {attempt+1}: could not locate CAPTCHA image")
                continue

            # Screenshot just the CAPTCHA element
            img_bytes = await captcha_img.screenshot()
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")

            log.info(f"[captcha-solver] Attempt {attempt+1}: sending CAPTCHA image to OpenAI Vision")

            # Send to OpenAI Vision API
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Read the distorted/warped text in this CAPTCHA image. Return ONLY the text characters you see, nothing else. No quotes, no explanation."},
                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}}
                            ]
                        }],
                        "max_tokens": 50,
                    }
                )

            if resp.status_code != 200:
                log.warning(f"[captcha-solver] OpenAI API error: {resp.status_code}")
                continue

            result = resp.json()
            captcha_text = result["choices"][0]["message"]["content"].strip()
            captcha_text = captcha_text.strip('"\'` ')

            if not captcha_text or len(captcha_text) < 2:
                log.warning(f"[captcha-solver] Empty or too short result: '{captcha_text}'")
                continue

            log.info(f"[captcha-solver] Attempt {attempt+1}: read text = '{captcha_text}'")

            # Find the input field and type the solution
            input_selectors = [
                "input[name*='captcha']", "input[id*='captcha']",
                "input[aria-label*='Type the text']",
                "input[type='text']:not([name='identifier']):not([name='Email'])",
            ]
            input_el = None
            for sel in input_selectors:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=1000):
                        input_el = el
                        break
                except Exception:
                    continue

            if not input_el:
                log.warning("[captcha-solver] Could not find CAPTCHA input field")
                continue

            await input_el.fill("")
            await input_el.type(captcha_text, delay=80)
            await page.wait_for_timeout(500)

            # Click Next
            next_selectors = [
                "button:has-text('Next')", "input[type='submit']",
                "button[type='submit']", "#next button",
            ]
            for sel in next_selectors:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=1000):
                        await el.click()
                        break
                except Exception:
                    continue

            await page.wait_for_timeout(3000)

            # Check if CAPTCHA is gone
            still_captcha = await _detect_google_signin_captcha(page)
            if not still_captcha:
                log.info(f"[captcha-solver] CAPTCHA solved on attempt {attempt+1}")
                return True
            else:
                log.warning(f"[captcha-solver] Attempt {attempt+1} failed — CAPTCHA still present")

        except Exception as e:
            log.error(f"[captcha-solver] Attempt {attempt+1} error: {e}")

    log.warning("[captcha-solver] All CAPTCHA solve attempts exhausted")
    return False


async def _prewarm_browser_for_login(page):
    """Visit Google properties to build cookie history before login, reducing CAPTCHA risk."""
    try:
        await page.goto("https://www.google.com", wait_until="domcontentloaded", timeout=10000)
        await page.wait_for_timeout(2000)

        # Dismiss cookie consent if present
        await _dismiss_cookie_consent(page)

        # Do a simple search to look more human
        search_box = page.locator("textarea[name='q'], input[name='q']").first
        try:
            if await search_box.is_visible(timeout=2000):
                await search_box.type("weather today", delay=100)
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(3000)
        except Exception:
            pass

        log.info("[auto-login] Browser pre-warmed with Google visit")
    except Exception as e:
        log.debug(f"[auto-login] Pre-warm failed (non-critical): {e}")


async def _dismiss_cookie_consent(page) -> bool:
    """Dismiss cookie consent banners. Returns True if dismissed."""
    consent_selectors = [
        "button:has-text('Allow all')",
        "button:has-text('Accept all')",
        "button:has-text('Accept All')",
        "button:has-text('Accept cookies')",
        "button:has-text('Accept Cookies')",
        "button:has-text('I agree')",
        "button:has-text('Got it')",
        "button:has-text('OK')",
        "button:has-text('Only necessary')",
        "button:has-text('Reject all')",
        "button[id*='accept']",
        "button[class*='accept']",
        "a:has-text('Accept')",
        "[data-testid='cookie-accept']",
        "div[class*='cookie'] button:first-of-type",
    ]
    for sel in consent_selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1500):
                await el.click()
                log.info(f"[auto-login] Dismissed cookie consent via: {sel}")
                await page.wait_for_timeout(1000)
                return True
        except Exception:
            continue
    return False


async def _complete_google_oauth_on_page(target_page, creds: dict, session: dict, session_id: str, target_provider: str):
    """Fill Google email/password on an OAuth page (popup or redirect). Returns True if completed."""
    from app.events import broadcast
    await target_page.wait_for_timeout(2000)

    # Check for CAPTCHA on OAuth page and try to solve
    if await _detect_google_signin_captcha(target_page):
        log.warning(f"[auto-login] {target_provider} CAPTCHA on Google OAuth page — attempting solve")
        solved = await _solve_google_signin_captcha(target_page)
        if not solved:
            log.warning(f"[auto-login] {target_provider} CAPTCHA on OAuth page unsolvable")
            return False
        await target_page.wait_for_timeout(1000)

    # Check if account picker is shown (Google session preloaded)
    try:
        account_email = creds.get("email", "")
        picker_selectors = [
            f"div[data-email='{account_email}']",
            f"li[data-email='{account_email}']",
            f"div[data-identifier='{account_email}']",
            f"div:has-text('{account_email}')",
        ]
        for sel in picker_selectors:
            try:
                el = target_page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    await el.click()
                    log.info(f"[auto-login] {target_provider} picked Google account from chooser")
                    await target_page.wait_for_timeout(3000)
                    # Handle Google consent/permission screen after account pick
                    consent_selectors = [
                        "button:has-text('Continue')",
                        "button:has-text('Allow')",
                        "button:has-text('Next')",
                        "#submit_approve_access",
                        "button[id='submit_approve_access']",
                    ]
                    for cs in consent_selectors:
                        try:
                            cbtn = target_page.locator(cs).first
                            if await cbtn.is_visible(timeout=2000):
                                await cbtn.click()
                                log.info(f"[auto-login] {target_provider} clicked consent: {cs}")
                                await target_page.wait_for_timeout(3000)
                                break
                        except Exception:
                            continue
                    return True
            except Exception:
                continue
    except Exception:
        pass

    # No account picker - fill email/password manually
    try:
        email_selectors = [
            "input[type='email']", "input[name='identifier']",
            "input[autocomplete='username']", "input[aria-label='Email or phone']",
        ]
        email_field = None
        for esel in email_selectors:
            try:
                ef = target_page.locator(esel).first
                if await ef.is_visible(timeout=2000):
                    email_field = ef
                    log.info(f"[auto-login] {target_provider} OAuth email field found: {esel}")
                    break
            except Exception:
                continue

        if email_field:
            session["message"] = "Entering Google email..."
            broadcast("auto_login", provider=target_provider, step="google_email", message=session["message"])
            await email_field.click()
            await target_page.wait_for_timeout(300)
            await email_field.fill("")
            await target_page.keyboard.type(creds.get("email", ""), delay=40)
            await target_page.wait_for_timeout(500)

            # Click Next
            for sel in ["#identifierNext", "div#identifierNext", "button:has-text('Next')", "button[type='submit']"]:
                try:
                    btn = target_page.locator(sel).first
                    if await btn.is_visible(timeout=3000):
                        await btn.scroll_into_view_if_needed()
                        await target_page.wait_for_timeout(300)
                        await btn.click(force=True)
                        log.info(f"[auto-login] {target_provider} OAuth clicked Next: {sel}")
                        break
                except Exception:
                    continue
            await target_page.wait_for_timeout(3000)

            # Password
            pwd_field = target_page.locator("input[type='password'], input[name='Passwd'], input[name='password']").first
            try:
                if await pwd_field.is_visible(timeout=5000):
                    session["message"] = "Entering Google password..."
                    broadcast("auto_login", provider=target_provider, step="google_password", message=session["message"])
                    await pwd_field.click()
                    await target_page.wait_for_timeout(300)
                    await pwd_field.fill("")
                    await target_page.keyboard.type(creds.get("password", ""), delay=40)
                    await target_page.wait_for_timeout(500)

                    for sel in ["#passwordNext", "div#passwordNext", "button:has-text('Next')", "button[type='submit']"]:
                        try:
                            btn = target_page.locator(sel).first
                            if await btn.is_visible(timeout=3000):
                                await btn.scroll_into_view_if_needed()
                                await target_page.wait_for_timeout(300)
                                await btn.click(force=True)
                                log.info(f"[auto-login] {target_provider} OAuth clicked password Next: {sel}")
                                break
                        except Exception:
                            continue
                    await target_page.wait_for_timeout(3000)
                    return True
            except Exception:
                pass
        else:
            log.info(f"[auto-login] {target_provider} no email field on OAuth page — may be account picker or already authed")
    except Exception as e:
        log.warning(f"[auto-login] {target_provider} Google OAuth fill error: {e}")

    return False


async def _run_auto_login(session_id: str, target_provider: str, flow_key: str, flow: dict, creds: dict, target_cookie_slot: int | None = None):
    """Execute automated login flow with CAPTCHA detection, OTP support, and interactive fallback."""
    from app.events import broadcast

    session = {
        "status": "running",
        "provider": target_provider,
        "step": "starting",
        "message": "Launching browser...",
        "needs_otp": False,
        "needs_interactive": False,
    }
    _auto_login_sessions[session_id] = session

    broadcast("auto_login", provider=target_provider, step="starting", message="Launching browser...")

    _sem_acquired = False

    try:
        log.info(f"[auto-login] {target_provider} launching browser (bypasses scraper pool)...")
        async with AsyncCamoufox(
            headless=_HEADLESS, humanize=True, block_webrtc=True, os="windows",
        ) as browser:
            # Preload Google cookies into context when flow requests it
            ctx_kwargs = {"viewport": {"width": VIEWPORT_W, "height": VIEWPORT_H}}
            if flow.get("preload_google_cookies"):
                google_db_key = "google_cookies" if not target_cookie_slot else f"google_cookies_{target_cookie_slot}"
                google_state = await _load_full_state(google_db_key)
                if not google_state or not google_state.get("cookies"):
                    google_state = await _load_full_state("google_cookies")
                if google_state and google_state.get("cookies"):
                    ctx_kwargs["storage_state"] = google_state
                    log.info(f"[auto-login] {target_provider} preloaded Google session ({len(google_state['cookies'])} cookies)")

            context = await browser.new_context(**ctx_kwargs)
            page = await context.new_page()
            page.on("pageerror", lambda _: None)

            # Pre-warm browser to reduce CAPTCHA risk for Google flows
            if flow_key == "google":
                session["step"] = "prewarming"
                session["message"] = "Pre-warming browser..."
                broadcast("auto_login", provider=target_provider, step="prewarming", message=session["message"])
                await _prewarm_browser_for_login(page)

            session["step"] = "navigating"
            session["message"] = f"Navigating to {flow['url']}..."
            broadcast("auto_login", provider=target_provider, step="navigating", message=session["message"])

            try:
                await page.goto(flow["url"], wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass

            # Wait for page to fully render — check for email field or CAPTCHA
            for _wait in range(10):
                await page.wait_for_timeout(1500)
                try:
                    # Check if email field or CAPTCHA input is visible
                    email_visible = await page.locator("input[type='email'], input[name='identifier']").first.is_visible(timeout=500)
                    if email_visible:
                        break
                    captcha_input = await page.locator("input[aria-label*='Type the text'], input[name*='captcha']").first.is_visible(timeout=500)
                    if captcha_input:
                        break
                except Exception:
                    continue

            # Debug: log page state after render
            try:
                page_text = (await page.inner_text("body"))[:500]
                log.info(f"[auto-login] {target_provider} page rendered (URL={page.url[:80]}): {page_text[:300]}")
                await page.screenshot(path=f"/tmp/login_debug_{target_provider}_{session_id}.png")
            except Exception as e:
                log.debug(f"[auto-login] debug capture failed: {e}")

            # Check for CAPTCHA — try to solve before giving up
            if await _detect_captcha(page):
                log.warning(f"[auto-login] {target_provider} CAPTCHA detected at start — attempting solve")
                session["message"] = "Solving CAPTCHA..."
                broadcast("auto_login", provider=target_provider, step="solving_captcha", message="Solving CAPTCHA...")

                if await _detect_google_signin_captcha(page):
                    solved = await _solve_google_signin_captcha(page)
                else:
                    solved = False

                if not solved:
                    session["status"] = "blocked"
                    session["needs_interactive"] = True
                    session["message"] = "CAPTCHA could not be solved. Use manual browser login."
                    broadcast("auto_login", provider=target_provider, step="blocked",
                              session_id=session_id,
                              message="CAPTCHA unsolvable - click 'Redo as Interactive' to login manually.")
                    log.warning(f"[auto-login] {target_provider} CAPTCHA unsolvable")
                    await context.close()
                    return
                log.info(f"[auto-login] {target_provider} CAPTCHA solved, continuing login")

            # ── Early logged-in detection (preloaded cookies may have already authenticated) ──
            already_logged_in = False
            provider_cfg = LOGIN_PROVIDERS.get(target_provider, {})
            current_url = page.url
            if any(sig in current_url for sig in provider_cfg.get("logged_in_signals", [])):
                already_logged_in = True
                log.info(f"[auto-login] {target_provider} already logged in via URL: {current_url[:80]}")
            if not already_logged_in:
                check_fn = {
                    "chatgpt": _check_chatgpt_logged_in,
                    "claude": _check_claude_logged_in,
                    "perplexity": _check_perplexity_logged_in,
                }.get(target_provider)
                if check_fn:
                    try:
                        already_logged_in = await check_fn(page)
                        if already_logged_in:
                            log.info(f"[auto-login] {target_provider} already logged in via UI check")
                    except Exception:
                        pass
            if not already_logged_in and target_provider == "perplexity":
                try:
                    has_sidebar = await page.locator("a:has-text('Spaces'), a:has-text('History'), a:has-text('Customize')").first.is_visible(timeout=2000)
                    if has_sidebar:
                        already_logged_in = True
                        log.info(f"[auto-login] perplexity already logged in via sidebar detection")
                except Exception:
                    pass
            if not already_logged_in and target_provider == "chatgpt":
                try:
                    has_chat_ui = await page.locator("a:has-text('New chat'), a:has-text('Deep research'), a:has-text('Search chats')").first.is_visible(timeout=2000)
                    if has_chat_ui:
                        already_logged_in = True
                        log.info(f"[auto-login] chatgpt already logged in via chat UI detection")
                except Exception:
                    pass

            if already_logged_in:
                log.info(f"[auto-login] {target_provider} skipping login steps — already authenticated")
                session["step"] = "already_logged_in"
                session["message"] = "Already logged in! Saving cookies..."
                broadcast("auto_login", provider=target_provider, step="already_logged_in", message="Already authenticated via preloaded cookies!")

            steps_to_run = [] if already_logged_in else flow["steps"]
            for i, step_def in enumerate(steps_to_run):
                action = step_def["action"]
                session["step"] = f"step_{i}_{action}"
                log.info(f"[auto-login] {target_provider} executing step {i}: {action}")

                if action == "wait":
                    await page.wait_for_timeout(step_def["duration"])
                    # Check for CAPTCHA after each wait — try solving before giving up
                    if await _detect_captcha(page):
                        log.warning(f"[auto-login] {target_provider} CAPTCHA at step {i} — attempting solve")
                        session["message"] = "Solving CAPTCHA..."
                        broadcast("auto_login", provider=target_provider, step="solving_captcha", message="Solving CAPTCHA...")
                        solved = False
                        if await _detect_google_signin_captcha(page):
                            solved = await _solve_google_signin_captcha(page)
                        if not solved:
                            session["status"] = "blocked"
                            session["needs_interactive"] = True
                            session["message"] = "CAPTCHA could not be solved. Use manual browser login."
                            broadcast("auto_login", provider=target_provider, step="blocked",
                                      session_id=session_id,
                                      message="CAPTCHA unsolvable - click 'Redo as Interactive' to login manually.")
                            log.warning(f"[auto-login] {target_provider} CAPTCHA unsolvable at step {i}")
                            await context.close()
                            return

                elif action == "dismiss_cookies":
                    session["message"] = "Dismissing cookie consent..."
                    broadcast("auto_login", provider=target_provider, step="dismiss_cookies", message=session["message"])
                    await _dismiss_cookie_consent(page)
                    await page.wait_for_timeout(500)

                elif action == "google_oauth":
                    session["message"] = "Finding Google login button..."
                    broadcast("auto_login", provider=target_provider, step="google_oauth", message=session["message"])

                    google_btn_selectors = [
                        "button:has-text('Continue with Google')",
                        "a:has-text('Continue with Google')",
                        "button:has-text('Sign in with Google')",
                        "a:has-text('Sign in with Google')",
                        "button:has-text('Google')",
                        "a:has-text('Google')",
                        "div[data-provider='google']",
                        "[data-testid*='google']",
                        "button[class*='google']",
                        "a[class*='google']",
                        "button[aria-label*='Google']",
                        "a[href*='accounts.google.com']",
                        "img[alt*='Google']",
                    ]

                    # Try to find and click Google OAuth button
                    google_clicked = False
                    for sel in google_btn_selectors:
                        try:
                            el = page.locator(sel).first
                            if await el.is_visible(timeout=2000):
                                oauth_page = page
                                try:
                                    # Start popup listener, then click
                                    popup_future = asyncio.ensure_future(
                                        page.context.wait_for_event("page", timeout=8000)
                                    )
                                    await el.click()
                                    popup = await popup_future
                                    await popup.wait_for_load_state("domcontentloaded", timeout=10000)
                                    oauth_page = popup
                                    log.info(f"[auto-login] {target_provider} Google OAuth opened in popup")
                                except Exception:
                                    if not popup_future.done():
                                        popup_future.cancel()
                                    # No popup - same-tab redirect
                                    await page.wait_for_timeout(3000)
                                    log.info(f"[auto-login] {target_provider} Google OAuth same-tab redirect")
                                google_clicked = True
                                log.info(f"[auto-login] {target_provider} clicked Google OAuth via: {sel}")

                                # Complete Google auth on whichever page
                                await _complete_google_oauth_on_page(oauth_page, creds, session, session_id, target_provider)

                                # If popup, wait for it to close and switch back
                                if oauth_page != page:
                                    try:
                                        await oauth_page.wait_for_event("close", timeout=15000)
                                    except Exception:
                                        pass
                                    await page.wait_for_timeout(2000)

                                break
                        except Exception:
                            continue

                    if not google_clicked:
                        log.warning(f"[auto-login] {target_provider} could not find Google OAuth button")
                        # Fallback: try clicking any social login that mentions Google
                        try:
                            all_buttons = await page.locator("button, a").all_inner_texts()
                            for idx, text in enumerate(all_buttons):
                                if "google" in text.lower():
                                    btn = page.locator(f"button, a").nth(idx)
                                    if await btn.is_visible(timeout=1000):
                                        await btn.click()
                                        log.info(f"[auto-login] {target_provider} fallback clicked button: {text.strip()[:50]}")
                                        await page.wait_for_timeout(5000)
                                        await _complete_google_oauth_on_page(page, creds, session, session_id, target_provider)
                                        google_clicked = True
                                        break
                        except Exception:
                            pass

                    if not google_clicked:
                        session["message"] = "Could not find Google login option"
                        log.warning(f"[auto-login] {target_provider} no Google OAuth button found on page")

                elif action == "fill":
                    value = creds.get(step_def["field"], "")
                    session["message"] = f"Entering {step_def['field']}..."
                    broadcast("auto_login", provider=target_provider, step=action, message=session["message"])

                    # Check for CAPTCHA on the page before filling (Google sign-in CAPTCHA)
                    if await _detect_google_signin_captcha(page):
                        log.warning(f"[auto-login] {target_provider} CAPTCHA on sign-in page — solving before fill")
                        session["message"] = "Solving sign-in CAPTCHA..."
                        broadcast("auto_login", provider=target_provider, step="solving_captcha", message="Solving CAPTCHA...")
                        solved = await _solve_google_signin_captcha(page)
                        if not solved:
                            session["status"] = "blocked"
                            session["needs_interactive"] = True
                            session["message"] = "Sign-in CAPTCHA could not be solved. Use manual browser login."
                            broadcast("auto_login", provider=target_provider, step="blocked",
                                      session_id=session_id,
                                      message="Sign-in CAPTCHA unsolvable - click 'Redo as Interactive'.")
                            log.warning(f"[auto-login] {target_provider} sign-in CAPTCHA unsolvable")
                            await context.close()
                            return
                        log.info(f"[auto-login] {target_provider} sign-in CAPTCHA solved")

                    selectors = step_def["selector"].split(", ")
                    filled = False
                    for sel in selectors:
                        try:
                            el = page.locator(sel).first
                            if await el.is_visible(timeout=5000):
                                await el.click()
                                await page.wait_for_timeout(300)
                                await el.fill("")
                                await page.wait_for_timeout(100)
                                await page.keyboard.type(value, delay=50)
                                filled = True
                                log.info(f"[auto-login] {target_provider} filled {step_def['field']} via {sel}")
                                break
                        except Exception:
                            continue
                    if not filled:
                        try:
                            await page.keyboard.type(value, delay=40)
                            filled = True
                            log.info(f"[auto-login] {target_provider} filled {step_def['field']} via keyboard fallback")
                        except Exception:
                            pass
                    if not filled:
                        log.warning(f"[auto-login] Could not fill {step_def['field']}")

                elif action == "click":
                    session["message"] = "Clicking..."
                    selectors = step_def["selector"].split(", ")
                    clicked = False
                    for sel in selectors:
                        try:
                            el = page.locator(sel).first
                            if await el.is_visible(timeout=3000):
                                # Scroll element into view before clicking
                                await el.scroll_into_view_if_needed()
                                await page.wait_for_timeout(300)
                                await el.click()
                                clicked = True
                                log.info(f"[auto-login] {target_provider} clicked {sel}")
                                break
                        except Exception as e:
                            log.debug(f"[auto-login] {target_provider} click {sel} failed: {e}")
                            continue
                    if not clicked:
                        try:
                            await page.keyboard.press("Enter")
                            log.info(f"[auto-login] {target_provider} pressed Enter (fallback)")
                        except Exception:
                            pass
                    await page.wait_for_timeout(1500)
                    # Debug: screenshot after click
                    try:
                        await page.screenshot(path=f"/tmp/after_click_{target_provider}_{session_id}_{i}.png")
                    except Exception:
                        pass
                    log.info(f"[auto-login] {target_provider} after step {i} click URL: {page.url[:100]}")

                elif action == "click_next":
                    log.info(f"[auto-login] {target_provider} ► click_next step {i} starting, URL={page.url[:80]}")
                    await page.wait_for_timeout(500)
                    selectors = step_def.get("selectors", "").split(", ")
                    next_clicked = False
                    for sel in selectors:
                        sel = sel.strip()
                        if not sel:
                            continue
                        try:
                            el = page.locator(sel).first
                            if await el.is_visible(timeout=3000):
                                await el.scroll_into_view_if_needed()
                                await page.wait_for_timeout(300)
                                await el.click(force=True)
                                next_clicked = True
                                log.info(f"[auto-login] {target_provider} clicked Next via {sel} at step {i}")
                                break
                        except Exception as e:
                            log.debug(f"[auto-login] {target_provider} Next click {sel} failed: {e}")
                            continue
                    if not next_clicked:
                        # JS fallback: click any visible Next/Continue button
                        try:
                            js_clicked = await page.evaluate("""() => {
                                const buttons = document.querySelectorAll('button, div[role="button"], span[role="button"]');
                                for (const b of buttons) {
                                    const text = b.textContent.trim().toLowerCase();
                                    if ((text === 'next' || text === 'continue') && b.offsetParent !== null) {
                                        b.click();
                                        return b.textContent.trim();
                                    }
                                }
                                return null;
                            }""")
                            if js_clicked:
                                next_clicked = True
                                log.info(f"[auto-login] {target_provider} clicked Next via JS fallback: '{js_clicked}' at step {i}")
                        except Exception:
                            pass
                    if not next_clicked:
                        # Last resort: press Enter
                        try:
                            await page.keyboard.press("Enter")
                            log.info(f"[auto-login] {target_provider} pressed Enter as last resort at step {i}")
                        except Exception as e:
                            log.warning(f"[auto-login] {target_provider} all Next click methods failed at step {i}: {e}")
                    await page.wait_for_timeout(1500)
                    # Debug: screenshot + URL after clicking Next
                    try:
                        await page.screenshot(path=f"/tmp/after_next_{target_provider}_{session_id}_{i}.png")
                        log.info(f"[auto-login] {target_provider} after step {i} URL: {page.url[:100]}")
                    except Exception:
                        pass

                elif action == "check_otp":
                    await page.wait_for_timeout(2000)

                    # Check CAPTCHA first — try solving
                    if await _detect_captcha(page):
                        log.warning(f"[auto-login] {target_provider} CAPTCHA after credentials — attempting solve")
                        session["message"] = "Solving post-login CAPTCHA..."
                        broadcast("auto_login", provider=target_provider, step="solving_captcha", message="Solving CAPTCHA...")
                        solved = False
                        if await _detect_google_signin_captcha(page):
                            solved = await _solve_google_signin_captcha(page)
                        if not solved:
                            session["status"] = "blocked"
                            session["needs_interactive"] = True
                            session["message"] = "CAPTCHA after credentials could not be solved. Use manual browser login."
                            broadcast("auto_login", provider=target_provider, step="blocked",
                                      session_id=session_id,
                                      message="CAPTCHA unsolvable - click 'Redo as Interactive'.")
                            log.warning(f"[auto-login] {target_provider} CAPTCHA after credentials unsolvable")
                            await context.close()
                            return

                    current_url = page.url
                    provider_cfg = LOGIN_PROVIDERS.get(target_provider, {})
                    is_logged_in = any(sig in current_url for sig in provider_cfg.get("logged_in_signals", []))

                    if not is_logged_in:
                        otp_selectors = [
                            "input[name='totpPin']", "input[name='code']", "input[name='otp']",
                            "input[name='verification_code']", "input[autocomplete='one-time-code']",
                            "input[type='tel']", "input[name='pin']",
                            "input[aria-label*='code']", "input[aria-label*='OTP']",
                            "input[aria-label*='verification']",
                        ]
                        has_otp_field = False
                        for sel in otp_selectors:
                            try:
                                if await page.locator(sel).first.is_visible(timeout=1000):
                                    has_otp_field = True
                                    break
                            except Exception:
                                continue

                        page_text = ""
                        try:
                            page_text = await page.inner_text("body")
                        except Exception:
                            pass
                        otp_phrases = ["verification code", "2-step", "two-step", "authenticator",
                                       "verify it's you", "enter the code", "security code",
                                       "confirm your identity", "6-digit"]
                        text_hint = any(phrase in page_text.lower() for phrase in otp_phrases)

                        if has_otp_field or text_hint:
                            session["needs_otp"] = True
                            session["status"] = "waiting_otp"
                            session["message"] = "OTP/2FA required - enter code to continue"
                            broadcast("auto_login_otp", provider=target_provider, session_id=session_id,
                                      message="OTP or verification code needed. Enter in dashboard.")

                            otp_event = asyncio.Event()
                            _otp_events[session_id] = otp_event

                            try:
                                await asyncio.wait_for(otp_event.wait(), timeout=180)
                            except asyncio.TimeoutError:
                                session["status"] = "otp_timeout"
                                session["needs_interactive"] = True
                                session["message"] = "OTP timeout (3 min). Click 'Redo as Interactive' to try manually."
                                broadcast("auto_login", provider=target_provider, step="otp_timeout",
                                          session_id=session_id,
                                          message="OTP timed out - click 'Redo as Interactive' to login manually.")
                                log.warning(f"[auto-login] {target_provider} OTP timeout")
                                await context.close()
                                return

                            otp_code = _otp_values.pop(session_id, "")
                            _otp_events.pop(session_id, None)

                            if otp_code:
                                session["message"] = "Entering OTP..."
                                broadcast("auto_login", provider=target_provider, step="otp_entry", message="Entering OTP...")

                                otp_entered = False
                                for sel in otp_selectors:
                                    try:
                                        el = page.locator(sel).first
                                        if await el.is_visible(timeout=1000):
                                            await el.fill(otp_code)
                                            otp_entered = True
                                            break
                                    except Exception:
                                        continue
                                if not otp_entered:
                                    await page.keyboard.type(otp_code, delay=50)

                                await page.wait_for_timeout(1000)

                                submit_selectors = [
                                    "button[type='submit']", "button:has-text('Next')",
                                    "button:has-text('Verify')", "button:has-text('Continue')",
                                    "button:has-text('Submit')", "#totpNext button",
                                ]
                                for sel in submit_selectors:
                                    try:
                                        el = page.locator(sel).first
                                        if await el.is_visible(timeout=1000):
                                            await el.click()
                                            break
                                    except Exception:
                                        continue

                                await page.wait_for_timeout(5000)

            # ── Post-step: check login result ────────────────────
            await page.wait_for_timeout(3000)

            # Wait for redirects to settle
            prev_url = ""
            transit_patterns = [
                "accounts.google.com/signin", "accounts.google.com/v3",
                "auth0.openai.com", "auth.openai.com",
                "/login", "/sign-in", "/auth/",
                "accounts.google.com/o/oauth2",
            ]
            for _ in range(10):
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=3000)
                except Exception:
                    pass
                await page.wait_for_timeout(1500)
                current = page.url
                in_transit = any(p in current for p in transit_patterns)
                if in_transit:
                    if current == prev_url:
                        break
                    prev_url = current
                    continue
                break

            final_url = page.url
            log.info(f"[auto-login] {target_provider} final URL: {final_url}")

            provider_cfg = LOGIN_PROVIDERS.get(target_provider, {})
            is_logged_in = any(sig in final_url for sig in provider_cfg.get("logged_in_signals", []))

            # For Google: check if we left the signin flow entirely
            if not is_logged_in and flow_key == "google":
                if "accounts.google.com/signin" not in final_url and "accounts.google.com" in final_url:
                    is_logged_in = True
                elif "google.com" in final_url and "signin" not in final_url and "ServiceLogin" not in final_url:
                    is_logged_in = True

            # Check for SID cookie as definitive proof of Google login
            if not is_logged_in and flow_key == "google":
                try:
                    cookies = await context.cookies()
                    has_sid = any(c.get("name") in ("SID", "__Secure-1PSID", "HSID") for c in cookies)
                    if has_sid:
                        is_logged_in = True
                        log.info(f"[auto-login] {target_provider} confirmed via SID cookie")
                except Exception:
                    pass

            if not is_logged_in:
                try:
                    if await page.locator("textarea, div[contenteditable='true']").first.is_visible(timeout=3000):
                        is_logged_in = True
                except Exception:
                    pass

            # Perplexity: check for logged-in UI indicators
            if not is_logged_in and target_provider == "perplexity":
                perplexity_logged_in_selectors = [
                    "img[alt*='avatar']", "img[alt*='Avatar']",
                    "button[aria-label*='profile']", "button[aria-label*='Profile']",
                    "a[href*='/settings']", "a[href*='/profile']",
                    "div[data-testid*='user']",
                ]
                for sel in perplexity_logged_in_selectors:
                    try:
                        if await page.locator(sel).first.is_visible(timeout=1500):
                            is_logged_in = True
                            log.info(f"[auto-login] perplexity confirmed via UI indicator: {sel}")
                            break
                    except Exception:
                        continue
                # Also check cookies for session token
                if not is_logged_in:
                    try:
                        cookies = await context.cookies()
                        has_session = any(
                            c.get("name") in ("next-auth.session-token", "__Secure-next-auth.session-token", "pplx.visitor-id")
                            and c.get("domain", "").endswith("perplexity.ai")
                            for c in cookies
                        )
                        if has_session:
                            is_logged_in = True
                            log.info(f"[auto-login] perplexity confirmed via session cookie")
                    except Exception:
                        pass

            # Claude: check for logged-in state via URL or chat UI
            if not is_logged_in and target_provider == "claude":
                if "claude.ai" in final_url and "/login" not in final_url:
                    is_logged_in = True
                    log.info(f"[auto-login] claude confirmed - left login page")
                if not is_logged_in:
                    try:
                        cookies = await context.cookies()
                        has_session = any(
                            c.get("name") in ("sessionKey", "__cf_bm", "lastActiveOrg")
                            and "claude" in c.get("domain", "")
                            for c in cookies
                        )
                        if has_session:
                            is_logged_in = True
                            log.info(f"[auto-login] claude confirmed via session cookie")
                    except Exception:
                        pass

            # ChatGPT: check for logged-in state
            if not is_logged_in and target_provider == "chatgpt":
                chatgpt_ui_logged_in = False
                if "chatgpt.com" in final_url and "/auth" not in final_url:
                    try:
                        chatgpt_logged_in = [
                            "nav:has-text('New chat')",
                            "a:has-text('New chat')",
                            "button:has-text('New chat')",
                            "a:has-text('Search chats')",
                            "a:has-text('Deep research')",
                            "div:has-text('Ask anything')",
                            "textarea[id='prompt-textarea']",
                            "button[data-testid='profile-button']",
                        ]
                        for sel in chatgpt_logged_in:
                            try:
                                if await page.locator(sel).first.is_visible(timeout=2000):
                                    chatgpt_ui_logged_in = True
                                    log.info(f"[auto-login] chatgpt logged-in UI detected: {sel}")
                                    break
                            except Exception:
                                continue
                    except Exception:
                        pass

                if chatgpt_ui_logged_in:
                    is_logged_in = True

                # Also accept if session cookie exists
                if not is_logged_in:
                    try:
                        cookies = await context.cookies()
                        has_session = any(
                            "chatgpt" in c.get("domain", "")
                            for c in cookies
                        )
                        if has_session:
                            is_logged_in = True
                            log.info(f"[auto-login] chatgpt confirmed via session token cookie")
                    except Exception:
                        pass

            if is_logged_in:
                session["step"] = "saving_cookies"
                session["message"] = "Login successful! Saving cookies..."
                broadcast("auto_login", provider=target_provider, step="saving", message="Login successful! Saving cookies...")

                # For Google flow, visit key domains to collect all session cookies
                if flow_key == "google":
                    for domain_url in ["https://myaccount.google.com", "https://www.google.com"]:
                        try:
                            await page.goto(domain_url, wait_until="domcontentloaded", timeout=8000)
                            await page.wait_for_timeout(1500)
                        except Exception:
                            pass

                # Wait for session cookies to finalize
                await page.wait_for_timeout(3000)
                state = await context.storage_state()

                # Validate critical session cookies before saving.
                _CRITICAL = {
                    "claude": [("sessionKey", "claude")],
                    "perplexity": [("next-auth.session-token", "perplexity")],
                    "google": [("SID", "google"), ("HSID", "google"), ("SSID", "google")],
                    "gemini": [("SID", "google"), ("HSID", "google")],
                    "chatgpt": [],
                }
                crit_key = flow_key if flow_key in _CRITICAL else target_provider
                crit_list = _CRITICAL.get(crit_key, [])
                cookies_in_state = state.get("cookies", [])
                missing_cookies = []
                if not already_logged_in:
                    for crit_name, crit_domain in crit_list:
                        has_it = any(
                            c.get("name") == crit_name
                            and crit_domain in c.get("domain", "")
                            for c in cookies_in_state
                        )
                        if not has_it:
                            missing_cookies.append(crit_name)
                else:
                    log.info(f"[auto-login] {target_provider} skipping critical cookie check — UI confirmed login")

                state_size = len(json.dumps(state))
                _MIN_STATE_BYTES = 5000

                if missing_cookies:
                    log.warning(f"[auto-login] {target_provider} missing critical cookies {missing_cookies} (state={state_size}b) - not saving")
                    session["status"] = "failed"
                    session["needs_interactive"] = True
                    session["message"] = f"Login incomplete — missing {', '.join(missing_cookies)}. Use manual browser login."
                    broadcast("auto_login", provider=target_provider, step="failed",
                              session_id=session_id,
                              message=f"Incomplete session for {target_provider}. Click 'Redo as Interactive'.")
                    await context.close()
                    return

                if state_size < _MIN_STATE_BYTES:
                    log.warning(f"[auto-login] {target_provider} state too small ({state_size}b < {_MIN_STATE_BYTES}b) - not saving")
                    session["status"] = "failed"
                    session["needs_interactive"] = True
                    session["message"] = f"Login session too small ({state_size}b). Use manual browser login."
                    broadcast("auto_login", provider=target_provider, step="failed",
                              session_id=session_id,
                              message=f"Thin session for {target_provider}. Click 'Redo as Interactive'.")
                    await context.close()
                    return

                saved_domains = set(c.get("domain", "") for c in state.get("cookies", []))
                log.info(f"[auto-login] {target_provider} saving {len(state.get('cookies', []))} cookies, domains={saved_domains}")
                dumped = json.dumps(state)

                if target_cookie_slot is not None and target_cookie_slot > 0:
                    from app.agent.account_pool import save_to_slot
                    await save_to_slot(target_provider, target_cookie_slot, dumped)
                    email_label = creds.get("email", "")
                    from app.agent.account_pool import update_slot_label
                    update_slot_label(target_provider, target_cookie_slot, email_label)
                else:
                    db_key = LOGIN_PROVIDERS[target_provider]["db_key"]
                    def _save(conn):
                        conn.execute("""
                            INSERT INTO app_settings (key, value, updated_at)
                            VALUES (%s, %s::jsonb, NOW())
                            ON CONFLICT (key) DO UPDATE SET value = %s::jsonb, updated_at = NOW()
                        """, (db_key, dumped, dumped))
                        conn.commit()
                    await run_db(_save)

                _clear_engine_cooldown(target_provider)

                if flow_key == "google":
                    slot_suffix = f"_{target_cookie_slot}" if target_cookie_slot and target_cookie_slot > 0 else ""
                    for related_base in ["gemini_cookies", "google_cookies"]:
                        related = f"{related_base}{slot_suffix}" if slot_suffix else related_base
                        default_key = LOGIN_PROVIDERS[target_provider]["db_key"] if target_cookie_slot is None or target_cookie_slot == 0 else None
                        if related == default_key:
                            continue
                        def _share(conn, rk=related):
                            conn.execute("""
                                INSERT INTO app_settings (key, value, updated_at)
                                VALUES (%s, %s::jsonb, NOW())
                                ON CONFLICT (key) DO UPDATE SET value = %s::jsonb, updated_at = NOW()
                            """, (rk, dumped, dumped))
                            conn.commit()
                        await run_db(_share)
                        _clear_engine_cooldown(related_base.replace("_cookies", ""))

                session["status"] = "success"
                session["message"] = f"Logged in to {target_provider} successfully!"
                broadcast("auto_login", provider=target_provider, step="success",
                          message=f"Auto-login successful for {target_provider}!")
                log.info(f"[auto-login] {target_provider} login successful")
            else:
                # Save debug screenshot on failure
                try:
                    screenshot_path = f"/tmp/auto_login_fail_{target_provider}.png"
                    await page.screenshot(path=screenshot_path)
                    log.info(f"[auto-login] {target_provider} failure screenshot saved: {screenshot_path}")
                except Exception:
                    pass
                session["status"] = "failed"
                session["needs_interactive"] = True
                session["message"] = f"Login failed - click 'Redo as Interactive' to try manually."
                broadcast("auto_login", provider=target_provider, step="failed",
                          session_id=session_id,
                          message=f"Auto-login failed for {target_provider}. Click 'Redo as Interactive'.")
                log.warning(f"[auto-login] {target_provider} login failed - URL: {final_url}")

            try:
                await context.close()
            except Exception:
                pass

    except Exception as e:
        if session.get("status") == "success":
            pass
        else:
            session["status"] = "error"
            session["needs_interactive"] = True
            session["message"] = f"Error: {str(e)[:150]}. Click 'Redo as Interactive'."
            broadcast("auto_login", provider=target_provider, step="error",
                      session_id=session_id,
                      message=f"Auto-login error for {target_provider}. Click 'Redo as Interactive'.")
            log.error(f"[auto-login] {target_provider} error: {e}")
    finally:
        _otp_events.pop(session_id, None)
        _otp_values.pop(session_id, None)


async def try_auto_relogin(engine_name: str, target_cookie_slot: int | None = None) -> bool:
    """Attempt automated re-login when cookies expire. Rotates through available credentials."""
    flow_key = engine_name
    if engine_name in ("gemini", "google_aio", "google_ai_mode"):
        flow_key = "google"

    flow = AUTO_LOGIN_FLOWS.get(flow_key)
    if not flow:
        return False

    cred_provider = flow_key
    if engine_name == "claude":
        cred_provider = "claude"

    all_creds = await _load_all_credentials(cred_provider)
    if not all_creds and cred_provider != "google":
        all_creds = await _load_all_credentials("google")
    if not all_creds:
        all_creds = await _load_all_credentials(engine_name)
    if not all_creds:
        # Fallback to legacy single-cred loader
        creds = await _load_credentials(flow_key)
        if not creds and flow_key != "google":
            creds = await _load_credentials("google")
        if not creds:
            log.info(f"[auto-relogin] No credentials for {engine_name} - skipping")
            return False
        all_creds = [{"email": creds["email"], "password": creds["password"], "slot_index": 0}]

    # Match credential to target slot for stable slot-account binding
    cred = all_creds[0]
    if target_cookie_slot is not None and target_cookie_slot < len(all_creds):
        cred = all_creds[target_cookie_slot]
    elif len(all_creds) > 1:
        import random
        cred = random.choice(all_creds)

    session_id = f"relogin_{engine_name}_{str(uuid.uuid4())[:4]}"
    log.info(f"[auto-relogin] Attempting re-login for {engine_name} with {cred['email'][:4]}***")

    await _run_auto_login(session_id, engine_name, flow_key, flow, cred, target_cookie_slot=target_cookie_slot)

    session = _auto_login_sessions.get(session_id, {})
    success = session.get("status") == "success"
    _auto_login_sessions.pop(session_id, None)
    return success


_LOGIN_REDIRECT_PATTERNS = {
    "chatgpt_cookies": ["auth.openai.com", "auth0.openai.com", "/login"],
    "claude_cookies": ["/login", "accounts.google.com"],
    "gemini_cookies": ["accounts.google.com/v3/signin", "accounts.google.com/ServiceLogin"],
    "perplexity_cookies": ["/login", "/sign-in"],
    "google_cookies": ["accounts.google.com/ServiceLogin"],
}

_refresh_lock = asyncio.Lock()
_last_refresh_attempt: dict[str, float] = {}
_REFRESH_MIN_INTERVAL = 120


def _is_login_redirect(db_key: str, url: str) -> bool:
    patterns = _LOGIN_REDIRECT_PATTERNS.get(db_key, ["/login"])
    return any(p in url for p in patterns)


async def _refresh_cookies(db_key: str, visit_url: str):
    import time
    now = time.time()
    last = _last_refresh_attempt.get(db_key, 0)
    if now - last < _REFRESH_MIN_INTERVAL:
        log.debug(f"Skipping {db_key} refresh - last attempt {int(now - last)}s ago")
        return False

    full_state = await _load_full_state(db_key)
    if not full_state:
        log.info(f"No {db_key} to refresh")
        return False

    cookies = full_state.get("cookies", [])
    origins = full_state.get("origins", [])

    async with _refresh_lock:
        _last_refresh_attempt[db_key] = time.time()
        try:
            async with AsyncCamoufox(
                headless=_HEADLESS,
                humanize=True,
                block_webrtc=True,
                os="windows",
            ) as browser:
                context = await browser.new_context()
                page = await context.new_page()
                page.on("pageerror", lambda _: None)
                await context.add_cookies(cookies)

                for origin_data in origins:
                    ls_items = origin_data.get("localStorage", [])
                    if ls_items:
                        ls_dict = {item["name"]: item["value"] for item in ls_items}
                        json_data = json.dumps(ls_dict)
                        await page.add_init_script(
                            f"try {{ var d = {json_data}; for (var k in d) localStorage.setItem(k, d[k]); }} catch(e) {{}}"
                        )

                try:
                    await page.goto(visit_url, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass
                await page.wait_for_timeout(5000)

                final_url = page.url
                is_redirect = _is_login_redirect(db_key, final_url)

                if is_redirect:
                    log.warning(
                        f"{db_key} refresh aborted - redirected to login ({final_url}). "
                        f"Existing cookies preserved. Manual re-auth required."
                    )
                    await _alert_refresh_failure(db_key, final_url)
                    await context.close()
                    return False

                state = await context.storage_state()
                new_cookies = state.get("cookies", [])

                if new_cookies:
                    dumped = json.dumps(state)

                    def _update(conn):
                        conn.execute(
                            "UPDATE app_settings SET value = %s::jsonb, updated_at = NOW() WHERE key = %s",
                            (dumped, db_key)
                        )
                        conn.commit()

                    await run_db(_update)
                    log.info(f"Refreshed {len(new_cookies)} cookies for {db_key}")
                    return True
                else:
                    log.warning(f"{db_key} refresh returned empty - preserving existing cookies")
                    return False

        except Exception as e:
            log.error(f"{db_key} refresh failed: {e}")
            return False


async def _alert_refresh_failure(db_key: str, redirect_url: str):
    provider = db_key.replace("_cookies", "")
    message = (
        f"{provider.upper()} session expired\n"
        f"Cookie refresh detected login redirect: {redirect_url}\n"
        f"Existing cookies preserved (not overwritten).\n"
        f"Manual re-login required at /auth/{provider}"
    )
    from app.notifications import send_alert
    await send_alert(
        title=f"{provider.upper()} Session Expired",
        message=message,
        severity="warning",
        ntype="auth",
    )
    log.warning(message)


async def try_reactive_refresh(engine_name: str, target_cookie_slot: int | None = None) -> bool:
    """Attempt cookie refresh for a failing auth engine. Falls back to auto-relogin if refresh fails."""
    provider_to_db = {
        "chatgpt": ("chatgpt_cookies", "https://chatgpt.com"),
        "claude": ("claude_cookies", "https://claude.ai"),
        "gemini": ("gemini_cookies", "https://gemini.google.com/app"),
        "perplexity": ("perplexity_cookies", "https://www.perplexity.ai"),
        "google_aio": ("google_cookies", "https://www.google.com"),
        "google_ai_mode": ("google_cookies", "https://www.google.com"),
    }
    mapping = provider_to_db.get(engine_name)
    if not mapping:
        return False
    base_key, url = mapping
    db_key = base_key if not target_cookie_slot else f"{base_key}_{target_cookie_slot}"
    log.info(f"[{engine_name}] Reactive cookie refresh triggered for {db_key}")
    refreshed = await _refresh_cookies(db_key, url)
    if refreshed:
        return True

    log.info(f"[{engine_name}] Cookie refresh failed - attempting auto re-login")
    return await try_auto_relogin(engine_name, target_cookie_slot=target_cookie_slot)


_heal_cooldown: dict[str, float] = {}
_HEAL_COOLDOWN_SECS = 900

_SESSION_KEYS = {
    "chatgpt": ["__Secure-next-auth.session-token", "_account", "SID", "__Secure-1PSID"],
    "claude": ["sessionKey", "lastActiveOrg"],
    "perplexity": ["next-auth.session-token", "__Secure-next-auth.session-token"],
    "gemini": ["SID", "__Secure-1PSID"],
}

_DOMAIN_FILTERS = {
    "chatgpt": ["openai", "chatgpt", "google"],
    "claude": ["claude"],
    "perplexity": ["perplexity"],
    "gemini": ["google"],
}


async def _check_recent_error_rate(engine_name: str) -> float:
    """Check error rate for an engine in the last hour. Returns 0.0-1.0."""
    def _q(conn):
        row = conn.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE raw_response_text LIKE 'Error:%%') AS errors
            FROM execution_logs
            WHERE engine_name = %s AND captured_at >= NOW() - INTERVAL '1 hour'
        """, (engine_name,)).fetchone()
        if not row or row["total"] == 0:
            return 0.0
        return row["errors"] / row["total"]
    try:
        return await run_db(_q)
    except Exception:
        return 0.0


async def validate_all_cookies():
    """Health check with auto-heal: checks all cookie slots and triggers re-login when degraded."""
    import time
    from app.events import broadcast

    auth_providers = ["chatgpt", "claude", "perplexity", "gemini"]
    now = time.time()
    healed = []
    healthy = []
    skipped = []

    for provider in auth_providers:
        base_key = f"{provider}_cookies"

        def _find_slots(conn, bk=base_key):
            rows = conn.execute(
                "SELECT key FROM app_settings WHERE key = %s OR key LIKE %s ORDER BY key",
                (bk, f"{bk}_%"),
            ).fetchall()
            return [r["key"] for r in rows] if rows else [bk]

        slot_keys = await run_db(_find_slots)

        for slot_idx, db_key in enumerate(slot_keys):
            cooldown_key = f"{provider}_{slot_idx}"
            full_state = await _load_full_state(db_key)

            if not full_state or not full_state.get("cookies"):
                log.info(f"[health] {provider} slot {slot_idx}: no cookies - checking if credentials exist for auto-login")
                if now - _heal_cooldown.get(cooldown_key, 0) < _HEAL_COOLDOWN_SECS:
                    skipped.append(f"{provider}:{slot_idx}")
                    continue
                _heal_cooldown[cooldown_key] = now
                success = await try_auto_relogin(provider, target_cookie_slot=slot_idx)
                if success:
                    healed.append(f"{provider}:{slot_idx}")
                    log.info(f"[health] {provider} slot {slot_idx}: auto-login successful (was missing)")
                else:
                    log.warning(f"[health] {provider} slot {slot_idx}: no cookies and auto-login failed")
                continue

            all_cookies = full_state.get("cookies", [])
            filters = _DOMAIN_FILTERS.get(provider, [provider])
            matched = [c for c in all_cookies if any(f in c.get("domain", "") for f in filters)]

            critical_names = _SESSION_KEYS.get(provider, [])
            has_session = any(c.get("name") in critical_names for c in matched)
            if not has_session and provider == "chatgpt":
                has_session = any("chatgpt" in c.get("domain", "") for c in matched)

            expired_count = sum(
                1 for c in matched
                if c.get("expires", -1) > 0 and c["expires"] < now
            )
            too_many_expired = len(matched) > 0 and expired_count > len(matched) * 0.5

            needs_heal = not has_session or too_many_expired

            if not needs_heal and slot_idx == 0:
                recent_error_rate = await _check_recent_error_rate(provider)
                if recent_error_rate >= 0.8:
                    needs_heal = True
                    log.warning(f"[health] {provider} slot {slot_idx}: cookies look OK but {recent_error_rate:.0%} error rate in last hour")

            if not needs_heal:
                healthy.append(f"{provider}:{slot_idx}")
                log.debug(f"[health] {provider} slot {slot_idx}: healthy ({len(matched)} cookies, session key present)")
                continue

            reason = "no session cookie" if not has_session else f"{expired_count}/{len(matched)} expired"
            if has_session and not too_many_expired:
                reason = "high error rate despite valid cookies"
            log.warning(f"[health] {provider} slot {slot_idx}: unhealthy ({reason}) - attempting auto-heal")

            if now - _heal_cooldown.get(cooldown_key, 0) < _HEAL_COOLDOWN_SECS:
                remaining = int(_HEAL_COOLDOWN_SECS - (now - _heal_cooldown.get(cooldown_key, 0)))
                log.info(f"[health] {provider} slot {slot_idx}: heal cooldown active ({remaining}s remaining)")
                skipped.append(f"{provider}:{slot_idx}")
                continue

            _heal_cooldown[cooldown_key] = now
            broadcast("auto_login", provider=provider, step="auto_heal",
                      message=f"Auto-healing {provider} slot {slot_idx} ({reason})")

            success = await try_reactive_refresh(provider, target_cookie_slot=slot_idx)
            if success:
                healed.append(f"{provider}:{slot_idx}")
                _clear_engine_cooldown(provider)
                log.info(f"[health] {provider} slot {slot_idx}: auto-healed successfully")
                broadcast("auto_login", provider=provider, step="success",
                          message=f"Auto-heal successful for {provider} slot {slot_idx}")
            else:
                log.warning(f"[health] {provider} slot {slot_idx}: auto-heal failed ({reason})")
                broadcast("auto_login", provider=provider, step="failed",
                          message=f"Auto-heal failed for {provider} slot {slot_idx} ({reason})")

    summary = f"healthy={healthy}, healed={healed}, skipped={skipped}"
    log.info(f"[health] Cookie validation complete: {summary}")


async def cleanup_browsers():
    """Shut down pre-warmed Camoufox and active sessions."""
    global _warm_camo
    async with _warm_camo_lock:
        if _warm_camo:
            try:
                await _warm_camo.__aexit__(None, None, None)
            except Exception:
                pass
            _warm_camo = None

    for sid in list(_sessions):
        await _cleanup_session(sid)

    log.info("All browser resources cleaned up")
