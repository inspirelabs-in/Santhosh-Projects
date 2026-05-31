"""DiscoveryWF: run signal collector, optionally fan out to DossierWF.

Deterministic. All I/O via activities. Designed for cron triggering
(via Temporal schedules) and ad-hoc CLI runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from .activities import (
        CollectorParams,
        CollectorResult,
        FanOutResult,
        fan_out_new_brands_activity,
        run_collector_activity,
    )
    from .dossier import DossierWF, DossierWFInput


_COLLECTOR_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=4,
    non_retryable_error_types=["ValueError"],
)


@dataclass(slots=True)
class DiscoveryWFInput:
    collector: str
    params: dict[str, Any] = field(default_factory=dict)
    fan_out_dossiers: bool = False
    fan_out_since_minutes: int = 60
    max_fan_out: int = 25


@dataclass(slots=True)
class DiscoveryWFResult:
    collector: str
    emitted: int
    deduped: int
    fanned_out_brand_ids: list[int]


@workflow.defn
class DiscoveryWF:
    @workflow.run
    async def run(self, inp: DiscoveryWFInput) -> DiscoveryWFResult:
        collector_result: CollectorResult = await workflow.execute_activity(
            run_collector_activity,
            CollectorParams(collector=inp.collector, params=inp.params),
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=_COLLECTOR_RETRY,
        )

        fanned: list[int] = []
        if inp.fan_out_dossiers and collector_result.emitted > 0:
            fan: FanOutResult = await workflow.execute_activity(
                fan_out_new_brands_activity,
                inp.fan_out_since_minutes,
                start_to_close_timeout=timedelta(seconds=30),
            )
            targets = fan.brand_ids[: inp.max_fan_out]
            # Kick off child workflows in parallel but cap concurrency by chunking.
            for chunk in _chunks(targets, 5):
                child_handles = [
                    workflow.start_child_workflow(
                        DossierWF.run,
                        DossierWFInput(brand_id=bid, reason=f"discovery:{inp.collector}"),
                        id=f"dossier-{bid}-{workflow.info().workflow_id[:8]}",
                        retry_policy=RetryPolicy(maximum_attempts=2),
                    )
                    for bid in chunk
                ]
                handles = [await h for h in child_handles]
                # Don't block on child completion — fire and forget for throughput.
                # (We persist via activities inside the children.)
                for bid, _h in zip(chunk, handles, strict=True):
                    fanned.append(bid)

        return DiscoveryWFResult(
            collector=collector_result.collector,
            emitted=collector_result.emitted,
            deduped=collector_result.deduped,
            fanned_out_brand_ids=fanned,
        )


def _chunks(seq: list[int], n: int) -> list[list[int]]:
    return [seq[i : i + n] for i in range(0, len(seq), n)]
