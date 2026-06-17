"""Dispatch the take-home assignment to the candidate.

Pipeline position: triggered after ``voice_screen_evaluated:clear_pass``.

If the role has an assignment (brief text or uploaded problem doc), sends
it to the candidate and sets stage to ASSIGNMENT_SENT. Otherwise skips
to ASSESSMENT_EVALUATED so auto-progress continues.
"""

from __future__ import annotations

import logging
from uuid import UUID

from src.config import get_settings
from src.db.base import Application, Candidate, Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.v1_application import set_stage
from src.models.v1 import PipelineStage

logger = logging.getLogger(__name__)


async def dispatch_assessment(*, application_id: UUID) -> None:
    """Send the role's take-home assignment to the candidate.

    If the role has an assignment (brief text or uploaded problem doc),
    emails it and sets stage to ASSIGNMENT_SENT. Otherwise skips to
    ASSESSMENT_EVALUATED so auto-progress continues.
    """

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise ValueError(f"application {application_id} not found")
        candidate = await session.get(Candidate, app.candidate_id)
        if candidate is None or not candidate.email:
            raise ValueError("candidate email missing")
        role = await session.get(Role, app.role_id) if app.role_id else None
        if role is None:
            raise ValueError("role missing")

        has_assignment = bool(role.assignment_brief or role.assignment_problem_doc_key)
        candidate_id = candidate.id
        role_id = role.id

        if has_assignment:
            await set_stage(
                session, application_id, PipelineStage.ASSIGNMENT_SENT, force=True
            )
        else:
            await set_stage(
                session, application_id, PipelineStage.ASSESSMENT_EVALUATED, force=True
            )
            await log_audit(
                session,
                candidate_id=candidate.id,
                application_id=application_id,
                action="assignment_dispatch_skipped",
                actor="agent",
                details={"reason": "role.assignment_brief not set; no take-home"},
            )

    if has_assignment:
        from src.activities.v1_send_assignment import send_assignment_email

        try:
            await send_assignment_email(
                application_id=application_id,
                candidate_id=candidate_id,
                role_id=role_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("assignment email dispatch failed: %s", exc)
            raise
    else:
        from src.services.auto_progress import auto_progress

        await auto_progress(application_id=application_id)
