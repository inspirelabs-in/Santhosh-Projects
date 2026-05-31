import asyncio
import json
import logging
from fastapi import APIRouter, Form, Response
from fastapi.responses import RedirectResponse
from sse_starlette.sse import EventSourceResponse
from app.database import run_db
from app.agent.pipeline import run_full_sweep
from app.events import subscribe, unsubscribe, get_recent

router = APIRouter(prefix="/api")
log = logging.getLogger("geo.api")


@router.post("/trigger")
async def trigger_scrape():
    log.info("Manual trigger received")
    asyncio.create_task(run_full_sweep())
    return RedirectResponse(url="/?triggered=1", status_code=303)


@router.post("/seed")
async def seed_prompts():
    def _seed(conn):
        cnt = conn.execute("SELECT COUNT(*) as cnt FROM prompts").fetchone()["cnt"]
        if cnt == 0:
            conn.execute("""
                INSERT INTO prompts (text, merchant_category, intent_type) VALUES
                ('ajio coupon code', 'Fashion', 'Transactional'),
                ('swiggy promo code today', 'Food', 'Transactional'),
                ('best coupon sites india', 'General', 'Commercial'),
                ('grabon coupons review', 'General', 'Direct Brand'),
                ('myntra discount code 2025', 'Fashion', 'Transactional'),
                ('amazon india coupon code', 'Electronics', 'Transactional'),
                ('zomato offers today', 'Food', 'Transactional'),
                ('flipkart sale coupon', 'Electronics', 'Transactional')
            """)
            conn.commit()
            log.info("Seeded 8 natural search prompts")
    await run_db(_seed)
    return RedirectResponse(url="/", status_code=303)


@router.post("/clear-logs")
async def clear_logs():
    def _clear(conn):
        conn.execute("DELETE FROM brand_mentions")
        conn.execute("DELETE FROM ai_hallucinated_coupons")
        conn.execute("DELETE FROM execution_logs")
        conn.commit()
    await run_db(_clear)
    log.info("All logs cleared")
    return RedirectResponse(url="/", status_code=303)


@router.post("/save-cookies")
async def save_cookies(cookie_json: str = Form(...)):
    try:
        parsed = json.loads(cookie_json)
    except json.JSONDecodeError:
        return Response("Invalid JSON", status_code=400)

    dumped = json.dumps(parsed)
    def _save(conn):
        conn.execute("""
            INSERT INTO app_settings (key, value, updated_at)
            VALUES ('storageState.json', %s::jsonb, NOW())
            ON CONFLICT (key)
            DO UPDATE SET value = %s::jsonb, updated_at = NOW()
        """, (dumped, dumped))
        conn.commit()
    await run_db(_save)
    return RedirectResponse(url="/", status_code=303)


@router.post("/delete-cookies")
async def delete_cookies():
    def _del(conn):
        conn.execute("DELETE FROM app_settings WHERE key = 'storageState.json'")
        conn.commit()
    await run_db(_del)
    return RedirectResponse(url="/", status_code=303)


@router.post("/add-prompt")
async def add_prompt(
    text: str = Form(...),
    category: str = Form(...),
    intent: str = Form(...),
):
    def _add(conn):
        conn.execute(
            """INSERT INTO prompts (text, merchant_category, intent_type)
               VALUES (%s, %s, %s) ON CONFLICT (text) DO NOTHING""",
            (text, category, intent),
        )
        conn.commit()
    await run_db(_add)
    log.info(f"Added prompt: {text[:50]}...")
    return RedirectResponse(url="/", status_code=303)


@router.post("/delete-prompt/{prompt_id}")
async def delete_prompt(prompt_id: str):
    def _del(conn):
        conn.execute("DELETE FROM prompts WHERE id = %s::uuid", (prompt_id,))
        conn.commit()
    await run_db(_del)
    log.info(f"Deleted prompt: {prompt_id}")
    return RedirectResponse(url="/", status_code=303)


@router.post("/import-keywords")
async def import_keywords():
    from scripts.import_keywords import run_import
    log.info("Keyword import triggered via API")
    asyncio.create_task(run_import())
    return {"status": "import_started", "message": "Importing keywords in background"}


@router.get("/keyword-stats")
async def keyword_stats():
    def _stats(conn):
        tiers = conn.execute("""
            SELECT tier, is_canonical,
                   COUNT(*) as count,
                   MIN(last_run_at) as oldest_run,
                   MAX(last_run_at) as newest_run,
                   COUNT(*) FILTER (WHERE last_run_at IS NULL) as never_run
            FROM prompts
            GROUP BY tier, is_canonical
            ORDER BY tier
        """).fetchall()
        total = conn.execute("SELECT COUNT(*) as cnt FROM prompts").fetchone()["cnt"]
        groups = conn.execute(
            "SELECT COUNT(DISTINCT keyword_group) as cnt FROM prompts WHERE keyword_group IS NOT NULL"
        ).fetchone()["cnt"]
        return {"total": total, "keyword_groups": groups, "tiers": [dict(r) for r in tiers]}
    return await run_db(_stats)


