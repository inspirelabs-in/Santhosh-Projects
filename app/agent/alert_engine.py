"""
Alert engine — generates persistent, deduplicated notifications from DB state.
Runs on a scheduler (every 30 min) and produces rows in the notifications table.
All detection is pure SQL — no LLM calls.
"""
import hashlib
import json
import logging
from datetime import date

from app.database import run_db

log = logging.getLogger("geo.alert_engine")

TARGET_BRAND = "grabon"


def _dedup_key(alert_type: str, entity: str) -> str:
    """type:entity:day-bucket — fires once per day per entity."""
    day = date.today().isoformat()
    raw = f"{alert_type}:{entity}:{day}"
    return hashlib.md5(raw.encode()).hexdigest()[:32]


async def _insert_alert(
    alert_type: str,
    title: str,
    message: str,
    severity: str,
    entity: str,
    metadata: dict | None = None,
    teams: bool = False,
):
    dedup = _dedup_key(alert_type, entity)

    def _q(conn):
        row = conn.execute(
            """INSERT INTO notifications (type, title, message, severity, dedup_key, metadata)
               VALUES (%s, %s, %s, %s, %s, %s::jsonb)
               ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL DO NOTHING
               RETURNING id""",
            (alert_type, title, message, severity, dedup,
             json.dumps(metadata or {})),
        ).fetchone()
        conn.commit()
        return row

    try:
        row = await run_db(_q)
        if row:
            from app.events import broadcast
            broadcast("notification", title=title, message=message,
                      severity=severity, ntype=alert_type)
            if teams and severity in ("warning", "critical"):
                from app.notifications import _send_teams
                await _send_teams(title, message, severity)
            return True
        return False  # duplicate
    except Exception as e:
        log.error(f"Alert insert failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════
# ALERT DETECTORS — all pure SQL
# ═══════════════════════════════════════════════════════════════════


async def detect_rank_drops():
    """GrabOn rank dropped ≥2 positions on any AI engine (critical if dropped out of top 3)."""
    def _q(conn):
        return conn.execute("""
            WITH recent AS (
                SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                    el.prompt_id, el.engine_name, bm.rank_position, el.captured_at
                FROM execution_logs el
                JOIN brand_mentions bm ON bm.log_id = el.id
                WHERE bm.is_target_brand = TRUE
                  AND el.captured_at > NOW() - INTERVAL '2 days'
                ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
            ),
            previous AS (
                SELECT DISTINCT ON (el.prompt_id, el.engine_name)
                    el.prompt_id, el.engine_name, bm.rank_position
                FROM execution_logs el
                JOIN brand_mentions bm ON bm.log_id = el.id
                WHERE bm.is_target_brand = TRUE
                  AND el.captured_at BETWEEN NOW() - INTERVAL '7 days' AND NOW() - INTERVAL '2 days'
                ORDER BY el.prompt_id, el.engine_name, el.captured_at DESC
            )
            SELECT p.text AS keyword, r.engine_name,
                   pr.rank_position AS old_rank, r.rank_position AS new_rank,
                   r.rank_position - pr.rank_position AS delta
            FROM recent r
            JOIN previous pr ON pr.prompt_id = r.prompt_id AND pr.engine_name = r.engine_name
            JOIN prompts p ON p.id = r.prompt_id
            WHERE r.rank_position - pr.rank_position >= 2
            ORDER BY delta DESC LIMIT 15
        """).fetchall()
    count = 0
    for r in await run_db(_q):
        sev = "critical" if r["new_rank"] > 3 and r["old_rank"] <= 3 else "warning"
        entity = f"{r['keyword']}:{r['engine_name']}"
        ok = await _insert_alert(
            "rank_drop",
            f"Rank drop: {r['keyword'][:60]} on {r['engine_name']}",
            f"#{r['old_rank']} → #{r['new_rank']} (dropped {r['delta']} positions)",
            sev, entity,
            metadata={"keyword": r["keyword"], "engine": r["engine_name"],
                       "old_rank": r["old_rank"], "new_rank": r["new_rank"]},
            teams=sev == "critical",
        )
        if ok:
            count += 1
    return count


async def detect_new_competitors():
    """New domain appeared in SERP top 5 in last 3 days."""
    def _q(conn):
        return conn.execute("""
            WITH recent_top5 AS (
                SELECT DISTINCT soe.domain
                FROM serp_results sr
                JOIN serp_organic_entries soe ON soe.serp_id = sr.id
                WHERE soe.rank_position <= 5
                  AND sr.captured_at > NOW() - INTERVAL '3 days'
            ),
            old_top5 AS (
                SELECT DISTINCT soe.domain
                FROM serp_results sr
                JOIN serp_organic_entries soe ON soe.serp_id = sr.id
                WHERE soe.rank_position <= 5
                  AND sr.captured_at BETWEEN NOW() - INTERVAL '10 days' AND NOW() - INTERVAL '3 days'
            )
            SELECT domain FROM recent_top5
            WHERE domain NOT IN (SELECT domain FROM old_top5)
            LIMIT 10
        """).fetchall()
    count = 0
    for r in await run_db(_q):
        ok = await _insert_alert(
            "new_competitor",
            f"New competitor in top 5: {r['domain']}",
            "First appeared in top 5 SERP results in the last 3 days",
            "info", r["domain"],
            metadata={"domain": r["domain"]},
        )
        if ok:
            count += 1
    return count


async def detect_citation_changes():
    """Citation URL count shifted ≥3."""
    def _q(conn):
        return conn.execute("""
            WITH recent_cites AS (
                SELECT COUNT(DISTINCT cited_url) AS cnt
                FROM citation_serp_overlaps WHERE detected_at > NOW() - INTERVAL '3 days'
            ),
            old_cites AS (
                SELECT COUNT(DISTINCT cited_url) AS cnt
                FROM citation_serp_overlaps
                WHERE detected_at BETWEEN NOW() - INTERVAL '7 days' AND NOW() - INTERVAL '3 days'
            )
            SELECT r.cnt AS recent, o.cnt AS old, r.cnt - o.cnt AS delta
            FROM recent_cites r, old_cites o
        """).fetchone()

    row = await run_db(_q)
    if not row or not row["delta"] or abs(row["delta"]) < 3:
        return 0
    direction = "increased" if row["delta"] > 0 else "decreased"
    sev = "info" if row["delta"] > 0 else "warning"
    ok = await _insert_alert(
        "citation_change",
        f"Citation URLs {direction} by {abs(row['delta'])}",
        f"Recent: {row['recent']} vs Previous: {row['old']}",
        sev, "citations_global",
        metadata={"recent": row["recent"], "old": row["old"], "delta": row["delta"]},
    )
    return 1 if ok else 0


async def detect_seo_movement():
    """Target domain SERP rank shifted ≥5 positions."""
    def _q(conn):
        return conn.execute("""
            WITH latest AS (
                SELECT DISTINCT ON (sr.prompt_id)
                    sr.prompt_id, soe.rank_position AS current_rank, sr.captured_at
                FROM serp_results sr
                JOIN serp_organic_entries soe ON soe.serp_id = sr.id
                WHERE soe.is_target = TRUE
                ORDER BY sr.prompt_id, sr.captured_at DESC
            ),
            previous AS (
                SELECT DISTINCT ON (sr.prompt_id)
                    sr.prompt_id, soe.rank_position AS prev_rank
                FROM serp_results sr
                JOIN serp_organic_entries soe ON soe.serp_id = sr.id
                WHERE soe.is_target = TRUE
                  AND sr.captured_at < (SELECT MIN(captured_at) FROM latest)
                ORDER BY sr.prompt_id, sr.captured_at DESC
            )
            SELECT p.text AS keyword, l.current_rank, pr.prev_rank,
                   l.current_rank - pr.prev_rank AS delta
            FROM latest l
            JOIN previous pr ON pr.prompt_id = l.prompt_id
            JOIN prompts p ON p.id = l.prompt_id
            WHERE ABS(l.current_rank - pr.prev_rank) >= 5
            ORDER BY ABS(l.current_rank - pr.prev_rank) DESC LIMIT 10
        """).fetchall()

    count = 0
    for r in await run_db(_q):
        direction = "dropped" if r["delta"] > 0 else "improved"
        sev = "warning" if r["delta"] > 0 else "info"
        if r["delta"] > 0 and r["current_rank"] > 10 and r["prev_rank"] <= 10:
            sev = "critical"
        ok = await _insert_alert(
            "seo_movement",
            f"SEO {direction}: {r['keyword'][:60]}",
            f"#{r['prev_rank']} → #{r['current_rank']} ({abs(r['delta'])} positions)",
            sev, f"seo:{r['keyword']}",
            metadata={"keyword": r["keyword"], "prev_rank": r["prev_rank"],
                       "current_rank": r["current_rank"]},
        )
        if ok:
            count += 1
    return count


async def detect_sentiment_shift():
    """Negative sentiment ratio spiked (>30% negative in last 2 days vs <15% prior)."""
    def _q(conn):
        return conn.execute("""
            WITH recent AS (
                SELECT
                    COUNT(*) FILTER (WHERE bm.sentiment = 'negative') AS neg,
                    COUNT(*) AS total
                FROM execution_logs el
                JOIN brand_mentions bm ON bm.log_id = el.id
                WHERE bm.is_target_brand = TRUE
                  AND el.captured_at > NOW() - INTERVAL '2 days'
            ),
            older AS (
                SELECT
                    COUNT(*) FILTER (WHERE bm.sentiment = 'negative') AS neg,
                    COUNT(*) AS total
                FROM execution_logs el
                JOIN brand_mentions bm ON bm.log_id = el.id
                WHERE bm.is_target_brand = TRUE
                  AND el.captured_at BETWEEN NOW() - INTERVAL '7 days' AND NOW() - INTERVAL '2 days'
            )
            SELECT r.neg AS r_neg, r.total AS r_total,
                   o.neg AS o_neg, o.total AS o_total
            FROM recent r, older o
        """).fetchone()

    row = await run_db(_q)
    if not row or not row["r_total"] or row["r_total"] < 5:
        return 0
    recent_pct = (row["r_neg"] / row["r_total"]) * 100
    old_pct = (row["o_neg"] / max(row["o_total"], 1)) * 100
    if recent_pct > 30 and old_pct < 15:
        ok = await _insert_alert(
            "sentiment_shift",
            f"Negative sentiment spike: {recent_pct:.0f}% (was {old_pct:.0f}%)",
            f"Recent: {row['r_neg']}/{row['r_total']} negative mentions vs prior {row['o_neg']}/{row['o_total']}",
            "warning", "sentiment_global",
            metadata={"recent_pct": round(recent_pct, 1), "old_pct": round(old_pct, 1)},
            teams=True,
        )
        return 1 if ok else 0
    return 0


async def detect_hallucinated_coupon_spike():
    """Hallucinated coupon count spiked (>5 new in last 2 days)."""
    def _q(conn):
        return conn.execute("""
            SELECT COUNT(*) AS cnt
            FROM ai_hallucinated_coupons ahc
            JOIN execution_logs el ON el.id = ahc.log_id
            WHERE el.captured_at > NOW() - INTERVAL '2 days'
              AND ahc.status_flag IN ('hallucinated', 'unverified')
        """).fetchone()

    row = await run_db(_q)
    if not row or row["cnt"] < 5:
        return 0
    ok = await _insert_alert(
        "hallucinated_coupon",
        f"Hallucinated coupon spike: {row['cnt']} in 2 days",
        f"{row['cnt']} unverified/hallucinated coupons detected across AI engines",
        "warning", "coupon_spike",
        metadata={"count": row["cnt"]},
    )
    return 1 if ok else 0


async def detect_engine_dark():
    """An engine has 0 successful scrapes in last 24h but had activity before."""
    def _q(conn):
        return conn.execute("""
            WITH active_engines AS (
                SELECT DISTINCT engine_name FROM execution_logs
                WHERE captured_at > NOW() - INTERVAL '7 days'
            ),
            recent_activity AS (
                SELECT engine_name, COUNT(*) AS cnt FROM execution_logs
                WHERE captured_at > NOW() - INTERVAL '24 hours'
                GROUP BY engine_name
            )
            SELECT ae.engine_name
            FROM active_engines ae
            LEFT JOIN recent_activity ra ON ra.engine_name = ae.engine_name
            WHERE ra.cnt IS NULL OR ra.cnt = 0
        """).fetchall()

    count = 0
    for r in await run_db(_q):
        ok = await _insert_alert(
            "engine_dark",
            f"Engine went dark: {r['engine_name']}",
            f"No successful scrapes from {r['engine_name']} in the last 24 hours",
            "critical", f"dark:{r['engine_name']}",
            metadata={"engine": r["engine_name"]},
            teams=True,
        )
        if ok:
            count += 1
    return count


async def detect_citation_lost():
    """GrabOn citation URLs that existed 3+ days ago but are missing in recent data."""
    def _q(conn):
        return conn.execute("""
            WITH old_citations AS (
                SELECT DISTINCT cited_url
                FROM citation_serp_overlaps cso
                JOIN brand_mentions bm ON bm.id = cso.brand_mention_id
                WHERE bm.is_target_brand = TRUE
                  AND cso.detected_at BETWEEN NOW() - INTERVAL '10 days' AND NOW() - INTERVAL '3 days'
            ),
            recent_citations AS (
                SELECT DISTINCT cited_url
                FROM citation_serp_overlaps cso
                JOIN brand_mentions bm ON bm.id = cso.brand_mention_id
                WHERE bm.is_target_brand = TRUE
                  AND cso.detected_at > NOW() - INTERVAL '3 days'
            )
            SELECT oc.cited_url FROM old_citations oc
            WHERE oc.cited_url NOT IN (SELECT cited_url FROM recent_citations)
            LIMIT 10
        """).fetchall()

    count = 0
    for r in await run_db(_q):
        ok = await _insert_alert(
            "citation_lost",
            f"Citation lost: {r['cited_url'][:80]}",
            "This GrabOn URL was previously cited by AI engines but is no longer appearing",
            "warning", f"cite_lost:{r['cited_url'][:100]}",
            metadata={"url": r["cited_url"]},
        )
        if ok:
            count += 1
    return count


async def detect_serp_feature_changes():
    """GrabOn lost a SERP feature (featured snippet, PAA, etc.) that it had before."""
    def _q(conn):
        return conn.execute("""
            WITH old_features AS (
                SELECT DISTINCT sf.feature_type, sf.domain
                FROM serp_features sf
                JOIN serp_results sr ON sr.id = sf.serp_id
                WHERE LOWER(sf.domain) LIKE '%%grabon%%'
                  AND sr.captured_at BETWEEN NOW() - INTERVAL '10 days' AND NOW() - INTERVAL '3 days'
            ),
            recent_features AS (
                SELECT DISTINCT sf.feature_type, sf.domain
                FROM serp_features sf
                JOIN serp_results sr ON sr.id = sf.serp_id
                WHERE LOWER(sf.domain) LIKE '%%grabon%%'
                  AND sr.captured_at > NOW() - INTERVAL '3 days'
            )
            SELECT of.feature_type, of.domain FROM old_features of
            WHERE (of.feature_type, of.domain) NOT IN (
                SELECT feature_type, domain FROM recent_features
            )
            LIMIT 10
        """).fetchall()

    count = 0
    for r in await run_db(_q):
        ok = await _insert_alert(
            "serp_feature_lost",
            f"Lost SERP feature: {r['feature_type']} ({r['domain']})",
            f"GrabOn no longer holds the {r['feature_type']} SERP feature",
            "warning", f"sf_lost:{r['feature_type']}:{r['domain']}",
            metadata={"feature_type": r["feature_type"], "domain": r["domain"]},
        )
        if ok:
            count += 1
    return count


async def detect_diagnosis_alerts():
    """High-priority diagnoses created in last 2 days."""
    def _q(conn):
        return conn.execute("""
            SELECT d.id, d.engine_name, d.priority, d.summary,
                   p.text AS keyword
            FROM diagnoses d
            JOIN prompts p ON p.id = d.prompt_id
            WHERE d.created_at > NOW() - INTERVAL '2 days'
              AND d.priority IN ('high', 'critical')
              AND d.status = 'active'
            ORDER BY d.created_at DESC LIMIT 10
        """).fetchall()

    count = 0
    for r in await run_db(_q):
        sev = "critical" if r["priority"] == "critical" else "warning"
        ok = await _insert_alert(
            "diagnosis",
            f"Diagnosis [{r['priority']}]: {r['keyword'][:50]} on {r['engine_name']}",
            (r["summary"] or "High priority issue detected")[:200],
            sev, f"diag:{r['id']}",
            metadata={"keyword": r["keyword"], "engine": r["engine_name"],
                       "diagnosis_id": str(r["id"])},
        )
        if ok:
            count += 1
    return count


async def detect_trend_decline():
    """3-day declining trend in GrabOn rank across any engine."""
    def _q(conn):
        return conn.execute("""
            WITH daily_ranks AS (
                SELECT el.engine_name,
                       DATE(el.captured_at) AS day,
                       AVG(bm.rank_position) AS avg_rank
                FROM execution_logs el
                JOIN brand_mentions bm ON bm.log_id = el.id
                WHERE bm.is_target_brand = TRUE
                  AND el.captured_at > NOW() - INTERVAL '4 days'
                GROUP BY el.engine_name, DATE(el.captured_at)
                HAVING COUNT(*) >= 3
            ),
            ranked_days AS (
                SELECT engine_name, day, avg_rank,
                       ROW_NUMBER() OVER (PARTITION BY engine_name ORDER BY day DESC) AS rn
                FROM daily_ranks
            )
            SELECT
                a.engine_name,
                a.avg_rank AS day1_rank,
                b.avg_rank AS day2_rank,
                c.avg_rank AS day3_rank
            FROM ranked_days a
            JOIN ranked_days b ON b.engine_name = a.engine_name AND b.rn = 2
            JOIN ranked_days c ON c.engine_name = a.engine_name AND c.rn = 3
            WHERE a.rn = 1
              AND a.avg_rank > b.avg_rank
              AND b.avg_rank > c.avg_rank
              AND a.avg_rank - c.avg_rank >= 1.0
        """).fetchall()

    count = 0
    for r in await run_db(_q):
        ok = await _insert_alert(
            "trend_decline",
            f"3-day declining trend on {r['engine_name']}",
            f"Avg rank worsening: {r['day3_rank']:.1f} → {r['day2_rank']:.1f} → {r['day1_rank']:.1f}",
            "warning", f"trend:{r['engine_name']}",
            metadata={"engine": r["engine_name"],
                       "ranks": [round(r["day3_rank"], 1), round(r["day2_rank"], 1), round(r["day1_rank"], 1)]},
        )
        if ok:
            count += 1
    return count


# ═══════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

ALL_DETECTORS = [
    detect_rank_drops,
    detect_new_competitors,
    detect_citation_changes,
    detect_seo_movement,
    detect_sentiment_shift,
    detect_hallucinated_coupon_spike,
    detect_engine_dark,
    detect_citation_lost,
    detect_serp_feature_changes,
    detect_diagnosis_alerts,
    detect_trend_decline,
]


async def generate_alerts():
    """Run all detectors. Called by scheduler every 30 min."""
    total = 0
    for detector in ALL_DETECTORS:
        try:
            count = await detector()
            total += count
        except Exception as e:
            log.error(f"Alert detector {detector.__name__} failed: {e}")
    if total:
        log.info(f"Alert engine generated {total} new alerts")
    return total


async def cleanup_old_alerts(days: int = 30):
    """Purge read alerts older than N days."""
    def _q(conn):
        cur = conn.execute(
            "DELETE FROM notifications WHERE is_read = TRUE AND created_at < NOW() - make_interval(days => %s)",
            (days,),
        )
        conn.commit()
        return cur.rowcount
    deleted = await run_db(_q)
    if deleted:
        log.info(f"Cleaned up {deleted} old read notifications")
    return deleted
