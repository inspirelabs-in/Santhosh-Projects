from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.database import run_db
from app.agent.scraper import ALL_ENGINES, VISIBLE_ENGINES

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

ENGINE_DISPLAY = {
    "google_aio": "AI Overview",
    "google_ai_mode": "AI Mode",
    "perplexity": "Perplexity",
    "gemini": "Gemini",
    "chatgpt": "ChatGPT",
    "claude": "Claude",
    "grok": "Grok",
}


@router.get("/", response_class=HTMLResponse)
async def dashboard_page(
    request: Request,
    engines: str = Query(",".join(VISIBLE_ENGINES), alias="engines"),
    category: str = Query("All", alias="category"),
    intent: str = Query("All", alias="intent"),
    tier: int = Query(0, alias="tier"),
    page: int = Query(1, alias="page"),
    page_size: int = Query(50, alias="page_size"),
):
    active_engines = [e.strip() for e in engines.split(",") if e.strip()]
    offset = (page - 1) * page_size

    def _fetch(conn):
        prompt_query = "SELECT * FROM prompts WHERE is_canonical = TRUE"
        prompt_params: list = []
        if tier > 0:
            prompt_query += " AND tier = %s"
            prompt_params.append(tier)
        if category != "All":
            prompt_query += " AND merchant_category = %s"
            prompt_params.append(category)
        if intent != "All":
            prompt_query += " AND intent_type = %s"
            prompt_params.append(intent)

        total_prompts = conn.execute(
            f"SELECT COUNT(*) as cnt FROM ({prompt_query}) sub", prompt_params
        ).fetchone()["cnt"]

        prompt_query += " ORDER BY last_run_at DESC NULLS LAST, created_at LIMIT %s OFFSET %s"
        prompt_params.extend([page_size, offset])
        prompts = conn.execute(prompt_query, prompt_params).fetchall()
        prompt_ids = [p["id"] for p in prompts]

        logs = []
        mentions = []
        if prompt_ids:
            ph = ", ".join(["%s"] * len(prompt_ids))
            log_query = f"""
                SELECT el.id as log_id, el.engine_name, el.country_code,
                       el.captured_at::date::text as date, el.prompt_id,
                       el.raw_response_text,
                       p.text as prompt_text, p.merchant_category, p.intent_type
                FROM execution_logs el
                JOIN prompts p ON p.id = el.prompt_id
                WHERE el.prompt_id IN ({ph})
            """
            log_params = list(prompt_ids)

            if active_engines:
                eph = ", ".join(["%s"] * len(active_engines))
                log_query += f" AND el.engine_name IN ({eph})"
                log_params.extend(active_engines)

            log_query += " ORDER BY el.captured_at DESC"
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
            GROUP BY engine_name
        """).fetchall()

        total_mentions_agg = conn.execute("""
            SELECT engine_name, COUNT(*) as cnt
            FROM brand_mentions bm
            JOIN execution_logs el ON el.id = bm.log_id
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
        grok_cookies = conn.execute(
            "SELECT id FROM app_settings WHERE key = 'grok_cookies'"
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
            WHERE el.raw_response_text NOT LIKE 'Error:%%'
              AND el.id NOT IN (SELECT DISTINCT log_id FROM brand_mentions)
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
                claude_cookies is not None, grok_cookies is not None,
                categories, cost_data, unparsed, engine_health,
                [dict(r) for r in recent_failures])

    (prompts, total_prompts, logs, mentions, agg, grabon_agg,
     total_mentions_agg, has_google_cookies, has_chatgpt_cookies,
     has_gemini_cookies, has_claude_cookies, has_grok_cookies,
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

    grabon_mentions_page = [m for m in mentions if "grabon" in m["brand_name"].lower()]
    grabon_weight = sum(1.0 / m["rank_position"] for m in grabon_mentions_page)
    competitor_weight = sum(
        1.0 / m["rank_position"] for m in mentions if "grabon" not in m["brand_name"].lower()
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

    total_mentions_map = {r["engine_name"]: r["cnt"] for r in total_mentions_agg}
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
                                     has_claude_cookies, has_grok_cookies),
        }

    country_stats = {"IN": {"visibility": sov, "mentions": total_grabon, "total_mentions": sum(total_mentions_map.values())}}

    rankings = {}
    for p in prompts:
        rankings[str(p["id"])] = {}
        for eng in VISIBLE_ENGINES:
            rankings[str(p["id"])][eng] = _get_latest_rank(mentions, p["id"], eng, logs)

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
                   has_gemini: bool = False, has_claude: bool = False, has_grok: bool = False) -> str:
    if eng in ("google_aio", "google_ai_mode") and not has_google:
        return "auth_required"
    if eng == "chatgpt" and not has_chatgpt:
        return "auth_required"
    if eng == "gemini" and not has_gemini:
        return "auth_required"
    if eng == "claude" and not has_claude:
        return "auth_required"
    if eng == "grok" and not has_grok:
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
    grabon_hits = [m for m in engine_mentions if "grabon" in m["brand_name"].lower()]
    if grabon_hits:
        return {"status": "ranked", "rank": grabon_hits[0]["rank_position"], "sentiment": grabon_hits[0]["sentiment"], "log_id": log_id}
    if engine_mentions:
        return {"status": "unranked", "log_id": log_id}
    if log_id:
        if raw_text.startswith("[No AI Overview]"):
            return {"status": "no_aio", "log_id": log_id}
        if raw_text.startswith("Error:"):
            return {"status": "error", "log_id": log_id}
        return {"status": "no_mention", "log_id": log_id}
    return {"status": "pending"}
