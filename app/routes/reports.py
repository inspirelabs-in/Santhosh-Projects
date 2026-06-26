"""Report export + comparison endpoints.

Context-aware exports (GEO, SEO, Analytics) with date range filtering,
period-vs-period comparison, and cycle-vs-cycle comparison.
"""
import logging
from datetime import date, datetime
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.database import run_db
from app.report_builder import build_csv, build_excel, build_pdf, make_response

router = APIRouter(prefix="/api")
log = logging.getLogger("geo.reports")


def _date_filter_sql(
    date_col: str = "el.captured_at",
    *,
    days: int = 0,
    date_from: str = "",
    date_to: str = "",
) -> tuple[str, list]:
    """Build WHERE clause fragment + params for date filtering."""
    parts = []
    params = []
    if date_from:
        parts.append(f"{date_col} >= %s::date")
        params.append(date_from)
    if date_to:
        parts.append(f"{date_col} <= (%s::date + INTERVAL '1 day')")
        params.append(date_to)
    if not parts and days > 0:
        parts.append(f"{date_col} >= CURRENT_DATE - make_interval(days => %s)")
        params.append(days)
    sql = (" AND ".join(parts)) if parts else "TRUE"
    return sql, params


# ═══════════════════════════════════════════════════════════════════════
#  Context-Aware Exports
# ═══════════════════════════════════════════════════════════════════════