@router.get("/activity-stream")
async def activity_stream():
    q = subscribe()

    async def _gen():
        try:
            while True:
                try:
                    evt = await asyncio.wait_for(q.get(), timeout=30)
                    yield {"event": evt["type"], "data": json.dumps(evt)}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        except asyncio.CancelledError:
            pass
        finally:
            unsubscribe(q)

    return EventSourceResponse(_gen())


@router.get("/activity-recent")
async def activity_recent():
    return get_recent()


@router.post("/test-keyword")
async def test_keyword(text: str = Form(...)):
    """Run a single keyword across all engines — for testing. Returns immediately, runs in background."""
    from app.agent.pipeline import run_pipeline, ENGINE_DELAY_MIN, ENGINE_DELAY_MAX
    from app.agent.scraper import ALL_ENGINES
    from app.models import PromptItem
    import random

    # Insert or find prompt in DB so FK constraint is satisfied
    def _ensure_prompt(conn):
        row = conn.execute("SELECT id FROM prompts WHERE text = %s", (text,)).fetchone()
        if row:
            return str(row["id"])
        cur = conn.execute(
            """INSERT INTO prompts (text, merchant_category, intent_type)
               VALUES (%s, 'Test', 'Transactional') RETURNING id""",
            (text,),
        )
        conn.commit()
        return str(cur.fetchone()["id"])

    prompt_id = await run_db(_ensure_prompt)

    async def _run():
        prompt = PromptItem(id=prompt_id, text=text, merchant_category="Test", intent_type="Transactional")
        shuffled = list(ALL_ENGINES)
        random.shuffle(shuffled)
        for i, engine in enumerate(shuffled):
            try:
                log.info(f"[test] Running: {engine} — {text[:50]}")
                await run_pipeline(prompt, engine, "IN")
            except Exception as e:
                log.error(f"[test] {engine} failed: {e}")
            if i < len(shuffled) - 1:
                delay = random.uniform(ENGINE_DELAY_MIN, ENGINE_DELAY_MAX)
                log.info(f"[test] Waiting {delay:.0f}s before next engine...")
                await asyncio.sleep(delay)
        log.info(f"[test] Keyword test complete: {text[:50]}")

    asyncio.create_task(_run())
    return {"status": "started", "keyword": text, "engines": ALL_ENGINES}


@router.post("/test-engine")
async def test_engine(text: str = Form(...), engine: str = Form(...)):
    """Run a single keyword on one engine. Returns immediately, runs in background."""
    from app.agent.pipeline import run_pipeline
    from app.agent.scraper import ALL_ENGINES
    from app.models import PromptItem

    if engine not in ALL_ENGINES:
        return {"error": f"Unknown engine: {engine}", "available": ALL_ENGINES}

    def _ensure_prompt(conn):
        row = conn.execute("SELECT id FROM prompts WHERE text = %s", (text,)).fetchone()
        if row:
            return str(row["id"])
        cur = conn.execute(
            """INSERT INTO prompts (text, merchant_category, intent_type)
               VALUES (%s, 'Test', 'Transactional') RETURNING id""",
            (text,),
        )
        conn.commit()
        return str(cur.fetchone()["id"])

    prompt_id = await run_db(_ensure_prompt)

    async def _run():
        prompt = PromptItem(id=prompt_id, text=text, merchant_category="Test", intent_type="Transactional")
        try:
            log.info(f"[test-engine] Running: {engine} — {text[:50]}")
            await run_pipeline(prompt, engine, "IN")
            log.info(f"[test-engine] {engine} complete: {text[:50]}")
        except Exception as e:
            log.error(f"[test-engine] {engine} failed: {e}")

    asyncio.create_task(_run())
    return {"status": "started", "keyword": text, "engine": engine}


@router.post("/trigger-tier/{tier}")
async def trigger_tier(tier: int):
    from app.agent.pipeline import run_rolling_batch
    from app.config import get_settings
    settings = get_settings()
    batch_map = {1: settings.tier1_batch_size, 2: settings.tier2_batch_size, 3: settings.tier3_batch_size}
    conc_map = {1: settings.tier1_concurrency, 2: settings.tier2_concurrency, 3: settings.tier3_concurrency}
    batch_size = batch_map.get(tier, 100)
    concurrency = conc_map.get(tier, 2)
    log.info(f"Manual trigger: tier={tier}, batch={batch_size}, concurrency={concurrency}")
    asyncio.create_task(run_rolling_batch(tier, batch_size, concurrency))
    return {"status": "started", "tier": tier, "batch_size": batch_size}


