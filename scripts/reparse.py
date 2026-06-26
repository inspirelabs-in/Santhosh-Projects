"""Re-parse execution logs that have raw_response_text but no brand_mentions."""
import asyncio
import re
import logging
from app.agent.parser import parse_response
from app.database import run_db
from app.events import broadcast

log = logging.getLogger("geo.reparse")


async def reparse_unparsed(batch_size: int = 50, delay: float = 1.0):
    def _fetch(conn):
        return conn.execute("""
            SELECT el.id, el.engine_name, el.raw_response_text, p.text as prompt_text
            FROM execution_logs el
            JOIN prompts p ON p.id = el.prompt_id
            WHERE el.raw_response_text NOT LIKE 'Error:%%'
              AND el.id NOT IN (SELECT DISTINCT log_id FROM brand_mentions)
            ORDER BY el.captured_at DESC
            LIMIT %s
        """, (batch_size,)).fetchall()

    rows = await run_db(_fetch)
    if not rows:
        log.info("No unparsed logs found.")
        return {"parsed": 0, "failed": 0, "total": 0}

    total = len(rows)
    parsed = 0
    failed = 0
    log.info(f"Re-parsing {total} logs...")
    broadcast("reparse_start", total=total)

    for i, row in enumerate(rows):
        log_id = row["id"]
        engine = row["engine_name"]
        prompt_text = row["prompt_text"]
        raw_text = row["raw_response_text"]

        try:
            extracted = await parse_response(engine, prompt_text, raw_text)
            if extracted and (extracted.brand_mentions or extracted.ai_hallucinated_coupons):
                def _commit(conn, lid=log_id, ext=extracted, eng=engine):
                    with conn.transaction():
                        for m in ext.brand_mentions:
                            is_target = bool(re.search(r'\bgrab\s*on\b', m.brand_name, re.IGNORECASE))
                            conn.execute(
                                """INSERT INTO brand_mentions
                                   (log_id, rank_position, brand_name, is_target_brand, sentiment, context_snippet, cited_url)
                                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                                (lid, m.rank_position, m.brand_name, is_target,
                                 m.sentiment.value, m.context_snippet, m.cited_url),
                            )
                        for c in ext.ai_hallucinated_coupons:
                            conn.execute(
                                """INSERT INTO ai_hallucinated_coupons
                                   (log_id, coupon_code, associated_merchant, status_flag)
                                   VALUES (%s, %s, %s, %s)""",
                                (lid, c.coupon_code, c.associated_merchant, c.status_flag.value),
                            )
                await run_db(_commit)
                parsed += 1
                log.info(f"[{i+1}/{total}] Re-parsed {engine} log {log_id}: {len(extracted.brand_mentions)} mentions")
            else:
                failed += 1
                log.warning(f"[{i+1}/{total}] Parser returned no data for {engine} log {log_id}")
        except Exception as e:
            failed += 1
            log.error(f"[{i+1}/{total}] Re-parse failed for {engine} log {log_id}: {e}")

        if delay > 0 and i < total - 1:
            await asyncio.sleep(delay)

    result = {"parsed": parsed, "failed": failed, "total": total}
    log.info(f"Re-parse complete: {result}")
    broadcast("reparse_done", **result)
    return result
