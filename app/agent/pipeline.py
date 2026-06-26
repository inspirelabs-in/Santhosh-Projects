import asyncio
import logging
import random
import re
import time
import httpx
from app.models import PipelineState, PromptItem
from app.agent.scraper import run_scrape
from app.engines import ALL_ENGINES, VISIBLE_ENGINES, AUTH_ENGINES
from app.agent.parser import parse_response
from app.agent.serp_parser import parse_serp_html
from app.database import run_db
from app.config import get_settings
from app.events import broadcast
from app.routes.auth import try_reactive_refresh, get_last_used_db_key
from app.agent.dedup_cache import is_cached, mark_cached, mark_cached_db

log = logging.getLogger("geo.pipeline")

MAX_RETRIES = 3
ACTIVE_COUNTRIES = ["IN"]

ENGINE_DELAY_MIN = 1
ENGINE_DELAY_MAX = 3
PROMPT_DELAY_MIN = 1
PROMPT_DELAY_MAX = 2

BATCH_COOLDOWN_MIN = 15
BATCH_COOLDOWN_MAX = 30
ENGINE_COOLDOWN_BASE = 120
ENGINE_COOLDOWN_MAX = 1800
CONSECUTIVE_FAIL_THRESHOLD = 10

ENGINE_CONCURRENCY = {
    "google_aio": 1,
    "google_ai_mode": 1,
    "gemini": 2,
    "chatgpt": 2,
    "claude": 2,
    "perplexity": 2,
}

_engine_fail_count: dict[str, int] = {}
_engine_cooldown_until: dict[str, float] = {}
_continuous_running = False