@router.get("/export/geo")
async def export_geo(
    format: str = Query("csv"),
    days: int = Query(0),
    date_from: str = Query(""),
    date_to: str = Query(""),
    category: str = Query("All"),
    intent: str = Query("All"),
    cycle_from: int = Query(0),
    cycle_to: int = Query(0),
    clean_only: bool = Query(False),
    limit: int = Query(0),
):
    """Export GEO rankings data with date range or cycle-based filtering."""
    from app.engines import VISIBLE_ENGINES, ENGINE_LABELS as engine_display

    use_cycles = cycle_from > 0 and cycle_to > 0

    if not use_cycles:
        date_sql, date_params = _date_filter_sql(days=days or 7, date_from=date_from, date_to=date_to)

    def _q(conn):
        filters = []
        params = []

        if category != "All":
            filters.append("p.merchant_category = %s")
            params.append(category)
        if intent != "All":
            filters.append("p.intent_type = %s")
            params.append(intent)

        filters.append("p.intent_type = 'GEO'")
        prompt_where = "WHERE " + " AND ".join(filters)

        prompts = conn.execute(f"""
            SELECT p.id, p.text, p.merchant_category, p.intent_type, p.tier
            FROM prompts p {prompt_where} ORDER BY p.text
        """, params).fetchall()

        if not prompts:
            return []

        prompt_ids = [str(p["id"]) for p in prompts]
        ph = ",".join(["%s::uuid"] * len(prompt_ids))

        if use_cycles:
            engine_count = len(VISIBLE_ENGINES)
            engine_list = list(VISIBLE_ENGINES)
            engine_ph = ",".join(["%s"] * engine_count)
            start_rn = (cycle_from - 1) * engine_count + 1
            end_rn = cycle_to * engine_count

            brand_rows = conn.execute(f"""
                WITH ranked AS (
                    SELECT el.id AS log_id, el.prompt_id, el.engine_name, el.captured_at,
                           ROW_NUMBER() OVER (PARTITION BY el.prompt_id ORDER BY el.captured_at) AS rn
                    FROM execution_logs el
                    WHERE el.prompt_id IN ({ph})
                      AND el.engine_name IN ({engine_ph})
                      AND el.raw_response_text NOT LIKE 'Error:%%'
                ),
                cycle_logs AS (
                    SELECT log_id, prompt_id, engine_name FROM ranked
                    WHERE rn BETWEEN %s AND %s
                )
                SELECT cl.prompt_id, cl.engine_name,
                       bm.brand_name, bm.rank_position, bm.sentiment, bm.cited_url
                FROM cycle_logs cl
                LEFT JOIN brand_mentions bm ON bm.log_id = cl.log_id
                ORDER BY cl.prompt_id, cl.engine_name, bm.rank_position NULLS LAST
            """, prompt_ids + engine_list + [start_rn, end_rn]).fetchall()

            error_rows = conn.execute(f"""
                WITH ranked AS (
                    SELECT el.id AS log_id, el.prompt_id, el.engine_name, el.raw_response_text,
                           ROW_NUMBER() OVER (PARTITION BY el.prompt_id ORDER BY el.captured_at) AS rn
                    FROM execution_logs el
                    WHERE el.prompt_id IN ({ph})
                      AND el.engine_name IN ({engine_ph})
                )
                SELECT prompt_id, engine_name, raw_response_text
                FROM ranked WHERE rn BETWEEN %s AND %s
                  AND raw_response_text LIKE 'Error:%%'
            """, prompt_ids + engine_list + [start_rn, end_rn]).fetchall()
        else:
            brand_rows = conn.execute(f"""
                WITH filtered AS (
                    SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                        el.prompt_id, el.engine_name, el.id as log_id
                    FROM execution_logs el
                    WHERE el.prompt_id IN ({ph})
                      AND el.raw_response_text NOT LIKE 'Error:%%'
                      AND {date_sql}
                    ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
                )
                SELECT f.prompt_id, f.engine_name,
                       bm.brand_name, bm.rank_position, bm.sentiment, bm.cited_url
                FROM filtered f
                LEFT JOIN brand_mentions bm ON bm.log_id = f.log_id
                ORDER BY f.prompt_id, f.engine_name, bm.rank_position NULLS LAST
            """, prompt_ids + date_params).fetchall()

            error_rows = conn.execute(f"""
                SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                    el.prompt_id, el.engine_name, el.raw_response_text
                FROM execution_logs el
                WHERE el.prompt_id IN ({ph})
                  AND {date_sql}
                ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
            """, prompt_ids + date_params).fetchall()

        engine_data = {}
        for r in brand_rows:
            pid = str(r["prompt_id"])
            eng = r["engine_name"]
            key = (pid, eng)
            if key not in engine_data:
                engine_data[key] = {"grabon": None, "leader": None, "has_scrape": True}
            if r["brand_name"] and "grabon" in r["brand_name"].lower():
                engine_data[key]["grabon"] = {
                    "rank": r["rank_position"], "sentiment": r["sentiment"],
                    "cited": bool(r["cited_url"]),
                }
            if r["rank_position"] == 1 and r["brand_name"]:
                engine_data[key]["leader"] = r["brand_name"]

        error_map = {}
        for r in error_rows:
            key = (str(r["prompt_id"]), r["engine_name"])
            if key not in engine_data:
                raw = r["raw_response_text"] or ""
                from app.routes.api_helpers import classify_error
                status, err_type, err_msg = classify_error(raw)
                error_map[key] = {"status": status, "error_type": err_type, "error_msg": err_msg}

        rows = []
        for p in prompts:
            pid = str(p["id"])
            row = {
                "keyword": p["text"],
                "category": p["merchant_category"],
                "intent": p["intent_type"],
                "tier": p["tier"],
            }
            for eng in VISIBLE_ENGINES:
                key = (pid, eng)
                ed = engine_data.get(key)
                err = error_map.get(key)

                if ed and ed["grabon"]:
                    g = ed["grabon"]
                    row[eng + "_status"] = f"#{g['rank']}"
                    row[eng + "_sentiment"] = g["sentiment"] or ""
                    row[eng + "_cited"] = "Yes" if g["cited"] else ""
                    row[eng + "_leader"] = ed.get("leader") or ""
                elif ed and ed["has_scrape"]:
                    row[eng + "_status"] = "Not ranked"
                    row[eng + "_sentiment"] = ""
                    row[eng + "_cited"] = ""
                    row[eng + "_leader"] = ed.get("leader") or ""
                elif err:
                    s = err["status"]
                    if s == "no_aio":
                        row[eng + "_status"] = "No AIO"
                    elif s == "limit_exhausted":
                        row[eng + "_status"] = f"Limit ({err['error_msg']})"
                    elif s == "error":
                        row[eng + "_status"] = f"Error: {err['error_msg']}"
                    else:
                        row[eng + "_status"] = "Pending"
                    row[eng + "_sentiment"] = ""
                    row[eng + "_cited"] = ""
                    row[eng + "_leader"] = ""
                else:
                    row[eng + "_status"] = "—"
                    row[eng + "_sentiment"] = ""
                    row[eng + "_cited"] = ""
                    row[eng + "_leader"] = ""
            rows.append(row)
        return rows

    all_rows = await run_db(_q)

    if clean_only:
        from app.engines import VISIBLE_ENGINES as _ve
        filtered = []
        for row in all_rows:
            all_good = True
            for eng in _ve:
                status = row.get(eng + "_status", "—")
                if status in ("—", "Pending") or status.startswith("Error") or status.startswith("Limit") or status == "No AIO":
                    all_good = False
                    break
            if all_good:
                filtered.append(row)
        all_rows = filtered

    rows = all_rows[:limit] if limit > 0 else all_rows

    columns = ["keyword", "category", "intent", "tier"]
    headers = ["Keyword", "Category", "Intent", "Tier"]
    for eng in VISIBLE_ENGINES:
        name = engine_display.get(eng, eng)
        columns += [eng + "_status", eng + "_sentiment", eng + "_cited", eng + "_leader"]
        headers += [f"{name}", f"{name} Sentiment", f"{name} Cited", f"{name} #1"]

    title = "GEO Rankings Report"
    if format == "xlsx":
        buf = build_excel({"GEO Rankings": (rows, columns, headers)}, title=title, date_from=date_from, date_to=date_to)
    elif format == "pdf":
        pdf_cols = ["keyword", "category"]
        pdf_headers = ["Keyword", "Category"]
        for eng in VISIBLE_ENGINES:
            pdf_cols += [eng + "_status", eng + "_leader"]
            pdf_headers += [engine_display.get(eng, eng), f"{engine_display.get(eng, eng)} #1"]
        buf = build_pdf(rows, pdf_cols, pdf_headers, title=title, date_from=date_from, date_to=date_to)
    else:
        buf = build_csv(rows, columns, headers)
        format = "csv"

    return make_response(buf, format, "geo_rankings_report")


