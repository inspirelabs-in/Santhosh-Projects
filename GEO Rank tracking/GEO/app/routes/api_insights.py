import logging
import json
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from app.database import run_db
from app.config import get_settings

router = APIRouter(prefix="/api")
log = logging.getLogger("geo.api.insights")



# ─── Feature 1: GEO vs SEO Correlation Matrix ─────────────────────────

@router.get("/insights/geo-seo-correlation")
async def geo_seo_correlation():
    def _q(conn):
        rows = conn.execute("""
            WITH geo_ranks AS (
                SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                    el.prompt_id, el.engine_name,
                    bm.rank_position AS geo_rank,
                    bm.sentiment
                FROM execution_logs el
                JOIN brand_mentions bm ON bm.log_id = el.id
                WHERE bm.is_target_brand = TRUE
                ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
            ),
            seo_ranks AS (
                SELECT DISTINCT ON (sr.prompt_id)
                    sr.prompt_id,
                    soe.rank_position AS seo_rank,
                    soe.domain
                FROM serp_results sr
                JOIN serp_organic_entries soe ON soe.serp_id = sr.id
                WHERE soe.is_target = TRUE
                ORDER BY sr.prompt_id, sr.captured_at DESC
            )
            SELECT
                p.text AS keyword,
                p.merchant_category AS category,
                g.engine_name,
                g.geo_rank,
                g.sentiment,
                s.seo_rank
            FROM geo_ranks g
            JOIN seo_ranks s ON s.prompt_id = g.prompt_id
            JOIN prompts p ON p.id = g.prompt_id
            ORDER BY p.text, g.engine_name
        """).fetchall()

        summary = conn.execute("""
            WITH geo AS (
                SELECT DISTINCT ON (el.prompt_id)
                    el.prompt_id,
                    MIN(bm.rank_position) AS best_geo
                FROM execution_logs el
                JOIN brand_mentions bm ON bm.log_id = el.id
                WHERE bm.is_target_brand = TRUE
                GROUP BY el.prompt_id
                ORDER BY el.prompt_id
            ),
            seo AS (
                SELECT DISTINCT ON (sr.prompt_id)
                    sr.prompt_id,
                    MIN(soe.rank_position) AS best_seo
                FROM serp_results sr
                JOIN serp_organic_entries soe ON soe.serp_id = sr.id
                WHERE soe.is_target = TRUE
                GROUP BY sr.prompt_id
                ORDER BY sr.prompt_id
            )
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE g.best_geo <= 3 AND s.best_seo <= 10) AS strong_both,
                COUNT(*) FILTER (WHERE g.best_geo <= 3 AND (s.best_seo > 10 OR s.best_seo IS NULL)) AS strong_geo_weak_seo,
                COUNT(*) FILTER (WHERE (g.best_geo > 3 OR g.best_geo IS NULL) AND s.best_seo <= 10) AS weak_geo_strong_seo,
                COUNT(*) FILTER (WHERE g.best_geo > 3 AND s.best_seo > 10) AS weak_both
            FROM geo g
            JOIN seo s ON s.prompt_id = g.prompt_id
        """).fetchone()

        return {"points": [dict(r) for r in rows], "quadrants": dict(summary) if summary else {}}
    return await run_db(_q)


# ─── Feature 2: Competitor Domain War Room ─────────────────────────────