async def run_pipeline(prompt: PromptItem, engine_name: str, country_code: str = "IN") -> PipelineState:
    state = PipelineState(target_prompt=prompt, engine_name=engine_name, country_code=country_code)

    for attempt in range(MAX_RETRIES):
        state.retry_count = attempt
        log.info(f"[{engine_name}/{country_code}] Attempt {attempt + 1}/{MAX_RETRIES} for: {prompt.text[:60]}...")
        broadcast("scrape_start", engine=engine_name, prompt=prompt.text[:80], attempt=attempt + 1)

        scrape_result = await run_scrape(engine_name, prompt.text, attempt)

        if scrape_result.error_log and not scrape_result.raw_response_text:
            state.error_log = scrape_result.error_log
            log.warning(f"[{engine_name}] Scrape failed: {scrape_result.error_log}")
            broadcast("scrape_fail", engine=engine_name, prompt=prompt.text[:80], error=scrape_result.error_log[:120])
            _track_engine_failure(engine_name)

            _err_lower = (scrape_result.error_log or "").lower()

            if engine_name in ("google_aio", "google_ai_mode"):
                if "engine captcha cooldown" in _err_lower:
                    log.warning(f"[{engine_name}] Global captcha cooldown active — not retrying")
                    break

                if "captcha" in _err_lower:
                    from app.agent.scraper import rotate_engine_proxy
                    rotate_engine_proxy(engine_name)
                    log.warning(f"[{engine_name}] Captcha detected - rotated proxy, retrying")
                    await asyncio.sleep(5)
                    continue

                _is_transient_google = (
                    "notimplementederror" in _err_lower
                    or "connection" in _err_lower
                    or "timeout" in _err_lower
                    or "browser" in _err_lower
                )
                if _is_transient_google:
                    log.warning(f"[{engine_name}] Transient browser error ({scrape_result.error_log[:80]}) - retrying after delay (attempt {attempt+1}/{MAX_RETRIES})")
                    await asyncio.sleep(5 + attempt * 5)
                    continue

            if engine_name in ("chatgpt", "claude", "gemini", "perplexity"):

                _is_proxy_err = (
                    ("proxy" in _err_lower and ("refusing" in _err_lower or "connection" in _err_lower))
                    or "sec_error_unknown_issuer" in _err_lower
                    or "ns_error_proxy" in _err_lower
                    or ("ssl" in _err_lower and "certificate" in _err_lower)
                )
                if _is_proxy_err:
                    from app.agent.scraper import rotate_engine_proxy
                    rotate_engine_proxy(engine_name)
                    log.warning(f"[{engine_name}] Proxy error detected - rotated to new IP, retrying")
                    await asyncio.sleep(3)
                    continue

                _is_rate_limit = (
                    "rate limit" in _err_lower
                    or "limit exhausted" in _err_lower
                    or "out of free messages" in _err_lower
                    or "message limit" in _err_lower
                    or "usage limit" in _err_lower
                )
                if _is_rate_limit:
                    _used_key = get_last_used_db_key(engine_name)
                    if _used_key:
                        from app.agent.account_pool import report_rate_limit
                        report_rate_limit(engine_name, _used_key)
                    log.warning(f"[{engine_name}] Rate limited on {_used_key or 'default'} - rotating to next account")
                    from app.agent.account_pool import get_storage_state as _check_pool
                    _next_state, _next_key = await _check_pool(engine_name)
                    if _next_state:
                        log.info(f"[{engine_name}] Retrying with account slot {_next_key}")
                        await asyncio.sleep(5)
                        continue
                    from app.agent.account_pool import time_until_next_available
                    _wait = time_until_next_available(engine_name)
                    if 0 < _wait <= 120:
                        log.info(f"[{engine_name}] All slots on cooldown, nearest recovers in {_wait:.0f}s - waiting")
                        broadcast("scrape_fail", engine=engine_name, prompt=prompt.text[:80],
                                  error=f"All accounts cooling, waiting {_wait:.0f}s for recovery")
                        await asyncio.sleep(_wait + 2)
                        continue
                    log.warning(f"[{engine_name}] All accounts on cooldown ({_wait:.0f}s until recovery) - skipping for now")
                    state.error_log = f"limit_exhausted: {engine_name} all accounts on cooldown, next in {_wait:.0f}s"
                    break

                _is_transient = (
                    "transient" in _err_lower
                    or "internal error" in _err_lower
                    or "notimplementederror" in _err_lower
                    or ("timeout" in _err_lower and "session" not in _err_lower)
                )
                if _is_transient:
                    log.info(f"[{engine_name}] Transient server error - retrying after delay (attempt {attempt+1}/{MAX_RETRIES})")
                    await asyncio.sleep(10 + attempt * 5)
                    continue

                _is_auth_err = (
                    "expired" in _err_lower
                    or "input not found" in _err_lower
                    or "input field not found" in _err_lower
                    or "session" in _err_lower
                    or "sign in" in _err_lower
                    or "log in" in _err_lower
                )
                if _is_auth_err and attempt == 0:
                    _auth_key = get_last_used_db_key(engine_name)
                    if _auth_key:
                        from app.agent.account_pool import report_auth_failure
                        report_auth_failure(engine_name, _auth_key)
                    log.info(f"[{engine_name}] Auth/session failure on {_auth_key or 'default'} - attempting reactive refresh")
                    refreshed = await try_reactive_refresh(engine_name)
                    if refreshed:
                        log.info(f"[{engine_name}] Cookies refreshed - retrying scrape")
                        broadcast("engine_recovery", engine=engine_name, action="cookie_refresh")
                        _engine_fail_count[engine_name] = 0
                        _engine_cooldown_until.pop(engine_name, None)
                        from app.agent.account_pool import clear_cooldown
                        clear_cooldown(engine_name)
                        continue
                    log.warning(f"[{engine_name}] Reactive refresh failed - will attempt auto-relogin")
                    break

                log.info(f"[{engine_name}] Unrecognized error, retrying (attempt {attempt+1}/{MAX_RETRIES})")
                await asyncio.sleep(5 + attempt * 3)

            if "RESOURCE_EXHAUSTED" in (scrape_result.error_log or ""):
                await asyncio.sleep(15)
            continue

        from app.agent.scraper import _validate_response
        validation_err = _validate_response(
            scrape_result.raw_response_text, prompt.text, engine_name
        )
        if validation_err == "captcha_detected":
            log.warning(f"[{engine_name}] Captcha/bot page detected - treating as failure")
            state.error_log = f"Error: captcha/bot detection on {engine_name}"
            _track_engine_failure(engine_name)
            if engine_name in ("google_aio", "google_ai_mode"):
                from app.agent.scraper import rotate_engine_proxy
                rotate_engine_proxy(engine_name)
                log.info(f"[{engine_name}] Rotated proxy after captcha detection")
            broadcast("scrape_fail", engine=engine_name, prompt=prompt.text[:80],
                      error="Captcha/bot detection - proxy rotated")
            continue

        if validation_err == "ui_noise":
            log.warning(f"[{engine_name}] UI noise detected in response - treating as scrape failure")
            state.error_log = f"Error: {engine_name} returned home/UI page instead of query response"
            _track_engine_failure(engine_name)
            broadcast("scrape_fail", engine=engine_name, prompt=prompt.text[:80],
                      error="UI noise captured instead of actual response")
            continue

        if validation_err == "rate_limited":
            log.warning(f"[{engine_name}] Rate limit text detected in scraped response")
            _used_key = get_last_used_db_key(engine_name)
            if _used_key:
                from app.agent.account_pool import report_rate_limit
                report_rate_limit(engine_name, _used_key)
            from app.agent.account_pool import get_storage_state as _check_pool2
            _next_state, _next_key = await _check_pool2(engine_name)
            if _next_state:
                log.info(f"[{engine_name}] Rate limit in response - retrying with slot {_next_key}")
                await asyncio.sleep(5)
                continue
            from app.agent.account_pool import time_until_next_available
            _wait = time_until_next_available(engine_name)
            if 0 < _wait <= 120:
                log.info(f"[{engine_name}] All slots on cooldown, nearest recovers in {_wait:.0f}s - waiting")
                broadcast("scrape_fail", engine=engine_name, prompt=prompt.text[:80],
                          error=f"All accounts cooling, waiting {_wait:.0f}s for recovery")
                await asyncio.sleep(_wait + 2)
                continue
            log.warning(f"[{engine_name}] All accounts on cooldown ({_wait:.0f}s until recovery) - skipping for now")
            state.error_log = f"limit_exhausted: {engine_name} all accounts on cooldown, next in {_wait:.0f}s"
            break

        if validation_err in ("paa_content", "featured_snippet", "maps_content"):
            log.info(f"[{engine_name}] Invalid content detected ({validation_err}) - treating as no AIO")
            state.raw_response_text = f"[No AI Overview] Rejected: {validation_err} — not a genuine AI Overview response."
            state.raw_html_payload = scrape_result.raw_html_payload
            state.error_log = None
            if engine_name in ("google_aio", "google_ai_mode") and state.raw_html_payload:
                try:
                    settings = get_settings()
                    state.serp_data = parse_serp_html(
                        state.raw_html_payload, prompt.text, settings.target_domain
                    )
                except Exception:
                    pass
            break

        if engine_name in ("chatgpt", "claude", "gemini", "perplexity") and len(scrape_result.raw_response_text) < 80:
            log.warning(f"[{engine_name}] Response too short ({len(scrape_result.raw_response_text)} chars), treating as incomplete")
            _track_engine_failure(engine_name)
            broadcast("scrape_fail", engine=engine_name, prompt=prompt.text[:80],
                      error=f"Response truncated ({len(scrape_result.raw_response_text)} chars)")
            continue

        _raw = scrape_result.raw_response_text
        _raw_lower = (_raw or "").lower()
        if _raw and ("not redirected within" in _raw_lower or
                      (_raw_lower.startswith("google search") and "please click" in _raw_lower)):
            log.warning(f"[{engine_name}] Garbage redirect page detected ({len(_raw)} chars), treating as failure")
            state.error_log = "Google redirect/interstitial page captured"
            _track_engine_failure(engine_name)
            continue

        state.raw_response_text = _raw
        state.raw_html_payload = scrape_result.raw_html_payload
        state.error_log = None
        _engine_fail_count[engine_name] = 0
        _used_key = get_last_used_db_key(engine_name)
        if _used_key:
            from app.agent.account_pool import report_success
            report_success(engine_name, _used_key)

        if engine_name in ("google_aio", "google_ai_mode") and state.raw_html_payload:
            try:
                settings = get_settings()
                state.serp_data = parse_serp_html(
                    state.raw_html_payload, prompt.text, settings.target_domain
                )
                if state.serp_data.organic_results:
                    log.info(f"[{engine_name}] Inline SERP parsed: {len(state.serp_data.organic_results)} organic results")
            except Exception as serp_err:
                log.warning(f"[{engine_name}] Inline SERP parse failed (non-fatal): {serp_err}")
        broadcast("scrape_done", engine=engine_name, prompt=prompt.text[:80],
                  chars=len(scrape_result.raw_response_text), urls=len(scrape_result.cited_urls))

        if state.raw_response_text.startswith("[No AI Overview]"):
            log.info(f"[{engine_name}] No AI Overview available - skipping parser")
            break

        if state.raw_response_text.startswith("[No AI Mode]"):
            log.info(f"[{engine_name}] No AI Mode response available - skipping parser")
            break

        extracted = await parse_response(engine_name, prompt.text, state.raw_response_text)

        if not extracted:
            state.error_log = "Parser returned no data"
            log.warning(f"[{engine_name}] Parse failed, retrying...")
            await asyncio.sleep(10)
            continue

        state.extracted_data = extracted
        state.error_log = None
        broadcast("parse_done", engine=engine_name, prompt=prompt.text[:80],
                  brands=len(extracted.brand_mentions), coupons=len(extracted.ai_hallucinated_coupons))
        break

    await _commit_to_db(state)

    if state.extracted_data and state.serp_data and state.serp_data.organic_results:
        try:
            from app.agent.content_comparator import build_citation_overlaps
            await build_citation_overlaps(prompt.id)
        except Exception as e:
            log.warning(f"[{engine_name}] Citation overlap build failed (non-fatal): {e}")

    return state


