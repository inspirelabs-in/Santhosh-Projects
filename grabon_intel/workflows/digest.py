"""DigestWF — cron-scheduled daily digest. Trivial wrapper over `notify.digest.send_digest`."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from ..notify.digest import send_digest


@dataclass(slots=True)
class DigestWFInput:
    period_hours: int = 24


@dataclass(slots=True)
class DigestWFResult:
    sent: dict[str, bool]
    title: str
    summary_lines: int


@activity.defn(name="send_digest")
async def send_digest_activity(period_hours: int) -> DigestWFResult:
    r = await send_digest(period_hours)
    return DigestWFResult(sent=r["sent"], title=r["title"], summary_lines=int(r["summary_lines"]))


@workflow.defn
class DigestWF:
    @workflow.run
    async def run(self, inp: DigestWFInput) -> DigestWFResult:
        return await workflow.execute_activity(
            send_digest_activity,
            inp.period_hours,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
