import asyncio
import csv
import io
import json
import logging
import time
from pathlib import Path
from fastapi import APIRouter, File, Form, Query, Response, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from sse_starlette.sse import EventSourceResponse
from app.database import run_db
from app.agent.pipeline import run_full_sweep
from app.config import get_settings
from app.events import subscribe, unsubscribe, get_recent
from app.routes.api_helpers import valid_uuid as _valid_uuid, classify_error as _classify_error

router = APIRouter(prefix="/api")
log = logging.getLogger("geo.api")

_boot_time = time.time()


@router.get("/health")
async def health_check():
    """Lightweight liveness probe — DB ping + uptime."""
    try:
        def _ping(conn):
            return conn.execute("SELECT 1 AS ok").fetchone()
        row = await run_db(_ping)
        db_ok = row and row.get("ok") == 1
    except Exception:
        db_ok = False
    import time
    uptime = int(time.time() - _boot_time)
    if not db_ok:
        return JSONResponse({"status": "unhealthy", "db": False, "uptime_s": uptime}, status_code=503)
    return {"status": "healthy", "db": True, "uptime_s": uptime}



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


@router.get("/account-pool")
async def account_pool_status():
    from app.agent.account_pool import get_pool_status, init_all_pools
    await init_all_pools()
    return get_pool_status()


@router.post("/account-pool/{engine_name}/save/{slot_index}")
async def save_account_slot(engine_name: str, slot_index: int, data: dict):
    from app.agent.account_pool import save_to_slot
    state_json = data.get("state_json") or json.dumps(data.get("state", {}))
    if not state_json or state_json == "{}":
        return JSONResponse({"detail": "state_json or state required"}, status_code=400)
    await save_to_slot(engine_name, slot_index, state_json)
    return {"status": "saved", "engine": engine_name, "slot": slot_index}


@router.post("/account-pool/{engine_name}/clear-cooldown")
async def clear_account_cooldown(engine_name: str, db_key: str = Query(None)):
    from app.agent.account_pool import clear_cooldown
    clear_cooldown(engine_name, db_key)
    return {"status": "cleared", "engine": engine_name}


@router.get("/engine-accounts")
async def engine_accounts():
    """Merged view: pool status + cookie health + labels for all engines."""
    from app.agent.account_pool import get_full_account_status, init_all_pools
    await init_all_pools()
    return await get_full_account_status()


@router.post("/engine-accounts/{engine_name}/relogin")
async def trigger_engine_relogin(engine_name: str):
    """Trigger auto re-login for an engine (refresh first, then full re-auth)."""
    from app.routes.auth import try_reactive_refresh
    success = await try_reactive_refresh(engine_name)
    if success:
        from app.agent.account_pool import clear_cooldown
        clear_cooldown(engine_name)
        return {"status": "success", "engine": engine_name, "message": "Session refreshed"}
    return JSONResponse(
        {"status": "failed", "engine": engine_name, "message": "Re-login failed. Try manual login."},
        status_code=200,
    )


@router.post("/engine-accounts/{engine_name}/force-relogin")
async def force_engine_relogin(engine_name: str):
    """Force full auto re-login (skip refresh, go straight to OAuth)."""
    from app.routes.auth import try_auto_relogin
    success = await try_auto_relogin(engine_name)
    if success:
        from app.agent.account_pool import clear_cooldown
        clear_cooldown(engine_name)
        return {"status": "success", "engine": engine_name, "message": "Full re-login successful"}
    return JSONResponse(
        {"status": "failed", "engine": engine_name, "message": "Auto re-login failed. Check credentials or use manual login."},
        status_code=200,
    )


@router.get("/cookie-health")
async def cookie_health():
    """Detailed cookie health for all engines: counts, domains, expiry, staleness."""
    import time as _time
    engines = {
        "google": {"db_key": "google_cookies", "domain_filter": "google", "label": "Google"},
        "chatgpt": {"db_key": "chatgpt_cookies", "domain_filter": "openai|chatgpt|google", "label": "ChatGPT"},
        "claude": {"db_key": "claude_cookies", "domain_filter": "claude", "label": "Claude"},
        "perplexity": {"db_key": "perplexity_cookies", "domain_filter": "perplexity", "label": "Perplexity"},
        "gemini": {"db_key": "gemini_cookies", "domain_filter": "google", "label": "Gemini"},
    }

    def _fetch_all(conn):
        keys = [e["db_key"] for e in engines.values()]
        placeholders = ",".join(["%s"] * len(keys))
        rows = conn.execute(
            f"SELECT key, value, updated_at::text as updated_at FROM app_settings WHERE key IN ({placeholders})",
            tuple(keys),
        ).fetchall()
        return {r["key"]: {"value": r["value"], "updated_at": r["updated_at"]} for r in rows}

    db_data = await run_db(_fetch_all)
    now = _time.time()
    result = {}

    for eng, cfg in engines.items():
        entry = db_data.get(cfg["db_key"])
        if not entry:
            result[eng] = {"status": "missing", "label": cfg["label"], "cookie_count": 0, "updated_at": None}
            continue
        state = entry["value"]
        cookie_list = state.get("cookies", []) if isinstance(state, dict) else []
        filters = cfg["domain_filter"].split("|")
        matched = [c for c in cookie_list if any(f in c.get("domain", "") for f in filters)]
        domains = {}
        expired_count = 0
        for c in matched:
            d = c.get("domain", "unknown")
            domains[d] = domains.get(d, 0) + 1
            exp = c.get("expires", -1)
            if exp > 0 and exp < now:
                expired_count += 1

        session_keys = {
            "google": ["SID", "__Secure-1PSID", "HSID"],
            "chatgpt": ["__Secure-next-auth.session-token", "_account", "SID", "__Secure-1PSID"],
            "claude": ["sessionKey", "lastActiveOrg"],
            "perplexity": ["next-auth.session-token", "__Secure-next-auth.session-token"],
            "gemini": ["SID", "__Secure-1PSID"],
        }
        critical = session_keys.get(eng, [])
        has_critical = any(c.get("name") in critical for c in matched)
        if not has_critical and eng == "chatgpt":
            has_critical = any("chatgpt" in c.get("domain", "") for c in matched)

        too_many_expired = len(matched) > 0 and expired_count > len(matched) * 0.5
        result[eng] = {
            "status": "healthy" if has_critical and not too_many_expired else ("stale" if not has_critical else "degraded"),
            "label": cfg["label"],
            "cookie_count": len(matched),
            "total_cookies": len(cookie_list),
            "expired_count": expired_count,
            "has_session_cookie": has_critical,
            "domains": domains,
            "updated_at": entry["updated_at"],
        }

    return result


@router.get("/scheduler-status")
async def scheduler_status():
    """Return next/last run times for cookie refresh and health check jobs."""
    from app.scheduler import _scheduler
    if not _scheduler:
        return {}
    result = {}
    for job in _scheduler.get_jobs():
        jid = job.id
        info = {
            "id": jid,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        }
        trigger = job.trigger
        if hasattr(trigger, "interval"):
            info["interval_seconds"] = int(trigger.interval.total_seconds())
        result[jid] = info
    return result


@router.get("/dedup-cache")
async def dedup_cache_status():
    from app.agent.dedup_cache import get_cache_stats
    return get_cache_stats()


