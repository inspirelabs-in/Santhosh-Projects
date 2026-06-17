import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.agent.pipeline import run_continuous_loop
from app.agent.serp_scheduler import run_serp_loop
from app.agent.diagnosis_scheduler import run_diagnosis_loop
from app.agent.verification import verify_all_monitoring_fixes
from app.routes.auth import (
    refresh_google_cookies, refresh_chatgpt_cookies,
    refresh_gemini_cookies, refresh_claude_cookies,
    refresh_perplexity_cookies,
    validate_all_cookies,
)
from app.config import get_settings

log = logging.getLogger("geo.scheduler")

_scheduler: AsyncIOScheduler | None = None
_continuous_task: asyncio.Task | None = None
_serp_task: asyncio.Task | None = None
_diagnosis_task: asyncio.Task | None = None


async def _init_pools_and_cache():
    try:
        from app.routes.auth import migrate_legacy_credentials
        await migrate_legacy_credentials()
    except Exception as e:
        log.warning(f"Credential migration failed (non-fatal): {e}")
    try:
        from app.agent.account_pool import init_all_pools
        await init_all_pools()
    except Exception as e:
        log.warning(f"Account pool init failed (non-fatal): {e}")
    try:
        from app.agent.dedup_cache import load_cache_from_db
        await load_cache_from_db()
    except Exception as e:
        log.warning(f"Dedup cache load failed (non-fatal): {e}")


def init_scheduler():
    global _scheduler, _continuous_task, _serp_task, _diagnosis_task
    if _scheduler is not None:
        return

    settings = get_settings()
    _scheduler = AsyncIOScheduler(job_defaults={"misfire_grace_time": 3600, "coalesce": True})

    asyncio.ensure_future(_init_pools_and_cache())
    log.info("Account pools and dedup cache initialization launched")

    _continuous_task = asyncio.ensure_future(run_continuous_loop())
    log.info("Continuous agent loop launched - oldest-first, all keywords")

    _serp_task = asyncio.ensure_future(run_serp_loop())
    log.info("SERP crawl loop launched - batch=%d, interval=%dh",
             settings.serp_batch_size, settings.serp_interval_hours)

    _diagnosis_task = asyncio.ensure_future(run_diagnosis_loop())
    log.info("Diagnosis loop launched - daily, top priority keywords")

    _scheduler.add_job(
        refresh_google_cookies,
        "interval",
        hours=6,
        id="google_cookie_refresh",
        replace_existing=True,
    )

    _scheduler.add_job(
        refresh_chatgpt_cookies,
        "interval",
        hours=6,
        id="chatgpt_cookie_refresh",
        replace_existing=True,
    )

    _scheduler.add_job(
        refresh_gemini_cookies,
        "interval",
        hours=6,
        id="gemini_cookie_refresh",
        replace_existing=True,
    )

    _scheduler.add_job(
        refresh_claude_cookies,
        "interval",
        hours=6,
        id="claude_cookie_refresh",
        replace_existing=True,
    )

    _scheduler.add_job(
        refresh_perplexity_cookies,
        "interval",
        hours=3,
        id="perplexity_cookie_refresh",
        replace_existing=True,
    )

    _scheduler.add_job(
        verify_all_monitoring_fixes,
        "interval",
        hours=6,
        id="verification_check",
        replace_existing=True,
    )

    _scheduler.add_job(
        validate_all_cookies,
        "interval",
        minutes=15,
        id="cookie_health_check",
        replace_existing=True,
    )

    _scheduler.start()
    log.info("Scheduler started - continuous oldest-first, cookies every 6h, health check every 15m")


def shutdown_scheduler():
    global _scheduler, _continuous_task, _serp_task, _diagnosis_task
    if _continuous_task and not _continuous_task.done():
        _continuous_task.cancel()
        _continuous_task = None
    if _serp_task and not _serp_task.done():
        _serp_task.cancel()
        _serp_task = None
    if _diagnosis_task and not _diagnosis_task.done():
        _diagnosis_task.cancel()
        _diagnosis_task = None
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("Scheduler shut down")
