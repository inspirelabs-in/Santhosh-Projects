import asyncio
import logging
import random
import re
import time
import httpx
from app.models import PipelineState, PromptItem
from app.agent.scraper import run_scrape, ALL_ENGINES, VISIBLE_ENGINES
from app.agent.parser import parse_response
from app.database import run_db
from app.config import get_settings
from app.events import broadcast

log = logging.getLogger("geo.pipeline")

MAX_RETRIES = 3
ACTIVE_COUNTRIES = ["IN"]

ENGINE_DELAY_MIN = 8
ENGINE_DELAY_MAX = 20
PROMPT_DELAY_MIN = 5
PROMPT_DELAY_MAX = 15

BATCH_COOLDOWN_MIN = 60
BATCH_COOLDOWN_MAX = 180
ENGINE_COOLDOWN_BASE = 300
ENGINE_COOLDOWN_MAX = 3600
CONSECUTIVE_FAIL_THRESHOLD = 3

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
            if engine_name in ("chatgpt", "claude", "grok", "gemini"):
                break
            if "RESOURCE_EXHAUSTED" in (scrape_result.error_log or ""):
                await asyncio.sleep(15)
            continue

        state.raw_response_text = scrape_result.raw_response_text
        state.raw_html_payload = scrape_result.raw_html_payload
        state.error_log = None
        _engine_fail_count[engine_name] = 0
        broadcast("scrape_done", engine=engine_name, prompt=prompt.text[:80],
                  chars=len(scrape_result.raw_response_text), urls=len(scrape_result.cited_urls))

        if state.raw_response_text.startswith("[No AI Overview]"):
            log.info(f"[{engine_name}] No AI Overview available — skipping parser")
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
                 state.raw_response_text or f"Error: {state.error_log or 'unknown'}"),
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
            return log_id

    try:
        log_id = await run_db(_insert)
        log.info(f"[{state.engine_name}] Committed to DB: log_id={log_id}")
    except Exception as e:
        log.error(f"[{state.engine_name}] DB commit failed: {e}")
        await _alert_slack(f"[CRITICAL] DB commit failed for {state.engine_name}: {e}")


async def _alert_slack(message: str):
    settings = get_settings()
    if not settings.slack_webhook_url:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(settings.slack_webhook_url, json={"text": message})
    except Exception as e:
        log.error(f"Slack alert failed: {e}")


async def run_rolling_batch(tier: int, batch_size: int, concurrency: int = 3):
    rows = await run_db(lambda conn: conn.execute(
        """SELECT id, text, merchant_category, intent_type
           FROM prompts
           WHERE tier = %s AND is_canonical = TRUE
           ORDER BY last_run_at ASC NULLS FIRST
           LIMIT %s""",
        (tier, batch_size),
    ).fetchall())

    if not rows:
        log.info(f"No prompts for tier {tier}. Skipping.")
        return

    engines = VISIBLE_ENGINES
    countries = ACTIVE_COUNTRIES
    sem = asyncio.Semaphore(concurrency)
    log.info(f"Rolling batch: tier={tier}, prompts={len(rows)}, engines={len(engines)}, concurrency={concurrency}")
    broadcast("batch_start", tier=tier, total_prompts=len(rows), engines=len(engines))

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

            for country in countries:
                for i, engine in enumerate(shuffled_engines):
                    try:
                        await run_pipeline(prompt, engine, country)
                    except Exception as e:
                        log.error(f"Pipeline failed for prompt={prompt.id}, engine={engine}: {e}")
                    # Randomized delay between engines to avoid detection
                    if i < len(shuffled_engines) - 1:
                        delay = random.uniform(ENGINE_DELAY_MIN, ENGINE_DELAY_MAX)
                        log.debug(f"Anti-detection pause: {delay:.1f}s before next engine")
                        await asyncio.sleep(delay)

            # Delay before marking done (spreads DB writes)
            await asyncio.sleep(random.uniform(PROMPT_DELAY_MIN, PROMPT_DELAY_MAX))

            def _update_last_run(conn):
                conn.execute("UPDATE prompts SET last_run_at = NOW() WHERE id = %s::uuid", (prompt.id,))
                conn.commit()
            await run_db(_update_last_run)

    await asyncio.gather(*[_process_prompt(row) for row in rows])
    log.info(f"Rolling batch complete: tier={tier}, processed={len(rows)}")
    broadcast("batch_done", tier=tier, processed=len(rows))


async def run_full_sweep():
    rows = await run_db(lambda conn: conn.execute(
        "SELECT id, text, merchant_category, intent_type FROM prompts"
    ).fetchall())

    if not rows:
        log.warning("No prompts found in database. Skipping sweep.")
        return

    engines = VISIBLE_ENGINES
    countries = ACTIVE_COUNTRIES
    log.info(f"Starting full sweep: {len(rows)} prompts x {len(engines)} engines x {len(countries)} countries")

    for row in rows:
        prompt = PromptItem(
            id=str(row["id"]),
            text=row["text"],
            merchant_category=row["merchant_category"],
            intent_type=row["intent_type"],
        )
        shuffled_engines = list(engines)
        random.shuffle(shuffled_engines)

        for country in countries:
            for i, engine in enumerate(shuffled_engines):
                try:
                    await run_pipeline(prompt, engine, country)
                except Exception as e:
                    log.error(f"Pipeline failed for prompt={prompt.id}, engine={engine}, country={country}: {e}")
                if i < len(shuffled_engines) - 1:
                    delay = random.uniform(ENGINE_DELAY_MIN, ENGINE_DELAY_MAX)
                    await asyncio.sleep(delay)

        await asyncio.sleep(random.uniform(PROMPT_DELAY_MIN, PROMPT_DELAY_MAX))

    log.info("Full sweep completed")