@router.post("/dedup-cache/clear")
async def clear_dedup_cache():
    from app.agent.dedup_cache import clear_cache
    clear_cache()
    return {"status": "cleared"}


@router.get("/ip-stats")
async def ip_usage_stats():
    """Return per-IP usage stats for Google scraping rate distribution."""
    from app.agent.scraper import get_ip_stats
    return get_ip_stats()


@router.post("/run-parallel")
async def trigger_parallel_run(data: dict = None):
    """Trigger a parallel engine run on a batch of keywords."""
    from app.agent.pipeline import run_parallel_engines
    from app.agent.scraper import VISIBLE_ENGINES
    data = data or {}
    batch_size = data.get("batch_size", 50)
    engines = data.get("engines") or list(VISIBLE_ENGINES)

    rows = await run_db(lambda conn: conn.execute(
        """SELECT id, text, merchant_category, intent_type
           FROM prompts
           ORDER BY last_run_at ASC NULLS FIRST
           LIMIT %s""",
        (batch_size,),
    ).fetchall())

    if not rows:
        return {"status": "no_prompts"}

    from app.models import PromptItem
    prompts = [
        PromptItem(id=str(r["id"]), text=r["text"],
                   merchant_category=r["merchant_category"],
                   intent_type=r["intent_type"])
        for r in rows
    ]
    asyncio.create_task(run_parallel_engines(
        prompts=prompts,
        engines=engines,
        concurrency_per_engine=data.get("concurrency", 2),
        skip_fresh=data.get("skip_fresh", True),
        use_dedup=data.get("use_dedup", True),
    ))
    return {"status": "started", "prompts": len(prompts), "engines": engines}


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


# ── Keyword CRUD ──────────────────────────────────────────────────────

@router.get("/keywords")
async def list_keywords(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
    tier: int | None = Query(None),
    category: str | None = Query(None),
    search: str | None = Query(None),
):
    def _list(conn):
        where_clauses = []
        params = []
        if tier:
            where_clauses.append("tier = %s")
            params.append(tier)
        if category:
            where_clauses.append("merchant_category = %s")
            params.append(category)
        if search:
            where_clauses.append("LOWER(text) LIKE %s")
            params.append(f"%{search.lower()}%")
        where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        total = conn.execute(f"SELECT COUNT(*) as cnt FROM prompts {where}", params).fetchone()["cnt"]
        rows = conn.execute(
            f"""SELECT id, text, tier, merchant_category, intent_type,
                       keyword_group, is_canonical, last_run_at, created_at
                FROM prompts {where}
                ORDER BY tier, text
                LIMIT %s OFFSET %s""",
            params + [per_page, (page - 1) * per_page],
        ).fetchall()
        return {"keywords": [dict(r) for r in rows], "total": total, "page": page, "per_page": per_page}
    return await run_db(_list)


@router.post("/keywords")
async def create_keyword(data: dict):
    text = (data.get("text") or "").strip()
    if not text:
        return JSONResponse({"detail": "Keyword text is required"}, status_code=400)
    tier = int(data.get("tier", 2))
    category = data.get("category", "General")
    intent = data.get("intent", "Transactional")
    group = data.get("group") or None

    def _create(conn):
        row = conn.execute(
            """INSERT INTO prompts (text, merchant_category, intent_type, tier, keyword_group)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (text) DO NOTHING
               RETURNING id""",
            (text, category, intent, tier, group),
        ).fetchone()
        conn.commit()
        return row
    result = await run_db(_create)
    if not result:
        return JSONResponse({"detail": "Keyword already exists"}, status_code=409)
    log.info(f"Created keyword: {text[:50]}")
    return {"id": str(result["id"]), "status": "created"}


@router.put("/keyword-bulk")
async def bulk_update_keywords(data: dict):
    ids = data.get("ids", [])
    if not ids:
        return JSONResponse({"detail": "No IDs provided"}, status_code=400)
    tier = data.get("tier")
    if tier is None:
        return JSONResponse({"detail": "No tier provided"}, status_code=400)
    def _bulk(conn):
        conn.execute(
            "UPDATE prompts SET tier = %s WHERE id = ANY(%s::uuid[])",
            (int(tier), ids),
        )
        conn.commit()
    await run_db(_bulk)
    return {"status": "updated", "count": len(ids)}


@router.delete("/keyword-bulk")
async def bulk_delete_keywords(data: dict):
    ids = data.get("ids", [])
    if not ids:
        return JSONResponse({"detail": "No IDs provided"}, status_code=400)
    def _bulk(conn):
        conn.execute("DELETE FROM prompts WHERE id = ANY(%s::uuid[])", (ids,))
        conn.commit()
    await run_db(_bulk)
    return {"status": "deleted", "count": len(ids)}


@router.post("/keyword-upload")
async def upload_keywords(
    file: UploadFile = File(...),
    default_tier: int = Form(2),
    default_category: str = Form("General"),
):
    content = await file.read()
    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    keywords = []
    if ext == "json":
        try:
            data = json.loads(content.decode("utf-8"))
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, str):
                        keywords.append({"text": item.strip()})
                    elif isinstance(item, dict):
                        keywords.append({
                            "text": (item.get("text") or item.get("keyword") or "").strip(),
                            "tier": item.get("tier"),
                            "category": item.get("category") or item.get("merchant_category"),
                            "intent": item.get("intent") or item.get("intent_type"),
                            "group": item.get("group") or item.get("keyword_group"),
                        })
        except Exception as e:
            return JSONResponse({"detail": f"Invalid JSON: {e}"}, status_code=400)

    elif ext == "csv":
        try:
            text = content.decode("utf-8")
            reader = csv.DictReader(io.StringIO(text))
            for row in reader:
                text_val = (row.get("text") or row.get("keyword") or row.get("Keyword") or row.get("query") or "").strip()
                if text_val:
                    keywords.append({
                        "text": text_val,
                        "tier": row.get("tier"),
                        "category": row.get("category") or row.get("merchant_category"),
                        "intent": row.get("intent") or row.get("intent_type"),
                        "group": row.get("group") or row.get("keyword_group"),
                    })
        except Exception as e:
            return JSONResponse({"detail": f"Invalid CSV: {e}"}, status_code=400)

    elif ext == "xlsx":
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
            ws = wb.active
            headers = [str(c.value or "").strip().lower() for c in next(ws.iter_rows(min_row=1, max_row=1))]
            text_col = next((i for i, h in enumerate(headers) if h in ("text", "keyword", "query", "keywords")), 0)
            tier_col = next((i for i, h in enumerate(headers) if h == "tier"), None)
            cat_col = next((i for i, h in enumerate(headers) if h in ("category", "merchant_category")), None)
            intent_col = next((i for i, h in enumerate(headers) if h in ("intent", "intent_type")), None)
            group_col = next((i for i, h in enumerate(headers) if h in ("group", "keyword_group")), None)

            for row in ws.iter_rows(min_row=2, values_only=True):
                if not row or not row[text_col]:
                    continue
                kw = {"text": str(row[text_col]).strip()}
                if tier_col is not None and row[tier_col]:
                    try: kw["tier"] = int(row[tier_col])
                    except (ValueError, TypeError): pass
                if cat_col is not None and row[cat_col]:
                    kw["category"] = str(row[cat_col]).strip()
                if intent_col is not None and row[intent_col]:
                    kw["intent"] = str(row[intent_col]).strip()
                if group_col is not None and row[group_col]:
                    kw["group"] = str(row[group_col]).strip()
                keywords.append(kw)
            wb.close()
        except ImportError:
            return JSONResponse({"detail": "openpyxl not installed - cannot parse Excel"}, status_code=400)
        except Exception as e:
            return JSONResponse({"detail": f"Invalid Excel: {e}"}, status_code=400)
    else:
        return JSONResponse({"detail": f"Unsupported file type: .{ext}"}, status_code=400)

    keywords = [k for k in keywords if k.get("text")]
    if not keywords:
        return JSONResponse({"detail": "No keywords found in file"}, status_code=400)

    def _import(conn):
        imported = 0
        skipped = 0
        for kw in keywords:
            row = conn.execute(
                """INSERT INTO prompts (text, merchant_category, intent_type, tier, keyword_group)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (text) DO NOTHING
                   RETURNING id""",
                (kw["text"],
                 kw.get("category") or default_category,
                 kw.get("intent") or "Transactional",
                 kw.get("tier") or default_tier,
                 kw.get("group")),
            ).fetchone()
            if row:
                imported += 1
            else:
                skipped += 1
        conn.commit()
        return {"imported": imported, "skipped": skipped}
    result = await run_db(_import)
    log.info(f"Keyword upload: {result['imported']} imported, {result['skipped']} skipped from {filename}")
    return result


