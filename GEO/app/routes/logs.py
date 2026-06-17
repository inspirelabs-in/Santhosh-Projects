from pathlib import Path

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
        if logs:
            log_ids = [r["id"] for r in logs]
            placeholders = ",".join(["%s"] * len(log_ids))
            all_mentions = conn.execute(
                f"SELECT * FROM brand_mentions WHERE log_id IN ({placeholders}) ORDER BY rank_position",
                log_ids,
            ).fetchall()
            mentions_by_log = {}
            for m in all_mentions:
                mentions_by_log.setdefault(m["log_id"], []).append(m)
            for log_row in logs:
                log_data.append({**log_row, "mentions": mentions_by_log.get(log_row["id"], [])})

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

            # Get latest per engine — batch fetch mentions
            seen = {}
            latest_logs = []
            for l in logs:
                if l["engine_name"] not in seen:
                    seen[l["engine_name"]] = l
                    latest_logs.append(l)
            if latest_logs:
                log_ids = [l["log_id"] for l in latest_logs]
                placeholders = ",".join(["%s"] * len(log_ids))
                all_mentions = conn.execute(
                    f"SELECT log_id, brand_name, rank_position, sentiment FROM brand_mentions WHERE log_id IN ({placeholders}) ORDER BY rank_position",
                    log_ids,
                ).fetchall()
                mentions_by_log = {}
                for m in all_mentions:
                    mentions_by_log.setdefault(m["log_id"], []).append(m)
                for eng, l in seen.items():
                    seen[eng] = {**l, "mentions": mentions_by_log.get(l["log_id"], [])}

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


@router.get("/keywords", response_class=HTMLResponse)
async def keywords_page(
    request: Request,
    tier: int | None = Query(None),
    category: str | None = Query(None),
    intent: str | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
):
    per_page = 50

    def _fetch(conn):
        where_clauses = []
        params = []
        if tier:
            where_clauses.append("tier = %s")
            params.append(tier)
        if category:
            where_clauses.append("merchant_category = %s")
            params.append(category)
        if intent:
            if intent == "GEO":
                where_clauses.append("intent_type = 'GEO'")
            elif intent == "SEO":
                where_clauses.append("intent_type <> 'GEO'")
        if search:
            where_clauses.append("LOWER(text) LIKE %s")
            params.append(f"%{search.lower()}%")
        where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        total = conn.execute(f"SELECT COUNT(*) as cnt FROM prompts {where}", params).fetchone()["cnt"]
        keywords = conn.execute(
            f"""SELECT id, text, tier, merchant_category, intent_type,
                       keyword_group, is_canonical, last_run_at, created_at
                FROM prompts {where}
                ORDER BY tier, text
                LIMIT %s OFFSET %s""",
            params + [per_page, (page - 1) * per_page],
        ).fetchall()

        tier_counts = {}
        for row in conn.execute("SELECT tier, COUNT(*) as cnt FROM prompts GROUP BY tier").fetchall():
            tier_counts[row["tier"]] = row["cnt"]

        geo_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM prompts WHERE intent_type = 'GEO'"
        ).fetchone()["cnt"]
        seo_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM prompts WHERE intent_type <> 'GEO'"
        ).fetchone()["cnt"]

        categories = [r["merchant_category"] for r in conn.execute(
            "SELECT DISTINCT merchant_category FROM prompts ORDER BY merchant_category"
        ).fetchall()]

        return {
            "keywords": keywords,
            "total": total,
            "tier_counts": tier_counts,
            "geo_count": geo_count,
            "seo_count": seo_count,
            "categories": categories,
            "total_pages": max(1, (total + per_page - 1) // per_page),
        }

    data = await run_db(_fetch)
    return templates.TemplateResponse(request, "keywords.html", {
        "keywords": data["keywords"],
        "total": data["total"],
        "tier_counts": data["tier_counts"],
        "geo_count": data["geo_count"],
        "seo_count": data["seo_count"],
        "categories": data["categories"],
        "all_categories": data["categories"],
        "total_pages": data["total_pages"],
        "page": page,
        "active_tier": tier,
        "active_category": category,
        "active_intent": intent,
        "search": search,
    })


@router.get("/system-docs", response_class=HTMLResponse)
async def system_docs_page(request: Request):
    return templates.TemplateResponse(request, "system_docs.html", {})


@router.get("/red-flags", response_class=HTMLResponse)
async def red_flags_page(
    request: Request,
    status: str = Query("All"),
    engine: str = Query("All"),
    merchant: str = Query("All"),
    search: str = Query(""),
    page: int = Query(1, ge=1),
):
    per_page = 50

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

        count_row = conn.execute(f"""
            SELECT COUNT(*) as cnt
            FROM ai_hallucinated_coupons hc
            JOIN execution_logs el ON el.id = hc.log_id
            JOIN prompts p ON p.id = el.prompt_id
            WHERE TRUE{where_sql}
        """, params).fetchone()
        filtered_total = count_row["cnt"]

        offset = (page - 1) * per_page
        coupons = conn.execute(f"""
            SELECT hc.*, el.engine_name, el.captured_at::text as captured_at,
                   p.text as prompt_text, p.merchant_category
            FROM ai_hallucinated_coupons hc
            JOIN execution_logs el ON el.id = hc.log_id
            JOIN prompts p ON p.id = el.prompt_id
            WHERE TRUE{where_sql}
            ORDER BY hc.created_at DESC
            LIMIT %s OFFSET %s
        """, params + [per_page, offset]).fetchall()

        stats = conn.execute("""
            SELECT hc.status_flag, COUNT(*) as count
            FROM ai_hallucinated_coupons hc
            GROUP BY hc.status_flag
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
            SELECT hc.associated_merchant,
                   COUNT(*) as total,
                   COUNT(*) FILTER (WHERE hc.status_flag = 'Hallucinated') as hallucinated,
                   COUNT(*) FILTER (WHERE hc.status_flag = 'Active-Valid') as active,
                   COUNT(*) FILTER (WHERE hc.status_flag = 'Expired-On-Site') as expired
            FROM ai_hallucinated_coupons hc
            JOIN execution_logs el ON el.id = hc.log_id
            GROUP BY hc.associated_merchant
            ORDER BY total DESC
            LIMIT 10
        """).fetchall()

        return (coupons, {r["status_flag"]: r["count"] for r in stats},
                engine_stats, merchant_list, top_merchants, filtered_total)

    coupons, stat_map, engine_stats, merchant_list, top_merchants, filtered_total = await run_db(_fetch)
    total = sum(stat_map.values())
    total_pages = max(1, (filtered_total + per_page - 1) // per_page)

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
        "page": page,
        "total_pages": total_pages,
        "filtered_total": filtered_total,
    })