def _track_engine_failure(engine_name: str):
    _engine_fail_count[engine_name] = _engine_fail_count.get(engine_name, 0) + 1
    fails = _engine_fail_count[engine_name]
    if fails >= CONSECUTIVE_FAIL_THRESHOLD:
        cooldown = min(ENGINE_COOLDOWN_BASE * (2 ** (fails - CONSECUTIVE_FAIL_THRESHOLD)), ENGINE_COOLDOWN_MAX)
        resume_at = time.time() + cooldown
        _engine_cooldown_until[engine_name] = resume_at
        log.warning(f"[{engine_name}] {fails} consecutive failures — cooldown {cooldown:.0f}s")
        broadcast("engine_cooldown", engine=engine_name, cooldown_seconds=int(cooldown),
                  resume_at=resume_at, fails=fails)


def get_engine_cooldowns() -> dict:
    now = time.time()
    result = {}
    for eng, until in _engine_cooldown_until.items():
        remaining = until - now
        if remaining > 0:
            result[eng] = {"resume_at": until, "remaining": int(remaining), "fails": _engine_fail_count.get(eng, 0)}
    return result


def _get_available_engines() -> list[str]:
    now = time.time()
    available = []
    for eng in VISIBLE_ENGINES:
        cooldown_until = _engine_cooldown_until.get(eng, 0)
        if now >= cooldown_until:
            if eng in _engine_cooldown_until:
                del _engine_cooldown_until[eng]
            available.append(eng)
    return available


async def run_continuous_loop():
    global _continuous_running
    if _continuous_running:
        log.warning("Continuous loop already running")
        return
    _continuous_running = True
    log.info("Continuous agent loop started — will process keywords non-stop")
    broadcast("agent_mode", mode="continuous")

    settings = get_settings()
    batch_size = settings.tier1_batch_size
    concurrency = settings.tier1_concurrency

    while _continuous_running:
        try:
            available = _get_available_engines()
            if not available:
                cooldowns = get_engine_cooldowns()
                if cooldowns:
                    soonest = min(c["remaining"] for c in cooldowns.values())
                    log.info(f"All engines on cooldown. Resuming in {soonest}s")
                    broadcast("agent_waiting", reason="all_engines_cooldown",
                              resume_in=soonest, cooldowns={k: v["remaining"] for k, v in cooldowns.items()})
                    await asyncio.sleep(min(soonest + 5, 120))
                    continue
                await asyncio.sleep(30)
                continue

            rows = await run_db(lambda conn: conn.execute(
                """SELECT id, text, merchant_category, intent_type
                   FROM prompts
                   WHERE is_canonical = TRUE
                   ORDER BY last_run_at ASC NULLS FIRST
                   LIMIT %s""",
                (batch_size,),
            ).fetchall())

            if not rows:
                log.info("No prompts to process. Waiting 60s...")
                broadcast("agent_waiting", reason="no_prompts", resume_in=60, cooldowns={})
                await asyncio.sleep(60)
                continue

            broadcast("batch_start", tier=1, total_prompts=len(rows), engines=len(available))
            sem = asyncio.Semaphore(concurrency)

            async def _process(row):
                async with sem:
                    prompt = PromptItem(
                        id=str(row["id"]), text=row["text"],
                        merchant_category=row["merchant_category"],
                        intent_type=row["intent_type"],
                    )
                    engines = list(available)
                    random.shuffle(engines)
                    for country in ACTIVE_COUNTRIES:
                        for i, engine in enumerate(engines):
                            if _engine_cooldown_until.get(engine, 0) > time.time():
                                continue
                            try:
                                await run_pipeline(prompt, engine, country)
                            except Exception as e:
                                log.error(f"Pipeline failed: prompt={prompt.id}, engine={engine}: {e}")
                            if i < len(engines) - 1:
                                await asyncio.sleep(random.uniform(ENGINE_DELAY_MIN, ENGINE_DELAY_MAX))
                    await asyncio.sleep(random.uniform(PROMPT_DELAY_MIN, PROMPT_DELAY_MAX))
                    await run_db(lambda conn: (
                        conn.execute("UPDATE prompts SET last_run_at = NOW() WHERE id = %s::uuid", (prompt.id,)),
                        conn.commit(),
                    ))

            await asyncio.gather(*[_process(row) for row in rows])
            broadcast("batch_done", tier=1, processed=len(rows))

            cooldown = random.uniform(BATCH_COOLDOWN_MIN, BATCH_COOLDOWN_MAX)
            log.info(f"Batch done. Anti-detection cooldown: {cooldown:.0f}s before next batch")
            broadcast("agent_waiting", reason="batch_cooldown", resume_in=int(cooldown),
                      cooldowns={k: v["remaining"] for k, v in get_engine_cooldowns().items()})
            await asyncio.sleep(cooldown)

        except Exception as e:
            log.error(f"Continuous loop error: {e}")
            broadcast("agent_waiting", reason="error_recovery", resume_in=60, cooldowns={})
            await asyncio.sleep(60)

    log.info("Continuous agent loop stopped")