@router.get("/keyword-export")
async def export_keywords(format: str = Query("csv")):
    def _export(conn):
        rows = conn.execute(
            """SELECT text, tier, merchant_category, intent_type, keyword_group, is_canonical
               FROM prompts ORDER BY tier, text"""
        ).fetchall()
        return [dict(r) for r in rows]
    rows = await run_db(_export)

    if format == "json":
        return JSONResponse(rows)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["text", "tier", "merchant_category", "intent_type", "keyword_group", "is_canonical"])
    writer.writeheader()
    writer.writerows(rows)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=keywords.csv"},
    )


@router.get("/keywords/{keyword_id}")
async def get_keyword(keyword_id: str):
    if not _valid_uuid(keyword_id):
        return JSONResponse({"detail": "Invalid ID"}, status_code=400)
    def _get(conn):
        row = conn.execute("SELECT * FROM prompts WHERE id = %s::uuid", (keyword_id,)).fetchone()
        return dict(row) if row else None
    result = await run_db(_get)
    if not result:
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return result


@router.put("/keywords/{keyword_id}")
async def update_keyword(keyword_id: str, data: dict):
    if not _valid_uuid(keyword_id):
        return JSONResponse({"detail": "Invalid ID"}, status_code=400)
    text = (data.get("text") or "").strip()
    if not text:
        return JSONResponse({"detail": "Keyword text is required"}, status_code=400)

    def _update(conn):
        conn.execute(
            """UPDATE prompts
               SET text = %s, tier = %s, merchant_category = %s,
                   intent_type = %s, keyword_group = %s, is_canonical = %s
               WHERE id = %s::uuid""",
            (text, int(data.get("tier", 2)), data.get("category", "General"),
             data.get("intent", "Transactional"), data.get("group") or None,
             data.get("is_canonical", True), keyword_id),
        )
        conn.commit()
    await run_db(_update)
    log.info(f"Updated keyword {keyword_id}: {text[:50]}")
    return {"status": "updated"}


@router.delete("/keywords/{keyword_id}")
async def delete_keyword_by_id(keyword_id: str):
    if not _valid_uuid(keyword_id):
        return JSONResponse({"detail": "Invalid ID"}, status_code=400)
    def _del(conn):
        conn.execute("DELETE FROM prompts WHERE id = %s::uuid", (keyword_id,))
        conn.commit()
    await run_db(_del)
    log.info(f"Deleted keyword: {keyword_id}")
    return {"status": "deleted"}


@router.get("/dashboard-kpis")
async def dashboard_kpis():
    """Live KPI data for dashboard auto-refresh."""
    from app.agent.scraper import VISIBLE_ENGINES

    def _query(conn):
        grabon_agg = conn.execute("""
            SELECT engine_name,
                   COUNT(*) as mentions,
                   AVG(rank_position)::numeric(4,1) as avg_rank,
                   COUNT(CASE WHEN cited_url IS NOT NULL AND cited_url != '' THEN 1 END) as cited,
                   COUNT(CASE WHEN sentiment = 'Positive' THEN 1 END) as pos,
                   COUNT(CASE WHEN sentiment = 'Neutral' THEN 1 END) as neu,
                   COUNT(CASE WHEN sentiment = 'Negative' THEN 1 END) as neg
            FROM brand_mentions bm
            JOIN execution_logs el ON el.id = bm.log_id
            WHERE LOWER(bm.brand_name) LIKE '%%grabon%%'
              AND el.captured_at >= NOW() - INTERVAL '7 days'
            GROUP BY engine_name
        """).fetchall()

        total_mentions_agg = conn.execute("""
            SELECT engine_name, COUNT(*) as cnt
            FROM brand_mentions bm
            JOIN execution_logs el ON el.id = bm.log_id
            WHERE el.captured_at >= NOW() - INTERVAL '7 days'
            GROUP BY engine_name
        """).fetchall()

        total_grabon = sum(r["mentions"] for r in grabon_agg)
        citations = sum(r["cited"] for r in grabon_agg)
        pos = sum(r["pos"] for r in grabon_agg)
        neu = sum(r["neu"] for r in grabon_agg)
        neg = sum(r["neg"] for r in grabon_agg)
        organic = total_grabon - citations

        total_mentions_map = {r["engine_name"]: r["cnt"] for r in total_mentions_agg}
        grabon_weight = sum(r["mentions"] / max(float(r["avg_rank"] or 1), 1) for r in grabon_agg)
        competitor_weight = sum(
            max(total_mentions_map.get(r["engine_name"], 0) - r["mentions"], 0)
            for r in grabon_agg
        )
        total_weight = grabon_weight + competitor_weight
        sov = round((grabon_weight / total_weight * 100) if total_weight else 0, 1)
        citation_rate = round((citations / total_grabon * 100) if total_grabon else 0, 1)
        pos_rate = round((pos / total_grabon * 100) if total_grabon else 0, 1)
        footprint = round((organic / total_grabon * 100) if total_grabon else 0, 0)
        engines_with_mentions = len(set(r["engine_name"] for r in grabon_agg if r["mentions"] > 0))
        engine_coverage = round((engines_with_mentions / len(VISIBLE_ENGINES) * 100), 1)
        ai_visibility = round(
            sov * 0.4 + citation_rate * 0.3 + pos_rate * 0.2 + engine_coverage * 0.1
        ) if total_grabon else 0

        engine_stats = {}
        grabon_map = {r["engine_name"]: r for r in grabon_agg}
        for eng in VISIBLE_ENGINES:
            g = grabon_map.get(eng, {"mentions": 0, "cited": 0, "avg_rank": None})
            engine_stats[eng] = {
                "mentions": g["mentions"] if isinstance(g["mentions"], int) else 0,
                "cited": g["cited"] if "cited" in g else 0,
                "total_mentions": total_mentions_map.get(eng, 0),
            }

        return {
            "ai_visibility": ai_visibility,
            "sov": sov,
            "citation_rate": citation_rate,
            "citations": citations,
            "pos": pos, "neu": neu, "neg": neg,
            "pos_rate": pos_rate,
            "footprint": int(footprint),
            "total_grabon": total_grabon,
            "engine_stats": engine_stats,
        }
    result = await run_db(_query)
    return result


