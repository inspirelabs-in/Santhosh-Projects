"""
Continuous diagnosis scheduler -- runs 24/7, prioritizes keywords where
target brand is absent, low-ranked, or has negative sentiment.
Re-diagnoses when evidence content actually changes (hash-based).
"""
import asyncio
import logging

from app.agent.diagnosis_engine import generate_diagnosis, get_diagnosis_priority_queue
from app.agent.verification import verify_all_monitoring_fixes
from app.database import run_db
from app.events import broadcast

log = logging.getLogger("geo.diagnosis_scheduler")

_diagnosis_running = False
_batches_since_verify = 0

BATCH_SIZE = 50
DELAY_BETWEEN_DIAGNOSES = 2
SLEEP_WHEN_EMPTY = 1800          # 30 min if nothing needs diagnosis
SLEEP_AFTER_BATCH = 300          # 5 min between batches
VERIFY_EVERY_N_BATCHES = 6      # run verification every ~6 batches (~30min)


async def run_diagnosis_loop():
    """Continuous diagnosis loop. Runs 24/7, processes all keywords in priority order."""
    global _diagnosis_running
    if _diagnosis_running:
        log.warning("Diagnosis loop already running")
        return
    _diagnosis_running = True
    log.info("Continuous diagnosis loop started")

    while _diagnosis_running:
        try:
            queue = await get_diagnosis_priority_queue()
            if not queue:
                log.info(f"No keywords need diagnosis. Sleeping {SLEEP_WHEN_EMPTY // 60}min.")
                await asyncio.sleep(SLEEP_WHEN_EMPTY)
                continue

            needs_work = [
                item for item in queue
                if item["last_diagnosed"] is None or item["priority_score"] >= 40
            ][:BATCH_SIZE]

            if not needs_work:
                log.info(f"All keywords diagnosed recently. Sleeping {SLEEP_WHEN_EMPTY // 60}min.")
                await asyncio.sleep(SLEEP_WHEN_EMPTY)
                continue

            log.info(f"Diagnosis batch: {len(needs_work)} keywords")
            broadcast("diagnosis_start", count=len(needs_work))

            success = 0
            skipped = 0
            for item in needs_work:
                try:
                    result = await generate_diagnosis(
                        prompt_id=item["prompt_id"],
                        keyword=item["keyword"],
                    )
                    if not result:
                        continue
                    if result.get("skipped"):
                        skipped += 1
                        continue
                    if "error" not in result:
                        success += 1
                        broadcast("diagnosis_done",
                                  keyword=item["keyword"],
                                  priority=result.get("priority", "unknown"))
                except Exception as e:
                    log.error(f"Diagnosis failed for {item['keyword']}: {e}")

                await asyncio.sleep(DELAY_BETWEEN_DIAGNOSES)

            log.info(f"Diagnosis batch done: {success} diagnosed, {skipped} skipped (unchanged)")
            broadcast("diagnosis_batch_done", success=success, skipped=skipped, total=len(needs_work))

            _batches_since_verify += 1
            if _batches_since_verify >= VERIFY_EVERY_N_BATCHES:
                _batches_since_verify = 0
                try:
                    verified = await verify_all_monitoring_fixes()
                    if verified:
                        log.info(f"Verification pass: {verified} fixes re-verified")
                except Exception as e:
                    log.error(f"Verification pass failed: {e}")

            await asyncio.sleep(SLEEP_AFTER_BATCH)

        except Exception as e:
            log.error(f"Diagnosis loop error: {e}")
            await asyncio.sleep(300)

    log.info("Diagnosis loop stopped")
