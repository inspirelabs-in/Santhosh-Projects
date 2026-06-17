"""
Diagnosis scheduler -- runs daily, prioritizes keywords where
target brand is absent, low-ranked, or has negative sentiment.
"""
import asyncio
import logging

from app.agent.diagnosis_engine import generate_diagnosis, get_diagnosis_priority_queue
from app.events import broadcast

log = logging.getLogger("geo.diagnosis_scheduler")

_diagnosis_running = False

DAILY_DIAGNOSIS_LIMIT = 100
DIAGNOSIS_INTERVAL_HOURS = 12
DELAY_BETWEEN_DIAGNOSES = 3


async def run_diagnosis_loop():
    """Continuous diagnosis loop. Runs once per day."""
    global _diagnosis_running
    if _diagnosis_running:
        log.warning("Diagnosis loop already running")
        return
    _diagnosis_running = True
    log.info("Diagnosis loop started")

    while _diagnosis_running:
        try:
            queue = await get_diagnosis_priority_queue()
            if not queue:
                log.info("No keywords need diagnosis. Sleeping 6h.")
                await asyncio.sleep(6 * 3600)
                continue

            needs_diagnosis = [
                item for item in queue
                if item["last_diagnosed"] is None or item["priority_score"] >= 60
            ][:DAILY_DIAGNOSIS_LIMIT]

            if not needs_diagnosis:
                log.info("All keywords diagnosed recently. Sleeping 24h.")
                await asyncio.sleep(DIAGNOSIS_INTERVAL_HOURS * 3600)
                continue

            log.info(f"Running diagnosis for {len(needs_diagnosis)} keywords")
            broadcast("diagnosis_start", count=len(needs_diagnosis))

            success = 0
            for item in needs_diagnosis:
                try:
                    result = await generate_diagnosis(
                        prompt_id=item["prompt_id"],
                        keyword=item["keyword"],
                    )
                    if result:
                        success += 1
                        broadcast("diagnosis_done",
                                  keyword=item["keyword"],
                                  priority=result.get("priority", "unknown"))
                except Exception as e:
                    log.error(f"Diagnosis failed for {item['keyword']}: {e}")

                await asyncio.sleep(DELAY_BETWEEN_DIAGNOSES)

            log.info(f"Diagnosis batch done: {success}/{len(needs_diagnosis)}")
            broadcast("diagnosis_batch_done", success=success, total=len(needs_diagnosis))

            log.info(f"Diagnosis loop sleeping {DIAGNOSIS_INTERVAL_HOURS}h")
            await asyncio.sleep(DIAGNOSIS_INTERVAL_HOURS * 3600)

        except Exception as e:
            log.error(f"Diagnosis loop error: {e}")
            await asyncio.sleep(300)

    log.info("Diagnosis loop stopped")