@router.get("/export/seo")
async def export_seo(
    format: str = Query("csv"),
    days: int = Query(0),
    date_from: str = Query(""),
    date_to: str = Query(""),
    cycle_from: int = Query(0),
    cycle_to: int = Query(0),
):
    """Export SEO/SERP rankings data with date range or cycle-based filtering."""
    from app.config import get_settings
    settings = get_settings()
    target_domain = getattr(settings, "target_domain", "grabon.in")

    use_cycles = cycle_from > 0 and cycle_to > 0

    if not use_cycles:
        date_sql, date_params = _date_filter_sql("sr.captured_at", days=days or 30, date_from=date_from, date_to=date_to)

    def _q(conn):
        if use_cycles:
            rows = conn.execute("""
                WITH ranked AS (
                    SELECT sr.id AS serp_id, sr.prompt_id, sr.captured_at, sr.results_count,
                           ROW_NUMBER() OVER (PARTITION BY sr.prompt_id ORDER BY sr.captured_at) AS rn
                    FROM serp_results sr
                    JOIN prompts p ON p.id = sr.prompt_id
                    WHERE p.intent_type <> 'GEO'
                )
                SELECT
                    p.text AS keyword,
                    p.merchant_category AS category,
                    p.intent_type AS intent,
                    r.captured_at::date::text AS date,
                    r.results_count,
                    r.serp_id,
                    target.rank_position AS target_rank,
                    target.title AS target_title,
                    target.url AS target_url,
                    target.domain AS target_domain,
                    top1.rank_position AS top1_rank,
                    top1.domain AS top1_domain,
                    top1.title AS top1_title,
                    top3_agg.top3_domains
                FROM ranked r
                JOIN prompts p ON p.id = r.prompt_id
                LEFT JOIN serp_organic_entries target ON target.serp_id = r.serp_id AND target.is_target = TRUE
                LEFT JOIN LATERAL (
                    SELECT rank_position, domain, title
                    FROM serp_organic_entries
                    WHERE serp_id = r.serp_id AND rank_position = 1
                    LIMIT 1
                ) top1 ON TRUE
                LEFT JOIN LATERAL (
                    SELECT STRING_AGG(domain, ', ' ORDER BY rank_position) AS top3_domains
                    FROM serp_organic_entries
                    WHERE serp_id = r.serp_id AND rank_position <= 3
                ) top3_agg ON TRUE
                WHERE r.rn BETWEEN %s AND %s
                ORDER BY p.text, r.rn
            """, [cycle_from, cycle_to]).fetchall()
        else:
            rows = conn.execute(f"""
                WITH latest AS (
                    SELECT DISTINCT ON (sr.prompt_id)
                        sr.id AS serp_id, sr.prompt_id, sr.captured_at, sr.results_count
                    FROM serp_results sr
                    WHERE {date_sql}
                    ORDER BY sr.prompt_id, sr.captured_at DESC
                )
                SELECT
                    p.text AS keyword,
                    p.merchant_category AS category,
                    p.intent_type AS intent,
                    l.captured_at::date::text AS date,
                    l.results_count,
                    l.serp_id,
                    target.rank_position AS target_rank,
                    target.title AS target_title,
                    target.url AS target_url,
                    target.domain AS target_domain,
                    top1.rank_position AS top1_rank,
                    top1.domain AS top1_domain,
                    top1.title AS top1_title,
                    top3_agg.top3_domains
                FROM latest l
                JOIN prompts p ON p.id = l.prompt_id AND p.intent_type <> 'GEO'
                LEFT JOIN serp_organic_entries target ON target.serp_id = l.serp_id AND target.is_target = TRUE
                LEFT JOIN LATERAL (
                    SELECT rank_position, domain, title
                    FROM serp_organic_entries
                    WHERE serp_id = l.serp_id AND rank_position = 1
                    LIMIT 1
                ) top1 ON TRUE
                LEFT JOIN LATERAL (
                    SELECT STRING_AGG(domain, ', ' ORDER BY rank_position) AS top3_domains
                    FROM serp_organic_entries
                    WHERE serp_id = l.serp_id AND rank_position <= 3
                ) top3_agg ON TRUE
                ORDER BY p.text
            """, date_params).fetchall()

        result = []
        for r in rows:
            d = dict(r)
            if d.get("target_rank"):
                d["status"] = f"#{d['target_rank']}"
            else:
                d["status"] = "Not ranked"
            d["competitor_1"] = d.get("top1_domain") or ""
            d["top_3"] = d.get("top3_domains") or ""
            result.append(d)
        return result

    rows = await run_db(_q)

    columns = ["keyword", "category", "intent", "date", "status", "target_rank",
               "target_domain", "target_url", "competitor_1", "top_3", "results_count"]
    headers = ["Keyword", "Category", "Intent", "Date", "Status", "Our Rank",
               "Our Domain", "Our URL", "#1 Competitor", "Top 3 Domains", "Results"]
    title = "SEO Rankings Report"

    if format == "xlsx":
        buf = build_excel({"SEO Rankings": (rows, columns, headers)}, title=title, date_from=date_from, date_to=date_to)
    elif format == "pdf":
        pdf_cols = ["keyword", "category", "status", "competitor_1", "top_3", "date"]
        pdf_headers = ["Keyword", "Category", "Status", "#1 Competitor", "Top 3", "Date"]
        buf = build_pdf(rows, pdf_cols, pdf_headers, title=title, date_from=date_from, date_to=date_to)
    else:
        buf = build_csv(rows, columns, headers)
        format = "csv"

    return make_response(buf, format, "seo_rankings_report")


