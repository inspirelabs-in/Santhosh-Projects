"""RetrainWF — wraps `learning.retrain` as a Temporal workflow.

Scheduled weekly via:
  temporal schedule create --schedule-id retrain-weekly --cron "0 3 * * 1" \
    --workflow-type RetrainWF --task-queue grabon-intel --input '{}'

Why a workflow at all (vs a cron-fired script)?
  - Single source of truth for execution history + retry semantics.
  - Failures show up next to discovery + dossier traces.
  - Trivially upgradable to fan-out if we ever need per-vertical models.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from ..learning import retrain as _retrain


@dataclass(slots=True)
class RetrainWFInput:
    note: str = ""


@dataclass(slots=True)
class RetrainWFResult:
    skipped: bool
    reason: str | None
    meeting_version: int | None
    close_value_version: int | None
    n_total: int
    n_meeting_positives: int
    n_close_won: int
    metrics: dict[str, Any]


@activity.defn(name="learning_retrain")
async def learning_retrain_activity() -> RetrainWFResult:
    r = await _retrain()
    return RetrainWFResult(
        skipped=r.skipped,
        reason=r.reason,
        meeting_version=r.meeting_version,
        close_value_version=r.close_value_version,
        n_total=r.n_total,
        n_meeting_positives=r.n_meeting_positives,
        n_close_won=r.n_close_won,
        metrics=r.metrics,
    )


@workflow.defn
class RetrainWF:
    @workflow.run
    async def run(self, inp: RetrainWFInput) -> RetrainWFResult:
        return await workflow.execute_activity(
            learning_retrain_activity,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
