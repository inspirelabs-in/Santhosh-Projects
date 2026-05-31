"""OutreachSequenceWF — process due outreach sequence steps.

Runs periodically (every 15 min via Temporal schedule) to send
pending sequence steps that are past their next_send_at time.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from ..outreach.sequence import get_due_sequences, send_next_step


@dataclass(slots=True)
class OutreachSeqResult:
    processed: int
    sent: int
    errors: list[str]


@activity.defn(name="process_due_sequences")
async def process_due_sequences_activity() -> dict[str, Any]:
    due = await get_due_sequences()
    sent = 0
    errors: list[str] = []

    for seq_id in due:
        try:
            result = await send_next_step(seq_id)
            if result.get("ok"):
                sent += 1
            elif result.get("error"):
                errors.append(f"seq_{seq_id}: {result['error']}")
        except Exception as exc:
            errors.append(f"seq_{seq_id}: {type(exc).__name__}: {exc}")

    return {"processed": len(due), "sent": sent, "errors": errors}


@workflow.defn
class OutreachSequenceWF:
    """Process all due outreach sequence steps."""

    @workflow.run
    async def run(self) -> OutreachSeqResult:
        result = await workflow.execute_activity(
            process_due_sequences_activity,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        return OutreachSeqResult(
            processed=result["processed"],
            sent=result["sent"],
            errors=result["errors"],
        )