@router.post("/reparse")
async def reparse_logs(batch_size: int = 50):
    """Re-parse execution logs that have raw text but no brand mentions. Runs in background."""
    from scripts.reparse import reparse_unparsed
    log.info(f"Re-parse triggered: batch_size={batch_size}")
    asyncio.create_task(reparse_unparsed(batch_size=batch_size, delay=0.5))
    return {"status": "started", "batch_size": batch_size}


@router.get("/reparse-stats")
async def reparse_stats():
    """How many logs need re-parsing."""
    def _stats(conn):
        total = conn.execute("SELECT COUNT(*) as cnt FROM execution_logs").fetchone()["cnt"]
        parsed = conn.execute("SELECT COUNT(DISTINCT log_id) as cnt FROM brand_mentions").fetchone()["cnt"]
        errors = conn.execute("SELECT COUNT(*) as cnt FROM execution_logs WHERE raw_response_text LIKE 'Error:%%'").fetchone()["cnt"]
        return {"total_logs": total, "parsed": parsed, "errors": errors, "need_reparse": total - parsed - errors}
    return await run_db(_stats)


@router.get("/api-costs")
async def api_costs():
    def _costs(conn):
        total = conn.execute("""
            SELECT COALESCE(SUM(cost_usd), 0) as total_cost,
                   COALESCE(SUM(input_tokens), 0) as total_input,
                   COALESCE(SUM(output_tokens), 0) as total_output,
                   COUNT(*) as total_calls
            FROM api_costs
        """).fetchone()
        today = conn.execute("""
            SELECT COALESCE(SUM(cost_usd), 0) as cost,
                   COUNT(*) as calls
            FROM api_costs WHERE created_at::date = CURRENT_DATE
        """).fetchone()
        by_model = conn.execute("""
            SELECT model, COUNT(*) as calls,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   SUM(cost_usd) as cost
            FROM api_costs GROUP BY model ORDER BY cost DESC
        """).fetchall()
        by_day = conn.execute("""
            SELECT created_at::date::text as date,
                   SUM(cost_usd) as cost, COUNT(*) as calls
            FROM api_costs
            GROUP BY created_at::date
            ORDER BY date DESC LIMIT 30
        """).fetchall()
        return {
            "total_cost_usd": float(total["total_cost"]),
            "total_input_tokens": total["total_input"],
            "total_output_tokens": total["total_output"],
            "total_calls": total["total_calls"],
            "today_cost_usd": float(today["cost"]),
            "today_calls": today["calls"],
            "by_model": [dict(r) for r in by_model],
            "by_day": [dict(r) for r in by_day],
        }
    return await run_db(_costs)


# ─── Analytics Endpoints ───────────────────────────────────────────────


COMPETITORS = ["GrabOn", "CouponDunia", "CashKaro", "DesiDime"]
ALL_ANALYTICS_ENGINES = [
    "google_aio", "google_ai_mode", "perplexity", "gemini", "chatgpt", "claude",
]


def _normalize_brand(name: str) -> str:
    n = name.lower()
    if "grabon" in n or "grab on" in n:
        return "GrabOn"
    if "coupon" in n and "dunia" in n:
        return "CouponDunia"
    if "cash" in n and "karo" in n:
        return "CashKaro"
    if "desi" in n and "dime" in n:
        return "DesiDime"
    return name