@router.get("/insights/competitor-warroom")
async def competitor_warroom():
    def _q(conn):
        share = conn.execute("""
            WITH latest_serps AS (
                SELECT DISTINCT ON (prompt_id)
                    id AS serp_id, prompt_id
                FROM serp_results
                ORDER BY prompt_id, captured_at DESC
            ),
            all_entries AS (
                SELECT soe.domain, COUNT(*) AS appearances,
                       AVG(soe.rank_position) AS avg_rank,
                       COUNT(*) FILTER (WHERE soe.rank_position <= 3) AS top3,
                       COUNT(*) FILTER (WHERE soe.rank_position = 1) AS rank1
                FROM latest_serps ls
                JOIN serp_organic_entries soe ON soe.serp_id = ls.serp_id
                WHERE soe.rank_position <= 20
                GROUP BY soe.domain
                ORDER BY appearances DESC
                LIMIT 30
            )
            SELECT * FROM all_entries
        """).fetchall()

        head2head = conn.execute("""
            WITH latest_serps AS (
                SELECT DISTINCT ON (prompt_id)
                    id AS serp_id, prompt_id
                FROM serp_results
                ORDER BY prompt_id, captured_at DESC
            ),
            target_kws AS (
                SELECT ls.serp_id, ls.prompt_id,
                       soe.rank_position AS target_rank
                FROM latest_serps ls
                JOIN serp_organic_entries soe ON soe.serp_id = ls.serp_id
                WHERE soe.is_target = TRUE
            ),
            comp_entries AS (
                SELECT tk.prompt_id, soe.domain,
                       soe.rank_position AS comp_rank,
                       tk.target_rank
                FROM target_kws tk
                JOIN serp_organic_entries soe ON soe.serp_id = tk.serp_id
                WHERE soe.is_target = FALSE AND soe.rank_position <= 20
            )
            SELECT domain,
                   COUNT(*) AS overlap_count,
                   COUNT(*) FILTER (WHERE comp_rank < target_rank) AS beats_us,
                   COUNT(*) FILTER (WHERE comp_rank > target_rank) AS we_beat,
                   ROUND(AVG(comp_rank)::numeric, 1) AS their_avg,
                   ROUND(AVG(target_rank)::numeric, 1) AS our_avg
            FROM comp_entries
            GROUP BY domain
            ORDER BY overlap_count DESC
            LIMIT 15
        """).fetchall()

        new_entrants = conn.execute("""
            WITH recent AS (
                SELECT soe.domain, MIN(sr.captured_at) AS first_seen
                FROM serp_results sr
                JOIN serp_organic_entries soe ON soe.serp_id = sr.id
                WHERE soe.rank_position <= 10
                GROUP BY soe.domain
                HAVING MIN(sr.captured_at) > NOW() - INTERVAL '7 days'
            )
            SELECT domain, first_seen
            FROM recent
            ORDER BY first_seen DESC
            LIMIT 20
        """).fetchall()

        return {
            "share_of_serp": [dict(r) for r in share],
            "head_to_head": [dict(r) for r in head2head],
            "new_entrants": [dict(r) for r in new_entrants],
        }
    return await run_db(_q)


# ─── Feature 3: Cycle-over-Cycle Dashboard ────────────────────────────

@router.get("/insights/cycle-changelog")
async def cycle_changelog():
    def _q(conn):
        rows = conn.execute("""
            WITH daily AS (
                SELECT
                    DATE(el.captured_at) AS day,
                    COUNT(DISTINCT el.prompt_id) AS keywords_scraped,
                    COUNT(*) AS total_scrapes,
                    COUNT(DISTINCT bm.id) FILTER (WHERE bm.is_target_brand) AS target_mentions,
                    COUNT(*) FILTER (WHERE bm.is_target_brand AND bm.rank_position = 1) AS rank1_count,
                    ROUND(AVG(bm.rank_position) FILTER (WHERE bm.is_target_brand)::numeric, 2) AS avg_rank,
                    COUNT(DISTINCT bm.id) FILTER (WHERE bm.is_target_brand AND bm.cited_url IS NOT NULL) AS citations
                FROM execution_logs el
                LEFT JOIN brand_mentions bm ON bm.log_id = el.id
                GROUP BY DATE(el.captured_at)
                ORDER BY day DESC
                LIMIT 30
            )
            SELECT day, keywords_scraped, total_scrapes, target_mentions,
                   rank1_count, avg_rank, citations,
                   target_mentions - LAG(target_mentions) OVER (ORDER BY day) AS mentions_delta,
                   rank1_count - LAG(rank1_count) OVER (ORDER BY day) AS rank1_delta,
                   ROUND((avg_rank - LAG(avg_rank) OVER (ORDER BY day))::numeric, 2) AS rank_delta,
                   citations - LAG(citations) OVER (ORDER BY day) AS citations_delta
            FROM daily
            ORDER BY day DESC
        """).fetchall()
        return [dict(r) for r in rows]
    return await run_db(_q)


# ─── Feature 4: PAA Intelligence Hub ──────────────────────────────────

