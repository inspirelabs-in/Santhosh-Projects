"""
Verification engine -- tracks before/after metrics when a fix is applied.
Compares AI rank position, citation presence, SERP rank, sentiment
before and after the fix date to prove whether changes moved the needle.
"""
import logging

from app.database import run_db

log = logging.getLogger("geo.verification")

MIN_POST_FIX_DATAPOINTS = 3


async def check_fix_impact(fix_id: str) -> dict:
    """
    Compare metrics before vs after applied_at date for a fix.
    Returns verification result with metric changes and verdict.
    """
    fix = await run_db(lambda conn: conn.execute(
        """SELECT af.*, p.text as keyword
           FROM applied_fixes af
           JOIN prompts p ON af.prompt_id = p.id
           WHERE af.id = %s::uuid""",
        (fix_id,),
    ).fetchone())

    if not fix:
        return {"error": "Fix not found"}

    prompt_id = str(fix["prompt_id"])
    applied_at = fix["applied_at"]

    before_ai = await _get_ai_metrics(prompt_id, before=applied_at)
    after_ai = await _get_ai_metrics(prompt_id, after=applied_at)

    before_serp = await _get_serp_metrics(prompt_id, before=applied_at)
    after_serp = await _get_serp_metrics(prompt_id, after=applied_at)

    before_citations = await _get_citation_metrics(prompt_id, before=applied_at)
    after_citations = await _get_citation_metrics(prompt_id, after=applied_at)

    sufficient_data = after_ai["datapoints"] >= MIN_POST_FIX_DATAPOINTS

    changes = []

    if before_ai["avg_rank"] is not None and after_ai["avg_rank"] is not None:
        rank_delta = before_ai["avg_rank"] - after_ai["avg_rank"]
        changes.append({
            "metric": "ai_rank_position",
            "before": round(before_ai["avg_rank"], 2),
            "after": round(after_ai["avg_rank"], 2),
            "delta": round(rank_delta, 2),
            "direction": "improved" if rank_delta > 0 else ("degraded" if rank_delta < 0 else "unchanged"),
        })

    if before_ai["mention_count"] is not None and after_ai["mention_count"] is not None:
        mention_delta = after_ai["mention_count"] - before_ai["mention_count"]
        changes.append({
            "metric": "ai_mention_count",
            "before": before_ai["mention_count"],
            "after": after_ai["mention_count"],
            "delta": mention_delta,
            "direction": "improved" if mention_delta > 0 else ("degraded" if mention_delta < 0 else "unchanged"),
        })

    if before_ai["positive_pct"] is not None and after_ai["positive_pct"] is not None:
        sent_delta = after_ai["positive_pct"] - before_ai["positive_pct"]
        changes.append({
            "metric": "positive_sentiment_pct",
            "before": round(before_ai["positive_pct"], 1),
            "after": round(after_ai["positive_pct"], 1),
            "delta": round(sent_delta, 1),
            "direction": "improved" if sent_delta > 0 else ("degraded" if sent_delta < 0 else "unchanged"),
        })

    if before_serp["target_rank"] is not None and after_serp["target_rank"] is not None:
        serp_delta = before_serp["target_rank"] - after_serp["target_rank"]
        changes.append({
            "metric": "serp_organic_rank",
            "before": before_serp["target_rank"],
            "after": after_serp["target_rank"],
            "delta": serp_delta,
            "direction": "improved" if serp_delta > 0 else ("degraded" if serp_delta < 0 else "unchanged"),
        })
    elif not before_serp["target_rank"] and after_serp["target_rank"]:
        changes.append({
            "metric": "serp_organic_rank",
            "before": None,
            "after": after_serp["target_rank"],
            "delta": None,
            "direction": "improved",
            "note": "Entered SERP rankings after fix",
        })

    citation_before = before_citations["target_cited"]
    citation_after = after_citations["target_cited"]
    if citation_before is not None or citation_after is not None:
        changes.append({
            "metric": "target_cited_in_ai",
            "before": citation_before or False,
            "after": citation_after or False,
            "delta": None,
            "direction": "improved" if citation_after and not citation_before else (
                "degraded" if citation_before and not citation_after else "unchanged"
            ),
        })

    improved_count = sum(1 for c in changes if c["direction"] == "improved")
    degraded_count = sum(1 for c in changes if c["direction"] == "degraded")

    if not sufficient_data:
        verdict = "insufficient_data"
    elif improved_count > degraded_count:
        verdict = "improved"
    elif degraded_count > improved_count:
        verdict = "degraded"
    else:
        verdict = "no_change"

    result = {
        "fix_id": fix_id,
        "keyword": fix["keyword"],
        "applied_at": str(applied_at),
        "metric_changes": changes,
        "verdict": verdict,
        "sufficient_data": sufficient_data,
        "post_fix_datapoints": after_ai["datapoints"],
        "days_since_fix": after_ai.get("days_since_fix", 0),
    }

    await _update_fix_status(fix_id, verdict, result)

    # Gap 3: link verdict back to the originating diagnosis
    diagnosis_id = fix.get("diagnosis_id")
    if diagnosis_id and verdict != "insufficient_data":
        await _update_diagnosis_from_verification(str(diagnosis_id), verdict, result)

    return result


