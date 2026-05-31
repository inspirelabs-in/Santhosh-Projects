import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.agent.pipeline import run_rolling_batch, run_continuous_loop
from app.routes.auth import (
    refresh_google_cookies, refresh_chatgpt_cookies,
    refresh_gemini_cookies, refresh_claude_cookies, refresh_grok_cookies,
    refresh_perplexity_cookies,
)
from app.config import get_settings

log = logging.getLogger("geo.scheduler")

_scheduler: AsyncIOScheduler | None = None
_continuous_task: asyncio.Task | None = None


def init_scheduler():
    global _scheduler, _continuous_task
    if _scheduler is not None:
        return

    settings = get_settings()
    _scheduler = AsyncIOScheduler()

    _continuous_task = asyncio.ensure_future(run_continuous_loop())
    log.info("Continuous agent loop launched — runs non-stop")

    _scheduler.add_job(
        run_rolling_batch,
        "cron",
        args=[2, settings.tier2_batch_size, settings.tier2_concurrency],
        day_of_week="sun",
        hour=settings.cron_hour,
        minute=settings.cron_minute + 30,
        id="geo_tier2_weekly",
        replace_existing=True,
    )

    _scheduler.add_job(
        run_rolling_batch,
        "cron",
        args=[3, settings.tier3_batch_size, settings.tier3_concurrency],
        day=1,
        hour=settings.cron_hour + 1,
        minute=settings.cron_minute,
        id="geo_tier3_monthly",
        replace_existing=True,
    )

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
        refresh_grok_cookies,
        "interval",
        hours=6,
        id="grok_cookie_refresh",
        replace_existing=True,
    )

    _scheduler.add_job(
        refresh_perplexity_cookies,
        "interval",
        hours=6,
        id="perplexity_cookie_refresh",
        replace_existing=True,
    )

    _scheduler.start()
    log.info("Scheduler started — Tier 1 continuous, Tier 2 weekly, Tier 3 monthly, cookies every 6h")


def shutdown_scheduler():
    global _scheduler, _continuous_task
    if _continuous_task and not _continuous_task.done():
        _continuous_task.cancel()
        _continuous_task = None
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("Scheduler shut down")
