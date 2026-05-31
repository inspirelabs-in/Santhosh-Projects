"""Multi-step outreach sequence orchestrator.

Manages 5-touch email sequences with configurable delays:
  Day 1  → Trigger/pain hook
  Day 3  → Case study
  Day 7  → New angle
  Day 10 → Pattern interrupt
  Day 14 → Breakup

Each step is sent via smtp_sender with sequence_step metadata.
Sequence state tracked in DB for resumability.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from ..db import session as session_ctx
from ..logging import get_logger
from .smtp_sender import send_email

log = get_logger(__name__)

DEFAULT_SCHEDULE = [
    {"step": 1, "delay_days": 0, "angle": "trigger_pain"},
    {"step": 2, "delay_days": 3, "angle": "case_study"},
    {"step": 3, "delay_days": 7, "angle": "new_angle"},
    {"step": 4, "delay_days": 10, "angle": "pattern_interrupt"},
    {"step": 5, "delay_days": 14, "angle": "breakup"},
]


@dataclass(slots=True)
class SequenceConfig:
    brand_id: int
    to_email: str
    subjects: list[str]
    bodies: list[str]
    html_bodies: list[str] | None = None
    schedule: list[dict[str, Any]] = field(default_factory=lambda: list(DEFAULT_SCHEDULE))


@dataclass(slots=True)
class SequenceState:
    id: int
    brand_id: int
    to_email: str
    current_step: int
    status: str  # active, paused, completed, stopped
    created_at: dt.datetime
    next_send_at: dt.datetime | None


async def create_sequence(config: SequenceConfig) -> int:
    """Create a new outreach sequence. Returns sequence_id."""
    import orjson

    async with session_ctx() as s:
        row = (
            await s.execute(
                text(
                    "INSERT INTO outreach_sequences "
                    "(brand_id, to_email, subjects, bodies, html_bodies, schedule, current_step, status, next_send_at) "
                    "VALUES (:bid, :to, :subj, :bod, :html, CAST(:sched AS JSONB), 1, 'active', NOW()) "
                    "RETURNING id"
                ),
                {
                    "bid": config.brand_id,
                    "to": config.to_email,
                    "subj": orjson.dumps(config.subjects).decode(),
                    "bod": orjson.dumps(config.bodies).decode(),
                    "html": orjson.dumps(config.html_bodies).decode() if config.html_bodies else None,
                    "sched": orjson.dumps(config.schedule).decode(),
                },
            )
        ).first()
    seq_id = int(row[0])
    log.info("sequence.created", sequence_id=seq_id, brand_id=config.brand_id)
    return seq_id


async def send_next_step(sequence_id: int) -> dict[str, Any]:
    """Send the next pending step of a sequence. Returns send result."""
    import orjson

    async with session_ctx() as s:
        row = (
            await s.execute(
                text(
                    "SELECT id, brand_id, to_email, subjects, bodies, html_bodies, "
                    "schedule, current_step, status FROM outreach_sequences WHERE id = :id"
                ),
                {"id": sequence_id},
            )
        ).first()

    if not row:
        return {"error": "sequence_not_found"}

    seq_id, brand_id, to_email, subjects_raw, bodies_raw, html_raw, schedule_raw, current_step, status = row

    if status != "active":
        return {"error": f"sequence_{status}", "step": current_step}

    subjects = orjson.loads(subjects_raw) if isinstance(subjects_raw, str) else subjects_raw
    bodies = orjson.loads(bodies_raw) if isinstance(bodies_raw, str) else bodies_raw
    schedule = orjson.loads(schedule_raw) if isinstance(schedule_raw, str) else schedule_raw

    step_idx = current_step - 1
    if step_idx >= len(subjects) or step_idx >= len(bodies):
        async with session_ctx() as s:
            await s.execute(
                text("UPDATE outreach_sequences SET status = 'completed' WHERE id = :id"),
                {"id": seq_id},
            )
        return {"status": "completed", "step": current_step}

    html_bodies = orjson.loads(html_raw) if isinstance(html_raw, str) and html_raw else html_raw
    body_html = html_bodies[step_idx] if html_bodies and step_idx < len(html_bodies) else None

    result = await send_email(
        to=to_email,
        subject=subjects[step_idx],
        body_text=bodies[step_idx],
        body_html=body_html,
        brand_id=brand_id,
        sequence_step=current_step,
    )

    # Advance to next step
    next_step = current_step + 1
    next_delay = schedule[step_idx]["delay_days"] if step_idx < len(schedule) else 3
    next_send_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=next_delay)
    new_status = "active" if next_step <= len(subjects) else "completed"

    async with session_ctx() as s:
        await s.execute(
            text(
                "UPDATE outreach_sequences SET current_step = :step, status = :st, "
                "next_send_at = :next, last_sent_at = NOW() WHERE id = :id"
            ),
            {"step": next_step, "st": new_status, "next": next_send_at, "id": seq_id},
        )

    log.info("sequence.step_sent", sequence_id=seq_id, step=current_step, ok=result.ok)
    return {
        "status": "sent",
        "step": current_step,
        "next_step": next_step if new_status == "active" else None,
        "next_send_at": next_send_at.isoformat() if new_status == "active" else None,
        "message_id": result.message_id,
        "ok": result.ok,
    }


async def get_due_sequences() -> list[int]:
    """Get sequence IDs that are due for their next step."""
    async with session_ctx() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT id FROM outreach_sequences "
                    "WHERE status = 'active' AND next_send_at <= NOW() "
                    "ORDER BY next_send_at ASC LIMIT 50"
                )
            )
        ).all()
    return [int(r[0]) for r in rows]


async def pause_sequence(sequence_id: int) -> None:
    async with session_ctx() as s:
        await s.execute(
            text("UPDATE outreach_sequences SET status = 'paused' WHERE id = :id AND status = 'active'"),
            {"id": sequence_id},
        )


async def resume_sequence(sequence_id: int) -> None:
    async with session_ctx() as s:
        await s.execute(
            text("UPDATE outreach_sequences SET status = 'active', next_send_at = NOW() WHERE id = :id AND status = 'paused'"),
            {"id": sequence_id},
        )


async def stop_sequence(sequence_id: int) -> None:
    async with session_ctx() as s:
        await s.execute(
            text("UPDATE outreach_sequences SET status = 'stopped' WHERE id = :id"),
            {"id": sequence_id},
        )