@router.get("/insights/paa-hub")
async def paa_hub():
    def _q(conn):
        questions = conn.execute("""
            WITH paa_data AS (
                SELECT
                    sf.question_text,
                    p.text AS keyword,
                    p.merchant_category AS category,
                    sr.captured_at
                FROM serp_features sf
                JOIN serp_results sr ON sr.id = sf.serp_id
                JOIN prompts p ON p.id = sr.prompt_id
                WHERE sf.feature_type = 'paa' AND sf.question_text IS NOT NULL
            )
            SELECT
                question_text,
                COUNT(*) AS frequency,
                COUNT(DISTINCT keyword) AS keyword_count,
                array_agg(DISTINCT category) AS categories,
                array_agg(DISTINCT keyword ORDER BY keyword) AS keywords,
                MAX(captured_at) AS last_seen
            FROM paa_data
            GROUP BY question_text
            ORDER BY frequency DESC
            LIMIT 100
        """).fetchall()

        trends = conn.execute("""
            SELECT
                DATE(sr.captured_at) AS day,
                COUNT(*) AS paa_count,
                COUNT(DISTINCT sf.question_text) AS unique_questions
            FROM serp_features sf
            JOIN serp_results sr ON sr.id = sf.serp_id
            WHERE sf.feature_type = 'paa'
            GROUP BY DATE(sr.captured_at)
            ORDER BY day DESC
            LIMIT 30
        """).fetchall()

        category_dist = conn.execute("""
            SELECT
                p.merchant_category AS category,
                COUNT(*) AS paa_count,
                COUNT(DISTINCT sf.question_text) AS unique_questions
            FROM serp_features sf
            JOIN serp_results sr ON sr.id = sf.serp_id
            JOIN prompts p ON p.id = sr.prompt_id
            WHERE sf.feature_type = 'paa'
            GROUP BY p.merchant_category
            ORDER BY paa_count DESC
        """).fetchall()

        return {
            "questions": [dict(r) for r in questions],
            "trends": [dict(r) for r in trends],
            "by_category": [dict(r) for r in category_dist],
        }
    return await run_db(_q)


# ─── Feature 5: SERP Volatility Index ─────────────────────────────────

@router.get("/insights/serp-volatility")
async def serp_volatility():
    def _q(conn):
        per_keyword = conn.execute("""
            WITH ranked_serps AS (
                SELECT
                    sr.prompt_id,
                    sr.captured_at::date AS day,
                    soe.domain,
                    soe.rank_position,
                    ROW_NUMBER() OVER (PARTITION BY sr.prompt_id, sr.captured_at::date ORDER BY sr.captured_at DESC) AS rn
                FROM serp_results sr
                JOIN serp_organic_entries soe ON soe.serp_id = sr.id
                WHERE soe.rank_position <= 10
                  AND sr.captured_at > NOW() - INTERVAL '14 days'
            ),
            daily_top10 AS (
                SELECT prompt_id, day, domain, rank_position
                FROM ranked_serps WHERE rn <= 10
            ),
            pair_changes AS (
                SELECT
                    a.prompt_id,
                    a.day,
                    COUNT(*) FILTER (
                        WHERE NOT EXISTS (
                            SELECT 1 FROM daily_top10 b
                            WHERE b.prompt_id = a.prompt_id
                              AND b.day = a.day - 1
                              AND b.domain = a.domain
                              AND b.rank_position = a.rank_position
                        )
                    ) AS changes
                FROM daily_top10 a
                GROUP BY a.prompt_id, a.day
            )
            SELECT
                p.text AS keyword,
                p.merchant_category AS category,
                ROUND(AVG(pc.changes)::numeric, 1) AS volatility_score,
                COUNT(DISTINCT pc.day) AS days_tracked
            FROM pair_changes pc
            JOIN prompts p ON p.id = pc.prompt_id
            GROUP BY p.text, p.merchant_category
            HAVING COUNT(DISTINCT pc.day) >= 2
            ORDER BY AVG(pc.changes) DESC
            LIMIT 50
        """).fetchall()

        daily_global = conn.execute("""
            WITH daily_domains AS (
                SELECT
                    sr.captured_at::date AS day,
                    sr.prompt_id,
                    array_agg(DISTINCT soe.domain ORDER BY soe.domain) AS domains
                FROM serp_results sr
                JOIN serp_organic_entries soe ON soe.serp_id = sr.id
                WHERE soe.rank_position <= 10
                  AND sr.captured_at > NOW() - INTERVAL '30 days'
                GROUP BY sr.captured_at::date, sr.prompt_id
            ),
            prev AS (
                SELECT day, prompt_id, domains,
                       LAG(domains) OVER (PARTITION BY prompt_id ORDER BY day) AS prev_domains
                FROM daily_domains
            )
            SELECT day,
                   COUNT(*) AS keywords_checked,
                   AVG(
                       CASE WHEN prev_domains IS NOT NULL
                       THEN (SELECT COUNT(*) FROM unnest(domains) d WHERE d != ALL(prev_domains))
                       END
                   )::numeric(5,2) AS avg_new_domains
            FROM prev
            WHERE prev_domains IS NOT NULL
            GROUP BY day
            ORDER BY day DESC
            LIMIT 30
        """).fetchall()

        return {
            "per_keyword": [dict(r) for r in per_keyword],
            "daily_global": [dict(r) for r in daily_global],
        }
    return await run_db(_q)


