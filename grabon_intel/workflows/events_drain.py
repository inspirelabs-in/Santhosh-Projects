"""EventDrainWF — periodic event bus drain. Schedule every 30s via Temporal cron."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from ..events import drain as _drain


@dataclass(slots=True)
class EventDrainResult:
    delivered: int
    failed: int


@activity.defn(name="events_drain")
async def events_drain_activity(batch_size: int = 100) -> EventDrainResult:
    r = await _drain(batch_size=batch_size)
    return EventDrainResult(delivered=int(r["delivered"]), failed=int(r["failed"]))


@workflow.defn
class EventDrainWF:
    @workflow.run
    async def run(self, batch_size: int = 100) -> EventDrainResult:
        return await workflow.execute_activity(
            events_drain_activity,
            batch_size,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