@router.get("/export/analytics")
async def export_analytics(
    format: str = Query("csv"),
    days: int = Query(0),
    date_from: str = Query(""),
    date_to: str = Query(""),
):
    """Export analytics data: daily summary, competitor comparison, sentiment."""
    date_sql, date_params = _date_filter_sql(days=days or 30, date_from=date_from, date_to=date_to)

    def _q(conn):
        daily = conn.execute(f"""
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
                    AND bm.cited_url IS NOT NULL AND bm.cited_url != '') AS citations
            FROM execution_logs el
            LEFT JOIN brand_mentions bm ON bm.log_id = el.id
            WHERE {date_sql}
              AND el.raw_response_text NOT LIKE 'Error:%%'
            GROUP BY day
            ORDER BY day DESC
        """, date_params).fetchall()

        competitors = conn.execute(f"""
            SELECT
                el.engine_name,
                CASE
                    WHEN LOWER(bm.brand_name) LIKE '%%grabon%%' OR LOWER(bm.brand_name) LIKE '%%grab on%%' THEN 'GrabOn'
                    WHEN LOWER(bm.brand_name) LIKE '%%coupondunia%%' OR LOWER(bm.brand_name) LIKE '%%coupon dunia%%' THEN 'CouponDunia'
                    WHEN LOWER(bm.brand_name) LIKE '%%cashkaro%%' OR LOWER(bm.brand_name) LIKE '%%cash karo%%' THEN 'CashKaro'
                    WHEN LOWER(bm.brand_name) LIKE '%%desidime%%' OR LOWER(bm.brand_name) LIKE '%%desi dime%%' THEN 'DesiDime'
                END AS brand,
                COUNT(*) AS mentions,
                ROUND(AVG(bm.rank_position)::numeric, 2) AS avg_rank,
                COUNT(*) FILTER (WHERE bm.cited_url IS NOT NULL AND bm.cited_url <> '') AS citations
            FROM brand_mentions bm
            JOIN execution_logs el ON el.id = bm.log_id
            WHERE {date_sql}
              AND (LOWER(bm.brand_name) LIKE '%%grabon%%' OR LOWER(bm.brand_name) LIKE '%%grab on%%'
                   OR LOWER(bm.brand_name) LIKE '%%coupondunia%%' OR LOWER(bm.brand_name) LIKE '%%coupon dunia%%'
                   OR LOWER(bm.brand_name) LIKE '%%cashkaro%%' OR LOWER(bm.brand_name) LIKE '%%cash karo%%'
                   OR LOWER(bm.brand_name) LIKE '%%desidime%%' OR LOWER(bm.brand_name) LIKE '%%desi dime%%')
            GROUP BY el.engine_name, brand
            ORDER BY el.engine_name, mentions DESC
        """, date_params).fetchall()

        daily_list = []
        for r in daily:
            d = dict(r)
            if d.get("avg_rank") is not None:
                d["avg_rank"] = float(d["avg_rank"])
            daily_list.append(d)

        comp_list = []
        for r in competitors:
            d = dict(r)
            if d.get("avg_rank") is not None:
                d["avg_rank"] = float(d["avg_rank"])
            comp_list.append(d)

        return daily_list, comp_list

    daily, competitors = await run_db(_q)

    daily_cols = ["day", "keywords_scraped", "total_scrapes", "grabon_mentions", "rank_1_count", "avg_rank", "positive", "neutral", "negative", "citations"]
    daily_hdrs = ["Date", "Keywords", "Scrapes", "Mentions", "#1 Rank", "Avg Rank", "Positive", "Neutral", "Negative", "Citations"]

    comp_cols = ["engine_name", "brand", "mentions", "avg_rank", "citations"]
    comp_hdrs = ["Engine", "Brand", "Mentions", "Avg Rank", "Citations"]

    title = "Analytics Report"

    if format == "xlsx":
        buf = build_excel({
            "Daily Summary": (daily, daily_cols, daily_hdrs),
            "Competitor Analysis": (competitors, comp_cols, comp_hdrs),
        }, title=title, date_from=date_from, date_to=date_to)
    elif format == "pdf":
        buf = build_pdf(daily, daily_cols, daily_hdrs, title=title, date_from=date_from, date_to=date_to)
    else:
        buf = build_csv(daily, daily_cols, daily_hdrs)
        format = "csv"

    return make_response(buf, format, "analytics_report")


# ═══════════════════════════════════════════════════════════════════════
#  Comparison Endpoints
# ═══════════════════════════════════════════════════════════════════════