@router.get("/analytics/visibility")
async def analytics_visibility():
    """Brand visibility (Share of Voice) over last 30 days, daily, per engine."""
    def _query(conn):
        rows = conn.execute("""
            SELECT
                el.captured_at::date::text AS day,
                el.engine_name,
                CASE
                    WHEN LOWER(bm.brand_name) LIKE '%%grabon%%' OR LOWER(bm.brand_name) LIKE '%%grab on%%' THEN 'GrabOn'
                    WHEN LOWER(bm.brand_name) LIKE '%%coupondunia%%' OR LOWER(bm.brand_name) LIKE '%%coupon dunia%%' THEN 'CouponDunia'
                    WHEN LOWER(bm.brand_name) LIKE '%%cashkaro%%' OR LOWER(bm.brand_name) LIKE '%%cash karo%%' THEN 'CashKaro'
                    WHEN LOWER(bm.brand_name) LIKE '%%desidime%%' OR LOWER(bm.brand_name) LIKE '%%desi dime%%' THEN 'DesiDime'
                END AS brand,
                SUM(1.0 / GREATEST(bm.rank_position, 1)) AS weighted_score
            FROM brand_mentions bm
            JOIN execution_logs el ON el.id = bm.log_id
            WHERE el.captured_at >= CURRENT_DATE - INTERVAL '30 days'
              AND (LOWER(bm.brand_name) LIKE '%%grabon%%' OR LOWER(bm.brand_name) LIKE '%%grab on%%'
                   OR LOWER(bm.brand_name) LIKE '%%coupondunia%%' OR LOWER(bm.brand_name) LIKE '%%coupon dunia%%'
                   OR LOWER(bm.brand_name) LIKE '%%cashkaro%%' OR LOWER(bm.brand_name) LIKE '%%cash karo%%'
                   OR LOWER(bm.brand_name) LIKE '%%desidime%%' OR LOWER(bm.brand_name) LIKE '%%desi dime%%')
            GROUP BY day, el.engine_name, brand
            ORDER BY day, el.engine_name, brand
        """).fetchall()

        # Build per-day, per-engine totals for SoV denominator
        day_engine_totals = {}
        for r in rows:
            key = (r["day"], r["engine_name"])
            day_engine_totals[key] = day_engine_totals.get(key, 0) + float(r["weighted_score"])

        result = []
        for r in rows:
            key = (r["day"], r["engine_name"])
            total = day_engine_totals[key]
            sov = float(r["weighted_score"]) / total * 100 if total > 0 else 0
            result.append({
                "day": r["day"],
                "engine": r["engine_name"],
                "brand": r["brand"],
                "weighted_score": round(float(r["weighted_score"]), 4),
                "sov_percent": round(sov, 2),
            })
        return result

    return {"data": await run_db(_query)}


@router.get("/analytics/rank-distribution")
async def analytics_rank_distribution():
    """How often GrabOn appears at rank #1, #2, #3, #4, #5+ across all engines."""
    def _query(conn):
        rows = conn.execute("""
            SELECT
                el.engine_name,
                CASE
                    WHEN bm.rank_position = 1 THEN '1'
                    WHEN bm.rank_position = 2 THEN '2'
                    WHEN bm.rank_position = 3 THEN '3'
                    WHEN bm.rank_position = 4 THEN '4'
                    ELSE '5+'
                END AS rank_bucket,
                COUNT(*) AS cnt
            FROM brand_mentions bm
            JOIN execution_logs el ON el.id = bm.log_id
            WHERE LOWER(bm.brand_name) LIKE '%%grabon%%'
            GROUP BY el.engine_name, rank_bucket
            ORDER BY el.engine_name, rank_bucket
        """).fetchall()

        # Also compute overall totals (across engines)
        overall = {}
        per_engine = []
        for r in rows:
            bucket = r["rank_bucket"]
            overall[bucket] = overall.get(bucket, 0) + r["cnt"]
            per_engine.append({
                "engine": r["engine_name"],
                "rank": bucket,
                "count": r["cnt"],
            })
        overall_list = [{"rank": k, "count": v} for k, v in sorted(overall.items())]
        return {"overall": overall_list, "per_engine": per_engine}

    return await run_db(_query)


@router.get("/analytics/heatmap")
async def analytics_heatmap():
    """Keyword x Engine heatmap: GrabOn's latest rank per canonical prompt per engine."""
    def _query(conn):
        rows = conn.execute("""
            WITH latest_logs AS (
                SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                    el.prompt_id, el.engine_name, el.id AS log_id
                FROM execution_logs el
                JOIN prompts p ON p.id = el.prompt_id
                WHERE p.is_canonical IS NOT FALSE
                ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
            )
            SELECT
                p.text AS keyword,
                ll.engine_name AS engine,
                bm.rank_position AS rank,
                bm.sentiment
            FROM latest_logs ll
            JOIN prompts p ON p.id = ll.prompt_id
            LEFT JOIN brand_mentions bm
                ON bm.log_id = ll.log_id
                AND LOWER(bm.brand_name) LIKE '%%grabon%%'
            ORDER BY p.text, ll.engine_name
        """).fetchall()
        return [dict(r) for r in rows]

    return {"data": await run_db(_query)}


