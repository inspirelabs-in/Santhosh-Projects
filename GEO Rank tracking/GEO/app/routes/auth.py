"""Interactive browser-based login routes and browser lifecycle management."""
import asyncio
import base64
import json
import logging
import sys
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from camoufox.async_api import AsyncCamoufox
from app.database import run_db
from app.config import get_settings

# ── Re-exports (keep existing imports working across codebase) ──
from app.routes.auth_cookies import (  # noqa: F401
    load_google_cookies, load_google_storage_state,
    load_chatgpt_cookies, load_chatgpt_storage_state,
    load_gemini_storage_state, load_claude_storage_state,
    load_perplexity_storage_state,
    get_last_used_db_key, try_reactive_refresh,
    refresh_google_cookies, refresh_chatgpt_cookies,
    refresh_gemini_cookies, refresh_claude_cookies,
    refresh_perplexity_cookies, validate_all_cookies,
)
from app.routes.auth_credentials import (  # noqa: F401
    migrate_legacy_credentials, OTPInput,
)
from app.routes.auth_auto_login import (  # noqa: F401
    AUTO_LOGIN_FLOWS, try_auto_relogin,
    _auto_login_sessions, _otp_events, _otp_values,
)

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


async def warmup_camoufox():
    """Pre-launch one Camoufox in background. Serialized."""
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


class ScrollInput(BaseModel):
    direction: str
    amount: int = 300


class ManualImportInput(BaseModel):
    cookies_json: str
    localstorage_json: str | None = None


# ── Include sub-routers ──────────────────────────────────

from app.routes.auth_credentials import router as _cred_router  # noqa: E402
from app.routes.auth_auto_login import router as _auto_login_router  # noqa: E402
router.include_router(_cred_router)
router.include_router(_auto_login_router)


# ── Pages ────────────────────────────────────────────────

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
    asyncio.create_task(warmup_camoufox())
    return templates.TemplateResponse(request, "auth_login.html", {
        "active_provider": provider,
        "provider_statuses": provider_statuses,
        "providers": LOGIN_PROVIDERS,
    })


# ── Generic session endpoints ────────────────────────────

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
        page.on("filechooser", lambda fc: None)

        _sessions[session_id] = {
            "camo_browser": camo_browser,
            "page": page,
            "provider": provider,
            "created": asyncio.get_event_loop().time(),
            "navigating": True,
        }

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
        asyncio.create_task(warmup_camoufox())

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

        warning = None
        if provider == "chatgpt":
            cookie_names = {c.get("name", "") for c in cookies}
            has_session = any("session-token" in n or "auth-session" in n for n in cookie_names)
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


@router.post("/api/auth/import/{provider}")
async def import_cookies(provider: str, data: ManualImportInput):
    if provider not in LOGIN_PROVIDERS:
        return JSONResponse({"error": f"Unknown provider: {provider}"}, status_code=400)

    try:
        raw = json.loads(data.cookies_json)
    except json.JSONDecodeError as e:
        return JSONResponse({"error": f"Invalid JSON: {e}"}, status_code=400)

    if isinstance(raw, list):
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
        state = raw
    else:
        return JSONResponse(
            {"error": "Unrecognized format. Paste Cookie-Editor JSON array or Playwright storage_state object."},
            status_code=400,
        )

    if data.localstorage_json:
        try:
            ls_raw = json.loads(data.localstorage_json)
        except json.JSONDecodeError:
            return JSONResponse({"error": "Invalid localStorage JSON"}, status_code=400)

        config = LOGIN_PROVIDERS[provider]
        origin_url = config["start_url"].rstrip("/")
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


# ── Helpers ──────────────────────────────────────────────

_CONSENT_SELECTORS = [
    "#onetrust-accept-btn-handler",
    "button.onetrust-close-btn-handler",
    "button.cky-btn-accept",
    "button.osano-cm-accept-all",
    "button.cm-btn-accept",
    "button[data-testid='accept-cookies']",
    "button:has-text('Accept All Cookies')",
    "button:has-text('Accept all cookies')",
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
    "button:has-text('Accept cookies')",
    "button:has-text('Allow all')",
    "button:has-text('Allow All')",
    "button:has-text('I agree')",
    "button:has-text('Got it')",
    "button[aria-label*='accept' i]",
    "button[aria-label*='consent' i]",
]


async def _dismiss_cookie_popups(page):
    """Dismiss cookie consent banners and Google One Tap popup after page load."""
    try:
        await page.wait_for_timeout(1500)
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
    """Remove Google One Tap popup only."""
    try:
        await page.evaluate("""() => {
            const selectors = [
                '#credential_picker_container',
                '#credential_picker_iframe',
                '[id*="credential_picker"]',
            ];
            selectors.forEach(sel => {
                document.querySelectorAll(sel).forEach(el => el.remove());
            });
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
                "button:has-text('Sign In')", "button:has-text('Sign in')",
                "a:has-text('Sign In')", "a:has-text('Sign in')",
                "a:has-text('Log In')", "a:has-text('Log in')",
                "[role='button']:has-text('Sign In')", "[role='button']:has-text('Sign in')",
                "div:has-text('Sign In')", "span:has-text('Sign In')",
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
            await page.evaluate("""() => {
                const observer = new MutationObserver(muts => {
                    for (const m of muts) {
                        for (const n of m.addedNodes) {
                            if (n.id && n.id.includes('credential_picker')) {
                                n.remove();
                            }
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
    """Fast check for cookie/consent popups during screenshot polling."""
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
            "div[contenteditable='true']", "div.ProseMirror",
            "button[data-testid='user-menu']", "img[alt*='Avatar']",
            "button:has-text('New task')", "button:has-text('New chat')",
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
            "textarea", "div[contenteditable='true'][id='prompt-textarea']",
            "button[aria-label*='User']", "img[alt*='User']",
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
            "textarea", "button[aria-label='Submit']",
            "a[href='/library']", "a[href='/settings']",
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
    """Copy cookies to another provider's DB key."""
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