@router.get("/comparison/cycles")
async def comparison_cycles(context: str = Query("geo")):
    """List completed scrape cycles. A cycle = one full pass through ALL keywords.
    Returns completed cycles + current progress info."""
    from app.engines import VISIBLE_ENGINES

    def _q(conn):
        if context == "seo":
            progress = conn.execute("""
                WITH per_kw AS (
                    SELECT p.id, COUNT(sr.id) AS scrape_count
                    FROM prompts p
                    LEFT JOIN serp_results sr ON sr.prompt_id = p.id
                    WHERE p.intent_type <> 'GEO'
                    GROUP BY p.id
                )
                SELECT COALESCE(MIN(scrape_count), 0) AS completed,
                       COUNT(*) AS total_kw,
                       (SELECT COUNT(*) FROM per_kw WHERE scrape_count >= COALESCE(
                           (SELECT MIN(scrape_count) FROM per_kw), 0) + 1) AS in_next
                FROM per_kw
            """).fetchone()
            completed = int(progress["completed"]) if progress else 0
            total_kw = int(progress["total_kw"]) if progress else 0
            in_next = int(progress["in_next"]) if progress and progress["in_next"] else 0

            cycles = []
            for c in range(1, completed + 1):
                info = conn.execute("""
                    WITH ranked AS (
                        SELECT sr.prompt_id, sr.captured_at,
                               ROW_NUMBER() OVER (PARTITION BY sr.prompt_id ORDER BY sr.captured_at) AS rn
                        FROM serp_results sr
                        JOIN prompts p ON p.id = sr.prompt_id
                        WHERE p.intent_type <> 'GEO'
                    )
                    SELECT MIN(captured_at)::text AS started,
                           MAX(captured_at)::text AS ended,
                           COUNT(DISTINCT prompt_id) AS keywords
                    FROM ranked WHERE rn = %s
                """, [c]).fetchone()
                cycles.append({
                    "cycle_num": c,
                    "started": info["started"][:10] if info["started"] else "",
                    "ended": info["ended"][:10] if info["ended"] else "",
                    "keywords": int(info["keywords"]) if info else 0,
                })
            return {
                "completed": completed,
                "total_keywords": total_kw,
                "next_cycle_pct": round(in_next / max(total_kw, 1) * 100, 1),
                "list": cycles,
            }
        else:
            engine_count = len(VISIBLE_ENGINES)
            engine_ph = ",".join(["%s"] * engine_count)
            engine_list = list(VISIBLE_ENGINES)

            progress = conn.execute(f"""
                WITH kw_counts AS (
                    SELECT p.id,
                           FLOOR(COUNT(el.id) FILTER (
                               WHERE el.raw_response_text NOT LIKE 'Error:%%'
                           )::numeric / {engine_count}) AS full_cycles
                    FROM prompts p
                    LEFT JOIN execution_logs el ON el.prompt_id = p.id
                        AND el.engine_name IN ({engine_ph})
                    WHERE p.intent_type = 'GEO'
                    GROUP BY p.id
                )
                SELECT COALESCE(MIN(full_cycles), 0)::int AS completed,
                       COUNT(*) AS total_kw,
                       (SELECT COUNT(*) FROM kw_counts
                        WHERE full_cycles >= COALESCE(
                            (SELECT MIN(full_cycles) FROM kw_counts), 0) + 1) AS in_next
                FROM kw_counts
            """, engine_list).fetchone()
            completed = int(progress["completed"]) if progress else 0
            total_kw = int(progress["total_kw"]) if progress else 0
            in_next = int(progress["in_next"]) if progress and progress["in_next"] else 0

            cycles = []
            for c in range(1, completed + 1):
                start_idx = (c - 1) * engine_count + 1
                end_idx = c * engine_count
                info = conn.execute(f"""
                    WITH ranked AS (
                        SELECT el.prompt_id, el.captured_at,
                               ROW_NUMBER() OVER (PARTITION BY el.prompt_id ORDER BY el.captured_at) AS rn
                        FROM execution_logs el
                        JOIN prompts p ON p.id = el.prompt_id
                        WHERE p.intent_type = 'GEO'
                          AND el.engine_name IN ({engine_ph})
                          AND el.raw_response_text NOT LIKE 'Error:%%'
                    )
                    SELECT MIN(captured_at)::text AS started,
                           MAX(captured_at)::text AS ended,
                           COUNT(DISTINCT prompt_id) AS keywords
                    FROM ranked WHERE rn BETWEEN %s AND %s
                """, engine_list + [start_idx, end_idx]).fetchone()
                cycles.append({
                    "cycle_num": c,
                    "started": info["started"][:10] if info["started"] else "",
                    "ended": info["ended"][:10] if info["ended"] else "",
                    "keywords": int(info["keywords"]) if info else 0,
                })
            return {
                "completed": completed,
                "total_keywords": total_kw,
                "next_cycle_pct": round(in_next / max(total_kw, 1) * 100, 1),
                "list": cycles,
            }

    result = await run_db(_q)
    return {"cycles": result.get("list", []), "progress": {
        "completed": result.get("completed", 0),
        "total_keywords": result.get("total_keywords", 0),
        "next_cycle_pct": result.get("next_cycle_pct", 0),
    }}


@router.get("/comparison/periods")
async def comparison_periods(
    context: str = Query("geo"),
    period_a: str = Query("30"),
    period_b: str = Query("prev"),
):
    """Compare two time periods side by side."""

    days_a = int(period_a) if period_a.isdigit() else 30

    def _q(conn):
        if context == "seo":
            return _compare_seo_periods(conn, days_a, period_b)
        else:
            return _compare_geo_periods(conn, days_a, period_b)

    return await run_db(_q)


