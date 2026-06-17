import json
from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.database import run_db
from app.agent.scraper import ALL_ENGINES, VISIBLE_ENGINES
from app.config import get_settings

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

ENGINE_DISPLAY = {
    "google_aio": "AI Overview",
    "google_ai_mode": "AI Mode",
    "perplexity": "Perplexity",
    "gemini": "Gemini",
    "chatgpt": "ChatGPT",
    "claude": "Claude",
}


@router.get("/", response_class=HTMLResponse)
async def dashboard_page(
    request: Request,
    engines: str = Query(",".join(VISIBLE_ENGINES), alias="engines"),
    category: str = Query("All", alias="category"),
    intent: str = Query("All", alias="intent"),
    tier: int = Query(0, alias="tier"),  # kept for backward compat, not shown in UI
    page: int = Query(1, alias="page"),
    page_size: int = Query(50, alias="page_size"),
):
    active_engines = [e.strip() for e in engines.split(",") if e.strip()]
    offset = (page - 1) * page_size

    def _fetch(conn):
        engine_count = len(active_engines)
        engine_placeholders = ",".join(["%s"] * engine_count)

        where_parts = [
            f"EXISTS (SELECT 1 FROM execution_logs el WHERE el.prompt_id = p.id AND el.engine_name IN ({engine_placeholders}))"
        ]
        prompt_params: list = list(active_engines)
        if tier > 0:
            where_parts.append("p.tier = %s")
            prompt_params.append(tier)
        if category != "All":
            where_parts.append("p.merchant_category = %s")
            prompt_params.append(category)
        if intent != "All":
            where_parts.append("p.intent_type = %s")
            prompt_params.append(intent)

        where_sql = " AND ".join(where_parts)

        total_prompts = conn.execute(
            f"SELECT COUNT(*) as cnt FROM prompts p WHERE {where_sql}", prompt_params
        ).fetchone()["cnt"]

        page_params = list(prompt_params) + [page_size, offset]
        prompts = conn.execute(
            f"""SELECT p.* FROM prompts p
                LEFT JOIN (
                    SELECT prompt_id, MAX(captured_at) as max_captured
                    FROM execution_logs GROUP BY prompt_id
                ) la ON la.prompt_id = p.id
                WHERE {where_sql}
                ORDER BY la.max_captured DESC NULLS LAST, p.created_at
                LIMIT %s OFFSET %s""",
            page_params,
        ).fetchall()
        prompt_ids = [p["id"] for p in prompts]

        logs = []
        mentions = []
        if prompt_ids:
            ph = ", ".join(["%s"] * len(prompt_ids))
            log_params = list(prompt_ids)

            engine_filter = ""
            if active_engines:
                eph = ", ".join(["%s"] * len(active_engines))
                engine_filter = f" AND el.engine_name IN ({eph})"
                log_params.extend(active_engines)

            log_query = f"""
                SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                       el.id as log_id, el.engine_name, el.country_code,
                       el.captured_at::date::text as date, el.prompt_id,
                       el.raw_response_text,
                       p.text as prompt_text, p.merchant_category, p.intent_type
                FROM execution_logs el
                JOIN prompts p ON p.id = el.prompt_id
                WHERE el.prompt_id IN ({ph}){engine_filter}
                ORDER BY el.prompt_id, el.engine_name,
                         (CASE WHEN el.raw_response_text LIKE 'Error:%%' THEN 1 ELSE 0 END),
                         el.captured_at DESC
            """
            logs = conn.execute(log_query, log_params).fetchall()

            log_ids = [row["log_id"] for row in logs]
            if log_ids:
                lph = ", ".join(["%s"] * len(log_ids))
                mentions = conn.execute(
                    f"""SELECT bm.*, el.engine_name, el.country_code,
                               el.captured_at::date::text as date, el.prompt_id
                        FROM brand_mentions bm
                        JOIN execution_logs el ON el.id = bm.log_id
                        WHERE bm.log_id IN ({lph})""",
                    log_ids,
                ).fetchall()

        agg = conn.execute("""
            SELECT COUNT(*) as total_logs,
                   COUNT(DISTINCT el.prompt_id) as prompts_with_data
            FROM execution_logs el
        """).fetchone()

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

        google_cookies = conn.execute(
            "SELECT id FROM app_settings WHERE key IN ('google_cookies', 'storageState.json')"
        ).fetchone()
        chatgpt_cookies = conn.execute(
            "SELECT id FROM app_settings WHERE key = 'chatgpt_cookies'"
        ).fetchone()
        gemini_cookies = conn.execute(
            "SELECT id FROM app_settings WHERE key = 'gemini_cookies'"
        ).fetchone()
        claude_cookies = conn.execute(
            "SELECT id FROM app_settings WHERE key = 'claude_cookies'"
        ).fetchone()
        categories = conn.execute(
            "SELECT DISTINCT merchant_category FROM prompts ORDER BY merchant_category"
        ).fetchall()

        cost_data = {"total_cost": 0, "today_cost": 0, "total_calls": 0}
        try:
            cost_row = conn.execute("""
                SELECT COALESCE(SUM(cost_usd), 0) as total_cost, COUNT(*) as total_calls
                FROM api_costs
            """).fetchone()
            today_row = conn.execute("""
                SELECT COALESCE(SUM(cost_usd), 0) as cost FROM api_costs
                WHERE created_at::date = CURRENT_DATE
            """).fetchone()
            cost_data = {
                "total_cost": float(cost_row["total_cost"]),
                "today_cost": float(today_row["cost"]),
                "total_calls": cost_row["total_calls"],
            }
        except Exception:
            pass

        unparsed = conn.execute("""
            SELECT COUNT(*) as cnt FROM execution_logs el
            LEFT JOIN brand_mentions bm ON bm.log_id = el.id
            WHERE el.raw_response_text NOT LIKE 'Error:%%'
              AND bm.log_id IS NULL
        """).fetchone()["cnt"]

        engine_health = {}
        health_rows = conn.execute("""
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

        recent_streaks = {}
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
        for sr in streak_rows:
            recent_streaks[sr["engine_name"]] = sr["results"]

        for r in health_rows:
            eng = r["engine_name"]
            total_4h = r["total_4h"]
            errors_4h = r["errors_4h"]
            rate_4h = round((errors_4h / total_4h * 100) if total_4h else 0, 1)
            rate_24h = round((r["errors_24h"] / r["total_24h"] * 100) if r["total_24h"] else 0, 1)

            streak = recent_streaks.get(eng, [])
            recent_success_streak = 0
            for s in streak:
                if s == 'S':
                    recent_success_streak += 1
                else:
                    break

            if total_4h == 0 and r["total_24h"] == 0:
                health = "inactive"
            elif recent_success_streak >= 5:
                health = "healthy"
            elif recent_success_streak >= 3:
                health = "recovering" if rate_4h >= 30 else "healthy"
            elif rate_4h >= 80:
                health = "blocked"
            elif rate_4h >= 50:
                health = "degraded"
            elif rate_24h >= 30 or rate_4h >= 20:
                health = "degraded" if recent_success_streak < 2 else "recovering"
            elif recent_success_streak >= 2:
                health = "recovering" if rate_24h >= 15 else "healthy"
            else:
                health = "healthy" if rate_4h == 0 and rate_24h < 10 else "recovering"

            engine_health[eng] = {
                "total_24h": r["total_24h"],
                "errors_24h": r["errors_24h"],
                "success_24h": r["success_24h"],
                "error_rate": rate_4h,
                "health": health,
                "last_success": str(r["last_success"])[:16] if r["last_success"] else None,
                "last_error": str(r["last_error"])[:16] if r["last_error"] else None,
                "streak": recent_success_streak,
            }

        recent_failures = conn.execute("""
            SELECT el.engine_name,
                   SUBSTRING(el.raw_response_text FROM 8 FOR 120) AS error_msg,
                   el.captured_at::text AS captured_at
            FROM execution_logs el
            WHERE el.raw_response_text LIKE 'Error:%%'
              AND el.captured_at >= NOW() - INTERVAL '24 hours'
            ORDER BY el.captured_at DESC
            LIMIT 5
        """).fetchall()

        return (prompts, total_prompts, logs, mentions, agg, grabon_agg,
                total_mentions_agg, google_cookies is not None,
                chatgpt_cookies is not None, gemini_cookies is not None,
                claude_cookies is not None,
                categories, cost_data, unparsed, engine_health,
                [dict(r) for r in recent_failures])

    (prompts, total_prompts, logs, mentions, agg, grabon_agg,
     total_mentions_agg, has_google_cookies, has_chatgpt_cookies,
     has_gemini_cookies, has_claude_cookies,
     cat_rows, cost_data, unparsed_count, engine_health,
     recent_failures) = await _fetch_data(_fetch)

    dates = sorted(set(row["date"] for row in logs))
    chart_data = _build_chart_data(dates, mentions)

    total_grabon = sum(r["mentions"] for r in grabon_agg)
    citations = sum(r["cited"] for r in grabon_agg)
    organic = total_grabon - citations
    pos = sum(r["pos"] for r in grabon_agg)
    neu = sum(r["neu"] for r in grabon_agg)
    neg = sum(r["neg"] for r in grabon_agg)

    total_mentions_map = {r["engine_name"]: r["cnt"] for r in total_mentions_agg}
    grabon_weight = sum(r["mentions"] / max(float(r["avg_rank"] or 1), 1) for r in grabon_agg)
    competitor_weight = sum(
        max(total_mentions_map.get(r["engine_name"], 0) - r["mentions"], 0)
        for r in grabon_agg
    )
    total_weight = grabon_weight + competitor_weight
    sov = round((grabon_weight / total_weight * 100) if total_weight else 0, 1)
    footprint = round((organic / total_grabon * 100) if total_grabon else 0, 0)

    citation_rate = round((citations / total_grabon * 100) if total_grabon else 0, 1)
    pos_rate = round((pos / total_grabon * 100) if total_grabon else 0, 1)
    engines_with_mentions = len(set(r["engine_name"] for r in grabon_agg if r["mentions"] > 0))
    engine_coverage = round((engines_with_mentions / len(VISIBLE_ENGINES) * 100), 1)
    ai_visibility = round(
        sov * 0.4 + citation_rate * 0.3 + pos_rate * 0.2 + engine_coverage * 0.1
    ) if total_grabon else 0

    grabon_map = {r["engine_name"]: r for r in grabon_agg}
    engine_stats = {}
    for eng in VISIBLE_ENGINES:
        g = grabon_map.get(eng, {"mentions": 0, "cited": 0})
        engine_stats[eng] = {
            "display_name": ENGINE_DISPLAY.get(eng, eng),
            "mentions": g["mentions"] if isinstance(g["mentions"], int) else 0,
            "cited_pages": g["cited"] if "cited" in g else 0,
            "total_mentions": total_mentions_map.get(eng, 0),
            "status": _engine_status(eng, g["mentions"] if isinstance(g["mentions"], int) else 0,
                                     has_google_cookies, has_chatgpt_cookies, has_gemini_cookies,
                                     has_claude_cookies),
        }

    country_stats = {"IN": {"visibility": sov, "mentions": total_grabon, "total_mentions": sum(total_mentions_map.values())}}

    # Pre-index mentions and logs by (prompt_id, engine) for O(1) lookup
    _mention_idx = {}
    for m in mentions:
        key = (str(m["prompt_id"]), m["engine_name"])
        _mention_idx.setdefault(key, []).append(m)
    _log_idx = {}
    for l in logs:
        key = (str(l["prompt_id"]), l["engine_name"])
        existing = _log_idx.get(key)
        if not existing:
            _log_idx[key] = l
        elif (existing.get("raw_response_text", "") or "").startswith("Error:") and \
             not (l.get("raw_response_text", "") or "").startswith("Error:"):
            _log_idx[key] = l

    rankings = {}
    for p in prompts:
        pid = str(p["id"])
        rankings[pid] = {}
        for eng in VISIBLE_ENGINES:
            rankings[pid][eng] = _get_latest_rank_fast(_mention_idx, _log_idx, pid, eng)

    categories = [r["merchant_category"] for r in cat_rows]
    total_pages = (total_prompts + page_size - 1) // page_size

    return templates.TemplateResponse(request, "dashboard.html", {
        "prompts": prompts,
        "chart_data": chart_data,
        "sov": sov,
        "ai_visibility": ai_visibility,
        "citations": citations,
        "citation_rate": citation_rate,
        "pos": pos,
        "neu": neu,
        "neg": neg,
        "pos_rate": pos_rate,
        "footprint": footprint,
        "total_grabon": total_grabon,
        "total_logs": len(logs),
        "total_raw_logs": agg["total_logs"],
        "active_engines": active_engines,
        "all_engines": VISIBLE_ENGINES,
        "engine_display": ENGINE_DISPLAY,
        "engine_stats": engine_stats,
        "country_stats": country_stats,
        "active_category": category,
        "active_intent": intent,
        "categories": categories,
        "has_google_cookies": has_google_cookies,
        "has_chatgpt_cookies": has_chatgpt_cookies,
        "cost_data": cost_data,
        "unparsed_count": unparsed_count,
        "rankings": rankings,
        "page": page,
        "total_pages": total_pages,
        "total_prompts": total_prompts,
        "active_tier": tier,
        "page_size": page_size,
        "engine_health": engine_health,
        "recent_failures": recent_failures,
    })


def _engine_status(eng: str, mention_count: int, has_google: bool, has_chatgpt: bool,
                   has_gemini: bool = False, has_claude: bool = False) -> str:
    if eng in ("google_aio", "google_ai_mode") and not has_google:
        return "auth_required"
    if eng == "chatgpt" and not has_chatgpt:
        return "auth_required"
    if eng == "gemini" and not has_gemini:
        return "auth_required"
    if eng == "claude" and not has_claude:
        return "auth_required"
    return "active" if mention_count > 0 else "no_data"


async def _fetch_data(func):
    return await run_db(func)


def _build_chart_data(dates, mentions):
    chart_data = []
    for d in dates:
        day_mentions = [m for m in mentions if m["date"] == d]
        weights = {"grabon": 0, "coupondunia": 0, "cashkaro": 0, "desidime": 0}
        for m in day_mentions:
            w = 1.0 / m["rank_position"]
            bn = m["brand_name"].lower()
            if "grabon" in bn:
                weights["grabon"] += w
            elif "dunia" in bn:
                weights["coupondunia"] += w
            elif "karo" in bn:
                weights["cashkaro"] += w
            elif "dime" in bn:
                weights["desidime"] += w
        total = sum(weights.values())
        chart_data.append({
            "date": d,
            "GrabOn": round((weights["grabon"] / total * 100) if total else 0, 1),
            "CouponDunia": round((weights["coupondunia"] / total * 100) if total else 0, 1),
            "CashKaro": round((weights["cashkaro"] / total * 100) if total else 0, 1),
            "DesiDime": round((weights["desidime"] / total * 100) if total else 0, 1),
        })
    return chart_data


def _get_latest_rank(all_mentions, prompt_id, engine, logs=None):
    """Legacy fallback — kept for compatibility."""
    engine_mentions = [
        m for m in all_mentions
        if str(m["prompt_id"]) == str(prompt_id) and m["engine_name"] == engine
    ]
    log_id = None
    raw_text = ""
    if logs:
        engine_logs = [l for l in logs if str(l["prompt_id"]) == str(prompt_id) and l["engine_name"] == engine]
        if engine_logs:
            log_id = str(engine_logs[0]["log_id"])
            raw_text = engine_logs[0].get("raw_response_text", "") or ""

    top_brands = sorted(
        [{"brand": m["brand_name"], "rank": m["rank_position"], "sentiment": m.get("sentiment", "")}
         for m in engine_mentions],
        key=lambda x: x["rank"]
    )

    grabon_hits = [m for m in engine_mentions if "grabon" in m["brand_name"].lower()]
    if grabon_hits:
        return {"status": "ranked", "rank": grabon_hits[0]["rank_position"], "sentiment": grabon_hits[0]["sentiment"], "log_id": log_id, "top_brands": top_brands}
    if engine_mentions:
        return {"status": "unranked", "log_id": log_id, "top_brands": top_brands}
    if log_id:
        if raw_text.startswith("[No AI Overview]"):
            return {"status": "no_aio", "log_id": log_id, "top_brands": []}
        if "limit_exhausted" in raw_text:
            return {"status": "limit_exhausted", "log_id": log_id, "top_brands": []}
        if raw_text.startswith("Error:"):
            return {"status": "error", "log_id": log_id, "top_brands": []}
        return {"status": "no_mention", "log_id": log_id, "top_brands": []}
    return {"status": "pending", "top_brands": []}


def _get_latest_rank_fast(mention_idx, log_idx, prompt_id, engine):
    """O(1) lookup version using pre-indexed dicts."""
    key = (prompt_id, engine)
    engine_mentions = mention_idx.get(key, [])
    log_entry = log_idx.get(key)
    log_id = str(log_entry["log_id"]) if log_entry else None
    raw_text = (log_entry.get("raw_response_text", "") or "") if log_entry else ""

    top_brands = sorted(
        [{"brand": m["brand_name"], "rank": m["rank_position"], "sentiment": m.get("sentiment", "")}
         for m in engine_mentions],
        key=lambda x: x["rank"]
    )

    grabon_hits = [m for m in engine_mentions if "grabon" in m["brand_name"].lower()]
    if grabon_hits:
        return {"status": "ranked", "rank": grabon_hits[0]["rank_position"], "sentiment": grabon_hits[0]["sentiment"], "log_id": log_id, "top_brands": top_brands}
    if engine_mentions:
        return {"status": "unranked", "log_id": log_id, "top_brands": top_brands}
    if log_id:
        if raw_text.startswith("[No AI Overview]"):
            return {"status": "no_aio", "log_id": log_id, "top_brands": []}
        if "limit_exhausted" in raw_text:
            return {"status": "limit_exhausted", "log_id": log_id, "top_brands": []}
        if raw_text.startswith("Error:"):
            return {"status": "error", "log_id": log_id, "top_brands": []}
        return {"status": "no_mention", "log_id": log_id, "top_brands": []}
    return {"status": "pending", "top_brands": []}


def _build_serp_data(conn, prompt_ids):
    if not prompt_ids:
        return {}
    try:
        return _build_serp_data_inner(conn, prompt_ids)
    except Exception:
        return {}


def _build_serp_data_inner(conn, prompt_ids):
    ph = ", ".join(["%s"] * len(prompt_ids))
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

    result = {}
    for r in serp_rows:
        pid = str(r["prompt_id"])
        result[pid] = {
            "target_rank": r["target_rank"],
            "target_domain": r["target_domain"],
            "serp_at": str(r["serp_at"])[:16] if r["serp_at"] else None,
            "features": [],
        }
    for r in feat_rows:
        pid = str(r["prompt_id"])
        if pid in result:
            ft = r["feature_type"]
            if ft not in result[pid]["features"]:
                result[pid]["features"].append(ft)
    return result


@router.get("/serp", response_class=HTMLResponse)
async def serp_page(request: Request):
    settings = get_settings()
    target_domain = settings.target_domain

    return templates.TemplateResponse(request, "serp.html", {
        "total_serps": 0,
        "total_keywords": 0,
        "keywords_with_serp": 0,
        "target_top10": 0,
        "avg_rank": None,
        "overlap_count": 0,
        "target_domain": target_domain,
    })


@router.get("/diagnosis", response_class=HTMLResponse)
async def diagnosis_page(request: Request):
    page = int(request.query_params.get("page", 1))
    per_page = 10
    priority_filter = request.query_params.get("priority", "")
    if priority_filter not in ("critical", "high", "medium", "low", ""):
        priority_filter = ""
    root_cause_filter = request.query_params.get("root_cause", "")
    queue_page = int(request.query_params.get("qpage", 1))
    queue_per_page = 15

    def _fetch(conn):
        where_clauses = ["d.status = 'active'"]
        if priority_filter:
            where_clauses.append(f"d.priority = '{priority_filter}'")
        where_sql = " AND ".join(where_clauses)

        total_filtered = conn.execute(f"""
            SELECT COUNT(*) as cnt FROM diagnoses d WHERE {where_sql}
        """).fetchone()["cnt"]

        diagnoses = conn.execute(f"""
            SELECT d.id, d.prompt_id, d.engine_name, d.root_causes,
                   d.action_items, d.priority, d.confidence, d.summary,
                   d.created_at::text as created_at, p.text as keyword
            FROM diagnoses d
            JOIN prompts p ON d.prompt_id = p.id
            WHERE {where_sql}
            ORDER BY
                CASE d.priority
                    WHEN 'critical' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'medium' THEN 3
                    ELSE 4
                END,
                d.created_at DESC
            LIMIT {per_page} OFFSET {(page - 1) * per_page}
        """).fetchall()

        counts = conn.execute("""
            SELECT COUNT(*) as total_active,
                   COUNT(*) FILTER (WHERE priority = 'critical') as critical,
                   COUNT(*) FILTER (WHERE priority = 'high') as high,
                   COUNT(*) FILTER (WHERE priority = 'medium') as medium,
                   COUNT(*) FILTER (WHERE priority = 'low' OR priority IS NULL) as low
            FROM diagnoses WHERE status = 'active'
        """).fetchone()
        active_count = counts["total_active"]
        critical_count = counts["critical"]
        high_count = counts["high"]
        medium_count = counts["medium"]
        low_count = counts["low"]

        total_diagnoses = conn.execute(
            "SELECT COUNT(*) as cnt FROM diagnoses"
        ).fetchone()["cnt"]

        action_count = 0
        all_root_causes = set()
        for d in conn.execute("SELECT root_causes, action_items FROM diagnoses WHERE status = 'active'").fetchall():
            items = d["action_items"]
            if isinstance(items, str):
                items = json.loads(items)
            action_count += len(items) if items else 0
            rcs = d["root_causes"]
            if isinstance(rcs, str):
                rcs = json.loads(rcs)
            if rcs:
                for rc in rcs:
                    if isinstance(rc, dict) and rc.get("category"):
                        all_root_causes.add(rc["category"])

        processed = []
        for d in diagnoses:
            row = dict(d)
            for field in ("root_causes", "action_items"):
                val = row[field]
                if isinstance(val, str):
                    row[field] = json.loads(val)
            if root_cause_filter and row.get("root_causes"):
                if not any(rc.get("category") == root_cause_filter for rc in row["root_causes"]):
                    continue
            processed.append(row)

        queue_total = conn.execute("""
            WITH latest_rank AS (
                SELECT DISTINCT ON (el.prompt_id)
                    el.prompt_id,
                    bm.rank_position,
                    bm.is_target_brand,
                    el.captured_at
                FROM execution_logs el
                LEFT JOIN brand_mentions bm ON bm.log_id = el.id AND bm.is_target_brand = TRUE
                ORDER BY el.prompt_id, el.captured_at DESC
            )
            SELECT COUNT(*) as cnt FROM prompts p
            LEFT JOIN latest_rank lr ON lr.prompt_id = p.id
        """).fetchone()["cnt"]

        priority_queue = conn.execute(f"""
            WITH latest_rank AS (
                SELECT DISTINCT ON (el.prompt_id)
                    el.prompt_id,
                    bm.rank_position,
                    bm.is_target_brand,
                    el.captured_at
                FROM execution_logs el
                LEFT JOIN brand_mentions bm ON bm.log_id = el.id AND bm.is_target_brand = TRUE
                ORDER BY el.prompt_id, el.captured_at DESC
            ),
            last_diag AS (
                SELECT DISTINCT ON (prompt_id) prompt_id, created_at::text as last_diagnosis
                FROM diagnoses ORDER BY prompt_id, created_at DESC
            )
            SELECT p.id as prompt_id, p.text as keyword,
                   lr.rank_position,
                   CASE
                       WHEN lr.rank_position IS NULL AND lr.prompt_id IS NOT NULL THEN 'absent'
                       WHEN lr.rank_position > 5 THEN 'low_rank'
                       ELSE 'visible'
                   END as target_status,
                   CASE
                       WHEN lr.rank_position IS NULL AND lr.prompt_id IS NOT NULL THEN 100
                       WHEN lr.rank_position > 5 THEN 60
                       WHEN lr.rank_position > 3 THEN 40
                       ELSE 20
                   END as priority_score,
                   ld.last_diagnosis
            FROM prompts p
            LEFT JOIN latest_rank lr ON lr.prompt_id = p.id
            LEFT JOIN last_diag ld ON ld.prompt_id = p.id
            ORDER BY
                CASE
                    WHEN lr.rank_position IS NULL AND lr.prompt_id IS NOT NULL THEN 100
                    WHEN lr.rank_position > 5 THEN 60
                    WHEN lr.rank_position > 3 THEN 40
                    ELSE 20
                END DESC,
                p.text
            LIMIT {queue_per_page} OFFSET {(queue_page - 1) * queue_per_page}
        """).fetchall()

        return {
            "diagnoses": processed,
            "active_count": active_count,
            "critical_count": critical_count,
            "high_count": high_count,
            "medium_count": medium_count,
            "low_count": low_count,
            "total_diagnoses": total_diagnoses,
            "total_filtered": total_filtered,
            "action_count": action_count,
            "priority_queue": [dict(r) for r in priority_queue],
            "queue_total": queue_total,
            "all_root_causes": sorted(all_root_causes),
        }

    data = await run_db(_fetch)
    total_pages = max(1, -(-data["total_filtered"] // per_page))
    queue_pages = max(1, -(-data["queue_total"] // queue_per_page))
    return templates.TemplateResponse(request, "diagnosis.html", {
        **data,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "priority_filter": priority_filter,
        "root_cause_filter": root_cause_filter,
        "queue_page": queue_page,
        "queue_pages": queue_pages,
    })


@router.get("/verification", response_class=HTMLResponse)
async def verification_page(request: Request):
    def _fetch(conn):
        fixes = conn.execute("""
            SELECT af.id, af.prompt_id, af.diagnosis_id, af.engine_name,
                   af.description, af.target_url,
                   af.applied_at::text as applied_at,
                   af.verification_status, af.last_verified_at,
                   af.verification_result, af.created_at,
                   p.text as keyword
            FROM applied_fixes af
            JOIN prompts p ON af.prompt_id = p.id
            ORDER BY af.applied_at DESC
        """).fetchall()

        processed = []
        for f in fixes:
            row = dict(f)
            vr = row.get("verification_result")
            if isinstance(vr, str):
                row["verification_result"] = json.loads(vr)
            processed.append(row)

        monitoring_count = sum(1 for f in processed if f["verification_status"] == "monitoring")
        improved_count = sum(1 for f in processed if f["verification_status"] == "improved")
        nochange_count = sum(1 for f in processed if f["verification_status"] == "no_change")
        degraded_count = sum(1 for f in processed if f["verification_status"] == "degraded")

        return {
            "fixes": processed,
            "monitoring_count": monitoring_count,
            "improved_count": improved_count,
            "nochange_count": nochange_count,
            "degraded_count": degraded_count,
        }

    data = await run_db(_fetch)
    return templates.TemplateResponse(request, "verification.html", data)