async def _get_ai_metrics(prompt_id: str, before=None, after=None) -> dict:
    """Get AI engine metrics either before or after a date."""
    if before:
        condition = "el.captured_at < %s"
        params = (prompt_id, before)
    else:
        condition = "el.captured_at >= %s"
        params = (prompt_id, after)

    row = await run_db(lambda conn: conn.execute(
        f"""SELECT
                AVG(bm.rank_position) FILTER (WHERE bm.is_target_brand) as avg_rank,
                COUNT(*) FILTER (WHERE bm.is_target_brand) as mention_count,
                COUNT(DISTINCT el.id) as datapoints,
                CASE WHEN COUNT(*) FILTER (WHERE bm.is_target_brand) > 0
                     THEN 100.0 * COUNT(*) FILTER (WHERE bm.is_target_brand AND bm.sentiment = 'Positive')
                          / NULLIF(COUNT(*) FILTER (WHERE bm.is_target_brand), 0)
                     ELSE NULL END as positive_pct,
                EXTRACT(DAY FROM NOW() - MAX(el.captured_at)) as days_since_fix
            FROM execution_logs el
            LEFT JOIN brand_mentions bm ON bm.log_id = el.id
            WHERE el.prompt_id = %s::uuid AND {condition}""",
        params,
    ).fetchone())

    return {
        "avg_rank": row["avg_rank"] if row else None,
        "mention_count": row["mention_count"] if row else 0,
        "positive_pct": row["positive_pct"] if row else None,
        "datapoints": row["datapoints"] if row else 0,
        "days_since_fix": int(row["days_since_fix"] or 0) if row else 0,
    }


async def _get_serp_metrics(prompt_id: str, before=None, after=None) -> dict:
    """Get SERP organic rank for target domain before/after date."""
    from app.config import get_settings
    settings = get_settings()
    target_domain = settings.target_domain

    if before:
        condition = "sr.captured_at < %s"
        params = (prompt_id, before, f"%{target_domain}%")
    else:
        condition = "sr.captured_at >= %s"
        params = (prompt_id, after, f"%{target_domain}%")

    row = await run_db(lambda conn: conn.execute(
        f"""SELECT AVG(soe.rank_position) as target_rank
            FROM serp_organic_entries soe
            JOIN serp_results sr ON soe.serp_id = sr.id
            WHERE sr.prompt_id = %s::uuid AND {condition}
              AND soe.domain ILIKE %s""",
        params,
    ).fetchone())

    return {
        "target_rank": round(row["target_rank"]) if row and row["target_rank"] else None,
    }


async def _get_citation_metrics(prompt_id: str, before=None, after=None) -> dict:
    """Check if target domain was cited in AI responses before/after date."""
    from app.config import get_settings
    settings = get_settings()
    target_domain = settings.target_domain

    if before:
        condition = "el.captured_at < %s"
        params = (prompt_id, before, f"%{target_domain}%")
    else:
        condition = "el.captured_at >= %s"
        params = (prompt_id, after, f"%{target_domain}%")

    row = await run_db(lambda conn: conn.execute(
        f"""SELECT COUNT(*) > 0 as target_cited
            FROM brand_mentions bm
            JOIN execution_logs el ON bm.log_id = el.id
            WHERE el.prompt_id = %s::uuid AND {condition}
              AND bm.cited_url ILIKE %s""",
        params,
    ).fetchone())

    return {"target_cited": row["target_cited"] if row else False}