# ─── Feature 6: Related Keywords Discovery ────────────────────────────

@router.get("/insights/related-keywords")
async def related_keywords():
    def _q(conn):
        from_paa = conn.execute("""
            SELECT
                p.text AS source_keyword,
                p.merchant_category AS category,
                sf.question_text AS related_question,
                COUNT(*) AS occurrences
            FROM serp_features sf
            JOIN serp_results sr ON sr.id = sf.serp_id
            JOIN prompts p ON p.id = sr.prompt_id
            WHERE sf.feature_type = 'paa' AND sf.question_text IS NOT NULL
            GROUP BY p.text, p.merchant_category, sf.question_text
            ORDER BY occurrences DESC
            LIMIT 80
        """).fetchall()

        from_answer_box = conn.execute("""
            SELECT
                p.text AS source_keyword,
                p.merchant_category AS category,
                sf.title AS answer_title,
                sf.snippet AS answer_snippet,
                sf.url AS answer_url
            FROM serp_features sf
            JOIN serp_results sr ON sr.id = sf.serp_id
            JOIN prompts p ON p.id = sr.prompt_id
            WHERE sf.feature_type = 'answer_box'
              AND sf.snippet IS NOT NULL
            ORDER BY sr.captured_at DESC
            LIMIT 50
        """).fetchall()

        return {
            "from_paa": [dict(r) for r in from_paa],
            "from_answer_box": [dict(r) for r in from_answer_box],
        }
    return await run_db(_q)


# ─── Feature 7: Content Blueprint from Citations ──────────────────────

@router.get("/insights/content-blueprint")
async def content_blueprint():
    def _q(conn):
        cited_pages = conn.execute("""
            SELECT
                cp.domain,
                cp.title,
                cp.url,
                cp.word_count,
                cp.h1_tags,
                cp.schema_types,
                cp.meta_description,
                COUNT(DISTINCT cso.engine_name) AS engines_citing,
                COUNT(DISTINCT cso.prompt_id) AS keywords_citing,
                array_agg(DISTINCT cso.engine_name) AS engines
            FROM citation_pages cp
            JOIN citation_serp_overlaps cso ON cso.cited_url = cp.url
            WHERE cp.fetch_status = 200
            GROUP BY cp.id, cp.domain, cp.title, cp.url, cp.word_count,
                     cp.h1_tags, cp.schema_types, cp.meta_description
            ORDER BY engines_citing DESC, keywords_citing DESC
            LIMIT 30
        """).fetchall()

        domain_patterns = conn.execute("""
            SELECT
                cp.domain,
                COUNT(*) AS pages_cited,
                ROUND(AVG(cp.word_count)::numeric) AS avg_word_count,
                COUNT(DISTINCT cso.engine_name) AS engines,
                COUNT(DISTINCT cso.prompt_id) AS keywords
            FROM citation_pages cp
            JOIN citation_serp_overlaps cso ON cso.cited_url = cp.url
            WHERE cp.fetch_status = 200
            GROUP BY cp.domain
            ORDER BY pages_cited DESC
            LIMIT 20
        """).fetchall()

        schema_stats = conn.execute("""
            SELECT
                unnest(cp.schema_types) AS schema_type,
                COUNT(*) AS count
            FROM citation_pages cp
            WHERE cp.fetch_status = 200 AND cp.schema_types IS NOT NULL
            GROUP BY schema_type
            ORDER BY count DESC
            LIMIT 15
        """).fetchall()

        return {
            "cited_pages": [dict(r) for r in cited_pages],
            "domain_patterns": [dict(r) for r in domain_patterns],
            "schema_stats": [dict(r) for r in schema_stats],
        }
    return await run_db(_q)


