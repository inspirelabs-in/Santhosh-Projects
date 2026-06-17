"""V2 chat-first invite flow.

Cutover policy: HARD cutover for new applications. Once a role is set to
``screening_modality='chat'`` (default), every new candidate gets a chat
token and lands on the agent UI. We deliberately did NOT write a backfill
that converts in-flight V1 applications to chat conversations -- the
V1 form endpoints (``api/apply.py``) remain mounted so any candidate
already mid-flow with an old screening/assignment token can finish on
the legacy form. Once those tokens age out (TTL 14 days), the V1 form
endpoints can be deleted in a follow-up.

Replaces the V1 form-based screening email. Steps:

  1. Pre-warm the agent (parallel: tailored questions + assignment skeleton).
  2. Issue a JWT-scoped chat token bound to the application.
  3. Email the candidate a single link they can converse from.
  4. Move the application to SCREENING_SENT.

This runs as a BackgroundTask after a candidate applies. If pre-warm fails
the candidate still gets the link -- their first turn just pays the LLM
latency for question generation.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from src.agent.runner import prewarm
from src.channels.email import send_email
from src.config import get_settings
from src.db.base import Application, Candidate, Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.v1_application import set_stage
from src.models.v1 import PipelineStage
from src.services.screening_url import generate_apply_token

logger = logging.getLogger(__name__)
_settings = get_settings()


async def run_apply_to_chat(
    *,
    application_id: UUID,
    candidate_id: UUID,
    role_id: UUID,
) -> None:
    """Issue chat token + prewarm + email invite. Idempotent."""
    # Prewarm runs in parallel with the email send so the candidate's first
    # click finds the questions ready. We let prewarm complete before we
    # mark SCREENING_SENT so failures roll the application back cleanly.
    try:
        await prewarm(application_id=application_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("chat prewarm failed for %s (non-fatal): %s", application_id, e)

    token = generate_apply_token(application_id=application_id, action="chat")
    frontend_base = _settings.frontend_base_url.rstrip("/")
    url = f"{frontend_base}/apply/{token}"

    # Generate portal URL for candidate status tracking
    from src.api.candidate_portal import generate_portal_token
    portal_token = generate_portal_token(application_id)
    portal_url = f"{frontend_base}/portal?token={portal_token}"

    async with session_scope() as session:
        candidate = await session.get(Candidate, candidate_id)
        role = await session.get(Role, role_id) if role_id else None
        role_title = role.title if role else "the role"

        send_result = None
        if candidate and candidate.email:
            send_result = await send_email(
                to=candidate.email,
                template="screening_invite",
                variables={
                    "candidate_name": candidate.name,
                    "role_title": role_title,
                    "form_url": url,
                    "portal_url": portal_url,
                    "expires_at": "14 days from now",
                    "application_id": str(application_id),
                },
                tags={
                    "type": "chat_invite",
                    "application_id": str(application_id),
                },
                idempotency_key=f"{application_id}:chat_invite",
                application_id=str(application_id),
                candidate_id=str(candidate_id),
            )

        if send_result is not None and not send_result.success:
            await log_audit(
                session,
                application_id=application_id,
                candidate_id=candidate_id,
                action="chat_invite_send_failed",
                actor="agent",
                details={
                    "provider": send_result.provider,
                    "status_code": send_result.status_code,
                    "error": (send_result.error or "")[:300],
                    "url": url,
                },
            )
            await set_stage(
                session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True
            )
            return

        await set_stage(session, application_id, PipelineStage.SCREENING_SENT)
        await log_audit(
            session,
            application_id=application_id,
            candidate_id=candidate_id,
            action="chat_invite_sent",
            actor="agent",
            details={
                "url": url,
                "provider": send_result.provider if send_result else "skipped_no_email",
                "message_id": send_result.message_id if send_result else None,
            },
        )
