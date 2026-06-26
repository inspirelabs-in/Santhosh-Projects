"""Cookie load, refresh, validation, and auto-heal logic."""
import asyncio
import json
import logging
import sys
import time

from camoufox.async_api import AsyncCamoufox
from app.database import run_db
from app.config import get_settings

_HEADLESS = "virtual" if sys.platform != "win32" else True

log = logging.getLogger("geo.auth.cookies")


# ── Internal DB loaders ──────────────────────────────────

async def load_full_state(db_key: str) -> dict | None:
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


async def load_cookies_from_db(db_key: str) -> list[dict] | None:
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


# ── Public helpers for other modules ─────────────────────
# Route through the account pool for multi-account rotation.

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
    return await load_full_state(fallback_key)


async def _pool_cookies(engine_name: str) -> list[dict] | None:
    state = await _pool_storage_state(engine_name)
    if state and isinstance(state, dict):
        return state.get("cookies", [])
    return None


async def load_google_cookies() -> list[dict] | None:
    return await _pool_cookies("google")


async def load_google_storage_state() -> dict | None:
    return await _pool_storage_state("google")


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


# ── Scheduled refresh ───────────────────────────────────

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


# ── Cookie refresh engine ───────────────────────────────

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
    now = time.time()
    last = _last_refresh_attempt.get(db_key, 0)
    if now - last < _REFRESH_MIN_INTERVAL:
        log.debug(f"Skipping {db_key} refresh - last attempt {int(now - last)}s ago")
        return False

    full_state = await load_full_state(db_key)
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


# ── Reactive refresh + auto-relogin ─────────────────────

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
    from app.routes.auth_auto_login import try_auto_relogin
    return await try_auto_relogin(engine_name, target_cookie_slot=target_cookie_slot)


# ── Health check + auto-heal ────────────────────────────

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
    from app.events import broadcast
    from app.routes.auth_auto_login import try_auto_relogin
    from app.routes.auth import _clear_engine_cooldown

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
            full_state = await load_full_state(db_key)

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
