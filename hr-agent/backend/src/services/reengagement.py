"""Re-engagement campaign engine.

Monitors candidates with declining engagement scores and orchestrates
a graduated nudge sequence to prevent ghosting:

  Day 0: gentle nudge — "We're still excited about your candidacy"
  Day 3: urgent nudge — "Your application needs your attention"
  Day 7: final notice — "We'll close your application in 48 hours"
  Day 9: auto-withdrawal if still no response

Runs as a periodic check (called from stall_detector or standalone).
Emits supervisor events so the engine can execute the actual nudges.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import Application, AuditLog, Candidate
from src.db.connection import session_scope
from src.services.engagement_scorer import compute_engagement

logger = logging.getLogger(__name__)

REENGAGEMENT_STAGES = {
    "screening_sent", "assignment_sent", "assessment_invited",
}

NUDGE_SCHEDULE = [
    {"day": 0, "nudge_type": "gentle", "action": "reengagement_gentle"},
    {"day": 3, "nudge_type": "urgent", "action": "reengagement_urgent"},
    {"day": 7, "nudge_type": "final", "action": "reengagement_final"},
    {"day": 9, "nudge_type": "withdrawal", "action": "reengagement_withdrawal"},
]


async def check_reengagement_candidates() -> list[dict[str, Any]]:
    """Find candidates needing re-engagement and emit appropriate events.

    Returns list of actions taken.
    """
    from src.services.typed_event_bus import EventType, publish_event

    actions_taken: list[dict[str, Any]] = []

    async with session_scope() as session:
        at_risk_apps = (
            await session.execute(
                select(Application)
                .where(Application.status == "active")
                .where(Application.current_stage.in_(REENGAGEMENT_STAGES))
                .order_by(Application.updated_at.asc())
                .limit(50)
            )
        ).scalars().all()

        for app in at_risk_apps:
            try:
                eng = await compute_engagement(session, app.id)
                if eng.risk != "high":
                    continue

                last_nudge = await _get_last_nudge(session, app.id)
                next_step = _determine_next_step(last_nudge)

                if not next_step:
                    continue

                if next_step["nudge_type"] == "withdrawal":
                    await publish_event(
                        session,
                        event_type=EventType.CANDIDATE_WITHDRAWAL,
                        application_id=app.id,
                        candidate_id=app.candidate_id,
                        payload={
                            "trigger": "reengagement_timeout",
                            "engagement_score": eng.overall,
                            "days_since_last_activity": last_nudge.get("days_elapsed", 0),
                        },
                    )
                else:
                    await publish_event(
                        session,
                        event_type=EventType.STALL_DETECTED,
                        application_id=app.id,
                        candidate_id=app.candidate_id,
                        payload={
                            "trigger": "reengagement",
                            "nudge_type": next_step["nudge_type"],
                            "engagement_score": eng.overall,
                            "engagement_risk": eng.risk,
                            "rule_name": next_step["action"],
                            "stage": app.current_stage,
                        },
                    )

                actions_taken.append({
                    "application_id": str(app.id),
                    "step": next_step["action"],
                    "engagement": eng.overall,
                })

            except Exception:
                logger.warning("reengagement check failed for app=%s", app.id, exc_info=True)

    return actions_taken


async def _get_last_nudge(
    session: AsyncSession, application_id: UUID,
) -> dict[str, Any]:
    """Get info about last nudge sent to this candidate."""
    last = (
        await session.execute(
            select(AuditLog)
            .where(AuditLog.application_id == application_id)
            .where(AuditLog.action.like("supervisor_nudge_%"))
            .order_by(AuditLog.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if not last:
        last_activity = (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.application_id == application_id)
                .order_by(AuditLog.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        days = 0
        if last_activity:
            days = (datetime.now(UTC) - last_activity.created_at).days

        return {"last_nudge_type": None, "days_elapsed": days}

    details = last.details or {}
    days_since = (datetime.now(UTC) - last.created_at).days
    return {
        "last_nudge_type": details.get("nudge_type", "gentle"),
        "days_elapsed": days_since,
        "last_nudge_at": last.created_at,
    }


def _determine_next_step(last_nudge: dict[str, Any]) -> dict[str, Any] | None:
    """Determine next step in re-engagement sequence."""
    last_type = last_nudge.get("last_nudge_type")
    days = last_nudge.get("days_elapsed", 0)

    if last_type is None and days >= 3:
        return NUDGE_SCHEDULE[0]
    elif last_type == "gentle" and days >= 3:
        return NUDGE_SCHEDULE[1]
    elif last_type == "urgent" and days >= 4:
        return NUDGE_SCHEDULE[2]
    elif last_type == "final" and days >= 2:
        return NUDGE_SCHEDULE[3]

    return None
