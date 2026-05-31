"""AdversarialDiscoveryWF — periodic blind-spot generator."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from ..graph.adversarial import run_adversarial


@dataclass(slots=True)
class AdversarialWFResult:
    proposal_count: int
    cost_cents: int
    proposals: list[dict[str, Any]]


@activity.defn(name="adversarial_discovery")
async def adversarial_activity() -> AdversarialWFResult:
    r = await run_adversarial()
    return AdversarialWFResult(
        proposal_count=len(r.proposals), cost_cents=r.cost_cents, proposals=r.proposals
    )


@workflow.defn
class AdversarialDiscoveryWF:
    @workflow.run
    async def run(self) -> AdversarialWFResult:
        return await workflow.execute_activity(
            adversarial_activity,
            start_to_close_timeout=timedelta(minutes=3),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