@router.get("/analytics/competitors")
async def analytics_competitors():
    """Per-engine mention counts, avg rank, and citation counts for key brands."""
    def _query(conn):
        rows = conn.execute("""
            SELECT
                el.engine_name,
                CASE
                    WHEN LOWER(bm.brand_name) LIKE '%%grabon%%' OR LOWER(bm.brand_name) LIKE '%%grab on%%' THEN 'GrabOn'
                    WHEN LOWER(bm.brand_name) LIKE '%%coupondunia%%' OR LOWER(bm.brand_name) LIKE '%%coupon dunia%%' THEN 'CouponDunia'
                    WHEN LOWER(bm.brand_name) LIKE '%%cashkaro%%' OR LOWER(bm.brand_name) LIKE '%%cash karo%%' THEN 'CashKaro'
                    WHEN LOWER(bm.brand_name) LIKE '%%desidime%%' OR LOWER(bm.brand_name) LIKE '%%desi dime%%' THEN 'DesiDime'
                END AS brand,
                COUNT(*) AS mention_count,
                ROUND(AVG(bm.rank_position)::numeric, 2) AS avg_rank,
                COUNT(*) FILTER (
                    WHERE bm.cited_url IS NOT NULL AND bm.cited_url <> ''
                ) AS citation_count
            FROM brand_mentions bm
            JOIN execution_logs el ON el.id = bm.log_id
            WHERE (LOWER(bm.brand_name) LIKE '%%grabon%%' OR LOWER(bm.brand_name) LIKE '%%grab on%%'
                   OR LOWER(bm.brand_name) LIKE '%%coupondunia%%' OR LOWER(bm.brand_name) LIKE '%%coupon dunia%%'
                   OR LOWER(bm.brand_name) LIKE '%%cashkaro%%' OR LOWER(bm.brand_name) LIKE '%%cash karo%%'
                   OR LOWER(bm.brand_name) LIKE '%%desidime%%' OR LOWER(bm.brand_name) LIKE '%%desi dime%%')
            GROUP BY el.engine_name, brand
            ORDER BY el.engine_name, mention_count DESC
        """).fetchall()
        return [
            {
                "engine": r["engine_name"],
                "brand": r["brand"],
                "mention_count": r["mention_count"],
                "avg_rank": float(r["avg_rank"]) if r["avg_rank"] else None,
                "citation_count": r["citation_count"],
            }
            for r in rows
        ]

    return {"data": await run_db(_query)}


@router.get("/analytics/keyword-gaps")
async def analytics_keyword_gaps():
    """Keyword gap analysis grouped by merchant_category."""
    def _query(conn):
        # Keywords where GrabOn ranks #1
        strongest = conn.execute("""
            WITH latest AS (
                SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                    el.prompt_id, el.engine_name, el.id AS log_id
                FROM execution_logs el
                ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
            )
            SELECT DISTINCT p.text AS keyword, p.merchant_category
            FROM latest l
            JOIN brand_mentions bm ON bm.log_id = l.log_id
            JOIN prompts p ON p.id = l.prompt_id
            WHERE LOWER(bm.brand_name) LIKE '%%grabon%%' AND bm.rank_position = 1
        """).fetchall()

        # Keywords where GrabOn is mentioned but never #1
        opportunity = conn.execute("""
            WITH latest AS (
                SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                    el.prompt_id, el.engine_name, el.id AS log_id
                FROM execution_logs el
                ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
            ),
            grabon_mentions AS (
                SELECT l.prompt_id, bm.rank_position
                FROM latest l
                JOIN brand_mentions bm ON bm.log_id = l.log_id
                WHERE LOWER(bm.brand_name) LIKE '%%grabon%%'
            )
            SELECT DISTINCT p.text AS keyword, p.merchant_category
            FROM grabon_mentions gm
            JOIN prompts p ON p.id = gm.prompt_id
            WHERE gm.prompt_id NOT IN (
                SELECT prompt_id FROM grabon_mentions WHERE rank_position = 1
            )
        """).fetchall()

        # Keywords that have been run but GrabOn is never mentioned
        gaps = conn.execute("""
            WITH run_prompts AS (
                SELECT DISTINCT el.prompt_id
                FROM execution_logs el
            ),
            grabon_prompts AS (
                SELECT DISTINCT el.prompt_id
                FROM execution_logs el
                JOIN brand_mentions bm ON bm.log_id = el.id
                WHERE LOWER(bm.brand_name) LIKE '%%grabon%%'
            )
            SELECT p.text AS keyword, p.merchant_category
            FROM run_prompts rp
            JOIN prompts p ON p.id = rp.prompt_id
            WHERE rp.prompt_id NOT IN (SELECT prompt_id FROM grabon_prompts)
        """).fetchall()

        def _group(rows):
            grouped = {}
            for r in rows:
                cat = r["merchant_category"] or "Uncategorized"
                grouped.setdefault(cat, []).append(r["keyword"])
            return grouped

        return {
            "strongest": _group(strongest),
            "opportunity": _group(opportunity),
            "gaps": _group(gaps),
            "counts": {
                "strongest": len(strongest),
                "opportunity": len(opportunity),
                "gaps": len(gaps),
            },
        }

    return await run_db(_query)