def _compare_geo_periods(conn, days_a: int, period_b: str):
    days_b = days_a if period_b == "prev" else int(period_b)

    if period_b == "prev":
        sql = """
            WITH period_a AS (
                SELECT
                    COUNT(DISTINCT el.prompt_id) AS keywords,
                    COUNT(bm.id) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%') AS mentions,
                    COUNT(bm.id) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%' AND bm.rank_position = 1) AS rank_1,
                    ROUND(AVG(bm.rank_position) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%'), 2) AS avg_rank,
                    COUNT(bm.id) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%' AND bm.sentiment = 'Positive') AS positive,
                    COUNT(bm.id) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%' AND bm.cited_url IS NOT NULL AND bm.cited_url != '') AS citations
                FROM execution_logs el
                LEFT JOIN brand_mentions bm ON bm.log_id = el.id
                WHERE el.captured_at >= CURRENT_DATE - make_interval(days => %s)
                  AND el.raw_response_text NOT LIKE 'Error:%%'
            ),
            period_b AS (
                SELECT
                    COUNT(DISTINCT el.prompt_id) AS keywords,
                    COUNT(bm.id) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%') AS mentions,
                    COUNT(bm.id) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%' AND bm.rank_position = 1) AS rank_1,
                    ROUND(AVG(bm.rank_position) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%'), 2) AS avg_rank,
                    COUNT(bm.id) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%' AND bm.sentiment = 'Positive') AS positive,
                    COUNT(bm.id) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%' AND bm.cited_url IS NOT NULL AND bm.cited_url != '') AS citations
                FROM execution_logs el
                LEFT JOIN brand_mentions bm ON bm.log_id = el.id
                WHERE el.captured_at >= CURRENT_DATE - make_interval(days => %s)
                  AND el.captured_at < CURRENT_DATE - make_interval(days => %s)
                  AND el.raw_response_text NOT LIKE 'Error:%%'
            )
            SELECT
                'a' AS period, a.* FROM period_a a
            UNION ALL
            SELECT
                'b' AS period, b.* FROM period_b b
        """
        rows = conn.execute(sql, (days_a, days_a * 2, days_a)).fetchall()
    else:
        sql = """
            SELECT
                %s AS period,
                COUNT(DISTINCT el.prompt_id) AS keywords,
                COUNT(bm.id) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%') AS mentions,
                COUNT(bm.id) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%' AND bm.rank_position = 1) AS rank_1,
                ROUND(AVG(bm.rank_position) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%'), 2) AS avg_rank,
                COUNT(bm.id) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%' AND bm.sentiment = 'Positive') AS positive,
                COUNT(bm.id) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%' AND bm.cited_url IS NOT NULL AND bm.cited_url != '') AS citations
            FROM execution_logs el
            LEFT JOIN brand_mentions bm ON bm.log_id = el.id
            WHERE el.captured_at >= CURRENT_DATE - make_interval(days => %s)
              AND el.raw_response_text NOT LIKE 'Error:%%'
        """
        row_a = conn.execute(sql, ("a", days_a)).fetchone()
        row_b = conn.execute(sql, ("b", days_b)).fetchone()
        rows = [row_a, row_b]

    result = {"period_a": {}, "period_b": {}, "deltas": {}}
    for r in rows:
        d = dict(r)
        period = d.pop("period")
        for k, v in d.items():
            if v is not None and hasattr(v, '__float__'):
                d[k] = float(v)
        result[f"period_{period}"] = d

    a = result["period_a"]
    b = result["period_b"]
    for key in ["mentions", "rank_1", "avg_rank", "positive", "citations", "keywords"]:
        va = a.get(key)
        vb = b.get(key)
        if va is not None and vb is not None:
            result["deltas"][key] = round(float(va) - float(vb), 2) if va and vb else 0
            if vb and float(vb) != 0:
                result["deltas"][key + "_pct"] = round((float(va) - float(vb)) / float(vb) * 100, 1)
            else:
                result["deltas"][key + "_pct"] = 0

    result["period_a_label"] = f"Last {days_a} days"
    result["period_b_label"] = f"Previous {days_a} days" if period_b == "prev" else f"Last {days_b} days"
    return result


