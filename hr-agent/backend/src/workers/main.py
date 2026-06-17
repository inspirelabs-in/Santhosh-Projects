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
            await asyncio.sleep(5)


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
    job_timeout = 600  # 10 minutes default per job
    keep_result = 60 * 60  # 1 hour
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
    ]

    cron_jobs = [
        cron(
            jobs.poll_due_callbacks,
            name="poll_due_callbacks",
            second={0, 15, 30, 45},
            run_at_startup=False,
        ),
        cron(
            jobs.campaign_dispatch_tick,
            name="campaign_dispatch_tick",
            second={7, 22, 37, 52},
            run_at_startup=False,
        ),
        cron(
            jobs.reconcile_stuck_voice_calls,
            name="reconcile_stuck_voice_calls",
            minute={3, 18, 33, 48},  # every 15 minutes
            run_at_startup=True,
        ),
        cron(
            jobs.pipeline_sla_monitor,
            name="pipeline_sla_monitor",
            minute={7},  # once per hour at HH:07
            run_at_startup=False,
        ),
        cron(
            jobs.prune_old_artifacts,
            name="prune_old_artifacts",
            hour={3},  # daily at 03:00 UTC
            minute={11},
            run_at_startup=False,
        ),
        cron(
            jobs.assignment_deadline_reminders,
            name="assignment_deadline_reminders",
            minute={0, 30},  # every 30 minutes
            run_at_startup=False,
        ),
    ]
