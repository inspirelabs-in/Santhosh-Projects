"""Automated login engine: CAPTCHA detection/solving, OTP handling, Google OAuth flows."""
import asyncio
import base64
import json
import logging
import sys
import uuid

import httpx
from fastapi import APIRouter, Request
from camoufox.async_api import AsyncCamoufox
from app.database import run_db
from app.config import get_settings
from app.routes.auth_credentials import OTPInput

_HEADLESS = "virtual" if sys.platform != "win32" else True

router = APIRouter()
log = logging.getLogger("geo.auth.auto_login")

VIEWPORT_W = 1280
VIEWPORT_H = 800

# ── State ────────────────────────────────────────────────

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


# ── Routes ───────────────────────────────────────────────

@router.post("/api/auth/auto-login/{provider}")
async def trigger_auto_login(provider: str, request: Request):
    """Start automated login for an engine. Accepts optional cred_slot_index and target_cookie_slot."""
    from app.routes.auth_credentials import load_credentials
    from app.routes.auth import LOGIN_PROVIDERS
    from fastapi.responses import JSONResponse

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
    creds = await load_credentials(cred_provider, slot_index=cred_slot)
    if not creds and cred_provider != "google":
        creds = await load_credentials("google", slot_index=cred_slot)
    if not creds:
        creds = await load_credentials(provider, slot_index=cred_slot)
    if not creds:
        return JSONResponse({"error": f"No credentials stored for {cred_provider}. Save credentials first."}, status_code=400)

    session_id = str(uuid.uuid4())[:8]
    asyncio.create_task(_run_auto_login(session_id, provider, flow_key, flow, creds, target_cookie_slot=target_cookie_slot))

    return {"status": "started", "session_id": session_id, "provider": provider, "target_cookie_slot": target_cookie_slot}


@router.post("/api/auth/auto-login/{session_id}/otp")
async def submit_otp(session_id: str, otp: OTPInput):
    """Submit OTP code for a pending auto-login session."""
    from fastapi.responses import JSONResponse
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
    """Login all engines using all available Google accounts."""
    from app.routes.auth_credentials import load_all_credentials
    from fastapi.responses import JSONResponse

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    delay_between = body.get("delay_seconds", 30)

    google_creds = await load_all_credentials("google")
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


# ── CAPTCHA Detection/Solving ───────────────────────────

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
    """Solve Google sign-in text CAPTCHA using OpenAI Vision API."""
    settings = get_settings()
    if not settings.openai_api_key:
        log.warning("[captcha-solver] No OpenAI API key — cannot solve CAPTCHA")
        return False

    for attempt in range(max_attempts):
        try:
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
                captcha_area = page.locator("div:has(> img):has(+ input), div:has(> img):has(~ input)").first
                try:
                    if await captcha_area.is_visible(timeout=1000):
                        captcha_img = captcha_area
                except Exception:
                    pass

            if not captcha_img:
                log.warning(f"[captcha-solver] Attempt {attempt+1}: could not locate CAPTCHA image")
                continue

            img_bytes = await captcha_img.screenshot()
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")

            log.info(f"[captcha-solver] Attempt {attempt+1}: sending CAPTCHA image to OpenAI Vision")

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


# ── Browser pre-warm + consent dismiss ──────────────────

async def _prewarm_browser_for_login(page):
    """Visit Google properties to build cookie history before login."""
    try:
        await page.goto("https://www.google.com", wait_until="domcontentloaded", timeout=10000)
        await page.wait_for_timeout(2000)
        await _dismiss_cookie_consent(page)
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


# ── Google OAuth on page ────────────────────────────────

async def _complete_google_oauth_on_page(target_page, creds: dict, session: dict, session_id: str, target_provider: str):
    """Fill Google email/password on an OAuth page (popup or redirect)."""
    from app.events import broadcast
    await target_page.wait_for_timeout(2000)

    if await _detect_google_signin_captcha(target_page):
        log.warning(f"[auto-login] {target_provider} CAPTCHA on Google OAuth page — attempting solve")
        solved = await _solve_google_signin_captcha(target_page)
        if not solved:
            log.warning(f"[auto-login] {target_provider} CAPTCHA on OAuth page unsolvable")
            return False
        await target_page.wait_for_timeout(1000)

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


# ── Main auto-login engine ──────────────────────────────