@router.get("/rankings-live")
async def rankings_live(
    tier: int = Query(0),
    category: str = Query("All"),
    intent: str = Query("All"),
    page: int = Query(1),
    page_size: int = Query(50),
    engines: str = Query(""),
    prompt_ids: str = Query(""),
):
    """Live rankings matrix data for auto-refresh."""
    from app.agent.scraper import VISIBLE_ENGINES
    active_engines = [e.strip() for e in engines.split(",") if e.strip()] if engines else list(VISIBLE_ENGINES)

    fixed_ids = [pid.strip() for pid in prompt_ids.split(",") if pid.strip() and _valid_uuid(pid.strip())] if prompt_ids else []

    def _query(conn):
        if fixed_ids:
            id_ph = ",".join(["%s"] * len(fixed_ids))
            prompts = conn.execute(
                f"SELECT id, text, tier, merchant_category, intent_type, last_run_at FROM prompts WHERE id IN ({id_ph})",
                fixed_ids,
            ).fetchall()
            total = len(prompts)
        else:
            engine_count = len(active_engines)
            engine_ph = ",".join(["%s"] * engine_count)

            coverage_cte = f"""full_cov AS (
                SELECT prompt_id
                FROM execution_logs
                WHERE engine_name IN ({engine_ph})
                GROUP BY prompt_id
                HAVING COUNT(DISTINCT engine_name) = {engine_count}
            )"""
            cte_params: list = list(active_engines)

            where_parts: list[str] = []
            filter_params: list = []
            if tier > 0:
                where_parts.append("p.tier = %s")
                filter_params.append(tier)
            if category != "All":
                where_parts.append("p.merchant_category = %s")
                filter_params.append(category)
            if intent != "All":
                where_parts.append("p.intent_type = %s")
                filter_params.append(intent)

            extra_where = (" AND " + " AND ".join(where_parts)) if where_parts else ""

            total = conn.execute(
                f"WITH {coverage_cte} SELECT COUNT(*) as cnt FROM prompts p JOIN full_cov fc ON fc.prompt_id = p.id WHERE TRUE{extra_where}",
                cte_params + filter_params,
            ).fetchone()["cnt"]

            offset = (page - 1) * page_size
            prompts = conn.execute(
                f"""WITH {coverage_cte}
                    SELECT p.id, p.text, p.tier, p.merchant_category, p.intent_type, p.last_run_at
                    FROM prompts p
                    JOIN full_cov fc ON fc.prompt_id = p.id
                    LEFT JOIN (
                        SELECT prompt_id, MAX(captured_at) as max_captured
                        FROM execution_logs GROUP BY prompt_id
                    ) la ON la.prompt_id = p.id
                    WHERE TRUE{extra_where}
                    ORDER BY la.max_captured DESC NULLS LAST
                    LIMIT %s OFFSET %s""",
                cte_params + filter_params + [page_size, offset],
            ).fetchall()

        prompt_ids = [p["id"] for p in prompts]
        rankings = {}
        serp_data = {}
        if prompt_ids:
            ph = ", ".join(["%s"] * len(prompt_ids))

            grabon_rows = conn.execute(f"""
                WITH latest AS (
                    SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                        el.prompt_id, el.engine_name, el.id AS log_id, el.raw_response_text
                    FROM execution_logs el
                    WHERE el.prompt_id IN ({ph})
                      AND el.raw_response_text NOT LIKE 'Error:%%'
                    ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
                )
                SELECT l.prompt_id, l.engine_name, l.raw_response_text, l.log_id,
                       bm.rank_position, bm.sentiment
                FROM latest l
                LEFT JOIN brand_mentions bm ON bm.log_id = l.log_id
                    AND LOWER(bm.brand_name) LIKE '%%grabon%%'
            """, prompt_ids).fetchall()

            all_brand_rows = conn.execute(f"""
                WITH latest AS (
                    SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                        el.prompt_id, el.engine_name, el.id AS log_id
                    FROM execution_logs el
                    WHERE el.prompt_id IN ({ph})
                      AND el.raw_response_text NOT LIKE 'Error:%%'
                    ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
                )
                SELECT l.prompt_id, l.engine_name,
                       bm.brand_name, bm.rank_position, bm.sentiment
                FROM latest l
                JOIN brand_mentions bm ON bm.log_id = l.log_id
                ORDER BY l.prompt_id, l.engine_name, bm.rank_position
            """, prompt_ids).fetchall()

            brand_index = {}
            for r in all_brand_rows:
                key = (str(r["prompt_id"]), r["engine_name"])
                brand_index.setdefault(key, []).append({
                    "brand": r["brand_name"], "rank": r["rank_position"],
                    "sentiment": r["sentiment"],
                })

            error_rows = conn.execute(f"""
                SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                    el.prompt_id, el.engine_name, el.id AS log_id, el.raw_response_text
                FROM execution_logs el
                WHERE el.prompt_id IN ({ph})
                ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
            """, prompt_ids).fetchall()
            error_index = {}
            for r in error_rows:
                key = (str(r["prompt_id"]), r["engine_name"])
                error_index[key] = r

            for p in prompts:
                pid = str(p["id"])
                rankings[pid] = {}
                for eng in VISIBLE_ENGINES:
                    hits = [r for r in grabon_rows if str(r["prompt_id"]) == pid and r["engine_name"] == eng]
                    top_brands = brand_index.get((pid, eng), [])
                    lid = str(hits[0]["log_id"]) if hits else None
                    if not hits:
                        err_row = error_index.get((pid, eng))
                        if err_row:
                            err_lid = str(err_row["log_id"])
                            raw = err_row["raw_response_text"] or ""
                            status, err_type, err_short = _classify_error(raw)
                            rankings[pid][eng] = {
                                "status": status, "top_brands": [], "log_id": err_lid,
                                "error_type": err_type, "error_msg": err_short,
                            }
                        else:
                            rankings[pid][eng] = {"status": "pending", "top_brands": []}
                    elif hits[0]["rank_position"]:
                        rankings[pid][eng] = {"status": "ranked", "rank": hits[0]["rank_position"], "sentiment": hits[0]["sentiment"], "top_brands": top_brands, "log_id": lid}
                    elif top_brands:
                        rankings[pid][eng] = {"status": "unranked", "top_brands": top_brands, "log_id": lid}
                    else:
                        rankings[pid][eng] = {"status": "no_mention", "top_brands": [], "log_id": lid}

            serp_rows = conn.execute(f"""
                WITH latest_serp AS (
                    SELECT DISTINCT ON (prompt_id)
                        id AS serp_id, prompt_id, captured_at
                    FROM serp_results
                    WHERE prompt_id IN ({ph})
                    ORDER BY prompt_id, captured_at DESC
                )
                SELECT ls.prompt_id,
                       soe.rank_position AS target_rank,
                       soe.domain AS target_domain,
                       ls.captured_at AS serp_at
                FROM latest_serp ls
                LEFT JOIN serp_organic_entries soe
                    ON soe.serp_id = ls.serp_id AND soe.is_target = TRUE
            """, prompt_ids).fetchall()

            feat_rows = conn.execute(f"""
                WITH latest_serp AS (
                    SELECT DISTINCT ON (prompt_id)
                        id AS serp_id, prompt_id
                    FROM serp_results
                    WHERE prompt_id IN ({ph})
                    ORDER BY prompt_id, captured_at DESC
                )
                SELECT ls.prompt_id, sf.feature_type
                FROM latest_serp ls
                JOIN serp_features sf ON sf.serp_id = ls.serp_id
            """, prompt_ids).fetchall()

            for r in serp_rows:
                pid = str(r["prompt_id"])
                serp_data[pid] = {
                    "target_rank": r["target_rank"],
                    "target_domain": r["target_domain"],
                    "serp_at": str(r["serp_at"])[:16] if r["serp_at"] else None,
                    "features": [],
                }
            for r in feat_rows:
                pid = str(r["prompt_id"])
                if pid in serp_data:
                    ft = r["feature_type"]
                    if ft not in serp_data[pid]["features"]:
                        serp_data[pid]["features"].append(ft)

            prev_rank_rows = conn.execute(f"""
                WITH latest AS (
                    SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                        el.prompt_id, el.engine_name, el.id AS log_id
                    FROM execution_logs el
                    WHERE el.prompt_id IN ({ph})
                      AND el.raw_response_text NOT LIKE 'Error:%%'
                    ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
                ),
                second AS (
                    SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                        el.prompt_id, el.engine_name, el.id AS log_id
                    FROM execution_logs el
                    JOIN latest l ON el.prompt_id = l.prompt_id
                        AND el.engine_name = l.engine_name
                        AND el.id <> l.log_id
                    WHERE el.prompt_id IN ({ph})
                      AND el.raw_response_text NOT LIKE 'Error:%%'
                    ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
                )
                SELECT s.prompt_id, s.engine_name, bm.rank_position AS prev_rank
                FROM second s
                LEFT JOIN brand_mentions bm ON bm.log_id = s.log_id
                    AND LOWER(bm.brand_name) LIKE '%%grabon%%'
            """, prompt_ids + prompt_ids).fetchall()

            for r in prev_rank_rows:
                pid = str(r["prompt_id"])
                eng = r["engine_name"]
                if pid in rankings and eng in rankings[pid]:
                    cur = rankings[pid][eng].get("rank")
                    prev = r["prev_rank"]
                    rankings[pid][eng]["prev_rank"] = prev
                    if cur is not None and prev is not None:
                        rankings[pid][eng]["rank_change"] = prev - cur

        return {
            "prompts": [{"id": str(p["id"]), "text": p["text"], "tier": p["tier"],
                         "category": p["merchant_category"], "last_run": str(p["last_run_at"])[:16] if p["last_run_at"] else None}
                        for p in prompts],
            "rankings": rankings,
            "serp": serp_data,
            "total": total,
            "page": page,
            "total_pages": (total + page_size - 1) // page_size,
        }
    result = await run_db(_query)
    return result