@router.get("/analytics/sentiment")
async def analytics_sentiment():
    """Sentiment breakdown for GrabOn mentions: by merchant_category and by engine."""
    def _query(conn):
        by_category = conn.execute("""
            SELECT
                p.merchant_category,
                bm.sentiment,
                COUNT(*) AS cnt
            FROM brand_mentions bm
            JOIN execution_logs el ON el.id = bm.log_id
            JOIN prompts p ON p.id = el.prompt_id
            WHERE LOWER(bm.brand_name) LIKE '%%grabon%%'
              AND bm.sentiment IS NOT NULL
            GROUP BY p.merchant_category, bm.sentiment
            ORDER BY p.merchant_category, bm.sentiment
        """).fetchall()

        by_engine = conn.execute("""
            SELECT
                el.engine_name,
                bm.sentiment,
                COUNT(*) AS cnt
            FROM brand_mentions bm
            JOIN execution_logs el ON el.id = bm.log_id
            WHERE LOWER(bm.brand_name) LIKE '%%grabon%%'
              AND bm.sentiment IS NOT NULL
            GROUP BY el.engine_name, bm.sentiment
            ORDER BY el.engine_name, bm.sentiment
        """).fetchall()

        return {
            "by_category": [dict(r) for r in by_category],
            "by_engine": [dict(r) for r in by_engine],
        }

    return await run_db(_query)


@router.get("/analytics/citations")
async def analytics_citations():
    """Citation tracking: per engine, GrabOn mentions with/without grabon URLs + URL list."""
    def _query(conn):
        per_engine = conn.execute("""
            SELECT
                el.engine_name,
                COUNT(*) AS total_mentions,
                COUNT(*) FILTER (
                    WHERE bm.cited_url IS NOT NULL AND LOWER(bm.cited_url) LIKE '%%grabon%%'
                ) AS grabon_citations,
                COUNT(*) FILTER (
                    WHERE bm.cited_url IS NULL OR bm.cited_url = ''
                    OR LOWER(bm.cited_url) NOT LIKE '%%grabon%%'
                ) AS non_grabon_citations
            FROM brand_mentions bm
            JOIN execution_logs el ON el.id = bm.log_id
            WHERE LOWER(bm.brand_name) LIKE '%%grabon%%'
            GROUP BY el.engine_name
            ORDER BY el.engine_name
        """).fetchall()

        urls = conn.execute("""
            SELECT DISTINCT
                bm.cited_url,
                el.engine_name,
                COUNT(*) AS times_cited
            FROM brand_mentions bm
            JOIN execution_logs el ON el.id = bm.log_id
            WHERE LOWER(bm.brand_name) LIKE '%%grabon%%'
              AND bm.cited_url IS NOT NULL
              AND bm.cited_url <> ''
            GROUP BY bm.cited_url, el.engine_name
            ORDER BY times_cited DESC
            LIMIT 100
        """).fetchall()

        return {
            "per_engine": [dict(r) for r in per_engine],
            "cited_urls": [dict(r) for r in urls],
        }

    return await run_db(_query)