# ─── Feature 8: Feature↔Citation Correlation ──────────────────────────

@router.get("/insights/feature-citation-correlation")
async def feature_citation_correlation():
    def _q(conn):
        rows = conn.execute("""
            WITH kw_features AS (
                SELECT
                    sr.prompt_id,
                    sf.feature_type,
                    COUNT(*) AS feature_count
                FROM serp_features sf
                JOIN serp_results sr ON sr.id = sf.serp_id
                GROUP BY sr.prompt_id, sf.feature_type
            ),
            kw_mentions AS (
                SELECT
                    el.prompt_id,
                    COUNT(DISTINCT bm.id) FILTER (WHERE bm.is_target_brand) AS target_mentions,
                    ROUND(AVG(bm.rank_position) FILTER (WHERE bm.is_target_brand)::numeric, 2) AS avg_geo_rank,
                    COUNT(DISTINCT bm.id) FILTER (WHERE bm.is_target_brand AND bm.cited_url IS NOT NULL) AS citations
                FROM execution_logs el
                LEFT JOIN brand_mentions bm ON bm.log_id = el.id
                GROUP BY el.prompt_id
            )
            SELECT
                kf.feature_type,
                COUNT(DISTINCT kf.prompt_id) AS keywords_with_feature,
                ROUND(AVG(km.target_mentions)::numeric, 2) AS avg_mentions,
                ROUND(AVG(km.avg_geo_rank)::numeric, 2) AS avg_geo_rank,
                ROUND(AVG(km.citations)::numeric, 2) AS avg_citations,
                ROUND(AVG(kf.feature_count)::numeric, 1) AS avg_feature_count
            FROM kw_features kf
            JOIN kw_mentions km ON km.prompt_id = kf.prompt_id
            GROUP BY kf.feature_type
            ORDER BY avg_mentions DESC
        """).fetchall()

        without_features = conn.execute("""
            WITH has_feature AS (
                SELECT DISTINCT sr.prompt_id
                FROM serp_features sf
                JOIN serp_results sr ON sr.id = sf.serp_id
            ),
            no_feature AS (
                SELECT el.prompt_id
                FROM execution_logs el
                WHERE el.prompt_id NOT IN (SELECT prompt_id FROM has_feature)
                GROUP BY el.prompt_id
            ),
            kw_mentions AS (
                SELECT
                    el.prompt_id,
                    COUNT(DISTINCT bm.id) FILTER (WHERE bm.is_target_brand) AS target_mentions,
                    ROUND(AVG(bm.rank_position) FILTER (WHERE bm.is_target_brand)::numeric, 2) AS avg_geo_rank,
                    COUNT(DISTINCT bm.id) FILTER (WHERE bm.is_target_brand AND bm.cited_url IS NOT NULL) AS citations
                FROM execution_logs el
                LEFT JOIN brand_mentions bm ON bm.log_id = el.id
                WHERE el.prompt_id IN (SELECT prompt_id FROM no_feature)
                GROUP BY el.prompt_id
            )
            SELECT
                COUNT(*) AS keywords_without_features,
                ROUND(AVG(target_mentions)::numeric, 2) AS avg_mentions,
                ROUND(AVG(avg_geo_rank)::numeric, 2) AS avg_geo_rank,
                ROUND(AVG(citations)::numeric, 2) AS avg_citations
            FROM kw_mentions
        """).fetchone()

        return {
            "with_features": [dict(r) for r in rows],
            "without_features": dict(without_features) if without_features else {},
        }
    return await run_db(_q)