@router.get("/pipeline-progress")
async def pipeline_progress():
    """Pipeline coverage with cycle tracking."""
    from app.agent.scraper import VISIBLE_ENGINES

    def _query(conn):
        engine_count = len(VISIBLE_ENGINES)
        engine_list = list(VISIBLE_ENGINES)
        engine_ph = ",".join(["%s"] * engine_count)

        overall = conn.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(CASE WHEN last_run_at IS NOT NULL OR last_serp_at IS NOT NULL THEN 1 END) as processed,
                COUNT(CASE WHEN last_run_at IS NULL AND last_serp_at IS NULL THEN 1 END) as never_run,
                ROUND(
                    COUNT(CASE WHEN last_run_at IS NOT NULL OR last_serp_at IS NOT NULL THEN 1 END)::numeric
                    / NULLIF(COUNT(*), 0) * 100, 1
                ) as pct_complete
            FROM prompts
        """).fetchone()

        geo = conn.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(last_run_at) as processed,
                ROUND(COUNT(last_run_at)::numeric / NULLIF(COUNT(*), 0) * 100, 1) as pct_complete
            FROM prompts
            WHERE intent_type = 'GEO'
        """).fetchone()

        seo = conn.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(last_serp_at) as processed,
                ROUND(COUNT(last_serp_at)::numeric / NULLIF(COUNT(*), 0) * 100, 1) as pct_complete
            FROM prompts
            WHERE intent_type <> 'GEO'
        """).fetchone()

        geo_cycle = conn.execute(f"""
            WITH keyword_engine_counts AS (
                SELECT p.id AS prompt_id,
                       COUNT(DISTINCT el.engine_name) AS engines_done,
                       COUNT(el.id) AS total_scrapes
                FROM prompts p
                LEFT JOIN execution_logs el ON el.prompt_id = p.id
                    AND el.engine_name IN ({engine_ph})
                    AND el.raw_response_text NOT LIKE 'Error:%%'
                WHERE p.intent_type = 'GEO'
                GROUP BY p.id
            ),
            per_keyword_cycles AS (
                SELECT prompt_id,
                       CASE WHEN engines_done = 0 THEN 0
                            ELSE FLOOR(total_scrapes::numeric / {engine_count})
                       END AS full_cycles
                FROM keyword_engine_counts
            ),
            cycle_stats AS (
                SELECT COALESCE(MIN(full_cycles), 0) AS completed,
                       COUNT(*) AS total_kw
                FROM per_keyword_cycles
            )
            SELECT cs.completed, cs.total_kw,
                   (SELECT COUNT(*) FROM per_keyword_cycles
                    WHERE full_cycles >= cs.completed + 1) AS in_next
            FROM cycle_stats cs
        """, engine_list).fetchone()

        seo_cycle = conn.execute("""
            WITH per_kw AS (
                SELECT p.id, COUNT(sr.id) AS scrape_count
                FROM prompts p
                LEFT JOIN serp_results sr ON sr.prompt_id = p.id
                WHERE p.intent_type <> 'GEO'
                GROUP BY p.id
            ),
            cycle_stats AS (
                SELECT COALESCE(MIN(scrape_count), 0) AS completed,
                       COUNT(*) AS total_kw
                FROM per_kw
            )
            SELECT cs.completed, cs.total_kw,
                   (SELECT COUNT(*) FROM per_kw
                    WHERE scrape_count >= cs.completed + 1) AS in_next
            FROM cycle_stats cs
        """).fetchone()

        def _build_cycle(row):
            comp = int(row["completed"])
            tot = int(row["total_kw"])
            inn = int(row["in_next"]) if row["in_next"] else 0
            pct = round(inn / max(tot, 1) * 100, 1)
            if pct >= 100 and inn < tot:
                pct = 99.9
            if pct == 0 and inn > 0:
                pct = 0.1
            return {
                "completed": comp,
                "current_pct": pct,
                "keywords_in_next": inn,
                "total_keywords": tot,
            }

        per_engine = conn.execute(f"""
            SELECT el.engine_name,
                   COUNT(DISTINCT el.prompt_id) AS processed,
                   (SELECT COUNT(*) FROM prompts) AS total
            FROM execution_logs el
            WHERE el.engine_name IN ({engine_ph})
              AND el.raw_response_text NOT LIKE 'Error:%%'
            GROUP BY el.engine_name
            ORDER BY el.engine_name
        """, engine_list).fetchall()

        engine_coverage = {}
        for r in per_engine:
            pct = round(int(r["processed"]) / max(int(r["total"]), 1) * 100, 1)
            engine_coverage[r["engine_name"]] = {
                "processed": int(r["processed"]),
                "total": int(r["total"]),
                "pct": min(pct, 100.0),
            }

        return {
            "overall": dict(overall),
            "geo": dict(geo),
            "seo": dict(seo),
            "geo_cycles": _build_cycle(geo_cycle),
            "seo_cycles": _build_cycle(seo_cycle),
            "engine_coverage": engine_coverage,
        }
    result = await run_db(_query)
    return result


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
    """Run a single keyword across all engines - for testing. Returns immediately, runs in background."""
    from app.agent.pipeline import run_pipeline, ENGINE_DELAY_MIN, ENGINE_DELAY_MAX
    from app.engines import ALL_ENGINES
    from app.models import PromptItem
    import random

    def _ensure_prompt(conn):
        cur = conn.execute(
            """INSERT INTO prompts (text, merchant_category, intent_type)
               VALUES (%s, 'Test', 'Transactional')
               ON CONFLICT (text) DO UPDATE SET text = EXCLUDED.text
               RETURNING id""",
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
                log.info(f"[test] Running: {engine} - {text[:50]}")
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
    from app.engines import ALL_ENGINES
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
            log.info(f"[test-engine] Running: {engine} - {text[:50]}")
            await run_pipeline(prompt, engine, "IN")
            log.info(f"[test-engine] {engine} complete: {text[:50]}")
        except Exception as e:
            log.error(f"[test-engine] {engine} failed: {e}")

    asyncio.create_task(_run())
    return {"status": "started", "keyword": text, "engine": engine}


@router.post("/trigger-batch")
async def trigger_batch(batch_size: int = 100, concurrency: int = 3):
    from app.agent.pipeline import run_rolling_batch
    log.info(f"Manual trigger: batch={batch_size}, concurrency={concurrency}")
    asyncio.create_task(run_rolling_batch(batch_size, concurrency))
    return {"status": "started", "batch_size": batch_size}


@router.get("/coverage-gaps")
async def coverage_gaps():
    """Find all keyword+engine combos with no execution_log (blank cells in matrix)."""
    from app.engines import VISIBLE_ENGINES
    engine_list = list(VISIBLE_ENGINES)

    def _gaps(conn):
        rows = conn.execute(
            """SELECT p.id AS prompt_id, p.text, p.tier, p.merchant_category,
                      e.engine_name
               FROM prompts p
               CROSS JOIN UNNEST(%s::text[]) AS e(engine_name)
               LEFT JOIN execution_logs el
                   ON el.prompt_id = p.id AND el.engine_name = e.engine_name
               WHERE p.is_canonical = TRUE AND p.intent_type = 'GEO'
                 AND el.id IS NULL
               ORDER BY p.tier, p.text, e.engine_name""",
            (engine_list,),
        ).fetchall()
        return [dict(r) for r in rows]

    gaps = await run_db(_gaps)
    by_engine = {}
    for g in gaps:
        eng = g["engine_name"]
        by_engine[eng] = by_engine.get(eng, 0) + 1

    return {
        "total_gaps": len(gaps),
        "by_engine": by_engine,
        "gaps": gaps[:200],
    }


@router.post("/fill-gaps")
async def fill_coverage_gaps(max_per_engine: int = 50):
    """Trigger targeted scrapes for keyword+engine gaps (blank cells). Runs in background."""
    from app.engines import VISIBLE_ENGINES
    from app.agent.pipeline import run_parallel_engines
    from app.models import PromptItem
    engine_list = list(VISIBLE_ENGINES)

    def _gaps(conn):
        rows = conn.execute(
            """SELECT DISTINCT p.id, p.text, p.merchant_category, p.intent_type,
                      ARRAY_AGG(e.engine_name) AS missing_engines
               FROM prompts p
               CROSS JOIN UNNEST(%s::text[]) AS e(engine_name)
               LEFT JOIN execution_logs el
                   ON el.prompt_id = p.id AND el.engine_name = e.engine_name
               WHERE p.is_canonical = TRUE AND p.intent_type = 'GEO'
                 AND el.id IS NULL
               GROUP BY p.id, p.text, p.merchant_category, p.intent_type
               ORDER BY p.tier, p.text
               LIMIT %s""",
            (engine_list, max_per_engine * len(engine_list)),
        ).fetchall()
        return [dict(r) for r in rows]

    gaps = await run_db(_gaps)
    if not gaps:
        return {"status": "no_gaps", "message": "All keywords covered by all engines"}

    prompts = [
        PromptItem(
            id=str(g["id"]), text=g["text"],
            merchant_category=g["merchant_category"],
            intent_type=g["intent_type"],
        )
        for g in gaps
    ]

    async def _run_fill():
        await run_parallel_engines(
            prompts=prompts,
            engines=None,
            skip_fresh=False,
            freshness_hours=0,
            use_dedup=False,
        )

    asyncio.create_task(_run_fill())

    return {
        "status": "started",
        "keywords_with_gaps": len(gaps),
        "total_prompts": len(prompts),
        "message": f"Filling gaps for {len(prompts)} keywords across all engines",
    }


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
        by_purpose = conn.execute("""
            SELECT purpose, provider, COUNT(*) as calls,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   SUM(cost_usd) as cost
            FROM api_costs GROUP BY purpose, provider ORDER BY cost DESC
        """).fetchall()
        avg_tokens = conn.execute("""
            SELECT purpose,
                   ROUND(AVG(input_tokens)) as avg_input,
                   ROUND(AVG(output_tokens)) as avg_output,
                   AVG(cost_usd) as avg_cost
            FROM api_costs GROUP BY purpose
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
            "by_purpose": [dict(r) for r in by_purpose],
            "avg_per_call": [dict(r) for r in avg_tokens],
        }
    return await run_db(_costs)