@router.get("/analytics/insights")
async def analytics_insights():
    """Auto-generated insights comparing last 7 days vs previous 7 days."""
    def _query(conn):
        insights = []

        # SoV per engine: last 7 days vs previous 7 days
        sov_data = conn.execute("""
            WITH period_data AS (
                SELECT
                    el.engine_name,
                    CASE
                        WHEN el.captured_at >= CURRENT_DATE - INTERVAL '7 days' THEN 'current'
                        ELSE 'previous'
                    END AS period,
                    bm.brand_name,
                    SUM(1.0 / GREATEST(bm.rank_position, 1)) AS weighted_score
                FROM brand_mentions bm
                JOIN execution_logs el ON el.id = bm.log_id
                WHERE el.captured_at >= CURRENT_DATE - INTERVAL '14 days'
                  AND (LOWER(bm.brand_name) LIKE '%%grabon%%' OR LOWER(bm.brand_name) LIKE '%%grab on%%'
                       OR LOWER(bm.brand_name) LIKE '%%coupondunia%%' OR LOWER(bm.brand_name) LIKE '%%coupon dunia%%'
                       OR LOWER(bm.brand_name) LIKE '%%cashkaro%%' OR LOWER(bm.brand_name) LIKE '%%cash karo%%'
                       OR LOWER(bm.brand_name) LIKE '%%desidime%%' OR LOWER(bm.brand_name) LIKE '%%desi dime%%')
                GROUP BY el.engine_name, period, bm.brand_name
            ),
            totals AS (
                SELECT engine_name, period,
                    SUM(weighted_score) AS total_score
                FROM period_data
                GROUP BY engine_name, period
            )
            SELECT
                pd.engine_name, pd.period,
                CASE WHEN t.total_score > 0
                    THEN (pd.weighted_score / t.total_score * 100)
                    ELSE 0
                END AS sov_pct
            FROM period_data pd
            JOIN totals t ON t.engine_name = pd.engine_name AND t.period = pd.period
            WHERE LOWER(pd.brand_name) LIKE '%%grabon%%'
            ORDER BY pd.engine_name, pd.period
        """).fetchall()

        # Build SoV comparison insights
        sov_by_engine = {}
        for r in sov_data:
            engine = r["engine_name"]
            sov_by_engine.setdefault(engine, {})
            sov_by_engine[engine][r["period"]] = float(r["sov_pct"])

        for engine, periods in sov_by_engine.items():
            current = periods.get("current", 0)
            previous = periods.get("previous", 0)
            if previous > 0:
                change = ((current - previous) / previous) * 100
                if abs(change) >= 5:
                    direction = "increased" if change > 0 else "decreased"
                    insights.append({
                        "type": "positive" if change > 0 else "negative",
                        "metric": "sov",
                        "engine": engine,
                        "detail": f"GrabOn SoV {direction} {abs(change):.0f}% on {engine} (from {previous:.1f}% to {current:.1f}%)",
                    })
            elif current > 0:
                insights.append({
                    "type": "positive",
                    "metric": "sov",
                    "engine": engine,
                    "detail": f"GrabOn newly appearing on {engine} with {current:.1f}% SoV",
                })

        # Rank changes per keyword
        rank_changes = conn.execute("""
            WITH ranked AS (
                SELECT
                    p.text AS keyword,
                    el.engine_name,
                    bm.rank_position,
                    CASE
                        WHEN el.captured_at >= CURRENT_DATE - INTERVAL '7 days' THEN 'current'
                        ELSE 'previous'
                    END AS period
                FROM brand_mentions bm
                JOIN execution_logs el ON el.id = bm.log_id
                JOIN prompts p ON p.id = el.prompt_id
                WHERE el.captured_at >= CURRENT_DATE - INTERVAL '14 days'
                  AND LOWER(bm.brand_name) LIKE '%%grabon%%'
            )
            SELECT keyword, engine_name, period,
                   ROUND(AVG(rank_position)::numeric, 1) AS avg_rank
            FROM ranked
            GROUP BY keyword, engine_name, period
            ORDER BY keyword, engine_name, period
        """).fetchall()

        rank_map = {}
        for r in rank_changes:
            key = (r["keyword"], r["engine_name"])
            rank_map.setdefault(key, {})
            rank_map[key][r["period"]] = float(r["avg_rank"])

        for (keyword, engine), periods in rank_map.items():
            curr = periods.get("current")
            prev = periods.get("previous")
            if curr and prev:
                if prev <= 1 and curr > 1:
                    insights.append({
                        "type": "negative",
                        "metric": "rank",
                        "engine": engine,
                        "detail": f"Lost rank #1 on '{keyword}' in {engine} (now #{curr:.0f})",
                    })
                elif curr <= 1 and prev > 1:
                    insights.append({
                        "type": "positive",
                        "metric": "rank",
                        "engine": engine,
                        "detail": f"Gained rank #1 on '{keyword}' in {engine} (was #{prev:.0f})",
                    })

        # Mention count trend
        mention_trend = conn.execute("""
            SELECT
                CASE
                    WHEN el.captured_at >= CURRENT_DATE - INTERVAL '7 days' THEN 'current'
                    ELSE 'previous'
                END AS period,
                COUNT(*) AS mentions
            FROM brand_mentions bm
            JOIN execution_logs el ON el.id = bm.log_id
            WHERE el.captured_at >= CURRENT_DATE - INTERVAL '14 days'
              AND LOWER(bm.brand_name) LIKE '%%grabon%%'
            GROUP BY period
        """).fetchall()

        mention_map = {r["period"]: r["mentions"] for r in mention_trend}
        curr_m = mention_map.get("current", 0)
        prev_m = mention_map.get("previous", 0)
        if prev_m > 0:
            m_change = ((curr_m - prev_m) / prev_m) * 100
            if abs(m_change) >= 10:
                direction = "up" if m_change > 0 else "down"
                insights.append({
                    "type": "positive" if m_change > 0 else "negative",
                    "metric": "mentions",
                    "engine": "all",
                    "detail": f"Total GrabOn mentions {direction} {abs(m_change):.0f}% week-over-week ({prev_m} -> {curr_m})",
                })

        # Sort: negative first, then positive, limit to top 5
        priority = {"negative": 0, "neutral": 1, "positive": 2}
        insights.sort(key=lambda x: priority.get(x["type"], 1))
        return insights[:5]

    return {"insights": await run_db(_query)}


