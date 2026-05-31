from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.database import run_db
from app.agent.scraper import VISIBLE_ENGINES

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    engine: str = Query("All", alias="engine"),
    page: int = Query(1, ge=1),
):
    per_page = 25
    offset = (page - 1) * per_page

    def _fetch(conn):
        base_query = """
            SELECT el.id, el.engine_name, el.country_code, el.raw_response_text,
                   el.captured_at::text as captured_at,
                   p.text as prompt_text, p.merchant_category, p.intent_type
            FROM execution_logs el
            JOIN prompts p ON p.id = el.prompt_id
        """
        count_query = """
            SELECT COUNT(*) as cnt FROM execution_logs el
            JOIN prompts p ON p.id = el.prompt_id
        """
        params = []

        if engine != "All":
            base_query += " WHERE el.engine_name = %s"
            count_query += " WHERE el.engine_name = %s"
            params.append(engine)

        total = conn.execute(count_query, params).fetchone()["cnt"]

        base_query += " ORDER BY el.captured_at DESC LIMIT %s OFFSET %s"
        logs = conn.execute(base_query, params + [per_page, offset]).fetchall()

        log_data = []
        for log_row in logs:
            mentions = conn.execute(
                "SELECT * FROM brand_mentions WHERE log_id = %s ORDER BY rank_position",
                (log_row["id"],),
            ).fetchall()
            log_data.append({**log_row, "mentions": mentions})

        return log_data, total

    log_data, total = await run_db(_fetch)
    total_pages = max(1, (total + per_page - 1) // per_page)

    return templates.TemplateResponse(request, "logs.html", {
        "logs": log_data,
        "active_engine": engine,
        "all_engines": ["All"] + VISIBLE_ENGINES,
        "page": page,
        "total_pages": total_pages,
        "total": total,
    })


@router.get("/responses", response_class=HTMLResponse)
async def responses_page(
    request: Request,
    prompt_id: str = Query(None),
    engine: str = Query("All"),
    page: int = Query(1, ge=1),
):
    per_page = 10

    def _fetch(conn):
        # Get prompts that have been run
        if prompt_id:
            prompts = conn.execute(
                "SELECT id, text, merchant_category, intent_type FROM prompts WHERE id = %s::uuid",
                (prompt_id,),
            ).fetchall()
            total_prompts = 1
        else:
            count_q = """SELECT COUNT(DISTINCT el.prompt_id) as cnt
                         FROM execution_logs el
                         WHERE el.raw_response_text NOT LIKE 'Error:%%'"""
            total_prompts = conn.execute(count_q).fetchone()["cnt"]

            prompts = conn.execute("""
                SELECT DISTINCT p.id, p.text, p.merchant_category, p.intent_type,
                       MAX(el.captured_at) as last_run
                FROM prompts p
                JOIN execution_logs el ON el.prompt_id = p.id
                WHERE el.raw_response_text NOT LIKE 'Error:%%'
                GROUP BY p.id, p.text, p.merchant_category, p.intent_type
                ORDER BY last_run DESC
                LIMIT %s OFFSET %s
            """, (per_page, (page - 1) * per_page)).fetchall()

        results = []
        for p in prompts:
            eng_filter = ""
            eng_params: list = [p["id"]]
            if engine != "All":
                eng_filter = " AND el.engine_name = %s"
                eng_params.append(engine)

            logs = conn.execute(f"""
                SELECT el.id as log_id, el.engine_name,
                       el.raw_response_text,
                       el.captured_at::text as captured_at,
                       LENGTH(el.raw_response_text) as response_length
                FROM execution_logs el
                WHERE el.prompt_id = %s
                  AND el.raw_response_text NOT LIKE 'Error:%%'
                  {eng_filter}
                ORDER BY el.captured_at DESC
            """, eng_params).fetchall()

            # Get latest per engine
            seen = {}
            for l in logs:
                if l["engine_name"] not in seen:
                    mentions = conn.execute(
                        "SELECT brand_name, rank_position, sentiment FROM brand_mentions WHERE log_id = %s ORDER BY rank_position",
                        (l["log_id"],),
                    ).fetchall()
                    seen[l["engine_name"]] = {**l, "mentions": mentions}

            results.append({"prompt": p, "engines": seen})

        return results, total_prompts

    results, total_prompts = await run_db(_fetch)
    total_pages = max(1, (total_prompts + per_page - 1) // per_page)

    return templates.TemplateResponse(request, "responses.html", {
        "results": results,
        "active_engine": engine,
        "all_engines": ["All"] + VISIBLE_ENGINES,
        "page": page,
        "total_pages": total_pages,
        "total": total_prompts,
        "prompt_id": prompt_id,
    })


@router.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request):
    return templates.TemplateResponse(request, "analytics.html", {})


@router.get("/red-flags", response_class=HTMLResponse)
async def red_flags_page(
    request: Request,
    status: str = Query("All"),
    engine: str = Query("All"),
    merchant: str = Query("All"),
    search: str = Query(""),
):
    def _fetch(conn):
        where_clauses = []
        params: list = []

        if status != "All":
            where_clauses.append("hc.status_flag = %s")
            params.append(status)
        if engine != "All":
            where_clauses.append("el.engine_name = %s")
            params.append(engine)
        if merchant != "All":
            where_clauses.append("hc.associated_merchant = %s")
            params.append(merchant)
        if search:
            where_clauses.append("(hc.coupon_code ILIKE %s OR hc.associated_merchant ILIKE %s OR p.text ILIKE %s)")
            like = f"%{search}%"
            params.extend([like, like, like])

        where_sql = (" AND " + " AND ".join(where_clauses)) if where_clauses else ""

        coupons = conn.execute(f"""
            SELECT hc.*, el.engine_name, el.captured_at::text as captured_at,
                   p.text as prompt_text, p.merchant_category
            FROM ai_hallucinated_coupons hc
            JOIN execution_logs el ON el.id = hc.log_id
            JOIN prompts p ON p.id = el.prompt_id
            WHERE 1=1 {where_sql}
            ORDER BY hc.created_at DESC
            LIMIT 200
        """, params).fetchall()

        stats = conn.execute("""
            SELECT status_flag, COUNT(*) as count
            FROM ai_hallucinated_coupons
            GROUP BY status_flag
        """).fetchall()

        engine_stats = conn.execute("""
            SELECT el.engine_name, COUNT(*) as count
            FROM ai_hallucinated_coupons hc
            JOIN execution_logs el ON el.id = hc.log_id
            GROUP BY el.engine_name
            ORDER BY count DESC
        """).fetchall()

        merchant_list = conn.execute("""
            SELECT DISTINCT associated_merchant
            FROM ai_hallucinated_coupons
            ORDER BY associated_merchant
        """).fetchall()

        top_merchants = conn.execute("""
            SELECT associated_merchant,
                   COUNT(*) as total,
                   COUNT(*) FILTER (WHERE status_flag = 'Hallucinated') as hallucinated,
                   COUNT(*) FILTER (WHERE status_flag = 'Active-Valid') as active,
                   COUNT(*) FILTER (WHERE status_flag = 'Expired-On-Site') as expired
            FROM ai_hallucinated_coupons
            GROUP BY associated_merchant
            ORDER BY total DESC
            LIMIT 15
        """).fetchall()

        return (coupons, {r["status_flag"]: r["count"] for r in stats},
                engine_stats, merchant_list, top_merchants)

    coupons, stat_map, engine_stats, merchant_list, top_merchants = await run_db(_fetch)
    total = sum(stat_map.values())

    return templates.TemplateResponse(request, "red_flags.html", {
        "coupons": coupons,
        "total_active": stat_map.get("Active-Valid", 0),
        "total_expired": stat_map.get("Expired-On-Site", 0),
        "total_hallucinated": stat_map.get("Hallucinated", 0),
        "total_coupons": total,
        "engine_stats": [dict(r) for r in engine_stats],
        "merchants": [r["associated_merchant"] for r in merchant_list],
        "top_merchants": [dict(r) for r in top_merchants],
        "active_status": status,
        "active_engine": engine,
        "active_merchant": merchant,
        "search_query": search,
        "all_engines": VISIBLE_ENGINES,
    })