# ─── Analytics Endpoints ───────────────────────────────────────────────


@router.get("/position-history")
async def position_history(
    prompt_id: str = Query(...),
    engine: str = Query("all"),
    days: int = Query(30),
):
    """Historical rank positions for a keyword across engines over time."""
    if not _valid_uuid(prompt_id):
        return JSONResponse({"error": "Invalid prompt_id"}, status_code=400)
    from app.agent.scraper import VISIBLE_ENGINES

    def _query(conn):
        engine_filter = ""
        params = [prompt_id, days]
        if engine != "all":
            engine_filter = "AND el.engine_name = %s"
            params.append(engine)

        rows = conn.execute(f"""
            SELECT
                el.captured_at::date::text AS day,
                el.engine_name,
                bm.rank_position,
                bm.brand_name,
                bm.sentiment
            FROM brand_mentions bm
            JOIN execution_logs el ON el.id = bm.log_id
            WHERE el.prompt_id = %s::uuid
              AND el.captured_at >= CURRENT_DATE - make_interval(days => %s)
              {engine_filter}
            ORDER BY el.captured_at::date, el.engine_name, bm.rank_position
        """, params).fetchall()

        prompt_row = conn.execute(
            "SELECT text, merchant_category, tier FROM prompts WHERE id = %s::uuid",
            (prompt_id,),
        ).fetchone()

        history = {}
        for r in rows:
            day = r["day"]
            eng = r["engine_name"]
            history.setdefault(day, {}).setdefault(eng, []).append({
                "brand": r["brand_name"],
                "rank": r["rank_position"],
                "sentiment": r["sentiment"],
            })

        grabon_timeline = []
        for day in sorted(history.keys()):
            entry = {"day": day}
            for eng in VISIBLE_ENGINES:
                brands = history.get(day, {}).get(eng, [])
                grabon = [b for b in brands if "grabon" in b["brand"].lower()]
                entry[eng] = grabon[0]["rank"] if grabon else None
            grabon_timeline.append(entry)

        return {
            "prompt": dict(prompt_row) if prompt_row else None,
            "timeline": grabon_timeline,
            "engines": VISIBLE_ENGINES,
        }

    return await run_db(_query)