@router.get("/engine-cooldowns")
async def engine_cooldowns():
    from app.agent.pipeline import get_engine_cooldowns
    return {"cooldowns": get_engine_cooldowns()}


@router.get("/engine-health")
async def engine_health():
    def _health(conn):
        rows = conn.execute("""
            SELECT
                el.engine_name,
                COUNT(*) AS total_24h,
                COUNT(*) FILTER (WHERE el.raw_response_text LIKE 'Error:%%') AS errors_24h,
                COUNT(*) FILTER (WHERE el.raw_response_text NOT LIKE 'Error:%%') AS success_24h,
                MAX(el.captured_at) FILTER (WHERE el.raw_response_text NOT LIKE 'Error:%%') AS last_success,
                MAX(el.captured_at) FILTER (WHERE el.raw_response_text LIKE 'Error:%%') AS last_error,
                COUNT(*) FILTER (WHERE el.captured_at >= NOW() - INTERVAL '4 hours') AS total_4h,
                COUNT(*) FILTER (WHERE el.captured_at >= NOW() - INTERVAL '4 hours'
                                   AND el.raw_response_text LIKE 'Error:%%') AS errors_4h
            FROM execution_logs el
            WHERE el.captured_at >= NOW() - INTERVAL '24 hours'
            GROUP BY el.engine_name
        """).fetchall()

        streak_rows = conn.execute("""
            SELECT engine_name,
                   ARRAY_AGG(
                       CASE WHEN raw_response_text LIKE 'Error:%%' THEN 'E' ELSE 'S' END
                       ORDER BY captured_at DESC
                   ) AS results
            FROM (
                SELECT engine_name, raw_response_text, captured_at,
                       ROW_NUMBER() OVER (PARTITION BY engine_name ORDER BY captured_at DESC) AS rn
                FROM execution_logs
                WHERE captured_at >= NOW() - INTERVAL '24 hours'
            ) sub
            WHERE rn <= 10
            GROUP BY engine_name
        """).fetchall()

        recent_errors = conn.execute("""
            SELECT el.engine_name,
                   SUBSTRING(el.raw_response_text FROM 8 FOR 200) AS error_msg,
                   el.captured_at::text AS captured_at
            FROM execution_logs el
            WHERE el.raw_response_text LIKE 'Error:%%'
              AND el.captured_at >= NOW() - INTERVAL '24 hours'
            ORDER BY el.captured_at DESC
            LIMIT 20
        """).fetchall()

        return [dict(r) for r in rows], {s["engine_name"]: s["results"] for s in streak_rows}, [dict(r) for r in recent_errors]

    stats, streaks, recent_errors = await run_db(_health)

    engines = {}
    for r in stats:
        eng = r["engine_name"]
        total_4h = r["total_4h"]
        errors_4h = r["errors_4h"]
        rate_4h = round((errors_4h / total_4h * 100) if total_4h else 0, 1)

        streak = streaks.get(eng, [])
        recent_success_streak = 0
        for s in streak:
            if s == 'S':
                recent_success_streak += 1
            else:
                break

        if total_4h >= 3 and recent_success_streak >= 3:
            health = "healthy"
        elif rate_4h >= 80:
            health = "blocked"
        elif rate_4h >= 50:
            health = "degraded"
        elif total_4h == 0 and r["total_24h"] == 0:
            health = "inactive"
        else:
            health = "healthy"

        engines[eng] = {
            "total_24h": r["total_24h"],
            "errors_24h": r["errors_24h"],
            "success_24h": r["success_24h"],
            "error_rate": rate_4h,
            "health": health,
            "last_success": str(r["last_success"]) if r["last_success"] else None,
            "last_error": str(r["last_error"]) if r["last_error"] else None,
            "streak": recent_success_streak,
        }

    return {"engines": engines, "recent_errors": recent_errors}


@router.get("/log-response/{log_id}")
async def get_log_response(log_id: str):
    def _fetch(conn):
        row = conn.execute(
            """SELECT el.raw_response_text, el.engine_name, el.captured_at::text as captured_at,
                      p.text as prompt_text
               FROM execution_logs el
               JOIN prompts p ON p.id = el.prompt_id
               WHERE el.id = %s::uuid""",
            (log_id,),
        ).fetchone()
        if not row:
            return None
        return dict(row)
    result = await run_db(_fetch)
    if not result:
        return {"error": "Log not found"}
    return result
