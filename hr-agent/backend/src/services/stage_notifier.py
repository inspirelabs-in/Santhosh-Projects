"""Stage transition notifier — auto-email candidates when they move stages.

Called from pipeline v1 stage transitions. Keeps candidates informed
about their application progress without HR lifting a finger.
"""

from __future__ import annotations

import logging

from uuid import UUID

from src.channels.email import send_email
from src.config import get_settings
from src.db.base import Application, Candidate, Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit

logger = logging.getLogger(__name__)
_settings = get_settings()

# Map stage transitions to candidate-facing notifications.
# Only notify on stages candidates care about, and ONLY with a template whose
# body actually fits that moment. The "acknowledgement" template is the
# application-RECEIVED email (body: "Thanks for applying, we have your
# application") and must NEVER be reused for progress/review notifications: doing
# so sends the candidate a second "application received" email at a late stage
# (e.g. right before a rejection), which reads as a duplicate confirmation.
# Progress stages that only had that mismatched template are intentionally
# omitted until they get correctly-worded templates of their own.
STAGE_NOTIFICATIONS: dict[str, dict] = {
    "screening_sent": {
        "template": "screening_reminder",
        "subject": "Next step: Complete your screening",
        "message_key": "screening_invite_received",
    },
    "assignment_sent": {
        "template": "nudge",
        "subject": "Your assignment is ready",
        "message_key": "assignment_available",
    },
}


async def notify_candidate_stage_change(
    application_id: UUID,
    new_stage: str,
) -> bool:
    """Send notification to candidate about their stage change.

    Returns True if notification was sent, False if skipped.
    """
    notification = STAGE_NOTIFICATIONS.get(new_stage)
    if notification is None:
        return False

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            return False
        cand = await session.get(Candidate, app.candidate_id)
        if cand is None or not cand.email:
            return False
        role = await session.get(Role, app.role_id) if app.role_id else None

        try:
            result = await send_email(
                to=cand.email,
                template=notification["template"],
                variables={
                    "candidate_name": cand.name or "there",
                    "role_title": role.title if role else "the position",
                    "reference_id": str(application_id),
                    "stage_message": notification.get("subject", ""),
                },
                tags={
                    "stage": "auto_notification",
                    "trigger": new_stage,
                },
            )

            await log_audit(
                session,
                action="candidate_stage_notification",
                actor="stage_notifier",
                candidate_id=cand.id,
                application_id=application_id,
                details={
                    "stage": new_stage,
                    "template": notification["template"],
                    "email_sent": result.success,
                    "provider": result.provider,
                },
            )
            return result.success

        except Exception:
            logger.exception(
                "stage notification failed for app=%s stage=%s",
                application_id, new_stage,
            )
            return False