async def _update_diagnosis_from_verification(diagnosis_id: str, verdict: str, verification_result: dict):
    """Update diagnosis record with verification outcome. Closes the feedback loop."""
    import json
    status_map = {
        "improved": "verified_effective",
        "degraded": "verified_ineffective",
        "no_change": "verified_no_impact",
    }
    new_status = status_map.get(verdict, "active")

    validated_causes = []
    if verdict == "improved":
        for change in verification_result.get("metric_changes", []):
            if change.get("direction") == "improved":
                validated_causes.append(change["metric"])

    def _update(conn):
        conn.execute(
            """UPDATE diagnoses
               SET status = %s,
                   evidence_snapshot = jsonb_set(
                       COALESCE(evidence_snapshot, '{}'::jsonb),
                       '{verification}',
                       %s::jsonb
                   )
               WHERE id = %s::uuid AND status = 'active'""",
            (
                new_status,
                json.dumps({
                    "verdict": verdict,
                    "validated_metrics": validated_causes,
                    "verified_at": verification_result.get("applied_at"),
                    "fix_id": verification_result.get("fix_id"),
                }),
                diagnosis_id,
            ),
        )
        conn.commit()
    try:
        await run_db(_update)
        log.info(f"Diagnosis {diagnosis_id} updated: {new_status}")
    except Exception as e:
        log.error(f"Diagnosis backlink update failed: {e}")


async def _update_fix_status(fix_id: str, verdict: str, result: dict):
    """Update fix verification status and result snapshot."""
    import json
    def _update(conn):
        conn.execute(
            """UPDATE applied_fixes
               SET verification_status = %s,
                   last_verified_at = NOW(),
                   verification_result = %s
               WHERE id = %s::uuid""",
            (verdict, json.dumps(result, default=str), fix_id),
        )
        conn.commit()
    try:
        await run_db(_update)
    except Exception as e:
        log.error(f"Fix status update failed: {e}")


async def verify_all_monitoring_fixes():
    """Re-verify all fixes with status 'monitoring' that have sufficient data."""
    rows = await run_db(lambda conn: conn.execute(
        """SELECT id FROM applied_fixes
           WHERE verification_status = 'monitoring'
           ORDER BY applied_at""",
    ).fetchall())

    if not rows:
        return 0

    verified = 0
    for row in rows:
        try:
            result = await check_fix_impact(str(row["id"]))
            if result.get("verdict") != "insufficient_data":
                verified += 1
        except Exception as e:
            log.error(f"Verification failed for fix {row['id']}: {e}")

    log.info(f"Verified {verified}/{len(rows)} monitoring fixes")
    return verified


async def get_fix_timeline(fix_id: str) -> dict:
    """Get time-series data for before/after visualization."""
    fix = await run_db(lambda conn: conn.execute(
        """SELECT af.prompt_id, af.applied_at, p.text as keyword
           FROM applied_fixes af
           JOIN prompts p ON af.prompt_id = p.id
           WHERE af.id = %s::uuid""",
        (fix_id,),
    ).fetchone())

    if not fix:
        return {"error": "Fix not found"}

    prompt_id = str(fix["prompt_id"])
    applied_at = fix["applied_at"]

    ai_timeline = await run_db(lambda conn: conn.execute(
        """SELECT el.captured_at::date as date, el.engine_name,
                  bm.rank_position, bm.sentiment, bm.brand_name
           FROM brand_mentions bm
           JOIN execution_logs el ON bm.log_id = el.id
           WHERE el.prompt_id = %s::uuid AND bm.is_target_brand = TRUE
           ORDER BY el.captured_at""",
        (prompt_id,),
    ).fetchall())

    serp_timeline = await run_db(lambda conn: conn.execute(
        """SELECT sr.captured_at::date as date, soe.rank_position, soe.domain
           FROM serp_organic_entries soe
           JOIN serp_results sr ON soe.serp_id = sr.id
           WHERE sr.prompt_id = %s::uuid AND soe.is_target = TRUE
           ORDER BY sr.captured_at""",
        (prompt_id,),
    ).fetchall())

    return {
        "keyword": fix["keyword"],
        "applied_at": str(applied_at),
        "ai_timeline": [dict(r) for r in ai_timeline],
        "serp_timeline": [dict(r) for r in serp_timeline],
    }
