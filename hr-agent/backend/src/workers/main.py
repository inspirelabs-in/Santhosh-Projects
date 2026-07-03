"""Arq worker entrypoint: ``arq src.workers.main.WorkerSettings``.

Registers every job in ``src.workers.jobs`` and a cron that polls for
voice-screen callbacks whose scheduled ring time has arrived.

To run locally:

    cd backend
    arq src.workers.main.WorkerSettings

In production, Supervisord starts a long-lived worker container alongside
the API. Worker concurrency is controlled by ``ARQ_MAX_JOBS`` env var.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from arq.connections import RedisSettings
from arq.cron import cron

from src.config import get_settings
from src.constants.timers import (
    ARQ_JOB_TIMEOUT_SECONDS,
    ARQ_KEEP_RESULT_SECONDS,
    ARQ_POLL_DELAY_SECONDS,
    ASSIGNMENT_DEADLINE_REMINDERS_CRON_MINUTES,
    CRASH_RETRY_BACKOFF_SECONDS,
    PIPELINE_SLA_MONITOR_CRON_MINUTES,
    POLL_DUE_CALLBACKS_CRON_SECONDS,
    PRUNE_OLD_ARTIFACTS_CRON_HOURS,
    PRUNE_OLD_ARTIFACTS_CRON_MINUTES,
    RECONCILE_STUCK_MEETINGS_CRON_MINUTES,
    RECONCILE_STUCK_VOICE_CALLS_CRON_MINUTES,
)
from src.workers import jobs

logger = logging.getLogger(__name__)
_settings = get_settings()


async def _restart_listener() -> None:
    """Exit on `config:restart-workers` so supervisord respawns the worker.

    Lets admin "Apply now" button take effect for non-hot-reload settings
    (MAIL_INBOXES, MAIL_POLL_INTERVAL_SECONDS).
    """
    from src.db.connection import get_redis

    while True:
        try:
            pubsub = get_redis().pubsub()
            await pubsub.subscribe("config:restart-workers")
            logger.info("worker restart listener subscribed")
            async for msg in pubsub.listen():
                if msg.get("type") == "message":
                    logger.warning("config restart signal received; exiting for supervised respawn")
                    os.kill(os.getpid(), signal.SIGTERM)
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("worker restart listener crashed; retry in 5s")
            await asyncio.sleep(CRASH_RETRY_BACKOFF_SECONDS)


async def startup(ctx: dict) -> None:
    logging.basicConfig(
        level=_settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("arq worker starting; queue=%s", _settings.arq_queue_name)
    ctx["_restart_task"] = asyncio.create_task(_restart_listener())


async def shutdown(ctx: dict) -> None:
    task = ctx.get("_restart_task")
    if task:
        task.cancel()
    logger.info("arq worker shutting down")


class WorkerSettings:
    """Arq worker config -- discovered via ``arq <module>:WorkerSettings``."""

    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    queue_name = _settings.arq_queue_name
    max_jobs = _settings.arq_max_jobs
    job_timeout = ARQ_JOB_TIMEOUT_SECONDS  # 10 minutes default per job
    keep_result = ARQ_KEEP_RESULT_SECONDS  # 1 hour
    # arq's built-in default poll_delay is 0.5s, polling Redis ~2x/sec 24/7 --
    # this is what drained the Upstash free-tier command quota. Slowing the
    # poll to 15s cuts Redis command volume ~30x; job pickup latency rises from
    # ~0.5s to ~15s, which is an acceptable tradeoff here.
    poll_delay = ARQ_POLL_DELAY_SECONDS
    on_startup = startup
    on_shutdown = shutdown

    functions = [
        jobs.dispatch_voice_screening,
        jobs.dispatch_voice_call,
        jobs.evaluate_voice_call,
        jobs.dispatch_assessment,
        jobs.dispatch_meeting_bot,
        jobs.analyze_meeting,
        jobs.schedule_meeting,
        jobs.schedule_meeting_reattempt,
        jobs.generate_ceo_brief,
        jobs.smart_schedule_meeting,
        jobs.panel_availability_request,
        jobs.poll_due_callbacks,
        jobs.reconcile_stuck_voice_calls,
        jobs.pipeline_sla_monitor,
        jobs.prune_old_artifacts,
        jobs.assignment_deadline_reminders,
        jobs.reconcile_stuck_meetings,
    ]

    cron_jobs = [
        # Merged tick: fires every 15s ({0,15,30,45}) and runs BOTH
        # poll_due_callbacks + campaign_dispatch_tick bodies each time, so each
        # keeps its original 4x/min frequency under one cron registration. The
        # campaign tick's original {7,22,37,52} offset was only cosmetic load-
        # spreading, dropped here. We do NOT re-check the exact second in the
        # body: with poll_delay=15s arq fires crons up to ~15s late, so an
        # exact-second gate would skip the work (see jobs.voice_and_campaign_tick).
        cron(
            jobs.voice_and_campaign_tick,
            name="voice_and_campaign_tick",
            second=POLL_DUE_CALLBACKS_CRON_SECONDS,
            run_at_startup=False,
        ),
        # Merged tick: registered once at the union of reconcile_stuck_voice_calls'
        # {3,18,33,48}min and reconcile_stuck_meetings' {10,25,40,55}min
        # schedules. jobs.reconcile_stuck_voice_and_meetings internally
        # re-checks the current minute against each job's own original set
        # (each try/except isolated), so both still run on exactly their
        # original 15-minute cadence. run_at_startup=True is kept here to
        # preserve the voice-call reconciler's original immediate boot-time
        # run (see the process-local flag in jobs.py for how that boot
        # firing -- which lands on an arbitrary minute -- is detected).
        cron(
            jobs.reconcile_stuck_voice_and_meetings,
            name="reconcile_stuck_voice_and_meetings",
            minute=RECONCILE_STUCK_VOICE_CALLS_CRON_MINUTES | RECONCILE_STUCK_MEETINGS_CRON_MINUTES,
            run_at_startup=True,
        ),
        cron(
            jobs.pipeline_sla_monitor,
            name="pipeline_sla_monitor",
            minute=PIPELINE_SLA_MONITOR_CRON_MINUTES,  # once per hour at HH:07
            run_at_startup=False,
        ),
        cron(
            jobs.prune_old_artifacts,
            name="prune_old_artifacts",
            hour=PRUNE_OLD_ARTIFACTS_CRON_HOURS,  # daily at 03:00 UTC
            minute=PRUNE_OLD_ARTIFACTS_CRON_MINUTES,
            run_at_startup=False,
        ),
        cron(
            jobs.assignment_deadline_reminders,
            name="assignment_deadline_reminders",
            minute=ASSIGNMENT_DEADLINE_REMINDERS_CRON_MINUTES,  # every 30 minutes
            run_at_startup=False,
        ),
    ]
