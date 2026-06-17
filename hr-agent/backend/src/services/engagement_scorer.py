"""Candidate engagement scoring.

Computes a 0-1 engagement score from behavioral signals:
- Response time (how quickly they reply to screening/chat/email)
- Completion rate (did they finish screening, submit assignment)
- Scheduling flexibility (accepted first slot, rescheduled, no-showed)
- Message sentiment (positive/neutral vs negative/withdrawal language)
- Document submission speed

The score feeds into the confidence analyzer and powers proactive
disengagement alerts so the supervisor can act before a candidate ghosts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import Application, AuditLog, VoiceCall

logger = logging.getLogger(__name__)


@dataclass
class EngagementScore:
    overall: float
    response_speed: float
    completion_rate: float
    scheduling_flexibility: float
    signals: list[str]
    risk: str  # "low" | "medium" | "high"


async def compute_engagement(
    session: AsyncSession,
    application_id: UUID,
) -> EngagementScore:
    """Compute engagement score from audit trail + application state."""
    app = await session.get(Application, application_id)
    if not app:
        return EngagementScore(
            overall=0.5, response_speed=0.5, completion_rate=0.5,
            scheduling_flexibility=0.5, signals=[], risk="medium",
        )

    # Fetch audit trail for timing analysis
    audit_rows = (
        await session.execute(
            select(AuditLog)
            .where(AuditLog.application_id == application_id)
            .order_by(AuditLog.created_at.asc())
            .limit(100)
        )
    ).scalars().all()

    signals: list[str] = []
    scores: dict[str, float] = {}

    # --- Response speed ---
    # Measure time between screening_sent → screening_submitted
    sent_at: datetime | None = None
    submitted_at: datetime | None = None
    for a in audit_rows:
        if a.action == "stage_set" and (a.details or {}).get("new_stage") == "screening_sent":
            sent_at = a.created_at
        elif a.action == "stage_set" and (a.details or {}).get("new_stage") == "screening_submitted":
            submitted_at = a.created_at
            break
    if sent_at and submitted_at:
        hours = (submitted_at - sent_at).total_seconds() / 3600
        if hours < 4:
            scores["response_speed"] = 1.0
            signals.append("responded_within_4h")
        elif hours < 24:
            scores["response_speed"] = 0.8
            signals.append("responded_within_24h")
        elif hours < 72:
            scores["response_speed"] = 0.5
        else:
            scores["response_speed"] = 0.2
            signals.append("slow_responder")
    else:
        scores["response_speed"] = 0.5

    # --- Completion rate ---
    stage_order = [
        "applied", "screening_sent", "screening_submitted", "screening_evaluated",
        "voice_screen_completed", "voice_screen_evaluated",
        "assessment_invited", "assessment_completed", "assessment_evaluated",
        "technical_meeting_scheduled", "technical_meeting_completed",
    ]
    current = app.current_stage or "applied"
    try:
        position = stage_order.index(current) if current in stage_order else 0
    except ValueError:
        position = 0
    scores["completion_rate"] = min(1.0, position / max(len(stage_order) - 1, 1))

    if current == "rejected":
        signals.append("rejected")
    elif app.screening_score and app.screening_score > 7:
        signals.append("high_screening_score")

    # --- Scheduling flexibility ---
    voice_rows = (
        await session.execute(
            select(VoiceCall)
            .where(VoiceCall.application_id == application_id)
            .order_by(VoiceCall.created_at.desc())
            .limit(5)
        )
    ).scalars().all()

    callback_count = sum(1 for v in voice_rows if v.callback_at is not None)
    noanswer_count = sum(1 for v in voice_rows if v.status == "no_answer")
    total_calls = len(voice_rows)

    if total_calls == 0:
        scores["scheduling_flexibility"] = 0.5
    elif noanswer_count >= 3:
        scores["scheduling_flexibility"] = 0.1
        signals.append("multiple_no_answers")
    elif callback_count > 0 and noanswer_count == 0:
        scores["scheduling_flexibility"] = 0.7
        signals.append("callback_cooperative")
    elif noanswer_count == 0:
        scores["scheduling_flexibility"] = 1.0
        signals.append("answered_first_call")
    else:
        scores["scheduling_flexibility"] = 0.4

    # --- Overall ---
    weights = {"response_speed": 0.35, "completion_rate": 0.30, "scheduling_flexibility": 0.35}
    overall = sum(scores.get(k, 0.5) * w for k, w in weights.items())

    # Disengagement risk
    if overall >= 0.7:
        risk = "low"
    elif overall >= 0.4:
        risk = "medium"
    else:
        risk = "high"
        signals.append("disengagement_risk")

    return EngagementScore(
        overall=round(overall, 3),
        response_speed=round(scores.get("response_speed", 0.5), 3),
        completion_rate=round(scores.get("completion_rate", 0.5), 3),
        scheduling_flexibility=round(scores.get("scheduling_flexibility", 0.5), 3),
        signals=signals,
        risk=risk,
    )
