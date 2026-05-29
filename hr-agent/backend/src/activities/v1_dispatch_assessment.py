"""Email the candidate the role's static Predictive Index Cognitive link.

Pipeline position: triggered after ``voice_screen_evaluated:clear_pass``.

This activity is now a notification-only side effect. The PI link itself is
a static URL pasted onto the role at creation time -- there is no per-call
PI API integration and no completion webhook. Behaviour:

  * If ``role.pi_cognitive_url`` is set -> send the cognitive-assessment
    email and write a tracking row on ``assessment_results``.
  * If it is empty -> no-op (skip the courtesy step).

Either way the application is moved to ``ASSESSMENT_EVALUATED`` so the
existing auto-progress chain (technical meeting / assignment) continues
unchanged.
"""

from __future__ import annotations

import logging
from uuid import UUID

from src.channels.email import send_email
from src.config import get_settings
from src.db.base import Application, Candidate, Role
from src.db.connection import session_scope
from src.db.repositories.assessment_result import create_invite
from src.db.repositories.audit import log_audit
from src.db.repositories.v1_application import set_stage
from src.models.v1 import PipelineStage

logger = logging.getLogger(__name__)


async def dispatch_assessment(*, application_id: UUID) -> list[UUID]:
    """Send the role's assignment document + instructions to the candidate.

    The "assessment round" in our pipeline is the take-home assignment:
      1. Email candidate the problem-statement doc HR uploaded onto the role,
         plus HR's free-form instructions, plus a unique upload URL.
      2. Set stage to ASSIGNMENT_SENT and wait for submission via /apply/<token>.
      3. PI Cognitive link is sent alongside as a courtesy if the role has
         one configured -- it does not block the assignment flow.

    Returns the list of ``assessment_results`` row ids created (empty when
    no PI link is configured).
    """

    settings = get_settings()

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

        pi_url = (role.pi_cognitive_url or "").strip() or None
        # An assignment exists if HR provided EITHER body text or a problem doc.
        has_assignment = bool(role.assignment_brief or role.assignment_problem_doc_key)
        candidate_email = candidate.email
        candidate_name = candidate.name or "there"
        role_title = role.title
        candidate_id = candidate.id
        role_id = role.id

        created_ids: list[UUID] = []
        if pi_url:
            row = await create_invite(
                session,
                application_id=application_id,
                provider="pi_cognitive",
                assessment_kind="cognitive",
                external_assessment_id=f"static-{application_id}",
                invite_url=pi_url,
            )
            created_ids.append(row.id)
            await log_audit(
                session,
                candidate_id=candidate.id,
                application_id=application_id,
                action="pi_cognitive_link_sent",
                actor="agent",
                details={"assessment_id": str(row.id), "invite_url": pi_url},
            )

        # Stage routing depends on whether HR set up an assignment for this
        # role. With an assignment we move to ASSIGNMENT_SENT and wait for
        # submission. Without one we fall back to the PI-only courtesy flow.
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

    # Side-effect emails outside the DB session.
    if pi_url:
        try:
            await send_email(
                to=candidate_email,
                template="assessment_invite",
                variables={
                    "candidate_name": candidate_name,
                    "role_title": role_title,
                    "cognitive_link": pi_url,
                    "application_id": str(application_id),
                },
                tags={
                    "category": "assessment_invite",
                    "application_id": str(application_id),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("PI cognitive invite email failed: %s", exc)

    if has_assignment:
        # Send the actual problem statement + upload URL. Failure here is
        # surfaced -- without the doc landing in the candidate's inbox the
        # round cannot proceed.
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
        # No assignment configured -- continue auto-progressing as before.
        from src.services.auto_progress import auto_progress

        await auto_progress(application_id=application_id)
    return created_ids