def _compare_seo_periods(conn, days_a: int, period_b: str):
    days_b = days_a if period_b == "prev" else int(period_b)

    sql_template = """
        SELECT
            COUNT(DISTINCT sr.prompt_id) AS keywords,
            COUNT(*) FILTER (WHERE soe.is_target AND soe.rank_position <= 10) AS top_10,
            COUNT(*) FILTER (WHERE soe.is_target AND soe.rank_position = 1) AS rank_1,
            ROUND(AVG(soe.rank_position) FILTER (WHERE soe.is_target), 2) AS avg_rank,
            COUNT(DISTINCT soe.domain) AS competing_domains
        FROM serp_results sr
        LEFT JOIN serp_organic_entries soe ON soe.serp_id = sr.id
        WHERE sr.captured_at >= CURRENT_DATE - make_interval(days => %s)
          {extra_where}
    """

    if period_b == "prev":
        row_a = conn.execute(sql_template.format(extra_where=""), (days_a,)).fetchone()
        row_b = conn.execute(
            sql_template.format(extra_where="AND sr.captured_at < CURRENT_DATE - make_interval(days => %s)"),
            (days_a * 2, days_a),
        ).fetchone()
    else:
        row_a = conn.execute(sql_template.format(extra_where=""), (days_a,)).fetchone()
        row_b = conn.execute(sql_template.format(extra_where=""), (days_b,)).fetchone()

    result = {"period_a": {}, "period_b": {}, "deltas": {}}

    for label, row in [("a", row_a), ("b", row_b)]:
        if row:
            d = dict(row)
            for k, v in d.items():
                if v is not None and hasattr(v, '__float__'):
                    d[k] = float(v)
            result[f"period_{label}"] = d

    a = result["period_a"]
    b = result["period_b"]
    for key in ["keywords", "top_10", "rank_1", "avg_rank", "competing_domains"]:
        va = a.get(key)
        vb = b.get(key)
        if va is not None and vb is not None:
            result["deltas"][key] = round(float(va) - float(vb), 2)
            if vb and float(vb) != 0:
                result["deltas"][key + "_pct"] = round((float(va) - float(vb)) / float(vb) * 100, 1)
            else:
                result["deltas"][key + "_pct"] = 0

    result["period_a_label"] = f"Last {days_a} days"
    result["period_b_label"] = f"Previous {days_a} days" if period_b == "prev" else f"Last {days_b} days"
    return result


@router.get("/comparison/cycles-detail")
async def comparison_cycles_detail(
    context: str = Query("geo"),
    cycle_a: str = Query(""),
    cycle_b: str = Query(""),
):
    """Compare two specific scrape cycles (dates) side by side."""
    if not cycle_a or not cycle_b:
        return JSONResponse({"error": "Both cycle_a and cycle_b required"}, status_code=400)

    def _q(conn):
        if context == "seo":
            return _compare_seo_cycles(conn, cycle_a, cycle_b)
        else:
            return _compare_geo_cycles(conn, cycle_a, cycle_b)

    return await run_db(_q)


def _compare_geo_cycles(conn, cycle_a: str, cycle_b: str):
    """Compare two GEO cycles. Cycle N = Nth full pass through all keywords."""
    from app.engines import VISIBLE_ENGINES
    engine_count = len(VISIBLE_ENGINES)
    engine_ph = ",".join(["%s"] * engine_count)
    engine_list = list(VISIBLE_ENGINES)

    ca, cb = int(cycle_a), int(cycle_b)

    def _geo_cycle_stats(cycle_num):
        start_rn = (cycle_num - 1) * engine_count + 1
        end_rn = cycle_num * engine_count
        row = conn.execute(f"""
            WITH ranked AS (
                SELECT el.id AS log_id, el.prompt_id, el.captured_at,
                       ROW_NUMBER() OVER (PARTITION BY el.prompt_id ORDER BY el.captured_at) AS rn
                FROM execution_logs el
                JOIN prompts p ON p.id = el.prompt_id
                WHERE p.intent_type = 'GEO'
                  AND el.engine_name IN ({engine_ph})
                  AND el.raw_response_text NOT LIKE 'Error:%%'
            ),
            cycle_logs AS (
                SELECT log_id, prompt_id, captured_at FROM ranked
                WHERE rn BETWEEN %s AND %s
            )
            SELECT
                COUNT(DISTINCT cl.prompt_id) AS keywords,
                MIN(cl.captured_at)::text AS started,
                MAX(cl.captured_at)::text AS ended,
                COUNT(bm.id) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%') AS mentions,
                COUNT(bm.id) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%' AND bm.rank_position = 1) AS rank_1,
                ROUND(AVG(bm.rank_position) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%'), 2) AS avg_rank,
                COUNT(bm.id) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%' AND bm.sentiment = 'Positive') AS positive,
                COUNT(bm.id) FILTER (WHERE LOWER(bm.brand_name) LIKE '%%grabon%%' AND bm.cited_url IS NOT NULL AND bm.cited_url != '') AS citations
            FROM cycle_logs cl
            LEFT JOIN brand_mentions bm ON bm.log_id = cl.log_id
        """, engine_list + [start_rn, end_rn]).fetchone()
        return dict(row) if row else {}

    row_a = _geo_cycle_stats(ca)
    row_b = _geo_cycle_stats(cb)

    for d in (row_a, row_b):
        for k, v in d.items():
            if v is not None and hasattr(v, '__float__'):
                d[k] = float(v)

    deltas = {}
    for key in ["mentions", "rank_1", "avg_rank", "positive", "citations", "keywords"]:
        va = row_a.get(key)
        vb = row_b.get(key)
        if va is not None and vb is not None:
            deltas[key] = round(float(va) - float(vb), 2)
            if vb and float(vb) != 0:
                deltas[key + "_pct"] = round((float(va) - float(vb)) / float(vb) * 100, 1)

    date_a = (row_a.get("started") or "")[:10]
    date_b = (row_b.get("started") or "")[:10]
    end_a = (row_a.get("ended") or "")[:10]
    end_b = (row_b.get("ended") or "")[:10]

    return {
        "period_a": row_a,
        "period_b": row_b,
        "deltas": deltas,
        "period_a_label": f"Cycle {ca} ({date_a} to {end_a})",
        "period_b_label": f"Cycle {cb} ({date_b} to {end_b})",
    }


