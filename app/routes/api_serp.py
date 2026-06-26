import asyncio
import logging
import json
import time
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from app.database import run_db
from app.config import get_settings
from app.routes.api_helpers import valid_uuid as _valid_uuid

router = APIRouter(prefix="/api")
log = logging.getLogger("geo.api.serp")



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
            target_snippet AS (
                SELECT DISTINCT ON (ls.prompt_id)
                    ls.prompt_id, soe.snippet as target_snippet,
                    soe.has_table as target_has_table
                FROM latest_serp ls
                JOIN serp_organic_entries soe ON soe.serp_id = ls.serp_id
                WHERE soe.domain = %s OR soe.domain LIKE %s
                ORDER BY ls.prompt_id, soe.rank_position
            ),
            kw_features AS (
                SELECT ls.prompt_id,
                       array_agg(DISTINCT sf.feature_type) as feature_types
                FROM latest_serp ls
                JOIN serp_features sf ON sf.serp_id = ls.serp_id
                GROUP BY ls.prompt_id
            ),
            combined AS (
                SELECT p.id::text as prompt_id, p.text as keyword, p.merchant_category as category,
                       ls.serp_id::text as serp_id,
                       tgt.serp_rank, top.top_domain,
                       ls.captured_at::text as crawled_at,
                       pt.prev_rank,
                       t3.top_competitors,
                       ar.ai_rank, ac.cited_in_ai,
                       ts.target_snippet, ts.target_has_table,
                       kf.feature_types
                FROM prompts p
                LEFT JOIN latest_serp ls ON ls.prompt_id = p.id
                LEFT JOIN target_organic tgt ON tgt.prompt_id = p.id
                LEFT JOIN top_organic top ON top.prompt_id = p.id
                LEFT JOIN top3_organic t3 ON t3.prompt_id = p.id
                LEFT JOIN prev_target pt ON pt.prompt_id = p.id
                LEFT JOIN ai_rank ar ON ar.prompt_id = p.id
                LEFT JOIN ai_cited ac ON ac.prompt_id = p.id
                LEFT JOIN target_snippet ts ON ts.prompt_id = p.id
                LEFT JOIN kw_features kf ON kf.prompt_id = p.id
                WHERE {where}
            )
        """
        all_params = [target_domain, f"%.{target_domain}", target_domain, f"%.{target_domain}", target_domain, f"%.{target_domain}", target_domain, f"%.{target_domain}"] + params

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

            total_crawls = conn.execute("SELECT COUNT(*) as cnt FROM serp_results").fetchone()["cnt"]

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

            comp = int(seo_cycle["completed"])
            tot_kw = int(seo_cycle["total_kw"])
            inn = int(seo_cycle["in_next"]) if seo_cycle["in_next"] else 0
            cycle_pct = round(inn / max(tot_kw, 1) * 100, 1)
            if cycle_pct >= 100 and inn < tot_kw:
                cycle_pct = 99.9
            if cycle_pct == 0 and inn > 0:
                cycle_pct = 0.1

            return {
                **dict(queue),
                **(dict(kpis) if kpis else {"top10": 0, "avg_rank": None}),
                "seo_cycle_completed": comp,
                "seo_cycle_pct": cycle_pct,
                "seo_cycle_in_next": inn,
                "seo_cycle_total": tot_kw,
                "total_crawls": total_crawls,
            }

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
            "seo_cycle_completed": data.get("seo_cycle_completed", 0),
            "seo_cycle_pct": data.get("seo_cycle_pct", 0),
            "seo_cycle_in_next": data.get("seo_cycle_in_next", 0),
            "seo_cycle_total": data.get("seo_cycle_total", 0),
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



@router.get("/serp/categories")
async def serp_categories():
    """Distinct categories for filter dropdown."""
    def _q(conn):
        rows = conn.execute(
            "SELECT DISTINCT merchant_category FROM prompts WHERE merchant_category IS NOT NULL ORDER BY merchant_category"
        ).fetchall()
        return [r["merchant_category"] for r in rows]
    return await run_db(_q)



# ─── Enhanced: Daily SEO Rank Trend ──────────────────────────────────

@router.get("/serp/daily-rank-trend")
async def serp_daily_rank_trend(days: int = Query(30)):
    """Daily avg rank, top10 count, and keywords tracked over time."""
    settings = get_settings()
    td = settings.target_domain

    def _q(conn):
        rows = conn.execute("""
            WITH daily AS (
                SELECT sr.captured_at::date AS day,
                       COUNT(DISTINCT sr.prompt_id) AS keywords_crawled,
                       COUNT(DISTINCT sr.prompt_id) FILTER (
                           WHERE soe.rank_position IS NOT NULL AND soe.rank_position <= 10
                       ) AS top10_count,
                       ROUND(AVG(soe.rank_position)::numeric, 1) AS avg_rank
                FROM serp_results sr
                JOIN serp_organic_entries soe ON soe.serp_id = sr.id
                    AND (soe.domain = %s OR soe.domain LIKE %s)
                WHERE sr.captured_at >= CURRENT_DATE - make_interval(days => %s)
                GROUP BY sr.captured_at::date
            )
            SELECT day::text, keywords_crawled, top10_count, avg_rank
            FROM daily ORDER BY day
        """, (td, f"%.{td}", days)).fetchall()
        return [dict(r) for r in rows]
    return await run_db(_q)


# ─── Enhanced: Overlap Stats & Citation Metadata ─────────────────────

@router.get("/serp/overlap-stats")
async def serp_overlap_stats():
    """Aggregate overlap statistics: rates, engine distribution, top domains."""
    settings = get_settings()

    def _q(conn):
        stats = conn.execute("""
            SELECT
                COUNT(*) AS total_overlaps,
                COUNT(DISTINCT cited_url) AS unique_urls,
                COUNT(DISTINCT prompt_id) AS keywords_with_overlap,
                COUNT(DISTINCT engine_name) AS engines_with_overlap
            FROM citation_serp_overlaps
        """).fetchone()

        by_engine = conn.execute("""
            SELECT engine_name, COUNT(*) AS overlap_count,
                   COUNT(DISTINCT cited_url) AS unique_urls,
                   COUNT(DISTINCT prompt_id) AS keywords
            FROM citation_serp_overlaps
            GROUP BY engine_name ORDER BY overlap_count DESC
        """).fetchall()

        top_domains = conn.execute("""
            SELECT
                SPLIT_PART(REPLACE(REPLACE(cited_url, 'https://', ''), 'http://', ''), '/', 1) AS domain,
                COUNT(*) AS overlap_count,
                COUNT(DISTINCT engine_name) AS engines,
                COUNT(DISTINCT prompt_id) AS keywords,
                BOOL_OR(LOWER(SPLIT_PART(REPLACE(REPLACE(cited_url, 'https://', ''), 'http://', ''), '/', 1)) LIKE %s) AS is_target
            FROM citation_serp_overlaps
            GROUP BY domain ORDER BY overlap_count DESC LIMIT 15
        """, (f"%{settings.target_domain}%",)).fetchall()

        total_kw = conn.execute(
            "SELECT COUNT(*) AS cnt FROM prompts WHERE intent_type = 'GEO'"
        ).fetchone()["cnt"]

        return {
            "total_overlaps": stats["total_overlaps"],
            "unique_urls": stats["unique_urls"],
            "keywords_with_overlap": stats["keywords_with_overlap"],
            "engines_with_overlap": stats["engines_with_overlap"],
            "overlap_rate": round(stats["keywords_with_overlap"] / max(total_kw, 1) * 100, 1),
            "by_engine": [dict(r) for r in by_engine],
            "top_domains": [dict(r) for r in top_domains],
        }
    return await run_db(_q)