@router.get("/engine-cooldowns")
async def engine_cooldowns():
    from app.agent.pipeline import get_engine_cooldowns
    return {"cooldowns": get_engine_cooldowns()}


@router.post("/engine-cooldowns/{engine_name}/reset")
async def reset_engine_cooldown(engine_name: str):
    from app.agent.pipeline import clear_engine_cooldown
    from app.engines import ALL_ENGINES
    if engine_name not in ALL_ENGINES:
        return JSONResponse({"error": f"Unknown engine: {engine_name}"}, status_code=404)
    clear_engine_cooldown(engine_name)
    return {"status": "ok", "engine": engine_name, "message": "Cooldown cleared, engine re-enabled"}


@router.get("/engine-health")
async def engine_health():
    def _health(conn):
        rows = conn.execute("""
            SELECT
                el.engine_name,
                COUNT(*) FILTER (WHERE el.captured_at >= NOW() - INTERVAL '24 hours') AS total_24h,
                COUNT(*) FILTER (WHERE el.captured_at >= NOW() - INTERVAL '24 hours'
                                   AND el.raw_response_text LIKE 'Error:%%') AS errors_24h,
                COUNT(*) FILTER (WHERE el.captured_at >= NOW() - INTERVAL '24 hours'
                                   AND el.raw_response_text NOT LIKE 'Error:%%') AS success_24h,
                MAX(el.captured_at) FILTER (WHERE el.raw_response_text NOT LIKE 'Error:%%') AS last_success,
                MAX(el.captured_at) FILTER (WHERE el.raw_response_text LIKE 'Error:%%') AS last_error,
                COUNT(*) FILTER (WHERE el.captured_at >= NOW() - INTERVAL '4 hours') AS total_4h,
                COUNT(*) FILTER (WHERE el.captured_at >= NOW() - INTERVAL '4 hours'
                                   AND el.raw_response_text LIKE 'Error:%%') AS errors_4h,
                COUNT(*) AS total_all
            FROM execution_logs el
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

        mention_rows = conn.execute("""
            SELECT
                el.engine_name,
                COUNT(bm.id) AS mentions,
                COUNT(bm.id) FILTER (WHERE bm.cited_url IS NOT NULL AND bm.cited_url LIKE '%%grabon%%') AS cited,
                (SELECT COUNT(*) FROM brand_mentions bm2
                 JOIN execution_logs el2 ON bm2.log_id = el2.id
                 WHERE el2.engine_name = el.engine_name) AS total_mentions
            FROM execution_logs el
            LEFT JOIN brand_mentions bm ON bm.log_id = el.id AND bm.is_target_brand = TRUE
            GROUP BY el.engine_name
        """).fetchall()

        return (
            [dict(r) for r in rows],
            {s["engine_name"]: s["results"] for s in streak_rows},
            [dict(r) for r in recent_errors],
            {m["engine_name"]: dict(m) for m in mention_rows},
        )

    stats, streaks, recent_errors, mention_stats = await run_db(_health)

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

        if r["total_24h"] == 0:
            health = "idle"
        elif recent_success_streak >= 3:
            health = "healthy"
        elif recent_success_streak >= 2:
            health = "recovering" if rate_4h >= 50 else "healthy"
        elif rate_4h >= 80:
            health = "blocked" if recent_success_streak == 0 else "recovering"
        elif rate_4h >= 50:
            health = "degraded" if recent_success_streak == 0 else "recovering"
        elif total_4h == 0 and r["total_24h"] == 0:
            health = "inactive"
        else:
            health = "healthy"

        ms = mention_stats.get(eng, {})
        engines[eng] = {
            "total_24h": r["total_24h"],
            "errors_24h": r["errors_24h"],
            "success_24h": r["success_24h"],
            "error_rate": rate_4h,
            "health": health,
            "last_success": str(r["last_success"]) if r["last_success"] else None,
            "last_error": str(r["last_error"]) if r["last_error"] else None,
            "streak": recent_success_streak,
            "mentions": ms.get("mentions", 0),
            "cited": ms.get("cited", 0),
            "total_mentions": ms.get("total_mentions", 0),
        }

    from app.agent.account_pool import get_pool_status
    pool_status = get_pool_status()
    per_eng_errors = {}
    for e in recent_errors:
        if e["engine_name"] not in per_eng_errors:
            per_eng_errors[e["engine_name"]] = e["error_msg"]
    for eng, h in engines.items():
        h["last_error_msg"] = per_eng_errors.get(eng)
        pool_eng = eng if eng not in ("google_aio", "google_ai_mode") else "google"
        ps = pool_status.get(pool_eng)
        if ps:
            max_cd = max((s["cooldown_remaining"] for s in ps["slots"]), default=0)
            h["cooldown_remaining"] = max_cd
            h["pool_available"] = ps["available_slots"]
            h["pool_total"] = ps["total_slots"]

    return {"engines": engines, "recent_errors": recent_errors}


@router.get("/log-response/{log_id}")
async def get_log_response(log_id: str):
    if not _valid_uuid(log_id):
        return JSONResponse({"error": "Invalid log_id"}, status_code=400)
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


@router.get("/system-docs")
async def system_docs_raw():
    docs_path = Path(__file__).resolve().parent.parent.parent / "system_docs.md"
    if not docs_path.exists():
        docs_path = Path("system_docs.md").resolve()
    if not docs_path.exists():
        return PlainTextResponse("# Documentation not found\n\nExpected at: " + str(docs_path), status_code=404)
    return PlainTextResponse(docs_path.read_text(encoding="utf-8"))


# --- Diagnosis Endpoints ---

@router.post("/diagnose/{prompt_id}")
async def diagnose_keyword(prompt_id: str, engine: str | None = None):
    """Generate on-demand diagnosis for a keyword."""
    from app.agent.diagnosis_engine import generate_diagnosis

    row = await run_db(lambda conn: conn.execute(
        "SELECT id, text FROM prompts WHERE id = %s::uuid", (prompt_id,),
    ).fetchone())
    if not row:
        return {"error": "Prompt not found"}

    result = await generate_diagnosis(prompt_id, row["text"], engine)
    if not result:
        return {"error": "Diagnosis generation failed. Check OpenAI API key and data availability."}
    return result


@router.get("/diagnoses")
async def list_diagnoses(
    status: str = "active",
    priority: str | None = None,
    limit: int = 50,
):
    """List diagnoses, filterable by status and priority."""
    def _q(conn):
        base = """
            SELECT d.id, d.prompt_id, d.engine_name, d.priority, d.confidence,
                   d.summary, d.status, d.cost_usd, d.created_at,
                   p.text as keyword,
                   jsonb_array_length(d.root_causes) as cause_count,
                   jsonb_array_length(d.action_items) as action_count
            FROM diagnoses d
            JOIN prompts p ON d.prompt_id = p.id
            WHERE d.status = %s
        """
        params = [status]
        if priority:
            base += " AND d.priority = %s"
            params.append(priority)
        base += " ORDER BY d.created_at DESC, d.priority LIMIT %s"
        params.append(limit)
        return conn.execute(base, params).fetchall()
    rows = await run_db(_q)
    return [dict(r) for r in rows]


@router.get("/diagnoses/{diagnosis_id}")
async def get_diagnosis(diagnosis_id: str):
    """Get full diagnosis with root causes, action items, and evidence."""
    row = await run_db(lambda conn: conn.execute(
        """SELECT d.*, p.text as keyword
           FROM diagnoses d
           JOIN prompts p ON d.prompt_id = p.id
           WHERE d.id = %s::uuid""",
        (diagnosis_id,),
    ).fetchone())
    if not row:
        return {"error": "Diagnosis not found"}
    result = dict(row)
    if isinstance(result.get("root_causes"), str):
        result["root_causes"] = json.loads(result["root_causes"])
    if isinstance(result.get("action_items"), str):
        result["action_items"] = json.loads(result["action_items"])
    if isinstance(result.get("evidence_snapshot"), str):
        result["evidence_snapshot"] = json.loads(result["evidence_snapshot"])
    return result


@router.get("/diagnoses/priority-queue")
async def diagnosis_priority_queue():
    """Get keywords ranked by diagnosis priority."""
    from app.agent.diagnosis_engine import get_diagnosis_priority_queue
    return await get_diagnosis_priority_queue()


@router.post("/diagnoses/{diagnosis_id}/dismiss")
async def dismiss_diagnosis(diagnosis_id: str):
    """Mark a diagnosis as dismissed."""
    def _update(conn):
        conn.execute(
            "UPDATE diagnoses SET status = 'dismissed' WHERE id = %s::uuid",
            (diagnosis_id,),
        )
        conn.commit()
    await run_db(_update)
    return {"status": "dismissed"}


@router.get("/rankings-export")
async def rankings_export(
    tier: int = Query(0),
    category: str = Query("All"),
    intent: str = Query("All"),
):
    """Export rankings matrix as CSV."""
    settings = get_settings()
    hidden = getattr(settings, "hidden_engines", set()) or set()
    visible = [e for e in ["google_aio", "google_ai_mode", "perplexity", "gemini", "chatgpt", "claude"] if e not in hidden]
    from app.engines import ENGINE_LABELS as engine_display

    def _q(conn):
        filters = []
        params = []
        if tier > 0:
            filters.append("p.tier = %s")
            params.append(tier)
        if category != "All":
            filters.append("p.merchant_category = %s")
            params.append(category)
        if intent != "All":
            filters.append("p.intent_type = %s")
            params.append(intent)
        where = ("WHERE " + " AND ".join(filters)) if filters else ""

        prompts = conn.execute(f"""
            SELECT p.id, p.text, p.merchant_category, p.intent_type, p.tier
            FROM prompts p {where} ORDER BY p.text
        """, params).fetchall()

        prompt_ids = [str(p["id"]) for p in prompts]
        if not prompt_ids:
            return prompts, {}, {}

        ph = ",".join(["%s::uuid"] * len(prompt_ids))

        rankings = {}
        rank_rows = conn.execute(f"""
            WITH latest AS (
                SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                    el.prompt_id, el.engine_name, el.id as log_id
                FROM execution_logs el
                WHERE el.prompt_id IN ({ph})
                  AND el.raw_response_text NOT LIKE 'Error:%%'
                ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
            )
            SELECT l.prompt_id, l.engine_name,
                   bm.rank_position, bm.brand_name, bm.sentiment
            FROM latest l
            LEFT JOIN brand_mentions bm ON bm.log_id = l.log_id
                AND LOWER(bm.brand_name) LIKE '%%grabon%%'
            ORDER BY l.prompt_id, l.engine_name
        """, prompt_ids).fetchall()
        for r in rank_rows:
            pid = str(r["prompt_id"])
            eng = r["engine_name"]
            if pid not in rankings:
                rankings[pid] = {}
            rankings[pid][eng] = {
                "rank": r["rank_position"],
                "sentiment": r["sentiment"],
            }

        serp = {}
        serp_rows = conn.execute(f"""
            WITH latest AS (
                SELECT DISTINCT ON (prompt_id)
                    id AS serp_id, prompt_id
                FROM serp_results WHERE prompt_id IN ({ph})
                ORDER BY prompt_id, captured_at DESC
            )
            SELECT l.prompt_id, soe.rank_position AS target_rank
            FROM latest l
            LEFT JOIN serp_organic_entries soe
                ON soe.serp_id = l.serp_id AND soe.is_target = TRUE
        """, prompt_ids).fetchall()
        for r in serp_rows:
            serp[str(r["prompt_id"])] = r["target_rank"]

        return prompts, rankings, serp

    prompts, rankings, serp = await run_db(_q)

    output = io.StringIO()
    writer = csv.writer(output)
    headers = ["Keyword", "Category", "Intent", "Tier", "Google Rank"]
    headers += [engine_display.get(e, e) for e in visible]
    writer.writerow(headers)

    for p in prompts:
        pid = str(p["id"])
        row = [p["text"], p["merchant_category"], p["intent_type"], p["tier"]]
        row.append(serp.get(pid) or "")
        for eng in visible:
            r = (rankings.get(pid) or {}).get(eng)
            if r and r["rank"]:
                row.append(f"#{r['rank']} ({r['sentiment'] or ''})")
            else:
                row.append("")
        writer.writerow(row)

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=rankings_export.csv"},
    )


# ─── Notifications ───────────────────────────────────────────────────


@router.get("/notifications")
async def get_notifications(limit: int = Query(50, ge=1, le=200)):
    def _q(conn):
        return conn.execute(
            "SELECT * FROM notifications ORDER BY created_at DESC LIMIT %s", (limit,)
        ).fetchall()
    rows = await run_db(_q)
    return [dict(r) for r in rows]


@router.get("/notifications/unread-count")
async def unread_count():
    def _q(conn):
        return conn.execute(
            "SELECT COUNT(*) as cnt FROM notifications WHERE is_read = FALSE"
        ).fetchone()
    row = await run_db(_q)
    return {"count": row["cnt"]}


@router.post("/notifications/{nid}/read")
async def mark_read(nid: str):
    def _q(conn):
        conn.execute(
            "UPDATE notifications SET is_read = TRUE WHERE id = %s::uuid", (nid,)
        )
        conn.commit()
    await run_db(_q)
    return {"status": "ok"}


@router.post("/notifications/read-all")
async def mark_all_read():
    def _q(conn):
        conn.execute("UPDATE notifications SET is_read = TRUE WHERE is_read = FALSE")
        conn.commit()
    await run_db(_q)
    return {"status": "ok"}


@router.delete("/notifications/{nid}")
async def delete_notification(nid: str):
    def _q(conn):
        conn.execute("DELETE FROM notifications WHERE id = %s::uuid", (nid,))
        conn.commit()
    await run_db(_q)
    return {"status": "ok"}


# ═══════════════════════════════════════════════════════════════════════
# INSIGHT FEATURES
# ═══════════════════════════════════════════════════════════════════════
