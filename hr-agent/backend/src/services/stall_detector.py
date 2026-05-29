"""Pipeline stall detector — background worker that finds stuck candidates
and creates PipelineAlert rows + Teams notifications.

Runs every 2 hours. Catches:
  - Screening sent but no response for >3 days
  - Assignment sent but no submission for >5 days
  - Report ready but no tech review for >2 days
  - HR review pending for >2 days
  - Interview proposed but no confirmation for >1 day
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, func, select

from src.channels import teams as teams_channel
from src.config import get_settings
from src.db.base import Application, Candidate, Interview, PipelineAlert, Role
from src.db.connection import session_scope

logger = logging.getLogger(__name__)
_settings = get_settings()


def _fmt_duration(hours: float) -> str:
    days = int(hours // 24)
    hrs = int(hours % 24)
    if days > 0:
        return f"{days}d {hrs}h"
    return f"{hrs}h"


STALL_RULES: list[dict] = [
    {
        "name": "screening_no_response",
        "stages": ("screening_sent",),
        "max_hours": 72,
        "message": "Candidate hasn't responded to screening in {duration}",
        "severity": "warning",
    },
    {
        "name": "assignment_no_submission",
        "stages": ("assignment_sent",),
        "max_hours": 120,
        "message": "Assignment not submitted after {duration}",
        "severity": "warning",
    },
    {
        "name": "tech_review_pending",
        "stages": ("report_ready", "technical_pending_approval"),
        "max_hours": 48,
        "message": "Tech panel review pending for {duration}",
        "severity": "urgent",
    },
    {
        "name": "hr_review_pending",
        "stages": ("needs_hr_review",),
        "max_hours": 48,
        "message": "HR review pending for {duration}",
        "severity": "urgent",
    },
    {
        "name": "interview_not_confirmed",
        "stages": ("voice_screen_scheduled",),
        "max_hours": 24,
        "message": "Interview slot proposed but not confirmed for {duration}",
        "severity": "info",
    },
    {
        "name": "ceo_approval_stale",
        "stages": ("ceo_pending_approval",),
        "max_hours": 72,
        "message": "CEO approval pending for {duration}",
        "severity": "urgent",
    },
    {
        "name": "voice_screen_stuck",
        "stages": ("voice_screen_scheduled", "voice_screen_in_progress"),
        "max_hours": 4,
        "message": "Voice screening stuck for {duration} — possible provider failure",
        "severity": "urgent",
    },
    {
        "name": "assessment_no_result",
        "stages": ("assessment_sent",),
        "max_hours": 168,
        "message": "Assessment sent {duration} ago with no result",
        "severity": "warning",
    },
]


async def _check_stalls() -> int:
    """Scan all active applications for stall conditions. Returns alert count."""
    alerts_created = 0
    now = datetime.now(tz=UTC)

    async with session_scope() as session:
        for rule in STALL_RULES:
            cutoff = now - timedelta(hours=rule["max_hours"])

            stale_apps = (
                await session.execute(
                    select(Application, Candidate, Role)
                    .join(Candidate, Candidate.id == Application.candidate_id)
                    .join(Role, Role.id == Application.role_id, isouter=True)
                    .where(
                        and_(
                            Application.current_stage.in_(rule["stages"]),
                            Application.updated_at < cutoff,
                            Application.status.notin_(("rejected", "hired", "withdrawn")),
                        )
                    )
                )
            ).all()

            for app, cand, role in stale_apps:
                hours_stuck = (now - app.updated_at).total_seconds() / 3600

                # Check if alert already exists (unresolved) for this app+type
                existing = await session.scalar(
                    select(PipelineAlert.id).where(
                        and_(
                            PipelineAlert.application_id == app.id,
                            PipelineAlert.alert_type == rule["name"],
                            PipelineAlert.resolved_at.is_(None),
                        )
                    )
                )
                if existing:
                    continue

                alert = PipelineAlert(
                    application_id=app.id,
                    alert_type=rule["name"],
                    stage=app.current_stage,
                    hours_stuck=round(hours_stuck, 1),
                    details={
                        "candidate_name": cand.name,
                        "candidate_email": cand.email,
                        "role_title": role.title if role else None,
                        "severity": rule["severity"],
                        "message": rule["message"].format(duration=_fmt_duration(hours_stuck)),
                    },
                )
                session.add(alert)
                alerts_created += 1

                if rule["severity"] == "urgent":
                    try:
                        await teams_channel.notify_hr(
                            title=f"Stall Alert: {rule['name']}",
                            text=rule["message"].format(duration=_fmt_duration(hours_stuck)),
                            fields={
                                "candidate": cand.name or cand.email or "unknown",
                                "role": role.title if role else "unknown",
                                "stage": app.current_stage,
                                "application_id": str(app.id),
                            },
                        )
                    except Exception:
                        logger.exception("Failed to send stall alert to Teams")

    return alerts_created


async def _auto_resolve() -> int:
    """Resolve alerts where application has moved past the stalled stage."""
    resolved = 0
    now = datetime.now(tz=UTC)

    async with session_scope() as session:
        open_alerts = (
            await session.execute(
                select(PipelineAlert, Application)
                .join(Application, Application.id == PipelineAlert.application_id)
                .where(PipelineAlert.resolved_at.is_(None))
            )
        ).all()

        for alert, app in open_alerts:
            rule = next((r for r in STALL_RULES if r["name"] == alert.alert_type), None)
            if rule is None:
                continue
            if app.current_stage not in rule["stages"] or app.status in ("rejected", "hired", "withdrawn"):
                alert.resolved_at = now
                resolved += 1

    return resolved


async def run_stall_detector() -> None:
    """Background loop. Runs every 2 hours."""
    while True:
        try:
            resolved = await _auto_resolve()
            created = await _check_stalls()
            if created or resolved:
                logger.info(
                    "stall detector: %d new alerts, %d auto-resolved",
                    created, resolved,
                )
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("stall detector crashed")
        await asyncio.sleep(7200)  # 2 hours
