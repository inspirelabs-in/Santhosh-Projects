"""Idempotent Temporal schedule bootstrap.

Creates (or updates) all recurring schedules the system needs for
autonomous 24/7 operation. Safe to run repeatedly.

Usage:
    grabon-intel schedule bootstrap
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleUpdate,
    TLSConfig,
)
from temporalio.service import RPCError

from .config import get_settings
from .logging import get_logger

log = get_logger(__name__)


def _build_schedule_defs(s: Any) -> list[dict[str, Any]]:
    """Return schedule definitions derived from current settings."""
    queries = s.parsed_icp_queries()
    geos = s.parsed_icp_geos()
    discovery_hours = s.icp_discovery_interval_hours
    max_fan_out = s.icp_max_fan_out

    return [
        {
            "id": "auto-discovery",
            "workflow": "AutoDiscoveryWF",
            "arg": {
                "queries": queries,
                "geos": geos,
                "max_fan_out": max_fan_out,
                "fan_out_since_minutes": discovery_hours * 60,
            },
            "interval": timedelta(hours=discovery_hours),
            "overlap": ScheduleOverlapPolicy.SKIP,
            "memo": "Autonomous e-commerce brand discovery",
        },
        {
            "id": "event-drain",
            "workflow": "EventDrainWF",
            "arg": 100,
            "interval": timedelta(seconds=120),
            "overlap": ScheduleOverlapPolicy.SKIP,
            "memo": "Drain event bus every 120s",
        },
        {
            "id": "outreach-sequence",
            "workflow": "OutreachSequenceWF",
            "arg": None,
            "interval": timedelta(minutes=15),
            "overlap": ScheduleOverlapPolicy.SKIP,
            "memo": "Process due outreach steps every 15min",
        },
        {
            "id": "daily-digest",
            "workflow": "DigestWF",
            "arg": {"period_hours": 24},
            "interval": timedelta(hours=24),
            "overlap": ScheduleOverlapPolicy.SKIP,
            "memo": "Daily summary digest",
        },
        {
            "id": "weekly-retrain",
            "workflow": "RetrainWF",
            "arg": {},
            "interval": timedelta(days=7),
            "overlap": ScheduleOverlapPolicy.SKIP,
            "memo": "Weekly model retraining",
        },
        {
            "id": "monitoring-rescore",
            "workflow": "MonitoringWF",
            "arg": {
                "brand_ids": [],
                "min_days_since_last": 3,
                "max_brands": 15,
                "score_drift_threshold": 10,
            },
            "interval": timedelta(hours=2),
            "overlap": ScheduleOverlapPolicy.SKIP,
            "memo": "Re-research stale brands, detect score drift and new signals",
        },
    ]


async def _upsert_schedule(
    client: Client,
    schedule_id: str,
    workflow_type: str,
    arg: Any,
    interval: timedelta,
    overlap: ScheduleOverlapPolicy,
    task_queue: str,
    memo: str,
) -> str:
    """Create or update a single schedule. Returns 'created' or 'updated'."""
    action_kwargs: dict[str, Any] = {
        "id": f"sched-{schedule_id}-{{{{workflow.ScheduleTime}}}}",
        "task_queue": task_queue,
    }
    if arg is not None:
        action_kwargs["arg"] = arg
    schedule = Schedule(
        action=ScheduleActionStartWorkflow(workflow_type, **action_kwargs),
        spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=interval)]),
        policy=SchedulePolicy(overlap=overlap),
    )

    try:
        await client.create_schedule(
            id=schedule_id,
            schedule=schedule,
            memo={"description": memo},
        )
        return "created"
    except (RPCError, ScheduleAlreadyRunningError) as e:
        err_msg = str(e).lower()
        if "already" in err_msg or isinstance(e, ScheduleAlreadyRunningError):
            handle = client.get_schedule_handle(schedule_id)
            await handle.update(lambda _: ScheduleUpdate(schedule=schedule))
            return "updated"
        raise


async def bootstrap() -> dict[str, str]:
    """Create/update all Temporal schedules. Returns {schedule_id: 'created'|'updated'}."""
    s = get_settings()
    client = await Client.connect(
        s.temporal_address,
        namespace=s.temporal_namespace,
        tls=TLSConfig() if s.temporal_tls else False,
    )

    results: dict[str, str] = {}
    defs = _build_schedule_defs(s)

    for d in defs:
        try:
            status = await _upsert_schedule(
                client=client,
                schedule_id=d["id"],
                workflow_type=d["workflow"],
                arg=d["arg"],
                interval=d["interval"],
                overlap=d["overlap"],
                task_queue=s.temporal_task_queue,
                memo=d["memo"],
            )
            results[d["id"]] = status
            log.info("schedule.upserted", id=d["id"], status=status)
        except Exception as exc:
            results[d["id"]] = f"error: {exc}"
            log.error("schedule.failed", id=d["id"], error=str(exc))

    return results


def run_bootstrap() -> dict[str, str]:
    return asyncio.run(bootstrap())
