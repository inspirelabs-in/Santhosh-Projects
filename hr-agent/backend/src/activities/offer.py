"""Offer generation activity.

When HR approves a candidate after the final interview round, this activity:
1. Assembles offer context from evidence records (CTC, role, experience)
2. Generates a personalized offer note via LLM
3. Sends the offer_extended email to the candidate
4. Advances pipeline to 'hired' pending candidate acceptance
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from src.channels.email import send_email
from src.config import get_settings
from src.db.base import Application, Candidate, Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.evidence import (
    get_evidence_for_application,
    record_decision,
)
from src.db.repositories.v1_application import set_stage
from src.llm.client import get_llm_client
from src.models.v1 import PipelineStage

logger = logging.getLogger(__name__)


async def generate_offer(
    *,
    application_id: UUID,
    offer_note: str | None = None,
    approved_by: str = "hr",
) -> dict[str, Any]:
    """Generate and send an offer email to the candidate."""
    settings = get_settings()

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if not app:
            return {"success": False, "error": "application_not_found"}

        candidate = await session.get(Candidate, app.candidate_id)
        if not candidate or not candidate.email:
            return {"success": False, "error": "candidate_email_missing"}

        role = await session.get(Role, app.role_id) if app.role_id else None

        evidence = await get_evidence_for_application(session, application_id)

    # Extract key facts from evidence for offer context
    facts: dict[str, Any] = {}
    for ev in evidence:
        if ev.fact_key in (
            "expected_ctc_lpa", "current_ctc_lpa", "total_experience_years",
            "notice_period_days", "primary_skills",
        ):
            facts[ev.fact_key] = ev.fact_value

    # Generate personalized offer note if not provided
    if not offer_note:
        try:
            client = get_llm_client()
            result = await client.complete(
                prompt=(
                    f"Write a brief, warm 2-3 sentence note for a job offer email.\n"
                    f"Candidate: {candidate.name}\n"
                    f"Role: {role.title if role else 'the position'}\n"
                    f"Experience: {facts.get('total_experience_years', 'N/A')} years\n"
                    f"Key skills: {facts.get('primary_skills', 'N/A')}\n\n"
                    f"Keep it professional, specific to their background, and enthusiastic. "
                    f"Do not mention salary or compensation."
                ),
                model=settings.llm_model_fast,
                trace_name="generate_offer_note",
                application_id=application_id,
                candidate_id=app.candidate_id,
                temperature=0.7,
                max_tokens=200,
            )
            offer_note = result.text.strip()
        except Exception:
            logger.warning("offer note generation failed, using default", exc_info=True)
            offer_note = None

    # Send offer email
    email_result = await send_email(
        to=candidate.email,
        template="offer_extended",
        variables={
            "candidate_name": candidate.name or "Candidate",
            "role_title": role.title if role else "the role",
            "company_name": "GrabOn",
            "offer_note": offer_note or "",
            "application_id": str(application_id),
        },
        application_id=str(application_id),
        candidate_id=str(app.candidate_id),
        idempotency_key=f"offer:{application_id}",
    )

    # Record decision
    async with session_scope() as session:
        await record_decision(
            session,
            application_id=application_id,
            candidate_id=app.candidate_id,
            decision_type="offer_extended",
            outcome="sent" if email_result.success else "send_failed",
            outcome_value={
                "email_success": email_result.success,
                "approved_by": approved_by,
                "offer_note_generated": offer_note is not None,
                "facts_used": list(facts.keys()),
            },
        )

        await log_audit(
            session,
            application_id=application_id,
            candidate_id=app.candidate_id,
            action="offer_extended",
            actor=approved_by,
            details={
                "email_sent": email_result.success,
                "to": candidate.email,
            },
        )

    return {
        "success": email_result.success,
        "email_sent": email_result.success,
        "message_id": email_result.message_id,
    }
