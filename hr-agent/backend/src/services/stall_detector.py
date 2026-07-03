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
from src.constants.statuses import TERMINAL_APPLICATION_STATUSES
from src.constants.timers import STALL_DETECTOR_LOOP_INTERVAL_SECONDS
from src.db.base import Application, Candidate, Interview, PipelineAlert, Role
from src.db.connection import session_scope
from src.db.repositories.policy import resolve_policy

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
        "policy_key": "stall_screening_no_response_hours",
        "default_hours": 72,
        "message": "Candidate hasn't responded to screening in {duration}",
        "severity": "warning",
    },
    {
        "name": "assignment_no_submission",
        "stages": ("assignment_sent",),
        "policy_key": "stall_assignment_no_submission_hours",
        "default_hours": 120,
        "message": "Assignment not submitted after {duration}",
        "severity": "warning",
    },
    {
        "name": "tech_review_pending",
        "stages": ("report_ready", "technical_pending_approval"),
        "policy_key": "stall_tech_review_pending_hours",
        "default_hours": 48,
        "message": "Tech panel review pending for {duration}",
        "severity": "urgent",
    },
    {
        "name": "hr_review_pending",
        "stages": ("needs_hr_review",),
        "policy_key": "stall_hr_review_pending_hours",
        "default_hours": 48,
        "message": "HR review pending for {duration}",
        "severity": "urgent",
    },
    {
        "name": "interview_not_confirmed",
        "stages": ("voice_screen_scheduled",),
        "policy_key": "stall_interview_not_confirmed_hours",
        "default_hours": 24,
        "message": "Interview slot proposed but not confirmed for {duration}",
        "severity": "info",
    },
    {
        "name": "ceo_approval_stale",
        "stages": ("ceo_pending_approval",),
        "policy_key": "stall_ceo_approval_stale_hours",
        "default_hours": 72,
        "message": "CEO approval pending for {duration}",
        "severity": "urgent",
    },
    {
        "name": "voice_screen_stuck",
        "stages": ("voice_screen_scheduled", "voice_screen_in_progress"),
        "policy_key": "stall_voice_screen_stuck_hours",
        "default_hours": 4,
        "message": "Voice screening stuck for {duration} — possible provider failure",
        "severity": "urgent",
    },
    {
        "name": "assessment_no_result",
        "stages": ("assessment_sent",),
        "policy_key": "stall_assessment_no_result_hours",
        "default_hours": 168,
        "message": "Assessment sent {duration} ago with no result",
        "severity": "warning",
    },
    {
        "name": "voice_screen_evaluated_stale",
        "stages": ("voice_screen_evaluated",),
        "policy_key": "stall_voice_screen_evaluated_hours",
        "default_hours": 6,
        "message": "Voice screen evaluated {duration} ago but next step not triggered",
        "severity": "warning",
    },
    {
        "name": "assessment_evaluated_stale",
        "stages": ("assessment_evaluated",),
        "policy_key": "stall_assessment_evaluated_hours",
        "default_hours": 24,
        "message": "Assessment evaluated {duration} ago — meeting not scheduled yet",
        "severity": "warning",
    },
    {
        "name": "screening_evaluated_stale",
        "stages": ("screening_evaluated",),
        "policy_key": "stall_screening_evaluated_hours",
        "default_hours": 6,
        "message": "Screening evaluated {duration} ago but next stage not triggered",
        "severity": "warning",
    },
    {
        "name": "technical_evaluated_stale",
        "stages": ("technical_evaluated",),
        "policy_key": "stall_technical_evaluated_hours",
        "default_hours": 24,
        "message": "Technical evaluation done {duration} ago — CEO round not scheduled",
        "severity": "warning",
    },
    {
        "name": "ceo_meeting_completed_stale",
        "stages": ("ceo_meeting_completed",),
        "policy_key": "stall_ceo_meeting_completed_hours",
        "default_hours": 24,
        "message": "CEO meeting completed {duration} ago — HR discussion not scheduled",
        "severity": "warning",
    },
    {
        "name": "hr_evaluated_stale",
        "stages": ("hr_evaluated",),
        "policy_key": "stall_hr_evaluated_hours",
        "default_hours": 48,
        "message": "HR evaluation done {duration} ago — offer not extended",
        "severity": "urgent",
    },
]