async def _commit_to_db(state: PipelineState):
    def _insert(conn):
        with conn.transaction():
            cur = conn.execute(
                """INSERT INTO execution_logs (prompt_id, engine_name, country_code, raw_response_text)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (state.target_prompt.id,
                 state.engine_name,
                 state.country_code,
                 f"Error: {state.error_log}" if state.error_log else (state.raw_response_text or "Error: unknown")),
            )
            log_id = cur.fetchone()["id"]

            if state.extracted_data:
                for m in state.extracted_data.brand_mentions:
                    is_target = bool(re.search(r'\bgrab\s*on\b', m.brand_name, re.IGNORECASE))
                    conn.execute(
                        """INSERT INTO brand_mentions
                           (log_id, rank_position, brand_name, is_target_brand, sentiment, context_snippet, cited_url)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (log_id, m.rank_position, m.brand_name, is_target,
                         m.sentiment.value, m.context_snippet, m.cited_url),
                    )

                for c in state.extracted_data.ai_hallucinated_coupons:
                    conn.execute(
                        """INSERT INTO ai_hallucinated_coupons
                           (log_id, coupon_code, associated_merchant, status_flag)
                           VALUES (%s, %s, %s, %s)""",
                        (log_id, c.coupon_code, c.associated_merchant, c.status_flag.value),
                    )

            if state.serp_data and state.serp_data.organic_results:
                cur2 = conn.execute(
                    """INSERT INTO serp_results (prompt_id, device, results_count, raw_html_hash)
                       VALUES (%s::uuid, 'desktop', %s, %s) RETURNING id""",
                    (state.target_prompt.id, state.serp_data.results_count,
                     state.serp_data.raw_html_hash),
                )
                serp_id = cur2.fetchone()["id"]

                for r in state.serp_data.organic_results:
                    conn.execute(
                        """INSERT INTO serp_organic_entries
                           (serp_id, rank_position, title, snippet, url, domain, has_table, is_target)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (serp_id, r.rank_position, r.title, r.snippet, r.url,
                         r.domain, r.has_table, r.is_target),
                    )

                for q in state.serp_data.people_also_ask:
                    conn.execute(
                        """INSERT INTO serp_features (serp_id, feature_type, question_text)
                           VALUES (%s, 'paa', %s)""",
                        (serp_id, q),
                    )

                for kw in state.serp_data.related_keywords:
                    conn.execute(
                        """INSERT INTO serp_features (serp_id, feature_type, title)
                           VALUES (%s, 'related_keyword', %s)""",
                        (serp_id, kw),
                    )

                conn.execute(
                    "UPDATE prompts SET last_serp_at = NOW() WHERE id = %s::uuid",
                    (state.target_prompt.id,),
                )

            return log_id

    try:
        log_id = await run_db(_insert)
        log.info(f"[{state.engine_name}] Committed to DB: log_id={log_id}")
    except Exception as e:
        log.error(f"[{state.engine_name}] DB commit failed: {e}")
        from app.notifications import send_alert
        await send_alert(
            title="DB Commit Failed",
            message=f"Engine: {state.engine_name}\nError: {e}",
            severity="critical",
            ntype="scraper",
        )


async def run_rolling_batch(batch_size: int = 100, concurrency: int = 3):
    rows = await run_db(lambda conn: conn.execute(
        """SELECT id, text, merchant_category, intent_type
           FROM prompts
           ORDER BY last_run_at ASC NULLS FIRST
           LIMIT %s""",
        (batch_size,),
    ).fetchall())

    if not rows:
        log.info("No prompts to process. Skipping.")
        return

    engines = VISIBLE_ENGINES
    countries = ACTIVE_COUNTRIES
    sem = asyncio.Semaphore(concurrency)
    log.info(f"Rolling batch: prompts={len(rows)}, engines={len(engines)}, concurrency={concurrency}")
    broadcast("batch_start", tier=0, total_prompts=len(rows), engines=len(engines))

    async def _process_prompt(row):
        async with sem:
            prompt = PromptItem(
                id=str(row["id"]),
                text=row["text"],
                merchant_category=row["merchant_category"],
                intent_type=row["intent_type"],
            )
            # Shuffle engine order per prompt to avoid predictable patterns
            shuffled_engines = list(engines)
            random.shuffle(shuffled_engines)

            any_success = False
            for country in countries:
                for i, engine in enumerate(shuffled_engines):
                    try:
                        result = await run_pipeline(prompt, engine, country)
                        if result.raw_response_text and not result.error_log:
                            any_success = True
                    except Exception as e:
                        log.error(f"Pipeline failed for prompt={prompt.id}, engine={engine}: {e}")
                    if i < len(shuffled_engines) - 1:
                        delay = random.uniform(ENGINE_DELAY_MIN, ENGINE_DELAY_MAX)
                        log.debug(f"Anti-detection pause: {delay:.1f}s before next engine")
                        await asyncio.sleep(delay)

            await asyncio.sleep(random.uniform(PROMPT_DELAY_MIN, PROMPT_DELAY_MAX))

            if any_success:
                def _update_last_run(conn):
                    conn.execute("UPDATE prompts SET last_run_at = NOW() WHERE id = %s::uuid", (prompt.id,))
                    conn.commit()
                await run_db(_update_last_run)

    await asyncio.gather(*[_process_prompt(row) for row in rows])
    log.info(f"Rolling batch complete: processed={len(rows)}")
    broadcast("batch_done", tier=0, processed=len(rows))


async def run_full_sweep(concurrency: int = 4):
    rows = await run_db(lambda conn: conn.execute(
        "SELECT id, text, merchant_category, intent_type FROM prompts"
    ).fetchall())

    if not rows:
        log.warning("No prompts found in database. Skipping sweep.")
        return

    engines = VISIBLE_ENGINES
    countries = ACTIVE_COUNTRIES
    log.info(f"Starting full sweep: {len(rows)} prompts x {len(engines)} engines x {len(countries)} countries")
    broadcast("batch_start", tier=0, total_prompts=len(rows), engines=len(engines))
    sem = asyncio.Semaphore(concurrency)

    async def _process(row):
        async with sem:
            prompt = PromptItem(
                id=str(row["id"]),
                text=row["text"],
                merchant_category=row["merchant_category"],
                intent_type=row["intent_type"],
            )
            shuffled_engines = list(engines)
            random.shuffle(shuffled_engines)
            any_success = False

            for country in countries:
                for i, engine in enumerate(shuffled_engines):
                    try:
                        result = await run_pipeline(prompt, engine, country)
                        if result.raw_response_text and not result.error_log:
                            any_success = True
                    except Exception as e:
                        log.error(f"Pipeline failed for prompt={prompt.id}, engine={engine}, country={country}: {e}")
                    if i < len(shuffled_engines) - 1:
                        delay = random.uniform(ENGINE_DELAY_MIN, ENGINE_DELAY_MAX)
                        await asyncio.sleep(delay)

            await asyncio.sleep(random.uniform(PROMPT_DELAY_MIN, PROMPT_DELAY_MAX))

            if any_success:
                await run_db(lambda conn: (
                    conn.execute("UPDATE prompts SET last_run_at = NOW() WHERE id = %s::uuid", (prompt.id,)),
                    conn.commit(),
                ))

    await asyncio.gather(*[_process(row) for row in rows])
    broadcast("batch_done", tier=0, processed=len(rows))
    log.info("Full sweep completed")


_auto_heal_in_progress: set[str] = set()


def _track_engine_failure(engine_name: str):
    _engine_fail_count[engine_name] = _engine_fail_count.get(engine_name, 0) + 1
    fails = _engine_fail_count[engine_name]

    if fails == CONSECUTIVE_FAIL_THRESHOLD:
        from app.agent.scraper import rotate_engine_proxy
        rotate_engine_proxy(engine_name)
        log.info(f"[{engine_name}] {fails} consecutive fails - rotating proxy before cooldown")

    if fails >= CONSECUTIVE_FAIL_THRESHOLD:
        if engine_name in ("google_aio", "google_ai_mode"):
            cooldown = min(ENGINE_COOLDOWN_BASE + 20 * (fails - CONSECUTIVE_FAIL_THRESHOLD), ENGINE_COOLDOWN_MAX // 2)
        else:
            cooldown = min(ENGINE_COOLDOWN_BASE + 30 * (fails - CONSECUTIVE_FAIL_THRESHOLD), ENGINE_COOLDOWN_MAX)
        resume_at = time.time() + cooldown
        _engine_cooldown_until[engine_name] = resume_at
        log.warning(f"[{engine_name}] {fails} consecutive failures - cooldown {cooldown:.0f}s")
        broadcast("engine_cooldown", engine=engine_name, cooldown_seconds=int(cooldown),
                  resume_at=resume_at, fails=fails)

        if engine_name not in _auto_heal_in_progress:
            asyncio.ensure_future(_auto_heal_engine(engine_name))


def clear_engine_cooldown(engine_name: str):
    """Reset cooldown and failure count for an engine (e.g. after fresh cookies saved)."""
    _engine_fail_count.pop(engine_name, None)
    removed = _engine_cooldown_until.pop(engine_name, None)
    _auto_heal_in_progress.discard(engine_name)
    if removed:
        log.info(f"[{engine_name}] Cooldown cleared - engine re-enabled")
        broadcast("engine_recovery", engine=engine_name, action="cooldown_cleared")


async def _auto_heal_engine(engine_name: str):
    """Background auto-heal: proxy rotate, cookie refresh, or re-login depending on error type."""
    if engine_name in _auto_heal_in_progress:
        return

    _auto_heal_in_progress.add(engine_name)
    try:
        fails = _engine_fail_count.get(engine_name, 0)
        log.info(f"[{engine_name}] Auto-heal triggered after {fails} consecutive failures")
        broadcast("auto_login", provider=engine_name, step="auto_heal",
                  message=f"Auto-healing {engine_name} after {fails} consecutive failures")

        # Step 1: Rotate proxy (helps with captcha/IP blocks)
        from app.agent.scraper import rotate_engine_proxy
        rotate_engine_proxy(engine_name)
        log.info(f"[{engine_name}] Auto-heal: rotated proxy")

        # Step 2: For Google engines with captcha, proxy rotation + cooldown is the main fix
        if engine_name in ("google_aio", "google_ai_mode"):
            cooldown = min(300 + 60 * (fails - CONSECUTIVE_FAIL_THRESHOLD), ENGINE_COOLDOWN_MAX // 2)
            _engine_cooldown_until[engine_name] = time.time() + cooldown
            log.info(f"[{engine_name}] Auto-heal: extended cooldown {cooldown}s to let IP cool off")
            broadcast("engine_recovery", engine=engine_name, action="auto_heal_proxy_rotate",
                      cooldown=cooldown)

            # Also try cookie refresh since it visits the site (warms the session)
            refreshed = await try_reactive_refresh(engine_name)
            if refreshed:
                log.info(f"[{engine_name}] Auto-heal: cookie refresh also succeeded")
            return

        # Step 3: For auth-based engines, try cookie refresh then re-login
        from app.agent.account_pool import get_storage_state
        state, _ = await get_storage_state(engine_name)
        has_cookies = state and state.get("cookies")

        if has_cookies:
            refreshed = await try_reactive_refresh(engine_name)
            if refreshed:
                log.info(f"[{engine_name}] Auto-heal: cookie refresh succeeded - clearing cooldown")
                clear_engine_cooldown(engine_name)
                broadcast("engine_recovery", engine=engine_name, action="auto_heal_refresh")
                return

        from app.routes.auth import try_auto_relogin
        success = await try_auto_relogin(engine_name)
        if success:
            log.info(f"[{engine_name}] Auto-heal: auto-relogin succeeded - clearing cooldown")
            clear_engine_cooldown(engine_name)
            broadcast("engine_recovery", engine=engine_name, action="auto_heal_relogin")
        else:
            log.warning(f"[{engine_name}] Auto-heal failed - engine remains on cooldown")
            broadcast("auto_login", provider=engine_name, step="failed",
                      message=f"Auto-heal failed for {engine_name} - manual relogin needed")
    except Exception as e:
        log.error(f"[{engine_name}] Auto-heal error: {e}")
    finally:
        _auto_heal_in_progress.discard(engine_name)


def get_engine_cooldowns() -> dict:
    now = time.time()
    result = {}
    for eng, until in _engine_cooldown_until.items():
        remaining = until - now
        if remaining > 0:
            result[eng] = {"resume_at": until, "remaining": int(remaining), "fails": _engine_fail_count.get(eng, 0)}
    return result


def _get_available_engines() -> list[str]:
    from app.agent.account_pool import has_available_slot
    now = time.time()
    available = []
    for eng in VISIBLE_ENGINES:
        cooldown_until = _engine_cooldown_until.get(eng, 0)
        if now >= cooldown_until:
            if eng in _engine_cooldown_until:
                del _engine_cooldown_until[eng]
            available.append(eng)
        elif eng in AUTH_ENGINES and has_available_slot(eng):
            available.append(eng)
    return available


async def _fetch_oldest_first_batch(batch_size: int) -> list:
    """Fetch next batch with tier priority and partial-coverage completion.

    Order:
      1. Partially-scraped tier 1-2 keywords (some engines done, not all)
      2. Never-scraped tier 1-2 keywords
      3. Remaining tier 1-2 keywords (oldest first)
      4. Partially-scraped tier 3+
      5. Never-scraped tier 3+
      6. Remaining tier 3+

    Tier 1-2 keywords (canonical high-priority) always fill first.
    Only when tier 1-2 has no work left does tier 3 get batch slots.
    """
    engine_count = len(VISIBLE_ENGINES)
    engine_list = list(VISIBLE_ENGINES)
    rows = await run_db(lambda conn: conn.execute(
        """SELECT p.id, p.text, p.merchant_category, p.intent_type,
                  COUNT(DISTINCT el.engine_name) FILTER (
                      WHERE el.engine_name = ANY(%s)
                  ) AS covered_engines
           FROM prompts p
           LEFT JOIN execution_logs el ON el.prompt_id = p.id
           WHERE p.is_canonical = TRUE AND p.intent_type = 'GEO'
           GROUP BY p.id, p.text, p.merchant_category, p.intent_type, p.tier
           ORDER BY
               CASE WHEN p.tier <= 2 THEN 0 ELSE 1 END,
               CASE
                   WHEN COUNT(DISTINCT el.engine_name) FILTER (
                       WHERE el.engine_name = ANY(%s)
                   ) BETWEEN 1 AND %s THEN 0
                   ELSE 1
               END,
               p.last_run_at ASC NULLS FIRST
           LIMIT %s""",
        (engine_list, engine_list, engine_count - 1, batch_size),
    ).fetchall())

    result = list(rows)
    random.shuffle(result)
    return result


async def run_continuous_loop():
    global _continuous_running
    if _continuous_running:
        log.warning("Continuous loop already running")
        return
    _continuous_running = True
    log.info("Continuous agent loop started - oldest-first, all keywords")
    broadcast("agent_mode", mode="continuous")

    try:
        settings = get_settings()
        batch_size = settings.continuous_batch_size
    except Exception as e:
        log.error(f"Continuous loop failed to initialize: {e}")
        _continuous_running = False
        return

    while _continuous_running:
        try:
            available = _get_available_engines()
            if not available:
                from app.agent.account_pool import time_until_next_available as _pool_wait
                pool_waits = {eng: _pool_wait(eng) for eng in VISIBLE_ENGINES}
                pool_soonest = min(pool_waits.values()) if pool_waits else 0

                cooldowns = get_engine_cooldowns()
                engine_soonest = min(c["remaining"] for c in cooldowns.values()) if cooldowns else 0

                soonest = min(pool_soonest, engine_soonest) if (pool_soonest and engine_soonest) else (pool_soonest or engine_soonest)
                if soonest > 0:
                    wait_time = min(soonest + 5, 300)
                    log.info(f"All engines on cooldown. Soonest recovery in {soonest:.0f}s (waiting {wait_time:.0f}s)")
                    broadcast("agent_waiting", reason="all_engines_cooldown",
                              resume_in=int(wait_time), cooldowns={k: v["remaining"] for k, v in cooldowns.items()})
                    await asyncio.sleep(wait_time)
                    continue
                await asyncio.sleep(30)
                continue

            rows = await _fetch_oldest_first_batch(batch_size)

            if not rows:
                log.info("No prompts to process. Waiting 60s...")
                broadcast("agent_waiting", reason="no_prompts", resume_in=60, cooldowns={})
                await asyncio.sleep(60)
                continue

            uncovered = await run_db(lambda conn: conn.execute(
                """SELECT COUNT(1) as cnt FROM prompts
                   WHERE intent_type = 'GEO' AND last_run_at IS NULL"""
            ).fetchone())
            if uncovered and uncovered["cnt"] == 0:
                cycle_age = await run_db(lambda conn: conn.execute(
                    """SELECT MIN(last_run_at) as oldest FROM prompts WHERE intent_type = 'GEO'"""
                ).fetchone())
                if cycle_age and cycle_age["oldest"]:
                    log.info(f"GEO cycle complete! All keywords covered. Oldest run: {cycle_age['oldest']}. Starting next cycle.")
                    broadcast("cycle_complete", oldest_run=str(cycle_age["oldest"]))

            log.info(f"Batch: {len(rows)} keywords, oldest-first")
            broadcast("batch_start", tier=0, total_prompts=len(rows), engines=len(available))

            prompts = [
                PromptItem(
                    id=str(row["id"]), text=row["text"],
                    merchant_category=row["merchant_category"],
                    intent_type=row["intent_type"],
                )
                for row in rows
            ]

            await run_parallel_engines(
                prompts=prompts,
                engines=available,
                concurrency_per_engine=settings.concurrency_per_engine,
                skip_fresh=settings.skip_fresh_data,
                freshness_hours=settings.freshness_hours,
                use_dedup=settings.use_dedup_cache,
            )

            broadcast("batch_done", tier=0, processed=len(rows))

            cooldown = random.uniform(BATCH_COOLDOWN_MIN, BATCH_COOLDOWN_MAX)
            log.info(f"Batch done ({len(rows)} keywords). Cooldown: {cooldown:.0f}s")
            broadcast("agent_waiting", reason="batch_cooldown", resume_in=int(cooldown),
                      cooldowns={k: v["remaining"] for k, v in get_engine_cooldowns().items()})
            await asyncio.sleep(cooldown)

        except Exception as e:
            log.error(f"Continuous loop error: {e}")
            broadcast("agent_waiting", reason="error_recovery", resume_in=60, cooldowns={})
            await asyncio.sleep(60)

    log.info("Continuous agent loop stopped")


# ── Incremental run helpers ──────────────────────────────────

async def _is_fresh(prompt_id: str, engine_name: str, max_age_hours: int = 6) -> bool:
    """Check if we already have recent data for this prompt+engine."""
    def _check(conn):
        row = conn.execute(
            """SELECT captured_at FROM execution_logs
               WHERE prompt_id = %s::uuid AND engine_name = %s
                 AND captured_at >= NOW() - make_interval(hours => %s)
                 AND raw_response_text NOT LIKE 'Error:%%'
                 AND raw_response_text NOT LIKE 'Google Search%%'
                 AND raw_response_text NOT LIKE '%%Please click%%'
                 AND raw_response_text NOT LIKE '%%not redirected%%'
               LIMIT 1""",
            (prompt_id, engine_name, max_age_hours),
        ).fetchone()
        return row is not None
    return await run_db(_check)


# ── Parallel engine pipeline ─────────────────────────────────

async def run_engine_pipeline(
    engine_name: str,
    prompts: list[PromptItem],
    concurrency: int = 2,
    skip_fresh: bool = True,
    freshness_hours: int = 6,
    use_dedup: bool = True,
):
    """Run a single engine across many prompts independently.

    Returns (processed_ids, covered_ids):
      - processed_ids: actually scraped this run
      - covered_ids: scraped + fresh-skipped + dedup-skipped (engine has data or equivalent)
    """
    sem = asyncio.Semaphore(concurrency)
    processed = 0
    skipped_fresh = 0
    skipped_dedup = 0
    success = 0
    processed_ids: set[str] = set()
    covered_ids: set[str] = set()

    log.info(f"[{engine_name}] Engine pipeline: {len(prompts)} prompts, concurrency={concurrency}")

    async def _process_one(prompt: PromptItem):
        nonlocal processed, skipped_fresh, skipped_dedup, success

        async with sem:
            intent = getattr(prompt, "intent_type", None)
            brand_key = getattr(prompt, "keyword_group", None)
            if not brand_key or not intent:
                brand_key, intent = await _get_keyword_group_and_intent(prompt.id)

            effective_freshness = 24 if intent == "GEO" else 24
            if skip_fresh and await _is_fresh(prompt.id, engine_name, effective_freshness):
                skipped_fresh += 1
                covered_ids.add(prompt.id)
                return
            if use_dedup and brand_key and intent != "GEO" and is_cached(brand_key, engine_name):
                skipped_dedup += 1
                covered_ids.add(prompt.id)
                return

            cooldown_remaining = _engine_cooldown_until.get(engine_name, 0) - time.time()
            if cooldown_remaining > 0:
                if engine_name in ("google_aio", "google_ai_mode") and cooldown_remaining <= 120:
                    log.info(f"[{engine_name}] Waiting {cooldown_remaining:.0f}s cooldown (Google engines never skip)")
                    await asyncio.sleep(cooldown_remaining + 1)
                    _engine_cooldown_until.pop(engine_name, None)
                    _engine_fail_count.pop(engine_name, None)
                elif engine_name in AUTH_ENGINES:
                    from app.agent.account_pool import has_available_slot
                    if has_available_slot(engine_name):
                        log.info(f"[{engine_name}] Engine on cooldown but pool has available slot - proceeding")
                    else:
                        return
                else:
                    return

            try:
                result = await run_pipeline(prompt, engine_name)
                processed += 1
                processed_ids.add(prompt.id)
                covered_ids.add(prompt.id)
                if result.raw_response_text and not result.error_log:
                    success += 1
                    if brand_key:
                        await mark_cached_db(brand_key, engine_name, prompt.id)
            except Exception as e:
                log.error(f"[{engine_name}] Pipeline error for {prompt.text[:40]}: {e}")
                processed += 1
                processed_ids.add(prompt.id)
                covered_ids.add(prompt.id)

            if engine_name in ("google_aio", "google_ai_mode"):
                delay = random.uniform(8, 15)
            else:
                delay = random.uniform(ENGINE_DELAY_MIN, ENGINE_DELAY_MAX)
            await asyncio.sleep(delay)

    await asyncio.gather(*[_process_one(p) for p in prompts], return_exceptions=True)

    log.info(
        f"[{engine_name}] Pipeline done: {success}/{processed} succeeded, "
        f"{skipped_fresh} fresh-skipped, {skipped_dedup} dedup-skipped, "
        f"{len(covered_ids)}/{len(prompts)} covered"
    )
    broadcast(
        "engine_pipeline_done",
        engine=engine_name,
        processed=processed,
        success=success,
        skipped_fresh=skipped_fresh,
        skipped_dedup=skipped_dedup,
    )
    return processed_ids, covered_ids


async def _get_keyword_group(prompt_id: str) -> str | None:
    def _q(conn):
        row = conn.execute(
            "SELECT keyword_group FROM prompts WHERE id = %s::uuid",
            (prompt_id,),
        ).fetchone()
        return row["keyword_group"] if row else None
    return await run_db(_q)


async def _get_keyword_group_and_intent(prompt_id: str) -> tuple[str | None, str | None]:
    def _q(conn):
        row = conn.execute(
            "SELECT keyword_group, intent_type FROM prompts WHERE id = %s::uuid",
            (prompt_id,),
        ).fetchone()
        if row:
            return row["keyword_group"], row["intent_type"]
        return None, None
    return await run_db(_q)


async def run_parallel_engines(
    prompts: list[PromptItem],
    engines: list[str] | None = None,
    concurrency_per_engine: int = 2,
    skip_fresh: bool = True,
    freshness_hours: int = 6,
    use_dedup: bool = True,
):
    """Run ALL engines in parallel, each with its own independent pipeline.

    Instead of: for each keyword -> for each engine (sequential)
    Now:        for each engine -> process all keywords (parallel across engines)
    """
    if engines is None:
        engines = list(VISIBLE_ENGINES)

    available = [e for e in engines if _engine_cooldown_until.get(e, 0) <= time.time()]
    if not available:
        log.warning("All engines on cooldown, skipping parallel run")
        return

    log.info(f"Parallel engine pipelines: {len(available)} engines x {len(prompts)} prompts")
    broadcast("parallel_start", engines=available, prompts=len(prompts))

    results = await asyncio.gather(*[
        run_engine_pipeline(
            engine_name=eng,
            prompts=prompts,
            concurrency=ENGINE_CONCURRENCY.get(eng, concurrency_per_engine),
            skip_fresh=skip_fresh,
            freshness_hours=freshness_hours,
            use_dedup=use_dedup,
        )
        for eng in available
    ], return_exceptions=True)

    # Mark last_run_at only for keywords covered by ALL available engines
    covered_per_engine: list[set[str]] = []
    for r in results:
        if isinstance(r, tuple) and len(r) == 2:
            covered_per_engine.append(r[1])

    any_covered = set()
    for c in covered_per_engine:
        any_covered |= c

    fully_covered = set.intersection(*covered_per_engine) if covered_per_engine else set()

    log.info(
        f"Parallel engines done: {len(available)} engines, "
        f"{len(any_covered)} keywords covered (any engine), "
        f"{len(fully_covered)} fully covered (all engines)"
    )

    if fully_covered:
        prompt_ids = list(fully_covered)
        def _update_batch(conn):
            conn.execute(
                "UPDATE prompts SET last_run_at = NOW() WHERE id = ANY(%s::uuid[])",
                (prompt_ids,),
            )
            conn.commit()
        try:
            await run_db(_update_batch)
        except Exception as e:
            log.warning(f"Batch last_run_at update failed: {e}")

    broadcast("parallel_done", engines=available, prompts=len(prompts))
