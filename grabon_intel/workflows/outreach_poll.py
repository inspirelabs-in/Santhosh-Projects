"""OutreachPollWF — periodic IMAP poll for replies. Schedule every 2 min."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from ..outreach.imap_poller import poll_replies


@dataclass(slots=True)
class OutreachPollResult:
    fetched: int
    classified: int
    last_uid: str | None


@activity.defn(name="outreach_imap_poll")
async def outreach_imap_poll_activity(max_messages: int = 50) -> OutreachPollResult:
    r = await poll_replies(max_messages=max_messages)
    return OutreachPollResult(fetched=r.fetched, classified=r.classified, last_uid=r.last_uid)


@workflow.defn
class OutreachPollWF:
    @workflow.run
    async def run(self, max_messages: int = 50) -> OutreachPollResult:
        return await workflow.execute_activity(
            outreach_imap_poll_activity,
            max_messages,
            start_to_close_timeout=timedelta(minutes=3),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
