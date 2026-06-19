import asyncio
import csv
import io
import json
import logging
import uuid as _uuid
from pathlib import Path
from fastapi import APIRouter, File, Form, Query, Response, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from sse_starlette.sse import EventSourceResponse
from app.database import run_db
from app.agent.pipeline import run_full_sweep
from app.config import get_settings
from app.events import subscribe, unsubscribe, get_recent

router = APIRouter(prefix="/api")
log = logging.getLogger("geo.api")

_boot_time = __import__("time").time()


@router.get("/health")
async def health_check():
    """Lightweight liveness probe — DB ping + uptime."""
    try:
        row = await run_db("SELECT 1 AS ok", fetchone=True)
        db_ok = row and row.get("ok") == 1
    except Exception:
        db_ok = False
    import time
    uptime = int(time.time() - _boot_time)
    if not db_ok:
        return JSONResponse({"status": "unhealthy", "db": False, "uptime_s": uptime}, status_code=503)
    return {"status": "healthy", "db": True, "uptime_s": uptime}


def _valid_uuid(val: str) -> bool:
    try:
        _uuid.UUID(val)
        return True
    except (ValueError, AttributeError):
        return False


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
):
    """Live rankings matrix data for auto-refresh."""
    from app.agent.scraper import VISIBLE_ENGINES
    active_engines = [e.strip() for e in engines.split(",") if e.strip()] if engines else list(VISIBLE_ENGINES)


    def _query(conn):
        engine_count = len(active_engines)
        engine_ph = ",".join(["%s"] * engine_count)

        where_parts = [
            f"EXISTS (SELECT 1 FROM execution_logs el WHERE el.prompt_id = p.id AND el.engine_name IN ({engine_ph}))"
        ]
        params: list = list(active_engines)
        if tier > 0:
            where_parts.append("p.tier = %s")
            params.append(tier)
        if category != "All":
            where_parts.append("p.merchant_category = %s")
            params.append(category)
        if intent != "All":
            where_parts.append("p.intent_type = %s")
            params.append(intent)

        where_sql = " AND ".join(where_parts)
        total = conn.execute(f"SELECT COUNT(*) as cnt FROM prompts p WHERE {where_sql}", params).fetchone()["cnt"]
        offset = (page - 1) * page_size
        page_params = list(params) + [page_size, offset]
        prompts = conn.execute(
            f"""SELECT p.id, p.text, p.tier, p.merchant_category, p.intent_type, p.last_run_at
                FROM prompts p
                LEFT JOIN (
                    SELECT prompt_id, MAX(captured_at) as max_captured
                    FROM execution_logs GROUP BY prompt_id
                ) la ON la.prompt_id = p.id
                WHERE {where_sql}
                ORDER BY la.max_captured DESC NULLS LAST
                LIMIT %s OFFSET %s""",
            page_params,
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

            for p in prompts:
                pid = str(p["id"])
                rankings[pid] = {}
                for eng in VISIBLE_ENGINES:
                    hits = [r for r in grabon_rows if str(r["prompt_id"]) == pid and r["engine_name"] == eng]
                    top_brands = brand_index.get((pid, eng), [])
                    lid = str(hits[0]["log_id"]) if hits else None
                    if not hits:
                        rankings[pid][eng] = {"status": "pending", "top_brands": []}
                    elif hits[0]["rank_position"]:
                        rankings[pid][eng] = {"status": "ranked", "rank": hits[0]["rank_position"], "sentiment": hits[0]["sentiment"], "top_brands": top_brands, "log_id": lid}
                    elif hits[0]["raw_response_text"] and hits[0]["raw_response_text"].startswith("[No AI Overview]"):
                        rankings[pid][eng] = {"status": "no_aio", "top_brands": [], "log_id": lid}
                    elif hits[0]["raw_response_text"] and hits[0]["raw_response_text"].startswith("Error:"):
                        rankings[pid][eng] = {"status": "error", "top_brands": [], "log_id": lid}
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
    """SEO vs GEO coverage for canonical keywords."""

    def _query(conn):
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
                (SELECT COUNT(*) FROM prompts WHERE intent_type <> 'GEO') as total,
                COUNT(DISTINCT sr.prompt_id) as processed,
                ROUND(
                    COUNT(DISTINCT sr.prompt_id)::numeric /
                    NULLIF((SELECT COUNT(*) FROM prompts WHERE intent_type <> 'GEO'), 0) * 100, 1
                ) as pct_complete
            FROM serp_results sr
            JOIN prompts p ON p.id = sr.prompt_id
        """).fetchone()
        return {
            "overall": dict(overall),
            "geo": dict(geo),
            "seo": dict(seo),
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
    from app.agent.scraper import ALL_ENGINES
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
async def analytics_visibility(tier: int = Query(0)):
    """Brand visibility (Share of Voice) over last 30 days, daily, per engine."""


    def _query(conn):
        tier_filter = "AND p.tier = %s" if tier > 0 else ""
        params = (tier,) if tier > 0 else ()
        rows = conn.execute(f"""
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
            JOIN prompts p ON p.id = el.prompt_id
            WHERE el.captured_at >= CURRENT_DATE - INTERVAL '30 days'
              {tier_filter}
              AND (LOWER(bm.brand_name) LIKE '%%grabon%%' OR LOWER(bm.brand_name) LIKE '%%grab on%%'
                   OR LOWER(bm.brand_name) LIKE '%%coupondunia%%' OR LOWER(bm.brand_name) LIKE '%%coupon dunia%%'
                   OR LOWER(bm.brand_name) LIKE '%%cashkaro%%' OR LOWER(bm.brand_name) LIKE '%%cash karo%%'
                   OR LOWER(bm.brand_name) LIKE '%%desidime%%' OR LOWER(bm.brand_name) LIKE '%%desi dime%%')
            GROUP BY day, el.engine_name, brand
            ORDER BY day, el.engine_name, brand
        """, params).fetchall()

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

    result = {"data": await run_db(_query)}
    return result


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
async def analytics_heatmap(tier: int = Query(0)):
    """Keyword x Engine heatmap: GrabOn's latest rank per canonical prompt per engine."""
    def _query(conn):
        tier_filter = "AND p.tier = %s" if tier > 0 else ""
        params = (tier,) if tier > 0 else ()
        rows = conn.execute(f"""
            WITH latest_logs AS (
                SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                    el.prompt_id, el.engine_name, el.id AS log_id
                FROM execution_logs el
                JOIN prompts p ON p.id = el.prompt_id
                WHERE el.raw_response_text NOT LIKE 'Error:%%' {tier_filter}
                ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
            )
            SELECT
                p.text AS keyword,
                p.id AS prompt_id,
                ll.engine_name AS engine,
                bm.rank_position AS rank,
                bm.sentiment,
                COALESCE(p.merchant_category, 'Uncategorized') AS category
            FROM latest_logs ll
            JOIN prompts p ON p.id = ll.prompt_id
            LEFT JOIN brand_mentions bm
                ON bm.log_id = ll.log_id
                AND LOWER(bm.brand_name) LIKE '%%grabon%%'
            ORDER BY p.text, ll.engine_name
        """, params).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["prompt_id"] = str(d["prompt_id"])
            result.append(d)
        return result

    return {"data": await run_db(_query)}


@router.get("/analytics/competitors")
async def analytics_competitors(tier: int = Query(0)):
    """Per-engine mention counts, avg rank, and citation counts for key brands."""


    def _query(conn):
        tier_filter = "AND p.tier = %s" if tier > 0 else ""
        params = (tier,) if tier > 0 else ()
        rows = conn.execute(f"""
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
            JOIN prompts p ON p.id = el.prompt_id
            WHERE (LOWER(bm.brand_name) LIKE '%%grabon%%' OR LOWER(bm.brand_name) LIKE '%%grab on%%'
                   OR LOWER(bm.brand_name) LIKE '%%coupondunia%%' OR LOWER(bm.brand_name) LIKE '%%coupon dunia%%'
                   OR LOWER(bm.brand_name) LIKE '%%cashkaro%%' OR LOWER(bm.brand_name) LIKE '%%cash karo%%'
                   OR LOWER(bm.brand_name) LIKE '%%desidime%%' OR LOWER(bm.brand_name) LIKE '%%desi dime%%')
              {tier_filter}
            GROUP BY el.engine_name, brand
            ORDER BY el.engine_name, mention_count DESC
        """, params).fetchall()
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

    result = {"data": await run_db(_query)}
    return result


@router.get("/analytics/weaknesses")
async def analytics_weaknesses(tier: int = Query(0)):
    """Competitive weakness analysis: keywords where competitors outrank GrabOn."""


    def _query(conn):
        tier_filter = "AND p.tier = %s" if tier > 0 else ""
        params = (tier,) if tier > 0 else ()
        rows = conn.execute(f"""
            WITH latest AS (
                SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                    el.prompt_id, el.engine_name, el.id AS log_id
                FROM execution_logs el
                JOIN prompts p ON p.id = el.prompt_id
                WHERE el.raw_response_text NOT LIKE 'Error:%%' {tier_filter}
                ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
            ),
            ranked AS (
                SELECT
                    p.text AS keyword,
                    p.merchant_category AS category,
                    l.engine_name,
                    bm.brand_name,
                    bm.rank_position,
                    CASE
                        WHEN LOWER(bm.brand_name) LIKE '%%grabon%%' OR LOWER(bm.brand_name) LIKE '%%grab on%%' THEN 'GrabOn'
                        WHEN LOWER(bm.brand_name) LIKE '%%coupondunia%%' OR LOWER(bm.brand_name) LIKE '%%coupon dunia%%' THEN 'CouponDunia'
                        WHEN LOWER(bm.brand_name) LIKE '%%cashkaro%%' OR LOWER(bm.brand_name) LIKE '%%cash karo%%' THEN 'CashKaro'
                        WHEN LOWER(bm.brand_name) LIKE '%%desidime%%' OR LOWER(bm.brand_name) LIKE '%%desi dime%%' THEN 'DesiDime'
                        ELSE NULL
                    END AS normalized_brand
                FROM latest l
                JOIN brand_mentions bm ON bm.log_id = l.log_id
                JOIN prompts p ON p.id = l.prompt_id
            ),
            grabon_ranks AS (
                SELECT keyword, category, engine_name, rank_position AS grabon_rank
                FROM ranked WHERE normalized_brand = 'GrabOn'
            ),
            competitor_ranks AS (
                SELECT keyword, category, engine_name, normalized_brand AS competitor,
                       rank_position AS competitor_rank
                FROM ranked WHERE normalized_brand IS NOT NULL AND normalized_brand != 'GrabOn'
            )
            SELECT
                c.keyword, c.category, c.engine_name, c.competitor, c.competitor_rank,
                g.grabon_rank
            FROM competitor_ranks c
            LEFT JOIN grabon_ranks g ON g.keyword = c.keyword AND g.engine_name = c.engine_name
            WHERE c.competitor_rank < COALESCE(g.grabon_rank, 999)
            ORDER BY c.competitor_rank ASC, c.keyword
        """, params).fetchall()

        beaten_by = {}
        keyword_losses = {}
        engine_weakness = {}

        for r in rows:
            comp = r["competitor"]
            eng = r["engine_name"]
            kw = r["keyword"]
            cat = r["category"] or "General"
            grabon_rank = r["grabon_rank"]
            comp_rank = r["competitor_rank"]

            beaten_by.setdefault(comp, {"count": 0, "keywords": [], "avg_rank": []})
            beaten_by[comp]["count"] += 1
            beaten_by[comp]["avg_rank"].append(comp_rank)
            if kw not in [k["keyword"] for k in beaten_by[comp]["keywords"][:20]]:
                beaten_by[comp]["keywords"].append({
                    "keyword": kw, "category": cat, "engine": eng,
                    "competitor_rank": comp_rank,
                    "grabon_rank": grabon_rank,
                })

            keyword_losses.setdefault(kw, {"category": cat, "losses": []})
            keyword_losses[kw]["losses"].append({
                "engine": eng, "competitor": comp,
                "competitor_rank": comp_rank,
                "grabon_rank": grabon_rank,
            })

            engine_weakness.setdefault(eng, {"total_losses": 0, "top_competitor": {}})
            engine_weakness[eng]["total_losses"] += 1
            engine_weakness[eng]["top_competitor"].setdefault(comp, 0)
            engine_weakness[eng]["top_competitor"][comp] += 1

        for comp, data in beaten_by.items():
            data["avg_rank"] = round(sum(data["avg_rank"]) / len(data["avg_rank"]), 1) if data["avg_rank"] else 0
            data["keywords"] = sorted(data["keywords"], key=lambda x: x["competitor_rank"])[:15]

        for eng, data in engine_weakness.items():
            if data["top_competitor"]:
                data["dominant_competitor"] = max(data["top_competitor"], key=data["top_competitor"].get)
                data["dominant_count"] = data["top_competitor"][data["dominant_competitor"]]

        worst_keywords = sorted(
            [{"keyword": kw, "category": d["category"], "loss_count": len(d["losses"]),
              "losses": d["losses"][:5]}
             for kw, d in keyword_losses.items()],
            key=lambda x: -x["loss_count"]
        )[:20]

        return {
            "beaten_by": beaten_by,
            "worst_keywords": worst_keywords,
            "engine_weakness": engine_weakness,
        }

    result = await run_db(_query)
    return result


@router.get("/analytics/keyword-gaps")
async def analytics_keyword_gaps(tier: int = Query(0)):
    """Keyword gap analysis grouped by merchant_category."""
    def _query(conn):
        tier_filter = "AND p.tier = %s" if tier > 0 else ""
        params = (tier,) if tier > 0 else ()
        strongest = conn.execute(f"""
            WITH latest AS (
                SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                    el.prompt_id, el.engine_name, el.id AS log_id
                FROM execution_logs el
                WHERE el.raw_response_text NOT LIKE 'Error:%%'
                ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
            )
            SELECT DISTINCT p.text AS keyword, p.merchant_category
            FROM latest l
            JOIN brand_mentions bm ON bm.log_id = l.log_id
            JOIN prompts p ON p.id = l.prompt_id
            WHERE LOWER(bm.brand_name) LIKE '%%grabon%%' AND bm.rank_position = 1
              {tier_filter}
        """, params).fetchall()

        opportunity = conn.execute(f"""
            WITH latest AS (
                SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                    el.prompt_id, el.engine_name, el.id AS log_id
                FROM execution_logs el
                WHERE el.raw_response_text NOT LIKE 'Error:%%'
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
            ) {tier_filter}
        """, params).fetchall()

        gaps = conn.execute(f"""
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
              {tier_filter}
        """, params).fetchall()

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
async def analytics_sentiment(tier: int = Query(0)):
    """Sentiment breakdown for GrabOn mentions: by merchant_category and by engine."""
    def _query(conn):
        tier_filter = "AND p.tier = %s" if tier > 0 else ""
        params = (tier,) if tier > 0 else ()
        by_category = conn.execute(f"""
            SELECT
                p.merchant_category,
                bm.sentiment,
                COUNT(*) AS cnt
            FROM brand_mentions bm
            JOIN execution_logs el ON el.id = bm.log_id
            JOIN prompts p ON p.id = el.prompt_id
            WHERE LOWER(bm.brand_name) LIKE '%%grabon%%'
              AND bm.sentiment IS NOT NULL
              {tier_filter}
            GROUP BY p.merchant_category, bm.sentiment
            ORDER BY p.merchant_category, bm.sentiment
        """, params).fetchall()

        by_engine = conn.execute(f"""
            SELECT
                el.engine_name,
                bm.sentiment,
                COUNT(*) AS cnt
            FROM brand_mentions bm
            JOIN execution_logs el ON el.id = bm.log_id
            JOIN prompts p ON p.id = el.prompt_id
            WHERE LOWER(bm.brand_name) LIKE '%%grabon%%'
              AND bm.sentiment IS NOT NULL
              {tier_filter}
            GROUP BY el.engine_name, bm.sentiment
            ORDER BY el.engine_name, bm.sentiment
        """, params).fetchall()

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


@router.get("/analytics/category-rollup")
async def analytics_category_rollup():
    """Per-category aggregate: keywords, mentions, avg rank, #1 count, sentiment, citations."""
    def _query(conn):
        rows = conn.execute("""
            WITH latest AS (
                SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                    el.prompt_id, el.engine_name, el.id AS log_id
                FROM execution_logs el
                WHERE el.raw_response_text NOT LIKE 'Error:%%'
                ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
            )
            SELECT
                COALESCE(p.merchant_category, 'Uncategorized') AS category,
                COUNT(DISTINCT p.id) AS total_keywords,
                COUNT(bm.id) AS total_mentions,
                COUNT(CASE WHEN bm.rank_position = 1 THEN 1 END) AS rank_1,
                COUNT(CASE WHEN bm.rank_position <= 3 THEN 1 END) AS top_3,
                ROUND(AVG(bm.rank_position)::numeric, 1) AS avg_rank,
                COUNT(CASE WHEN bm.sentiment = 'Positive' THEN 1 END) AS positive,
                COUNT(CASE WHEN bm.sentiment IS NOT NULL THEN 1 END) AS sentiment_total,
                COUNT(CASE WHEN bm.cited_url IS NOT NULL AND bm.cited_url <> '' THEN 1 END) AS citations
            FROM prompts p
            JOIN latest l ON l.prompt_id = p.id
            LEFT JOIN brand_mentions bm ON bm.log_id = l.log_id
                AND (LOWER(bm.brand_name) LIKE '%%grabon%%' OR LOWER(bm.brand_name) LIKE '%%grab on%%')
            GROUP BY COALESCE(p.merchant_category, 'Uncategorized')
            ORDER BY COUNT(DISTINCT p.id) DESC
        """).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("avg_rank") is not None:
                d["avg_rank"] = float(d["avg_rank"])
            result.append(d)
        return result
    return {"data": await run_db(_query)}


@router.get("/analytics/needs-attention")
async def analytics_needs_attention(page: int = Query(1), page_size: int = Query(50)):
    """Keywords where competitors outrank GrabOn or GrabOn absent, sorted by impact."""
    def _query(conn):
        rows = conn.execute("""
            WITH latest AS (
                SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                    el.prompt_id, el.engine_name, el.id AS log_id
                FROM execution_logs el
                WHERE el.raw_response_text NOT LIKE 'Error:%%'
                ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
            ),
            grabon_ranks AS (
                SELECT l.prompt_id, l.engine_name, bm.rank_position
                FROM latest l
                LEFT JOIN brand_mentions bm ON bm.log_id = l.log_id
                    AND (LOWER(bm.brand_name) LIKE '%%grabon%%' OR LOWER(bm.brand_name) LIKE '%%grab on%%')
            ),
            competitor_leaders AS (
                SELECT l.prompt_id, l.engine_name,
                       MIN(bm.rank_position) AS best_rank,
                       (array_agg(bm.brand_name ORDER BY bm.rank_position))[1] AS leader
                FROM latest l
                JOIN brand_mentions bm ON bm.log_id = l.log_id
                    AND NOT (LOWER(bm.brand_name) LIKE '%%grabon%%' OR LOWER(bm.brand_name) LIKE '%%grab on%%')
                GROUP BY l.prompt_id, l.engine_name
            )
            SELECT
                p.id AS prompt_id, p.text AS keyword, p.tier,
                COALESCE(p.merchant_category, 'Uncategorized') AS category,
                g.engine_name, g.rank_position AS grabon_rank,
                c.best_rank AS competitor_rank, c.leader AS competitor_leader,
                CASE
                    WHEN g.rank_position IS NULL THEN (4 - p.tier) * 10
                    ELSE (4 - p.tier) * (g.rank_position - COALESCE(c.best_rank, g.rank_position))
                END AS impact
            FROM grabon_ranks g
            JOIN prompts p ON p.id = g.prompt_id
            LEFT JOIN competitor_leaders c ON c.prompt_id = g.prompt_id AND c.engine_name = g.engine_name
            WHERE c.best_rank IS NOT NULL
              AND (g.rank_position IS NULL OR c.best_rank < g.rank_position)
            ORDER BY CASE
                WHEN g.rank_position IS NULL THEN (4 - p.tier) * 10
                ELSE (4 - p.tier) * (g.rank_position - COALESCE(c.best_rank, g.rank_position))
            END DESC
        """).fetchall()

        kw_map = {}
        for r in rows:
            key = str(r["prompt_id"])
            if key not in kw_map:
                kw_map[key] = {
                    "keyword": r["keyword"], "tier": r["tier"], "category": r["category"],
                    "engines": [], "max_impact": 0,
                }
            kw_map[key]["engines"].append({
                "engine": r["engine_name"],
                "grabon_rank": r["grabon_rank"],
                "competitor_rank": r["competitor_rank"],
                "competitor": r["competitor_leader"],
            })
            kw_map[key]["max_impact"] = max(kw_map[key]["max_impact"], r["impact"] or 0)

        sorted_kws = sorted(kw_map.values(), key=lambda x: -x["max_impact"])
        total = len(sorted_kws)
        start = (page - 1) * page_size
        return {
            "items": sorted_kws[start:start + page_size],
            "total": total,
            "page": page,
            "total_pages": (total + page_size - 1) // page_size,
        }
    return await run_db(_query)


@router.get("/analytics/engine-matrix")
async def analytics_engine_matrix():
    """Category x Engine matrix: mentions, rank, #1 count, sentiment, citations."""
    def _query(conn):
        rows = conn.execute("""
            WITH latest AS (
                SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                    el.prompt_id, el.engine_name, el.id AS log_id
                FROM execution_logs el
                WHERE el.raw_response_text NOT LIKE 'Error:%%'
                ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
            )
            SELECT
                COALESCE(p.merchant_category, 'Uncategorized') AS category,
                l.engine_name,
                COUNT(DISTINCT p.id) AS total_keywords,
                COUNT(bm.id) AS mentions,
                COUNT(CASE WHEN bm.rank_position = 1 THEN 1 END) AS rank_1,
                COUNT(CASE WHEN bm.rank_position <= 3 THEN 1 END) AS top_3,
                ROUND(AVG(bm.rank_position)::numeric, 1) AS avg_rank,
                COUNT(CASE WHEN bm.sentiment = 'Positive' THEN 1 END) AS positive,
                COUNT(CASE WHEN bm.cited_url IS NOT NULL AND bm.cited_url <> '' THEN 1 END) AS citations
            FROM prompts p
            JOIN latest l ON l.prompt_id = p.id
            LEFT JOIN brand_mentions bm ON bm.log_id = l.log_id
                AND (LOWER(bm.brand_name) LIKE '%%grabon%%' OR LOWER(bm.brand_name) LIKE '%%grab on%%')
            GROUP BY COALESCE(p.merchant_category, 'Uncategorized'), l.engine_name
            ORDER BY COALESCE(p.merchant_category, 'Uncategorized'), l.engine_name
        """).fetchall()

        engine_totals = {}
        for r in rows:
            eng = r["engine_name"]
            if eng not in engine_totals:
                engine_totals[eng] = {"total_keywords": 0, "mentions": 0, "rank_1": 0, "top_3": 0, "avg_ranks": [], "positive": 0, "citations": 0}
            engine_totals[eng]["total_keywords"] += r["total_keywords"]
            engine_totals[eng]["mentions"] += r["mentions"]
            engine_totals[eng]["rank_1"] += r["rank_1"]
            engine_totals[eng]["top_3"] += r["top_3"]
            if r["avg_rank"]:
                engine_totals[eng]["avg_ranks"].append((float(r["avg_rank"]), r["total_keywords"]))
            engine_totals[eng]["positive"] += r["positive"]
            engine_totals[eng]["citations"] += r["citations"]

        for eng, d in engine_totals.items():
            if d["avg_ranks"]:
                total_w = sum(w for _, w in d["avg_ranks"])
                d["avg_rank"] = round(sum(v * w for v, w in d["avg_ranks"]) / total_w, 1) if total_w else None
            else:
                d["avg_rank"] = None
            del d["avg_ranks"]

        matrix = []
        for r in rows:
            d = dict(r)
            if d.get("avg_rank") is not None:
                d["avg_rank"] = float(d["avg_rank"])
            matrix.append(d)

        return {
            "matrix": matrix,
            "engine_totals": engine_totals,
        }
    return await run_db(_query)


@router.get("/analytics/daily-summary")
async def analytics_daily_summary(days: int = Query(30)):
    """Day-by-day aggregate stats for the analytics timeline."""
    def _query(conn):
        rows = conn.execute("""
            SELECT
                el.captured_at::date::text AS day,
                COUNT(DISTINCT el.prompt_id) AS keywords_scraped,
                COUNT(DISTINCT el.id) AS total_scrapes,
                COUNT(bm.id) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%') AS grabon_mentions,
                COUNT(bm.id) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%' AND bm.rank_position = 1) AS rank_1_count,
                ROUND(AVG(bm.rank_position) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%'), 1) AS avg_rank,
                COUNT(bm.id) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%' AND bm.sentiment = 'Positive') AS positive,
                COUNT(bm.id) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%' AND bm.sentiment = 'Neutral') AS neutral,
                COUNT(bm.id) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%' AND bm.sentiment = 'Negative') AS negative,
                COUNT(bm.id) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%'
                    AND bm.cited_url IS NOT NULL AND bm.cited_url != '') AS citations,
                COUNT(DISTINCT bm.brand_name) AS unique_brands
            FROM execution_logs el
            LEFT JOIN brand_mentions bm ON bm.log_id = el.id
            WHERE el.captured_at >= CURRENT_DATE - make_interval(days => %s)
              AND el.raw_response_text NOT LIKE 'Error:%%'
            GROUP BY day
            ORDER BY day DESC
        """, (days,)).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("avg_rank") is not None:
                d["avg_rank"] = float(d["avg_rank"])
            result.append(d)
        return result
    return {"data": await run_db(_query)}


@router.get("/analytics/keyword-detail")
async def analytics_keyword_detail(prompt_id: str = Query(...)):
    """Full history for one keyword: per-day per-engine, all brands, rank changes."""
    if not _valid_uuid(prompt_id):
        return JSONResponse({"error": "Invalid prompt_id"}, status_code=400)
    from app.agent.scraper import VISIBLE_ENGINES

    def _query(conn):
        prompt = conn.execute(
            "SELECT text, merchant_category, tier, intent_type FROM prompts WHERE id = %s::uuid",
            (prompt_id,),
        ).fetchone()
        if not prompt:
            return {"error": "Prompt not found"}

        rows = conn.execute("""
            SELECT
                el.captured_at::date::text AS day,
                el.engine_name,
                bm.brand_name,
                bm.rank_position,
                bm.sentiment,
                bm.cited_url
            FROM execution_logs el
            JOIN brand_mentions bm ON bm.log_id = el.id
            WHERE el.prompt_id = %s::uuid
              AND el.raw_response_text NOT LIKE 'Error:%%'
            ORDER BY el.captured_at::date DESC, el.engine_name, bm.rank_position
        """, (prompt_id,)).fetchall()

        days_data = {}
        for r in rows:
            day = r["day"]
            eng = r["engine_name"]
            days_data.setdefault(day, {}).setdefault(eng, []).append({
                "brand": r["brand_name"],
                "rank": r["rank_position"],
                "sentiment": r["sentiment"],
                "cited": bool(r["cited_url"]),
            })

        timeline = []
        for day in sorted(days_data.keys(), reverse=True):
            day_entry = {"day": day, "engines": {}}
            for eng in VISIBLE_ENGINES:
                brands = days_data.get(day, {}).get(eng, [])
                grabon = [b for b in brands if "grabon" in b["brand"].lower()]
                day_entry["engines"][eng] = {
                    "grabon_rank": grabon[0]["rank"] if grabon else None,
                    "grabon_sentiment": grabon[0]["sentiment"] if grabon else None,
                    "leader": brands[0]["brand"] if brands else None,
                    "leader_rank": brands[0]["rank"] if brands else None,
                    "total_brands": len(brands),
                    "brands": brands[:5],
                }
            timeline.append(day_entry)

        return {
            "prompt": dict(prompt),
            "timeline": timeline,
            "engines": VISIBLE_ENGINES,
        }

    return await run_db(_query)


@router.get("/analytics/keyword-search")
async def analytics_keyword_search(q: str = Query(""), limit: int = Query(20)):
    """Search keywords for the analytics drill-down."""
    def _query(conn):
        if q:
            rows = conn.execute(
                "SELECT id, text, merchant_category, tier FROM prompts WHERE LOWER(text) LIKE %s ORDER BY tier, text LIMIT %s",
                (f"%{q.lower()}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, text, merchant_category, tier FROM prompts ORDER BY last_run_at DESC NULLS LAST LIMIT %s",
                (limit,),
            ).fetchall()
        return [{"id": str(r["id"]), "text": r["text"], "category": r["merchant_category"], "tier": r["tier"]} for r in rows]
    return {"results": await run_db(_query)}


@router.get("/engine-cooldowns")
async def engine_cooldowns():
    from app.agent.pipeline import get_engine_cooldowns
    return {"cooldowns": get_engine_cooldowns()}


@router.post("/engine-cooldowns/{engine_name}/reset")
async def reset_engine_cooldown(engine_name: str):
    from app.agent.pipeline import clear_engine_cooldown
    from app.agent.scraper import ALL_ENGINES
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

        if recent_success_streak >= 3:
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


# --- SERP Endpoints ---

@router.get("/serp/latest")
async def serp_latest(prompt_id: str | None = None, limit: int = 20):
    """Get latest SERP snapshots, optionally filtered by prompt."""
    def _q(conn):
        if prompt_id:
            return conn.execute(
                """SELECT sr.id, sr.prompt_id, sr.device, sr.results_count, sr.captured_at,
                          p.text as keyword,
                          (SELECT COUNT(*) FROM serp_organic_entries WHERE serp_id = sr.id) as organic_count
                   FROM serp_results sr
                   JOIN prompts p ON sr.prompt_id = p.id
                   WHERE sr.prompt_id = %s::uuid
                   ORDER BY sr.captured_at DESC LIMIT %s""",
                (prompt_id, limit),
            ).fetchall()
        return conn.execute(
            """SELECT sr.id, sr.prompt_id, sr.device, sr.results_count, sr.captured_at,
                      p.text as keyword,
                      (SELECT COUNT(*) FROM serp_organic_entries WHERE serp_id = sr.id) as organic_count
               FROM serp_results sr
               JOIN prompts p ON sr.prompt_id = p.id
               ORDER BY sr.captured_at DESC LIMIT %s""",
            (limit,),
        ).fetchall()
    rows = await run_db(_q)
    return [dict(r) for r in rows]


@router.get("/serp/{serp_id}/organic")
async def serp_organic(serp_id: str):
    """Get organic results for a SERP snapshot."""
    rows = await run_db(lambda conn: conn.execute(
        """SELECT rank_position, title, snippet, url, domain, has_table, is_target
           FROM serp_organic_entries
           WHERE serp_id = %s::uuid
           ORDER BY rank_position""",
        (serp_id,),
    ).fetchall())
    return [dict(r) for r in rows]


@router.get("/serp/{serp_id}/features")
async def serp_features(serp_id: str):
    """Get SERP features (ads, PAA, related keywords) for a snapshot."""
    rows = await run_db(lambda conn: conn.execute(
        """SELECT feature_type, rank_position, title, snippet, url, domain, question_text
           FROM serp_features
           WHERE serp_id = %s::uuid
           ORDER BY feature_type, rank_position""",
        (serp_id,),
    ).fetchall())
    return [dict(r) for r in rows]


@router.get("/serp/rank-history")
async def serp_rank_history(domain: str | None = None, prompt_id: str | None = None, days: int = 30):
    """Track organic rank position over time for a domain across keywords."""
    target = domain or get_settings().target_domain
    def _q(conn):
        base = """
            SELECT sr.captured_at::date as date, p.text as keyword,
                   soe.rank_position, soe.domain
            FROM serp_organic_entries soe
            JOIN serp_results sr ON soe.serp_id = sr.id
            JOIN prompts p ON sr.prompt_id = p.id
            WHERE soe.domain ILIKE %s
              AND sr.captured_at >= NOW() - make_interval(days => %s)
        """
        params = [f"%{target}%", days]
        if prompt_id:
            base += " AND sr.prompt_id = %s::uuid"
            params.append(prompt_id)
        base += " ORDER BY sr.captured_at"
        return conn.execute(base, params).fetchall()
    rows = await run_db(_q)
    return [dict(r) for r in rows]


@router.post("/serp/trigger")
async def serp_trigger(prompt_id: str | None = None):
    """Trigger on-demand SERP crawl for a specific prompt or all."""
    from app.agent.serp_scheduler import run_serp_for_prompt
    from app.models import PromptItem

    if prompt_id:
        row = await run_db(lambda conn: conn.execute(
            "SELECT id, text, merchant_category, intent_type FROM prompts WHERE id = %s::uuid",
            (prompt_id,),
        ).fetchone())
        if not row:
            return {"error": "Prompt not found"}
        prompt = PromptItem(
            id=str(row["id"]), text=row["text"],
            merchant_category=row["merchant_category"], intent_type=row["intent_type"],
        )
        asyncio.ensure_future(run_serp_for_prompt(prompt))
        return {"status": "SERP crawl triggered", "keyword": prompt.text}

    return {"status": "Use prompt_id param to trigger SERP crawl for specific keyword"}


# --- Citation Analysis Endpoints ---

@router.get("/analytics/citation-overlap")
async def citation_overlap(prompt_id: str | None = None, days: int = 7):
    """Show which AI-cited URLs also rank in organic SERP."""
    def _q(conn):
        base = """
            SELECT cso.cited_url, cso.engine_name, cso.serp_rank, cso.detected_at,
                   p.text as keyword, bm.brand_name, bm.sentiment, bm.rank_position as ai_rank
            FROM citation_serp_overlaps cso
            JOIN prompts p ON cso.prompt_id = p.id
            LEFT JOIN brand_mentions bm ON cso.brand_mention_id = bm.id
            WHERE cso.detected_at >= NOW() - make_interval(days => %s)
        """
        params = [days]
        if prompt_id:
            base += " AND cso.prompt_id = %s::uuid"
            params.append(prompt_id)
        base += " ORDER BY cso.detected_at DESC LIMIT 200"
        return conn.execute(base, params).fetchall()
    rows = await run_db(_q)
    return [dict(r) for r in rows]


@router.get("/analytics/citation-content")
async def citation_content(url: str):
    """Get fetched page analysis for a given URL."""
    row = await run_db(lambda conn: conn.execute(
        """SELECT url, domain, title, meta_description, h1_tags, word_count,
                  main_text_preview, schema_types, canonical_url, fetch_status, fetched_at
           FROM citation_pages WHERE url = %s""",
        (url,),
    ).fetchone())
    if not row:
        return {"error": "URL not in cache. Trigger a citation analysis first."}
    return dict(row)


@router.post("/analytics/citation-analyze")
async def citation_analyze(prompt_id: str):
    """Run citation overlap detection and content comparison for a prompt."""
    from app.agent.content_comparator import build_citation_overlaps, analyze_citations_for_prompt

    row = await run_db(lambda conn: conn.execute(
        "SELECT id, text FROM prompts WHERE id = %s::uuid", (prompt_id,),
    ).fetchone())
    if not row:
        return {"error": "Prompt not found"}

    await build_citation_overlaps(prompt_id)

    results = await analyze_citations_for_prompt(prompt_id, row["text"])
    return {
        "keyword": row["text"],
        "comparisons": [r.model_dump() for r in results],
        "count": len(results),
    }


@router.get("/analytics/citation-summary")
async def citation_summary():
    """High-level citation overlap stats."""
    def _q(conn):
        return conn.execute("""
            SELECT
                COUNT(DISTINCT cited_url) as total_cited_urls,
                COUNT(*) FILTER (WHERE serp_rank IS NOT NULL) as urls_in_serp,
                COUNT(*) FILTER (WHERE serp_rank IS NULL) as urls_not_in_serp,
                COUNT(*) FILTER (WHERE serp_rank <= 3) as urls_in_top3,
                COUNT(DISTINCT engine_name) as engines_citing,
                COUNT(DISTINCT prompt_id) as keywords_with_citations
            FROM citation_serp_overlaps
            WHERE detected_at >= NOW() - INTERVAL '7 days'
        """).fetchone()
    row = await run_db(_q)
    return dict(row) if row else {}


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


@router.get("/analytics/diagnosis-summary")
async def diagnosis_summary():
    """Aggregated diagnosis stats."""
    def _q(conn):
        return conn.execute("""
            SELECT
                COUNT(*) as total_diagnoses,
                COUNT(*) FILTER (WHERE status = 'active') as active,
                COUNT(*) FILTER (WHERE priority = 'critical') as critical,
                COUNT(*) FILTER (WHERE priority = 'high') as high,
                COUNT(*) FILTER (WHERE priority = 'medium') as medium,
                COUNT(*) FILTER (WHERE priority = 'low') as low,
                COUNT(DISTINCT prompt_id) as keywords_diagnosed,
                COALESCE(SUM(cost_usd), 0) as total_cost,
                AVG(confidence) as avg_confidence
            FROM diagnoses
            WHERE status = 'active'
        """).fetchone()
    row = await run_db(_q)
    return dict(row) if row else {}


@router.get("/serp/movement-cards")
async def serp_movement_cards():
    """Movement summary: first page movers, dropped, striking distance, lost."""

    def _q(conn):
        rows = conn.execute("""
            WITH latest AS (
                SELECT DISTINCT ON (prompt_id)
                    id AS serp_id, prompt_id, captured_at
                FROM serp_results ORDER BY prompt_id, captured_at DESC
            ),
            previous AS (
                SELECT DISTINCT ON (sr.prompt_id)
                    sr.id AS serp_id, sr.prompt_id
                FROM serp_results sr
                JOIN latest l ON sr.prompt_id = l.prompt_id AND sr.captured_at < l.captured_at
                ORDER BY sr.prompt_id, sr.captured_at DESC
            ),
            curr AS (
                SELECT l.prompt_id, soe.rank_position AS rank_now
                FROM latest l
                LEFT JOIN serp_organic_entries soe ON soe.serp_id = l.serp_id AND soe.is_target = TRUE
            ),
            prev AS (
                SELECT p.prompt_id, soe.rank_position AS rank_prev
                FROM previous p
                LEFT JOIN serp_organic_entries soe ON soe.serp_id = p.serp_id AND soe.is_target = TRUE
            )
            SELECT c.prompt_id, c.rank_now, p.rank_prev,
                   pr.text AS keyword
            FROM curr c
            LEFT JOIN prev p ON c.prompt_id = p.prompt_id
            JOIN prompts pr ON c.prompt_id = pr.id
        """).fetchall()

        first_page = []
        dropped = []
        striking = []
        lost = []
        improved = 0
        declined = 0

        for r in rows:
            now = r["rank_now"]
            prev = r["rank_prev"]
            kw = r["keyword"]

            if now and now <= 10 and (prev is None or prev > 10):
                first_page.append({"keyword": kw, "rank": now, "prev": prev})
            if prev and now and now > prev and now - prev >= 3:
                dropped.append({"keyword": kw, "rank": now, "prev": prev, "delta": now - prev})
            if now and 11 <= now <= 20:
                striking.append({"keyword": kw, "rank": now})
            if prev and prev <= 50 and now is None:
                lost.append({"keyword": kw, "prev": prev})
            if now and prev:
                if now < prev:
                    improved += 1
                elif now > prev:
                    declined += 1

        return {
            "first_page": sorted(first_page, key=lambda x: x["rank"])[:10],
            "first_page_count": len(first_page),
            "dropped": sorted(dropped, key=lambda x: -x["delta"])[:10],
            "dropped_count": len(dropped),
            "striking_distance": sorted(striking, key=lambda x: x["rank"])[:10],
            "striking_distance_count": len(striking),
            "lost": lost[:10],
            "lost_count": len(lost),
            "improved_count": improved,
            "declined_count": declined,
            "total_tracked": len(rows),
        }
    result = await run_db(_q)
    return result


@router.get("/serp/rank-distribution-seo")
async def serp_rank_distribution_seo():
    """SEO rank distribution: how many keywords rank in each bucket."""

    def _q(conn):
        rows = conn.execute("""
            WITH latest AS (
                SELECT DISTINCT ON (prompt_id)
                    id AS serp_id, prompt_id
                FROM serp_results ORDER BY prompt_id, captured_at DESC
            )
            SELECT
                CASE
                    WHEN soe.rank_position BETWEEN 1 AND 3 THEN 'Top 3'
                    WHEN soe.rank_position BETWEEN 4 AND 10 THEN '4-10'
                    WHEN soe.rank_position BETWEEN 11 AND 20 THEN '11-20'
                    WHEN soe.rank_position BETWEEN 21 AND 50 THEN '21-50'
                    WHEN soe.rank_position > 50 THEN '50+'
                END AS bucket,
                COUNT(*) AS cnt
            FROM latest l
            LEFT JOIN serp_organic_entries soe
                ON soe.serp_id = l.serp_id AND soe.is_target = TRUE
            WHERE soe.rank_position IS NOT NULL
            GROUP BY bucket
        """).fetchall()

        not_ranked = conn.execute("""
            WITH latest AS (
                SELECT DISTINCT ON (prompt_id) id AS serp_id, prompt_id
                FROM serp_results ORDER BY prompt_id, captured_at DESC
            )
            SELECT COUNT(*) AS cnt FROM latest l
            WHERE NOT EXISTS (
                SELECT 1 FROM serp_organic_entries soe
                WHERE soe.serp_id = l.serp_id AND soe.is_target = TRUE
            )
        """).fetchone()

        result = {r["bucket"]: r["cnt"] for r in rows}
        result["Not Ranked"] = not_ranked["cnt"] if not_ranked else 0
        return result
    result = await run_db(_q)
    return result


@router.get("/serp/top-competitors")
async def serp_top_competitors(limit: int = 15):
    """Top competing domains by keyword count from SERP organic entries."""
    target = get_settings().target_domain
    def _q(conn):
        rows = conn.execute("""
            WITH latest AS (
                SELECT DISTINCT ON (prompt_id)
                    id AS serp_id, prompt_id
                FROM serp_results ORDER BY prompt_id, captured_at DESC
            )
            SELECT soe.domain,
                   COUNT(DISTINCT l.prompt_id) AS keyword_count,
                   ROUND(AVG(soe.rank_position)::numeric, 1) AS avg_rank,
                   COUNT(*) FILTER (WHERE soe.rank_position <= 3) AS top3_count,
                   COUNT(*) FILTER (WHERE soe.rank_position <= 10) AS top10_count
            FROM latest l
            JOIN serp_organic_entries soe ON soe.serp_id = l.serp_id
            WHERE soe.domain IS NOT NULL
              AND soe.domain NOT ILIKE %s
            GROUP BY soe.domain
            ORDER BY keyword_count DESC
            LIMIT %s
        """, (f"%{target}%", limit)).fetchall()
        return [dict(r) for r in rows]
    result = await run_db(_q)
    return result


@router.get("/serp/feature-summary")
async def serp_feature_summary():
    """SERP feature type counts across all latest snapshots."""

    def _q(conn):
        rows = conn.execute("""
            WITH latest AS (
                SELECT DISTINCT ON (prompt_id)
                    id AS serp_id, prompt_id
                FROM serp_results ORDER BY prompt_id, captured_at DESC
            )
            SELECT sf.feature_type, COUNT(DISTINCT l.prompt_id) AS keyword_count
            FROM latest l
            JOIN serp_features sf ON sf.serp_id = l.serp_id
            GROUP BY sf.feature_type
            ORDER BY keyword_count DESC
        """).fetchall()
        return [dict(r) for r in rows]
    result = await run_db(_q)
    return result


@router.get("/serp/rankings")
async def serp_rankings_paginated(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=200),
    search: str = Query(""),
    category: str = Query("All"),
    rank_filter: str = Query("all"),
):
    """Paginated SEO rankings table data."""

    settings = get_settings()
    target_domain = settings.target_domain

    def _q(conn):
        where_parts = []
        params = []
        if search:
            where_parts.append("LOWER(p.text) LIKE %s")
            params.append(f"%{search.lower()}%")
        if category != "All":
            where_parts.append("p.merchant_category = %s")
            params.append(category)
        where = " AND ".join(where_parts) if where_parts else "1=1"

        base_query = f"""
            WITH latest_serp AS (
                SELECT DISTINCT ON (sr.prompt_id) sr.id as serp_id, sr.prompt_id, sr.captured_at
                FROM serp_results sr ORDER BY sr.prompt_id, sr.captured_at DESC
            ),
            target_organic AS (
                SELECT ls.prompt_id, MIN(soe.rank_position) as serp_rank
                FROM latest_serp ls
                JOIN serp_organic_entries soe ON soe.serp_id = ls.serp_id
                WHERE soe.domain = %s OR soe.domain LIKE %s
                GROUP BY ls.prompt_id
            ),
            top_organic AS (
                SELECT DISTINCT ON (ls.prompt_id) ls.prompt_id, soe.domain as top_domain
                FROM latest_serp ls
                JOIN serp_organic_entries soe ON soe.serp_id = ls.serp_id
                WHERE soe.rank_position = 1
                ORDER BY ls.prompt_id, soe.rank_position
            ),
            top3_organic AS (
                SELECT ls.prompt_id,
                       json_agg(json_build_object('rank', soe.rank_position, 'domain', soe.domain)
                                ORDER BY soe.rank_position) as top_competitors
                FROM latest_serp ls
                JOIN serp_organic_entries soe ON soe.serp_id = ls.serp_id
                WHERE soe.rank_position <= 5
                  AND soe.domain <> %s AND NOT soe.domain LIKE %s
                GROUP BY ls.prompt_id
            ),
            ai_rank AS (
                SELECT DISTINCT ON (el.prompt_id)
                    el.prompt_id, bm.rank_position as ai_rank
                FROM execution_logs el
                JOIN brand_mentions bm ON bm.log_id = el.id
                WHERE LOWER(bm.brand_name) LIKE '%%grabon%%'
                ORDER BY el.prompt_id, el.captured_at DESC
            ),
            ai_cited AS (
                SELECT DISTINCT ON (el.prompt_id)
                    el.prompt_id, TRUE as cited_in_ai
                FROM execution_logs el
                JOIN brand_mentions bm ON bm.log_id = el.id
                WHERE LOWER(bm.brand_name) LIKE '%%grabon%%'
                  AND bm.cited_url IS NOT NULL AND bm.cited_url != ''
                ORDER BY el.prompt_id, el.captured_at DESC
            ),
            prev_serp AS (
                SELECT DISTINCT ON (sr.prompt_id)
                    sr.id as serp_id, sr.prompt_id
                FROM serp_results sr
                JOIN latest_serp ls ON sr.prompt_id = ls.prompt_id AND sr.captured_at < ls.captured_at
                ORDER BY sr.prompt_id, sr.captured_at DESC
            ),
            prev_target AS (
                SELECT ps.prompt_id, MIN(soe.rank_position) as prev_rank
                FROM prev_serp ps
                JOIN serp_organic_entries soe ON soe.serp_id = ps.serp_id
                WHERE soe.domain = %s OR soe.domain LIKE %s
                GROUP BY ps.prompt_id
            ),
            combined AS (
                SELECT p.id::text as prompt_id, p.text as keyword, p.merchant_category as category,
                       ls.serp_id::text as serp_id,
                       tgt.serp_rank, top.top_domain,
                       ls.captured_at::text as crawled_at,
                       pt.prev_rank,
                       t3.top_competitors
                FROM prompts p
                LEFT JOIN latest_serp ls ON ls.prompt_id = p.id
                LEFT JOIN target_organic tgt ON tgt.prompt_id = p.id
                LEFT JOIN top_organic top ON top.prompt_id = p.id
                LEFT JOIN top3_organic t3 ON t3.prompt_id = p.id
                LEFT JOIN prev_target pt ON pt.prompt_id = p.id
                WHERE {where}
            )
        """
        all_params = [target_domain, f"%.{target_domain}", target_domain, f"%.{target_domain}", target_domain, f"%.{target_domain}"] + params

        rank_where = ""
        if rank_filter == "top10":
            rank_where = "WHERE serp_rank IS NOT NULL AND serp_rank <= 10"
        elif rank_filter == "top20":
            rank_where = "WHERE serp_rank IS NOT NULL AND serp_rank <= 20"
        elif rank_filter == "ranked":
            rank_where = "WHERE serp_rank IS NOT NULL"
        elif rank_filter == "unranked":
            rank_where = "WHERE serp_rank IS NULL"

        count_q = base_query + f"SELECT COUNT(*) as cnt FROM combined {rank_where}"
        total = conn.execute(count_q, all_params).fetchone()["cnt"]

        offset = (page - 1) * page_size
        data_q = base_query + f"""
            SELECT * FROM combined {rank_where}
            ORDER BY crawled_at DESC NULLS LAST, keyword
            LIMIT %s OFFSET %s
        """
        rows = conn.execute(data_q, all_params + [page_size, offset]).fetchall()

        result_rows = []
        for r in rows:
            row = dict(r)
            tc = row.get("top_competitors")
            if isinstance(tc, str):
                try:
                    row["top_competitors"] = json.loads(tc)
                except Exception:
                    row["top_competitors"] = []
            elif tc is None:
                row["top_competitors"] = []
            result_rows.append(row)

        return {
            "rows": result_rows,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
        }
    result = await run_db(_q)
    return result


@router.get("/serp/keyword-detail")
async def serp_keyword_detail(prompt_id: str = Query(...)):
    """Full SERP snapshot for a keyword: organic results, features, competitor positions."""
    if not _valid_uuid(prompt_id):
        return JSONResponse({"error": "Invalid prompt_id"}, status_code=400)
    settings = get_settings()
    target_domain = settings.target_domain

    def _q(conn):
        prompt = conn.execute(
            "SELECT id, text, merchant_category FROM prompts WHERE id = %s::uuid",
            (prompt_id,),
        ).fetchone()
        if not prompt:
            return {"error": "Keyword not found"}

        serp = conn.execute("""
            SELECT id, captured_at::text as captured_at, results_count, device
            FROM serp_results WHERE prompt_id = %s::uuid
            ORDER BY captured_at DESC LIMIT 1
        """, (prompt_id,)).fetchone()
        if not serp:
            return {"keyword": prompt["text"], "category": prompt["merchant_category"],
                    "organic": [], "features": [], "serp_at": None}

        organic = conn.execute("""
            SELECT rank_position, title, snippet, url, domain, has_table, is_target
            FROM serp_organic_entries WHERE serp_id = %s::uuid
            ORDER BY rank_position
        """, (serp["id"],)).fetchall()

        features = conn.execute("""
            SELECT feature_type, rank_position, title, snippet, url, domain, question_text
            FROM serp_features WHERE serp_id = %s::uuid
            ORDER BY feature_type, rank_position
        """, (serp["id"],)).fetchall()

        target_rank = None
        for o in organic:
            if o["is_target"]:
                target_rank = o["rank_position"]
                break

        known_coupon_domains = [
            "grabon.in", "coupondunia.in", "cashkaro.com", "desidime.com",
            "gopaisa.com", "zoutons.com", "couponsly.in", "couponzguru.com",
            "dealsshutter.com", "grabdeals.in",
        ]
        competitors = []
        for o in organic:
            is_comp = any(cd in (o["domain"] or "").lower() for cd in known_coupon_domains)
            if is_comp and not o["is_target"]:
                competitors.append({
                    "domain": o["domain"], "rank": o["rank_position"],
                    "title": o["title"],
                })

        return {
            "keyword": prompt["text"],
            "category": prompt["merchant_category"],
            "serp_at": serp["captured_at"],
            "results_count": serp["results_count"],
            "target_rank": target_rank,
            "organic": [dict(o) for o in organic],
            "features": [dict(f) for f in features],
            "competitors": competitors,
        }
    return await run_db(_q)


@router.get("/serp/overlaps")
async def serp_overlaps_api(limit: int = Query(30)):
    """Citation-SERP overlap data with live rank lookup."""

    def _q(conn):
        overlaps = conn.execute("""
            WITH latest_serp AS (
                SELECT DISTINCT ON (prompt_id) id AS serp_id, prompt_id
                FROM serp_results ORDER BY prompt_id, captured_at DESC
            )
            SELECT cso.cited_url, cso.engine_name, cso.serp_rank,
                   p.text AS keyword, cso.prompt_id,
                   soe.rank_position AS live_serp_rank, soe.domain AS serp_domain
            FROM citation_serp_overlaps cso
            JOIN prompts p ON cso.prompt_id = p.id
            LEFT JOIN latest_serp ls ON ls.prompt_id = cso.prompt_id
            LEFT JOIN serp_organic_entries soe ON soe.serp_id = ls.serp_id
                AND LOWER(soe.domain) = LOWER(SPLIT_PART(
                    REPLACE(REPLACE(cso.cited_url, 'https://', ''), 'http://', ''),
                    '/', 1
                ))
            ORDER BY cso.detected_at DESC LIMIT %s
        """, (limit,)).fetchall()

        result_overlaps = []
        seen = set()
        for r in overlaps:
            key = (r["cited_url"], r["engine_name"], r["keyword"])
            if key in seen:
                continue
            seen.add(key)
            rank = r["live_serp_rank"] or r["serp_rank"]
            result_overlaps.append({
                "cited_url": r["cited_url"],
                "engine_name": r["engine_name"],
                "serp_rank": rank,
                "keyword": r["keyword"],
            })

        rank_history = conn.execute("""
            SELECT sr.captured_at::date::text AS date,
                   ROUND(AVG(soe.rank_position)::numeric, 1) AS rank,
                   COUNT(DISTINCT sr.prompt_id) AS keywords
            FROM serp_organic_entries soe
            JOIN serp_results sr ON soe.serp_id = sr.id
            WHERE soe.domain ILIKE %s
            GROUP BY sr.captured_at::date
            ORDER BY date
        """, (f"%{get_settings().target_domain}%",)).fetchall()

        return {
            "overlaps": result_overlaps,
            "rank_history": [dict(r) for r in rank_history],
        }
    result = await run_db(_q)
    return result


@router.get("/serp/stats")
async def serp_stats():
    """Live SERP scraping stats: speed, curl vs browser, queue depth, KPIs."""
    try:
        from app.agent.serp_scheduler import get_serp_stats
        settings = get_settings()
        stats = get_serp_stats()

        def _q(conn):
            queue = conn.execute("""
                SELECT COUNT(*) as total,
                       COUNT(*) FILTER (WHERE last_serp_at IS NULL) as never_crawled,
                       COUNT(*) FILTER (WHERE last_serp_at < NOW() - INTERVAL '12 hours') as stale,
                       COUNT(*) FILTER (WHERE last_serp_at IS NOT NULL) as crawled
                FROM prompts WHERE intent_type <> 'GEO'
            """).fetchone()

            kpis = conn.execute("""
                WITH latest AS (
                    SELECT DISTINCT ON (sr.prompt_id) sr.id as serp_id, sr.prompt_id
                    FROM serp_results sr
                    ORDER BY sr.prompt_id, sr.captured_at DESC
                ),
                target_ranks AS (
                    SELECT l.prompt_id, MIN(soe.rank_position) as rank
                    FROM latest l
                    JOIN serp_organic_entries soe ON soe.serp_id = l.serp_id
                    WHERE soe.domain = %s OR soe.domain LIKE %s
                    GROUP BY l.prompt_id
                )
                SELECT
                    COUNT(*) FILTER (WHERE rank <= 10) as top10,
                    ROUND(AVG(rank)::numeric, 1) as avg_rank
                FROM target_ranks
            """, (settings.target_domain, f"%.{settings.target_domain}")).fetchone()

            return {**dict(queue), **(dict(kpis) if kpis else {"top10": 0, "avg_rank": None})}

        data = await run_db(_q)

        rate = stats.get("rate_per_hour", 0)
        if rate == 0:
            db_rate = await run_db(lambda conn: conn.execute(
                "SELECT COUNT(*) as cnt FROM serp_results WHERE captured_at >= NOW() - INTERVAL '1 hour'"
            ).fetchone())
            rate = db_rate["cnt"] if db_rate else 0

        result = {
            **stats,
            "rate_per_hour": round(rate, 1),
            "queue_total": data["total"],
            "queue_pending": data["never_crawled"],
            "queue_stale": data["stale"],
            "keywords_crawled": data["crawled"],
            "target_top10": data.get("top10") or 0,
            "avg_rank": float(data["avg_rank"]) if data.get("avg_rank") else None,
        }
        return result
    except Exception as e:
        log.warning(f"serp/stats error: {e}")
        return {"error": str(e), "queue_total": 0, "queue_pending": 0, "queue_stale": 0,
                "keywords_crawled": 0, "target_top10": 0, "avg_rank": None, "rate_per_hour": 0}


@router.get("/serp/activity")
async def serp_activity():
    """Recent SERP crawl activity from execution logs."""
    def _q(conn):
        rows = conn.execute("""
            SELECT sr.prompt_id, p.text as keyword,
                   sr.results_count, sr.captured_at::text as captured_at,
                   (SELECT COUNT(*) FROM serp_organic_entries WHERE serp_id = sr.id) as organic_count,
                   (SELECT MIN(soe.rank_position) FROM serp_organic_entries soe
                    WHERE soe.serp_id = sr.id AND soe.is_target = TRUE) as target_rank
            FROM serp_results sr
            JOIN prompts p ON sr.prompt_id = p.id
            ORDER BY sr.captured_at DESC
            LIMIT 30
        """).fetchall()
        return [dict(r) for r in rows]
    return await run_db(_q)


@router.get("/serp/rank-history-keyword")
async def serp_rank_history_keyword(prompt_id: str = Query(...), days: int = Query(30)):
    """Historical SERP rank positions for a specific keyword over time."""
    if not _valid_uuid(prompt_id):
        return JSONResponse({"error": "Invalid prompt_id"}, status_code=400)
    settings = get_settings()

    def _q(conn):
        rows = conn.execute("""
            SELECT sr.captured_at::text as captured_at,
                   sr.results_count,
                   (SELECT MIN(soe.rank_position)
                    FROM serp_organic_entries soe
                    WHERE soe.serp_id = sr.id AND soe.is_target = TRUE) as target_rank,
                   (SELECT soe.domain FROM serp_organic_entries soe
                    WHERE soe.serp_id = sr.id AND soe.rank_position = 1
                    LIMIT 1) as top_domain
            FROM serp_results sr
            WHERE sr.prompt_id = %s::uuid
              AND sr.captured_at >= NOW() - make_interval(days => %s)
            ORDER BY sr.captured_at
        """, (prompt_id, days)).fetchall()
        return [dict(r) for r in rows]
    return await run_db(_q)


@router.get("/analytics/rank-movement")
async def analytics_rank_movement(days: int = Query(7)):
    """AI rank movement: winners/losers comparing current vs previous scrape per keyword per engine."""

    from app.agent.scraper import VISIBLE_ENGINES

    def _query(conn):
        rows = conn.execute("""
            WITH ranked_scrapes AS (
                SELECT
                    el.prompt_id, el.engine_name, el.captured_at::date AS day,
                    bm.rank_position,
                    ROW_NUMBER() OVER (
                        PARTITION BY el.prompt_id, el.engine_name
                        ORDER BY el.captured_at DESC
                    ) AS rn
                FROM execution_logs el
                JOIN brand_mentions bm ON bm.log_id = el.id
                WHERE LOWER(bm.brand_name) LIKE '%%grabon%%'
                  AND el.captured_at >= CURRENT_DATE - make_interval(days => %s)
            ),
            current AS (
                SELECT prompt_id, engine_name, rank_position AS rank_now, day AS day_now
                FROM ranked_scrapes WHERE rn = 1
            ),
            previous AS (
                SELECT prompt_id, engine_name, rank_position AS rank_prev, day AS day_prev
                FROM ranked_scrapes WHERE rn = 2
            )
            SELECT c.prompt_id, c.engine_name,
                   c.rank_now, p.rank_prev,
                   c.day_now::text AS day_now, p.day_prev::text AS day_prev,
                   pr.text AS keyword, pr.merchant_category AS category
            FROM current c
            LEFT JOIN previous p ON c.prompt_id = p.prompt_id AND c.engine_name = p.engine_name
            JOIN prompts pr ON c.prompt_id = pr.id
            WHERE p.rank_prev IS NOT NULL
            ORDER BY
                CASE WHEN c.rank_now < p.rank_prev THEN 0 ELSE 1 END,
                ABS(c.rank_now - p.rank_prev) DESC
        """, (days,)).fetchall()

        winners = []
        losers = []
        stable = []
        new_entries = []

        for r in rows:
            entry = {
                "keyword": r["keyword"],
                "category": r["category"],
                "engine": r["engine_name"],
                "rank_now": r["rank_now"],
                "rank_prev": r["rank_prev"],
                "change": r["rank_prev"] - r["rank_now"],
            }
            if entry["change"] > 0:
                winners.append(entry)
            elif entry["change"] < 0:
                losers.append(entry)
            else:
                stable.append(entry)

        new_rows = conn.execute("""
            WITH ranked_scrapes AS (
                SELECT
                    el.prompt_id, el.engine_name,
                    bm.rank_position,
                    ROW_NUMBER() OVER (
                        PARTITION BY el.prompt_id, el.engine_name
                        ORDER BY el.captured_at DESC
                    ) AS rn,
                    COUNT(*) OVER (
                        PARTITION BY el.prompt_id, el.engine_name
                    ) AS total
                FROM execution_logs el
                JOIN brand_mentions bm ON bm.log_id = el.id
                WHERE LOWER(bm.brand_name) LIKE '%%grabon%%'
                  AND el.captured_at >= CURRENT_DATE - make_interval(days => %s)
            )
            SELECT rs.prompt_id, rs.engine_name, rs.rank_position,
                   p.text AS keyword, p.merchant_category AS category
            FROM ranked_scrapes rs
            JOIN prompts p ON rs.prompt_id = p.id
            WHERE rs.rn = 1 AND rs.total = 1
            ORDER BY rs.rank_position
        """, (days,)).fetchall()

        for r in new_rows:
            new_entries.append({
                "keyword": r["keyword"],
                "category": r["category"],
                "engine": r["engine_name"],
                "rank": r["rank_position"],
            })

        return {
            "winners": sorted(winners, key=lambda x: -x["change"])[:20],
            "losers": sorted(losers, key=lambda x: x["change"])[:20],
            "stable_count": len(stable),
            "new_entries": new_entries[:20],
            "total_tracked": len(rows) + len(new_rows),
            "summary": {
                "improved": len(winners),
                "declined": len(losers),
                "stable": len(stable),
                "new": len(new_entries),
            },
        }

    result = await run_db(_query)
    return result


@router.get("/analytics/ai-serp-matrix")
async def analytics_ai_serp_matrix(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=200),
    gap_filter: str = Query("all"),
    category: str = Query("All"),
):
    """AI vs SERP overlap matrix: for each keyword, AI rank per engine + SERP rank + gap analysis."""

    settings = get_settings()
    target_domain = settings.target_domain

    def _query(conn):
        where_parts = ["p.intent_type <> 'GEO'"]
        params = []
        if category != "All":
            where_parts.append("p.merchant_category = %s")
            params.append(category)
        where = " AND ".join(where_parts)

        total = conn.execute(
            f"SELECT COUNT(*) as cnt FROM prompts p WHERE {where}", params
        ).fetchone()["cnt"]

        summary_row = conn.execute(f"""
            WITH all_prompts AS (
                SELECT p.id FROM prompts p WHERE {where}
            ),
            ai_ranked AS (
                SELECT DISTINCT el.prompt_id
                FROM execution_logs el
                JOIN brand_mentions bm ON bm.log_id = el.id
                    AND (LOWER(bm.brand_name) LIKE '%%grabon%%' OR LOWER(bm.brand_name) LIKE '%%grab on%%')
                WHERE el.prompt_id IN (SELECT id FROM all_prompts)
                  AND el.raw_response_text NOT LIKE 'Error:%%'
                  AND bm.rank_position IS NOT NULL
            ),
            serp_ranked AS (
                SELECT DISTINCT sr.prompt_id
                FROM serp_results sr
                JOIN serp_organic_entries soe ON soe.serp_id = sr.id
                WHERE sr.prompt_id IN (SELECT id FROM all_prompts)
                  AND (soe.domain = %s OR soe.domain LIKE %s)
                  AND soe.is_target = TRUE
            )
            SELECT
                COUNT(*) FILTER (WHERE ap.id IN (SELECT prompt_id FROM ai_ranked) AND ap.id IN (SELECT prompt_id FROM serp_ranked)) AS both_count,
                COUNT(*) FILTER (WHERE ap.id IN (SELECT prompt_id FROM ai_ranked) AND ap.id NOT IN (SELECT prompt_id FROM serp_ranked)) AS ai_only,
                COUNT(*) FILTER (WHERE ap.id NOT IN (SELECT prompt_id FROM ai_ranked) AND ap.id IN (SELECT prompt_id FROM serp_ranked)) AS serp_only,
                COUNT(*) FILTER (WHERE ap.id NOT IN (SELECT prompt_id FROM ai_ranked) AND ap.id NOT IN (SELECT prompt_id FROM serp_ranked)) AS neither
            FROM all_prompts ap
        """, params + [target_domain, f"%.{target_domain}"]).fetchone()

        offset = (page - 1) * page_size
        prompts = conn.execute(
            f"""SELECT p.id, p.text, p.merchant_category
                FROM prompts p WHERE {where}
                ORDER BY p.text
                LIMIT %s OFFSET %s""",
            params + [page_size, offset],
        ).fetchall()

        prompt_ids = [p["id"] for p in prompts]
        if not prompt_ids:
            return {"rows": [], "total": total, "page": page,
                    "total_pages": (total + page_size - 1) // page_size,
                    "summary": {"ai_and_serp": summary_row["both_count"], "ai_only": summary_row["ai_only"],
                                "serp_only": summary_row["serp_only"], "neither": summary_row["neither"]}}

        ph = ", ".join(["%s"] * len(prompt_ids))

        ai_rows = conn.execute(f"""
            WITH latest AS (
                SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                    el.prompt_id, el.engine_name, el.id AS log_id
                FROM execution_logs el
                WHERE el.prompt_id IN ({ph})
                  AND el.raw_response_text NOT LIKE 'Error:%%'
                ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
            )
            SELECT l.prompt_id, l.engine_name, bm.rank_position
            FROM latest l
            LEFT JOIN brand_mentions bm ON bm.log_id = l.log_id
                AND (LOWER(bm.brand_name) LIKE '%%grabon%%' OR LOWER(bm.brand_name) LIKE '%%grab on%%')
        """, prompt_ids).fetchall()

        ai_map = {}
        for r in ai_rows:
            pid = str(r["prompt_id"])
            ai_map.setdefault(pid, {})[r["engine_name"]] = r["rank_position"]

        serp_rows = conn.execute(f"""
            WITH latest_serp AS (
                SELECT DISTINCT ON (sr.prompt_id)
                    sr.id AS serp_id, sr.prompt_id
                FROM serp_results sr
                WHERE sr.prompt_id IN ({ph})
                ORDER BY sr.prompt_id, sr.captured_at DESC
            )
            SELECT ls.prompt_id, MIN(soe.rank_position) AS serp_rank
            FROM latest_serp ls
            JOIN serp_organic_entries soe ON soe.serp_id = ls.serp_id
            WHERE soe.domain = %s OR soe.domain LIKE %s
            GROUP BY ls.prompt_id
        """, prompt_ids + [target_domain, f"%.{target_domain}"]).fetchall()

        serp_map = {str(r["prompt_id"]): r["serp_rank"] for r in serp_rows}

        rows = []
        for p in prompts:
            pid = str(p["id"])
            ai_ranks = ai_map.get(pid, {})
            serp_rank = serp_map.get(pid)
            has_any_ai = any(v is not None for v in ai_ranks.values())
            has_serp = serp_rank is not None

            if has_any_ai and has_serp:
                gap_type = "both"
            elif has_any_ai and not has_serp:
                gap_type = "ai_only"
            elif not has_any_ai and has_serp:
                gap_type = "serp_only"
            else:
                gap_type = "neither"

            if gap_filter != "all" and gap_type != gap_filter:
                continue

            rows.append({
                "keyword": p["text"],
                "category": p["merchant_category"],
                "serp_rank": serp_rank,
                "ai_ranks": ai_ranks,
                "gap_type": gap_type,
            })

        return {
            "rows": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "summary": {
                "ai_and_serp": summary_row["both_count"],
                "ai_only": summary_row["ai_only"],
                "serp_only": summary_row["serp_only"],
                "neither": summary_row["neither"],
            },
        }

    result = await run_db(_query)
    return result


