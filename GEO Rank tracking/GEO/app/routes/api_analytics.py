import logging
import json
import time
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from app.database import run_db
from app.config import get_settings
from app.engines import ALL_ENGINES
from app.routes.api_helpers import valid_uuid as _valid_uuid

router = APIRouter(prefix="/api")
log = logging.getLogger("geo.api.analytics")


COMPETITORS = ["GrabOn", "CouponDunia", "CashKaro", "DesiDime"]
ALL_ANALYTICS_ENGINES = ALL_ENGINES


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
                    WHEN bm.is_target_brand = TRUE THEN 'GrabOn'
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
              AND (bm.is_target_brand = TRUE
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
            WHERE bm.is_target_brand = TRUE
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
                WHERE el.captured_at >= NOW() - INTERVAL '30 days'
                    AND el.raw_response_text NOT LIKE 'Error:%%' {tier_filter}
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
                AND bm.is_target_brand = TRUE
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
                    WHEN bm.is_target_brand = TRUE THEN 'GrabOn'
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
            WHERE (bm.is_target_brand = TRUE
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
                WHERE el.captured_at >= NOW() - INTERVAL '30 days'
                    AND el.raw_response_text NOT LIKE 'Error:%%' {tier_filter}
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
                        WHEN bm.is_target_brand = TRUE THEN 'GrabOn'
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
                WHERE el.captured_at >= NOW() - INTERVAL '30 days'
                    AND el.raw_response_text NOT LIKE 'Error:%%'
                ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
            )
            SELECT DISTINCT p.text AS keyword, p.merchant_category
            FROM latest l
            JOIN brand_mentions bm ON bm.log_id = l.log_id
            JOIN prompts p ON p.id = l.prompt_id
            WHERE bm.is_target_brand = TRUE AND bm.rank_position = 1
              {tier_filter}
        """, params).fetchall()

        opportunity = conn.execute(f"""
            WITH latest AS (
                SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                    el.prompt_id, el.engine_name, el.id AS log_id
                FROM execution_logs el
                WHERE el.captured_at >= NOW() - INTERVAL '30 days'
                    AND el.raw_response_text NOT LIKE 'Error:%%'
                ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
            ),
            grabon_mentions AS (
                SELECT l.prompt_id, bm.rank_position
                FROM latest l
                JOIN brand_mentions bm ON bm.log_id = l.log_id
                WHERE bm.is_target_brand = TRUE
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
                WHERE bm.is_target_brand = TRUE
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
            WHERE bm.is_target_brand = TRUE
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
            WHERE bm.is_target_brand = TRUE
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
            WHERE bm.is_target_brand = TRUE
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
            WHERE bm.is_target_brand = TRUE
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
                  AND (bm.is_target_brand = TRUE
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
                  AND bm.is_target_brand = TRUE
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
              AND bm.is_target_brand = TRUE
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



@router.get("/analytics/category-rollup")
async def analytics_category_rollup():
    """Per-category aggregate: keywords, mentions, avg rank, #1 count, sentiment, citations."""
    def _query(conn):
        rows = conn.execute("""
            WITH latest AS (
                SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                    el.prompt_id, el.engine_name, el.id AS log_id
                FROM execution_logs el
                WHERE el.captured_at >= NOW() - INTERVAL '30 days'
                    AND el.raw_response_text NOT LIKE 'Error:%%'
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
                AND (bm.is_target_brand = TRUE)
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
                WHERE el.captured_at >= NOW() - INTERVAL '30 days'
                    AND el.raw_response_text NOT LIKE 'Error:%%'
                ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
            ),
            grabon_ranks AS (
                SELECT l.prompt_id, l.engine_name, bm.rank_position
                FROM latest l
                LEFT JOIN brand_mentions bm ON bm.log_id = l.log_id
                    AND (bm.is_target_brand = TRUE)
            ),
            competitor_leaders AS (
                SELECT l.prompt_id, l.engine_name,
                       MIN(bm.rank_position) AS best_rank,
                       (array_agg(bm.brand_name ORDER BY bm.rank_position))[1] AS leader
                FROM latest l
                JOIN brand_mentions bm ON bm.log_id = l.log_id
                    AND bm.is_target_brand = FALSE
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
                WHERE el.captured_at >= NOW() - INTERVAL '30 days'
                    AND el.raw_response_text NOT LIKE 'Error:%%'
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
                AND (bm.is_target_brand = TRUE)
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
                COUNT(bm.id) FILTER (WHERE bm.is_target_brand = TRUE) AS grabon_mentions,
                COUNT(bm.id) FILTER (WHERE bm.is_target_brand = TRUE AND bm.rank_position = 1) AS rank_1_count,
                ROUND(AVG(bm.rank_position) FILTER (WHERE bm.is_target_brand = TRUE), 1) AS avg_rank,
                COUNT(bm.id) FILTER (WHERE bm.is_target_brand = TRUE AND bm.sentiment = 'Positive') AS positive,
                COUNT(bm.id) FILTER (WHERE bm.is_target_brand = TRUE AND bm.sentiment = 'Neutral') AS neutral,
                COUNT(bm.id) FILTER (WHERE bm.is_target_brand = TRUE AND bm.sentiment = 'Negative') AS negative,
                COUNT(bm.id) FILTER (WHERE bm.is_target_brand = TRUE
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
                WHERE bm.is_target_brand = TRUE
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
                WHERE bm.is_target_brand = TRUE
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
                    AND (bm.is_target_brand = TRUE)
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
                AND (bm.is_target_brand = TRUE)
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



# ─── Enhanced: AI Movement Engine Breakdown ──────────────────────────

@router.get("/analytics/ai-movement-breakdown")
async def analytics_ai_movement_breakdown(days: int = Query(7)):
    """Per-engine AI rank movement breakdown + daily trend."""

    def _q(conn):
        engine_breakdown = conn.execute("""
            WITH ranked AS (
                SELECT el.prompt_id, el.engine_name,
                       bm.rank_position,
                       ROW_NUMBER() OVER (PARTITION BY el.prompt_id, el.engine_name ORDER BY el.captured_at DESC) AS rn
                FROM execution_logs el
                JOIN brand_mentions bm ON bm.log_id = el.id
                WHERE bm.is_target_brand = TRUE
                  AND el.captured_at >= CURRENT_DATE - make_interval(days => %s)
            ),
            curr AS (SELECT prompt_id, engine_name, rank_position FROM ranked WHERE rn = 1),
            prev AS (SELECT prompt_id, engine_name, rank_position FROM ranked WHERE rn = 2)
            SELECT c.engine_name,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE c.rank_position < p.rank_position) AS improved,
                   COUNT(*) FILTER (WHERE c.rank_position > p.rank_position) AS declined,
                   COUNT(*) FILTER (WHERE c.rank_position = p.rank_position) AS stable,
                   ROUND(AVG(c.rank_position)::numeric, 1) AS avg_rank
            FROM curr c
            JOIN prev p ON c.prompt_id = p.prompt_id AND c.engine_name = p.engine_name
            GROUP BY c.engine_name ORDER BY c.engine_name
        """, (days,)).fetchall()

        daily_trend = conn.execute("""
            SELECT el.captured_at::date::text AS day,
                   COUNT(DISTINCT CONCAT(el.prompt_id, el.engine_name)) AS mentions,
                   ROUND(AVG(bm.rank_position)::numeric, 1) AS avg_rank,
                   COUNT(*) FILTER (WHERE bm.rank_position <= 3) AS top3
            FROM execution_logs el
            JOIN brand_mentions bm ON bm.log_id = el.id
            WHERE bm.is_target_brand = TRUE
              AND el.captured_at >= CURRENT_DATE - make_interval(days => %s)
            GROUP BY el.captured_at::date
            ORDER BY day
        """, (days,)).fetchall()

        sentiment = conn.execute("""
            SELECT bm.sentiment, COUNT(*) AS cnt
            FROM execution_logs el
            JOIN brand_mentions bm ON bm.log_id = el.id
            WHERE bm.is_target_brand = TRUE
              AND el.captured_at >= CURRENT_DATE - make_interval(days => %s)
            GROUP BY bm.sentiment ORDER BY cnt DESC
        """, (days,)).fetchall()

        return {
            "engine_breakdown": [dict(r) for r in engine_breakdown],
            "daily_trend": [dict(r) for r in daily_trend],
            "sentiment": [dict(r) for r in sentiment],
        }
    return await run_db(_q)


# ─── Enhanced: AI vs SERP Gap Trend ──────────────────────────────────

@router.get("/analytics/ai-serp-gap-trend")
async def analytics_ai_serp_gap_trend(days: int = Query(14)):
    """How AI+SERP gap counts change over time."""
    settings = get_settings()
    td = settings.target_domain

    def _q(conn):
        rows = conn.execute("""
            WITH daily_serp AS (
                SELECT sr.captured_at::date AS day, sr.prompt_id,
                       BOOL_OR(soe.domain = %s OR soe.domain LIKE %s) AS has_serp
                FROM serp_results sr
                LEFT JOIN serp_organic_entries soe ON soe.serp_id = sr.id
                    AND (soe.domain = %s OR soe.domain LIKE %s)
                WHERE sr.captured_at >= CURRENT_DATE - make_interval(days => %s)
                GROUP BY sr.captured_at::date, sr.prompt_id
            ),
            daily_ai AS (
                SELECT el.captured_at::date AS day, el.prompt_id,
                       BOOL_OR(bm.is_target_brand = TRUE) AS has_ai
                FROM execution_logs el
                LEFT JOIN brand_mentions bm ON bm.log_id = el.id
                WHERE el.captured_at >= CURRENT_DATE - make_interval(days => %s)
                GROUP BY el.captured_at::date, el.prompt_id
            ),
            combined AS (
                SELECT COALESCE(s.day, a.day) AS day,
                       COALESCE(s.prompt_id, a.prompt_id) AS prompt_id,
                       COALESCE(s.has_serp, FALSE) AS has_serp,
                       COALESCE(a.has_ai, FALSE) AS has_ai
                FROM daily_serp s
                FULL OUTER JOIN daily_ai a ON s.day = a.day AND s.prompt_id = a.prompt_id
            )
            SELECT day::text,
                   COUNT(*) FILTER (WHERE has_serp AND has_ai) AS both,
                   COUNT(*) FILTER (WHERE has_ai AND NOT has_serp) AS ai_only,
                   COUNT(*) FILTER (WHERE has_serp AND NOT has_ai) AS serp_only,
                   COUNT(*) FILTER (WHERE NOT has_serp AND NOT has_ai) AS neither
            FROM combined GROUP BY day ORDER BY day
        """, (td, f"%.{td}", td, f"%.{td}", days, days)).fetchall()
        return [dict(r) for r in rows]
    return await run_db(_q)