async def _run_auto_login(session_id: str, target_provider: str, flow_key: str, flow: dict, creds: dict, target_cookie_slot: int | None = None):
    """Execute automated login flow with CAPTCHA detection, OTP support, and interactive fallback."""
    from app.events import broadcast
    from app.routes.auth import LOGIN_PROVIDERS, _clear_engine_cooldown
    from app.routes.auth_cookies import load_full_state
    from app.routes.auth import _check_chatgpt_logged_in, _check_claude_logged_in, _check_perplexity_logged_in

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
            ctx_kwargs = {"viewport": {"width": VIEWPORT_W, "height": VIEWPORT_H}}
            if flow.get("preload_google_cookies"):
                google_db_key = "google_cookies" if not target_cookie_slot else f"google_cookies_{target_cookie_slot}"
                google_state = await load_full_state(google_db_key)
                if not google_state or not google_state.get("cookies"):
                    google_state = await load_full_state("google_cookies")
                if google_state and google_state.get("cookies"):
                    ctx_kwargs["storage_state"] = google_state
                    log.info(f"[auto-login] {target_provider} preloaded Google session ({len(google_state['cookies'])} cookies)")

            context = await browser.new_context(**ctx_kwargs)
            page = await context.new_page()
            page.on("pageerror", lambda _: None)

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

            for _wait in range(10):
                await page.wait_for_timeout(1500)
                try:
                    email_visible = await page.locator("input[type='email'], input[name='identifier']").first.is_visible(timeout=500)
                    if email_visible:
                        break
                    captcha_input = await page.locator("input[aria-label*='Type the text'], input[name*='captcha']").first.is_visible(timeout=500)
                    if captcha_input:
                        break
                except Exception:
                    continue

            try:
                page_text = (await page.inner_text("body"))[:500]
                log.info(f"[auto-login] {target_provider} page rendered (URL={page.url[:80]}): {page_text[:300]}")
                await page.screenshot(path=f"/tmp/login_debug_{target_provider}_{session_id}.png")
            except Exception as e:
                log.debug(f"[auto-login] debug capture failed: {e}")

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

            # ── Early logged-in detection ──
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

                elif action == "solve_turnstile":
                    session["message"] = "Checking for Cloudflare Turnstile..."
                    broadcast("auto_login", provider=target_provider, step="solve_turnstile", message=session["message"])
                    turnstile_frame = None
                    for f in page.frames:
                        if "challenges.cloudflare.com" in f.url or "turnstile" in f.url:
                            turnstile_frame = f
                            break
                    if turnstile_frame:
                        log.info(f"[auto-login] {target_provider} Turnstile detected, attempting solve")
                        session["message"] = "Solving Turnstile challenge..."
                        broadcast("auto_login", provider=target_provider, step="solving_turnstile", message=session["message"])
                        for _t_attempt in range(3):
                            try:
                                checkbox = await turnstile_frame.query_selector("input[type='checkbox']")
                                if checkbox:
                                    await checkbox.click()
                                else:
                                    body = await turnstile_frame.query_selector("body")
                                    if body:
                                        await body.click()
                                await page.wait_for_timeout(4000)
                                still_there = any(
                                    "challenges.cloudflare.com" in f.url or "turnstile" in f.url
                                    for f in page.frames if f != page.main_frame
                                )
                                if not still_there:
                                    log.info(f"[auto-login] {target_provider} Turnstile solved")
                                    break
                            except Exception:
                                await page.wait_for_timeout(2000)
                    else:
                        log.info(f"[auto-login] {target_provider} no Turnstile present, continuing")

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

                    google_clicked = False
                    for sel in google_btn_selectors:
                        try:
                            el = page.locator(sel).first
                            if await el.is_visible(timeout=2000):
                                oauth_page = page
                                try:
                                    popup_future = asyncio.ensure_future(
                                        page.context.wait_for_event("page", timeout=8000)
                                    )
                                    await el.click(no_wait_after=True)
                                    popup = await popup_future
                                    await popup.wait_for_load_state("domcontentloaded", timeout=10000)
                                    oauth_page = popup
                                    log.info(f"[auto-login] {target_provider} Google OAuth opened in popup")
                                except Exception:
                                    if not popup_future.done():
                                        popup_future.cancel()
                                    await page.wait_for_timeout(3000)
                                    log.info(f"[auto-login] {target_provider} Google OAuth same-tab redirect")
                                google_clicked = True
                                log.info(f"[auto-login] {target_provider} clicked Google OAuth via: {sel}")

                                await _complete_google_oauth_on_page(oauth_page, creds, session, session_id, target_provider)

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
                        try:
                            await page.keyboard.press("Enter")
                            log.info(f"[auto-login] {target_provider} pressed Enter as last resort at step {i}")
                        except Exception as e:
                            log.warning(f"[auto-login] {target_provider} all Next click methods failed at step {i}: {e}")
                    await page.wait_for_timeout(1500)
                    try:
                        await page.screenshot(path=f"/tmp/after_next_{target_provider}_{session_id}_{i}.png")
                        log.info(f"[auto-login] {target_provider} after step {i} URL: {page.url[:100]}")
                    except Exception:
                        pass

                elif action == "check_otp":
                    await page.wait_for_timeout(2000)

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

            prev_url = ""
            transit_patterns = [
                "accounts.google.com/signin", "accounts.google.com/v3",
                "auth0.openai.com", "auth.openai.com",
                "/login", "/sign-in", "/auth/",
                "accounts.google.com/o/oauth2",
                "login.microsoftonline.com", "login.live.com",
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

            if not is_logged_in and flow_key == "google":
                if "accounts.google.com/signin" not in final_url and "accounts.google.com" in final_url:
                    is_logged_in = True
                elif "google.com" in final_url and "signin" not in final_url and "ServiceLogin" not in final_url:
                    is_logged_in = True

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

                if flow_key == "google":
                    for domain_url in ["https://myaccount.google.com", "https://www.google.com"]:
                        try:
                            await page.goto(domain_url, wait_until="domcontentloaded", timeout=8000)
                            await page.wait_for_timeout(1500)
                        except Exception:
                            pass

                await page.wait_for_timeout(3000)
                state = await context.storage_state()

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
    """Attempt automated re-login when cookies expire."""
    from app.routes.auth_credentials import load_credentials, load_all_credentials

    flow_key = engine_name
    if engine_name in ("gemini", "google_aio", "google_ai_mode"):
        flow_key = "google"

    flow = AUTO_LOGIN_FLOWS.get(flow_key)
    if not flow:
        return False

    cred_provider = flow_key
    if engine_name == "claude":
        cred_provider = "claude"

    all_creds = await load_all_credentials(cred_provider)
    if not all_creds and cred_provider != "google":
        all_creds = await load_all_credentials("google")
    if not all_creds:
        all_creds = await load_all_credentials(engine_name)
    if not all_creds:
        creds = await load_credentials(flow_key)
        if not creds and flow_key != "google":
            creds = await load_credentials("google")
        if not creds:
            log.info(f"[auto-relogin] No credentials for {engine_name} - skipping")
            return False
        all_creds = [{"email": creds["email"], "password": creds["password"], "slot_index": 0}]

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
