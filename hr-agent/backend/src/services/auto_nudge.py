"""Auto-nudge worker — sends reminders to candidates who haven't responded.

Runs every 6 hours. Nudge schedule:
  - screening_sent + 3 days → first reminder
  - screening_sent + 5 days → final reminder (warns of deadline)
  - assignment_sent + 5 days → reminder with 2 days left warning
  - interview proposed + 1 day → confirm-your-slot reminder

Each nudge is tracked in audit_log to prevent duplicate sends.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, select

from src.channels.email import send_email
from src.config import get_settings
from src.db.base import Application, AuditLog, Candidate, Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit

logger = logging.getLogger(__name__)
_settings = get_settings()

NUDGE_RULES = [
    {
        "name": "screening_reminder_1",
        "stage": "screening_sent",
        "after_hours": 72,
        "before_hours": 120,
        "template": "screening_reminder",
        "audit_action": "auto_nudge_screening_1",
        "subject_hint": "friendly reminder about your screening",
    },
    {
        "name": "screening_reminder_final",
        "stage": "screening_sent",
        "after_hours": 120,
        "before_hours": 168,
        "template": "screening_reminder",
        "audit_action": "auto_nudge_screening_final",
        "subject_hint": "last reminder — screening deadline approaching",
        "extra_vars": {"urgency": "final"},
    },
    {
        "name": "assignment_reminder",
        "stage": "assignment_sent",
        "after_hours": 120,
        "before_hours": 168,
        "template": "nudge",
        "audit_action": "auto_nudge_assignment",
        "subject_hint": "reminder about your assignment submission",
    },
]


async def _already_nudged(session, application_id, audit_action: str) -> bool:
    """Check if this specific nudge was already sent."""
    exists = await session.scalar(
        select(AuditLog.id).where(
            and_(
                AuditLog.application_id == application_id,
                AuditLog.action == audit_action,
            )
        ).limit(1)
    )
    return exists is not None


async def _run_nudges() -> int:
    """Execute one pass of all nudge rules. Returns count of nudges sent."""
    sent = 0
    now = datetime.now(tz=UTC)

    async with session_scope() as session:
        for rule in NUDGE_RULES:
            after_cutoff = now - timedelta(hours=rule["after_hours"])
            before_cutoff = now - timedelta(hours=rule["before_hours"])

            candidates_to_nudge = (
                await session.execute(
                    select(Application, Candidate, Role)
                    .join(Candidate, Candidate.id == Application.candidate_id)
                    .join(Role, Role.id == Application.role_id, isouter=True)
                    .where(
                        and_(
                            Application.current_stage == rule["stage"],
                            Application.updated_at <= after_cutoff,
                            Application.updated_at > before_cutoff,
                            Application.status.notin_(("rejected", "hired", "withdrawn", "cold")),
                        )
                    )
                )
            ).all()

            for app, cand, role in candidates_to_nudge:
                if not cand.email:
                    continue
                if await _already_nudged(session, app.id, rule["audit_action"]):
                    continue

                try:
                    variables = {
                        "candidate_name": cand.name or "there",
                        "role_title": role.title if role else "the role you applied for",
                        "reference_id": str(app.id),
                        **(rule.get("extra_vars") or {}),
                    }
                    result = await send_email(
                        to=cand.email,
                        template=rule["template"],
                        variables=variables,
                        tags={"stage": "auto_nudge", "rule": rule["name"]},
                    )
                    await log_audit(
                        session,
                        action=rule["audit_action"],
                        actor="auto_nudge_worker",
                        candidate_id=cand.id,
                        application_id=app.id,
                        details={
                            "rule": rule["name"],
                            "email_sent": result.success,
                            "provider": result.provider,
                            "hours_since_update": round(
                                (now - app.updated_at).total_seconds() / 3600, 1
                            ),
                        },
                    )
                    await session.flush()
                    if result.success:
                        sent += 1
                except Exception:
                    logger.exception(
                        "auto_nudge failed for app=%s rule=%s",
                        app.id, rule["name"],
                    )

    return sent


async def run_auto_nudge_worker() -> None:
    """Background loop. Runs every 6 hours."""
    await asyncio.sleep(300)  # initial delay to let app boot
    while True:
        try:
            count = await _run_nudges()
            if count:
                logger.info("auto_nudge: sent %d reminders", count)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("auto_nudge worker crashed")
        await asyncio.sleep(21600)  # 6 hours
