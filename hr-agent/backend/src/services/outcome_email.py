"""Send the final hire / reject email after HR finalizes a candidate."""

from __future__ import annotations

import logging
from uuid import UUID

from src.channels.email import send_email
from src.db.base import Application, Candidate, Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit

logger = logging.getLogger(__name__)


async def dispatch_outcome_email(
    *,
    application_id: UUID,
    outcome: str,
    notes: str | None = None,
) -> None:
    if outcome not in {"hired", "rejected"}:
        return

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            return
        candidate = await session.get(Candidate, app.candidate_id)
        role = await session.get(Role, app.role_id) if app.role_id else None

    if candidate is None or not candidate.email:
        return

    template = "offer" if outcome == "hired" else "rejection"
    body_html = (notes or "").strip()

    variables = {
        "candidate_name": candidate.name,
        "role_title": role.title if role else None,
        "application_id": str(application_id),
        "notes": notes,
        # rejection.html.j2 expects body_html
        "body_html": body_html
        or "Thanks for the time you invested in our process. We won't be moving forward with your application at this time.",
    }

    result = await send_email(
        to=candidate.email,
        template=template,
        variables=variables,
        tags={"kind": "outcome", "outcome": outcome},
    )
    async with session_scope() as session:
        await log_audit(
            session,
            application_id=application_id,
            candidate_id=candidate.id,
            action=f"outcome_email_{outcome}",
            actor="agent",
            details={
                "provider": result.provider,
                "success": result.success,
                "error": result.error,
            },
        )