@router.get("/serp/categories")
async def serp_categories():
    """Distinct categories for filter dropdown."""
    def _q(conn):
        rows = conn.execute(
            "SELECT DISTINCT merchant_category FROM prompts WHERE merchant_category IS NOT NULL ORDER BY merchant_category"
        ).fetchall()
        return [r["merchant_category"] for r in rows]
    return await run_db(_q)


@router.get("/analytics/seo-daily")
async def analytics_seo_daily(days: int = Query(30)):
    """Daily SEO stats: avg rank, top10 count, keywords crawled."""
    settings = get_settings()
    def _q(conn):
        rows = conn.execute("""
            SELECT sr.captured_at::date::text as day,
                   COUNT(DISTINCT sr.prompt_id) as keywords_crawled,
                   COUNT(*) FILTER (
                       WHERE soe.is_target = TRUE AND soe.rank_position <= 10
                   ) as top10_count,
                   COUNT(*) FILTER (
                       WHERE soe.is_target = TRUE AND soe.rank_position <= 3
                   ) as top3_count,
                   ROUND(AVG(soe.rank_position) FILTER (
                       WHERE soe.is_target = TRUE
                   )::numeric, 1) as avg_rank
            FROM serp_results sr
            LEFT JOIN serp_organic_entries soe ON soe.serp_id = sr.id
            WHERE sr.captured_at >= CURRENT_DATE - make_interval(days => %s)
            GROUP BY day
            ORDER BY day
        """, (days,)).fetchall()
        return [dict(r) for r in rows]
    return {"data": await run_db(_q)}


