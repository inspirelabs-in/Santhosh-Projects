"""Email admin/recruiter when an agentic round completes and needs review."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from src.channels.email import send_email
from src.config import get_settings
from src.db.base import Application, Candidate
from src.db.connection import session_scope

logger = logging.getLogger(__name__)


_ROUND_LABEL = {
    "assessment": "Assessment",
    "technical": "Technical interview",
    "ceo": "CEO interview",
    "hr": "HR Discussion",
}


async def notify_round_complete(
    *,
    application_id: UUID,
    round: str,
    analysis: Any | None = None,
) -> None:
    settings = get_settings()
    label = _ROUND_LABEL.get(round, round.title())

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            return
        candidate = await session.get(Candidate, app.candidate_id)
        candidate_name = candidate.full_name if candidate else "Candidate"

    review_url = (
        f"{settings.frontend_base_url.rstrip('/')}/candidates/{application_id}"
    )

    overall_score = getattr(analysis, "overall_score", None)
    verdict = getattr(analysis, "verdict", None)
    summary = getattr(analysis, "summary", None)

    await send_email(
        to=settings.admin_notify_email,
        template="admin_review_required",
        variables={
            "candidate_name": candidate_name,
            "round_label": label,
            "application_id": str(application_id),
            "review_url": review_url,
            "overall_score": overall_score,
            "verdict": verdict,
            "summary": summary,
        },
        tags={"kind": "admin_review", "round": round},
    )
