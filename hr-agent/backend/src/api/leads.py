"""Public landing-page lead capture.

A visitor on the marketing site drops their email (and an optional note); we
forward it to ``LEADS_EMAIL`` via the normal mail chain. Unauthenticated by
design -- it's a public contact form -- so it validates input tightly and does
nothing when ``LEADS_EMAIL`` is unset (the site hides the form in that case).
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from src.channels.email import send_email
from src.config import get_settings

router = APIRouter(prefix="/leads", tags=["leads"])
logger = logging.getLogger(__name__)

# Deliberately loose but sane; the real check is that a human typed an address.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LeadIn(BaseModel):
    email: str
    message: str | None = None


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def submit_lead(body: LeadIn) -> dict[str, bool]:
    settings = get_settings()

    email = (body.email or "").strip()
    if not _EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(status_code=422, detail="invalid_email")

    to = settings.leads_email
    if not to:
        # Not configured -- the client hides the form, but guard anyway.
        raise HTTPException(status_code=503, detail="leads_not_configured")

    result = await send_email(
        to=to,
        template="lead_notification",
        variables={
            "lead_email": email,
            "lead_message": (body.message or "").strip()[:2000],
        },
        reply_to=email,
        tags={"type": "lead"},
    )
    if not result.success:
        logger.warning("lead email send failed for %s: %s", email, result.error)
        raise HTTPException(status_code=502, detail="send_failed")

    return {"ok": True}