@router.get("/analytics/seo-competitor-trend")
async def analytics_seo_competitor_trend(days: int = Query(14)):
    """Competitor domain positions over time."""
    known_domains = [
        "coupondunia.in", "cashkaro.com", "desidime.com",
        "gopaisa.com", "zoutons.com",
    ]
    settings = get_settings()
    target = settings.target_domain
    all_domains = [target] + known_domains

    def _q(conn):
        domain_filters = " OR ".join(["soe.domain ILIKE %s"] * len(all_domains))
        params = [f"%{d}%" for d in all_domains] + [days]
        rows = conn.execute(f"""
            SELECT sr.captured_at::date::text as day,
                   soe.domain,
                   ROUND(AVG(soe.rank_position)::numeric, 1) as avg_rank,
                   COUNT(DISTINCT sr.prompt_id) as keyword_count
            FROM serp_organic_entries soe
            JOIN serp_results sr ON soe.serp_id = sr.id
            WHERE ({domain_filters})
              AND sr.captured_at >= CURRENT_DATE - make_interval(days => %s)
            GROUP BY day, soe.domain
            ORDER BY day, soe.domain
        """, params).fetchall()
        return [dict(r) for r in rows]
    return {"data": await run_db(_q)}


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
    engine_display = {
        "google_aio": "AI Overview", "google_ai_mode": "AI Mode",
        "perplexity": "Perplexity", "gemini": "Gemini",
        "chatgpt": "ChatGPT", "claude": "Claude"
    }

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