def _compare_seo_cycles(conn, cycle_a: str, cycle_b: str):
    """Compare two SEO cycles. Cycle N = Nth SERP scrape per keyword."""
    ca, cb = int(cycle_a), int(cycle_b)

    def _seo_cycle_stats(cycle_num):
        row = conn.execute("""
            WITH ranked AS (
                SELECT sr.id AS serp_id, sr.prompt_id, sr.captured_at,
                       ROW_NUMBER() OVER (PARTITION BY sr.prompt_id ORDER BY sr.captured_at) AS rn
                FROM serp_results sr
                JOIN prompts p ON p.id = sr.prompt_id
                WHERE p.intent_type <> 'GEO'
            ),
            cycle_serps AS (
                SELECT serp_id, prompt_id, captured_at FROM ranked WHERE rn = %s
            )
            SELECT
                COUNT(DISTINCT cs.prompt_id) AS keywords,
                MIN(cs.captured_at)::text AS started,
                MAX(cs.captured_at)::text AS ended,
                COUNT(*) FILTER (WHERE soe.is_target AND soe.rank_position <= 10) AS top_10,
                COUNT(*) FILTER (WHERE soe.is_target AND soe.rank_position = 1) AS rank_1,
                ROUND(AVG(soe.rank_position) FILTER (WHERE soe.is_target), 2) AS avg_rank,
                COUNT(DISTINCT soe.domain) AS competing_domains
            FROM cycle_serps cs
            LEFT JOIN serp_organic_entries soe ON soe.serp_id = cs.serp_id
        """, [cycle_num]).fetchone()
        return dict(row) if row else {}

    row_a = _seo_cycle_stats(ca)
    row_b = _seo_cycle_stats(cb)

    for d in (row_a, row_b):
        for k, v in d.items():
            if v is not None and hasattr(v, '__float__'):
                d[k] = float(v)

    deltas = {}
    for key in ["keywords", "top_10", "rank_1", "avg_rank", "competing_domains"]:
        va = row_a.get(key)
        vb = row_b.get(key)
        if va is not None and vb is not None:
            deltas[key] = round(float(va) - float(vb), 2)
            if vb and float(vb) != 0:
                deltas[key + "_pct"] = round((float(va) - float(vb)) / float(vb) * 100, 1)

    date_a = (row_a.get("started") or "")[:10]
    date_b = (row_b.get("started") or "")[:10]
    end_a = (row_a.get("ended") or "")[:10]
    end_b = (row_b.get("ended") or "")[:10]

    return {
        "period_a": row_a,
        "period_b": row_b,
        "deltas": deltas,
        "period_a_label": f"Cycle {ca} ({date_a} to {end_a})",
        "period_b_label": f"Cycle {cb} ({date_b} to {end_b})",
    }


# ═══════════════════════════════════════════════════════════════════════
#  Date-parameterized analytics endpoints
# ═══════════════════════════════════════════════════════════════════════


@router.get("/analytics/visibility-ranged")
async def analytics_visibility_ranged(
    days: int = Query(30),
    date_from: str = Query(""),
    date_to: str = Query(""),
    tier: int = Query(0),
):
    """Brand visibility with date range support."""
    date_sql, date_params = _date_filter_sql(days=days, date_from=date_from, date_to=date_to)

    def _query(conn):
        tier_filter = "AND p.tier = %s" if tier > 0 else ""
        params = list(date_params) + ([tier] if tier > 0 else [])
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
            WHERE {date_sql}
              {tier_filter}
              AND (LOWER(bm.brand_name) LIKE '%%grabon%%' OR LOWER(bm.brand_name) LIKE '%%grab on%%'
                   OR LOWER(bm.brand_name) LIKE '%%coupondunia%%' OR LOWER(bm.brand_name) LIKE '%%coupon dunia%%'
                   OR LOWER(bm.brand_name) LIKE '%%cashkaro%%' OR LOWER(bm.brand_name) LIKE '%%cash karo%%'
                   OR LOWER(bm.brand_name) LIKE '%%desidime%%' OR LOWER(bm.brand_name) LIKE '%%desi dime%%')
            GROUP BY day, el.engine_name, brand
            ORDER BY day, el.engine_name, brand
        """, params).fetchall()

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


@router.get("/analytics/daily-summary-ranged")
async def analytics_daily_summary_ranged(
    days: int = Query(30),
    date_from: str = Query(""),
    date_to: str = Query(""),
):
    """Daily summary with full date range support."""
    date_sql, date_params = _date_filter_sql(days=days, date_from=date_from, date_to=date_to)

    def _query(conn):
        rows = conn.execute(f"""
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
            WHERE {date_sql}
              AND el.raw_response_text NOT LIKE 'Error:%%'
            GROUP BY day
            ORDER BY day DESC
        """, date_params).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("avg_rank") is not None:
                d["avg_rank"] = float(d["avg_rank"])
            result.append(d)
        return result
    return {"data": await run_db(_query)}