async def _check_stalls() -> int:
    """Scan all active applications for stall conditions. Returns alert count."""
    alerts_created = 0
    now = datetime.now(tz=UTC)

    async with session_scope() as session:
        for rule in STALL_RULES:
            max_hours, _ = await resolve_policy(
                session, rule["policy_key"], fallback=rule["default_hours"]
            )
            cutoff = now - timedelta(hours=max_hours)

            stale_apps = (
                await session.execute(
                    select(Application, Candidate, Role)
                    .join(Candidate, Candidate.id == Application.candidate_id)
                    .join(Role, Role.id == Application.role_id, isouter=True)
                    .where(
                        and_(
                            Application.current_stage.in_(rule["stages"]),
                            Application.updated_at < cutoff,
                            Application.status.notin_(TERMINAL_APPLICATION_STATUSES),
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

                # Emit supervisor event
                from src.services.typed_event_bus import EventType, publish_event
                event_type = EventType.STALL_DETECTED
                if rule["name"] == "assignment_no_submission":
                    event_type = EventType.ASSIGNMENT_OVERDUE
                await publish_event(
                    session,
                    event_type,
                    application_id=app.id,
                    candidate_id=app.candidate_id,
                    payload={
                        "rule_name": rule["name"],
                        "stage": app.current_stage,
                        "hours_stuck": round(hours_stuck, 1),
                        "severity": rule["severity"],
                    },
                    dedup_extra=f"{rule['name']}:{app.id}",
                )

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


async def _escalate_unresolved() -> int:
    """Emit ALERT_UNRESOLVED for alerts sitting open past escalation threshold."""
    escalated = 0
    now = datetime.now(tz=UTC)

    async with session_scope() as session:
        escalation_hours, _ = await resolve_policy(
            session, "stall_alert_escalation_hours", fallback=48
        )
        cutoff = now - timedelta(hours=escalation_hours)

        old_alerts = (
            await session.execute(
                select(PipelineAlert)
                .where(
                    and_(
                        PipelineAlert.resolved_at.is_(None),
                        PipelineAlert.created_at < cutoff,
                    )
                )
            )
        ).scalars().all()

        from src.services.typed_event_bus import EventType, publish_event
        for alert in old_alerts:
            hours_open = (now - alert.created_at).total_seconds() / 3600
            await publish_event(
                session,
                EventType.ALERT_UNRESOLVED,
                application_id=alert.application_id,
                payload={
                    "alert_type": alert.alert_type,
                    "stage": alert.stage,
                    "hours_open": round(hours_open, 1),
                    "original_details": alert.details,
                },
                dedup_extra=f"unresolved:{alert.id}",
            )
            escalated += 1

    return escalated


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
            if app.current_stage not in rule["stages"] or app.status in TERMINAL_APPLICATION_STATUSES:
                alert.resolved_at = now
                resolved += 1

    return resolved


async def run_stall_detector() -> None:
    """Background loop. Runs every 2 hours."""
    while True:
        try:
            resolved = await _auto_resolve()
            created = await _check_stalls()
            escalated = await _escalate_unresolved()
            if created or resolved or escalated:
                logger.info(
                    "stall detector: %d new alerts, %d auto-resolved, %d escalated",
                    created, resolved, escalated,
                )
            # Run re-engagement checks alongside stall detection
            try:
                from src.services.reengagement import check_reengagement_candidates
                reeng = await check_reengagement_candidates()
                if reeng:
                    logger.info("re-engagement: %d candidates flagged", len(reeng))
            except Exception:
                logger.warning("re-engagement check failed", exc_info=True)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("stall detector crashed")
        await asyncio.sleep(STALL_DETECTOR_LOOP_INTERVAL_SECONDS)  # 2 hours