# ─── Feature 9: Snippet Content Analysis ──────────────────────────────

@router.get("/insights/snippet-analysis")
async def snippet_analysis():
    def _q(conn):
        snippet_stats = conn.execute("""
            WITH latest_serps AS (
                SELECT DISTINCT ON (prompt_id)
                    id AS serp_id, prompt_id
                FROM serp_results
                ORDER BY prompt_id, captured_at DESC
            ),
            target_snippets AS (
                SELECT
                    p.text AS keyword,
                    p.merchant_category AS category,
                    soe.snippet,
                    soe.title,
                    soe.rank_position,
                    soe.has_table,
                    LENGTH(soe.snippet) AS snippet_length
                FROM latest_serps ls
                JOIN serp_organic_entries soe ON soe.serp_id = ls.serp_id
                JOIN prompts p ON p.id = ls.prompt_id
                WHERE soe.is_target = TRUE
            )
            SELECT
                keyword, category, snippet, title, rank_position,
                has_table, snippet_length
            FROM target_snippets
            ORDER BY rank_position
        """).fetchall()

        length_vs_rank = conn.execute("""
            WITH latest_serps AS (
                SELECT DISTINCT ON (prompt_id)
                    id AS serp_id
                FROM serp_results
                ORDER BY prompt_id, captured_at DESC
            )
            SELECT
                CASE
                    WHEN LENGTH(soe.snippet) < 80 THEN 'Short (<80)'
                    WHEN LENGTH(soe.snippet) < 160 THEN 'Medium (80-160)'
                    ELSE 'Long (160+)'
                END AS length_bucket,
                ROUND(AVG(soe.rank_position)::numeric, 1) AS avg_rank,
                COUNT(*) AS count,
                COUNT(*) FILTER (WHERE soe.rank_position <= 3) AS top3_count
            FROM latest_serps ls
            JOIN serp_organic_entries soe ON soe.serp_id = ls.serp_id
            WHERE soe.snippet IS NOT NULL AND soe.rank_position <= 20
            GROUP BY length_bucket
            ORDER BY avg_rank
        """).fetchall()

        table_impact = conn.execute("""
            WITH latest_serps AS (
                SELECT DISTINCT ON (prompt_id)
                    id AS serp_id
                FROM serp_results
                ORDER BY prompt_id, captured_at DESC
            )
            SELECT
                has_table,
                ROUND(AVG(rank_position)::numeric, 1) AS avg_rank,
                COUNT(*) AS count,
                COUNT(*) FILTER (WHERE rank_position <= 3) AS top3_count,
                COUNT(*) FILTER (WHERE rank_position <= 10) AS top10_count
            FROM latest_serps ls
            JOIN serp_organic_entries soe ON soe.serp_id = ls.serp_id
            WHERE soe.rank_position <= 20
            GROUP BY has_table
        """).fetchall()

        return {
            "target_snippets": [dict(r) for r in snippet_stats],
            "length_vs_rank": [dict(r) for r in length_vs_rank],
            "table_impact": [dict(r) for r in table_impact],
        }
    return await run_db(_q)


# ─── Feature 10: Smart Alerts ─────────────────────────────────────────

@router.get("/insights/smart-alerts")
async def smart_alerts():
    """Read persisted alerts from notifications table (generated by alert_engine)."""
    def _q(conn):
        rows = conn.execute("""
            SELECT id, type, title, message, severity, is_read, metadata, created_at
            FROM notifications
            WHERE type != 'system'
              AND created_at > NOW() - INTERVAL '7 days'
            ORDER BY
                CASE severity
                    WHEN 'critical' THEN 0
                    WHEN 'warning' THEN 1
                    WHEN 'info' THEN 2
                    ELSE 3
                END,
                created_at DESC
            LIMIT 50
        """).fetchall()
        alerts = []
        for r in rows:
            meta = r.get("metadata") or {}
            if isinstance(meta, str):
                import json
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            alerts.append({
                "id": str(r["id"]),
                "type": r["type"],
                "severity": r["severity"],
                "title": r["title"],
                "detail": r["message"],
                "is_read": r["is_read"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                **{k: v for k, v in meta.items() if k in ("keyword", "engine", "domain")},
            })
        return alerts
    return await run_db(_q)
