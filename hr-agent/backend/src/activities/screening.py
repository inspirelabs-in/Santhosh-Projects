"""Stage 5: Screening invitation + reminders (multi-channel).

One activity per send event so Temporal can retry each independently. The
sub-workflow in src/workflows/screening.py decides *when* each fires.

Channel matrix (references/pipeline-stages.md):
  - Invite         → email + WhatsApp
  - Day 3 reminder → email + WhatsApp
  - Day 5 final    → email + WhatsApp + SMS
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from temporalio import activity

from src.channels.email import send_email
from src.channels.sms import send_sms
from src.channels.whatsapp import send_template as send_whatsapp_template
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.candidate import get_application, update_application_status
from src.db.repositories.candidate import get_candidate
from src.db.repositories.role import get_role
from src.models.candidate import ApplicationStatus, CandidateStatus
from src.services.dedup import is_valid_indian_mobile
from src.services.screening_url import generate_screening_url

logger = logging.getLogger(__name__)


@dataclass
class ScreeningSendInput:
    candidate_id: UUID
    application_id: UUID
    kind: str  # "invite" | "reminder" | "final_reminder"


@dataclass
class ScreeningSendResult:
    sent_email: bool
    sent_whatsapp: bool
    sent_sms: bool
    form_url: str | None
    failures: list[str] = field(default_factory=list)


_WHATSAPP_TEMPLATES = {
    "invite": "screening_invite_v1",
    "reminder": "screening_reminder_v1",
    "final_reminder": "screening_reminder_v1",
}


async def run_send_screening(payload: ScreeningSendInput) -> ScreeningSendResult:
    async with session_scope() as session:
        app = await get_application(session, payload.application_id)
        if app is None or app.role_id is None:
            raise ValueError("application missing or has no role")
        candidate = await get_candidate(session, payload.candidate_id)
        if candidate is None:
            raise ValueError("candidate not found")
        role = await get_role(session, app.role_id)
        if role is None:
            raise ValueError("role not found")

        snapshot = {
            "candidate_email": candidate.email,
            "candidate_name": candidate.name,
            "candidate_phone": candidate.phone,
            "role_title": role.title,
            "role_id": role.id,
            "app_status": app.status,
        }

    form_url, expires_at = generate_screening_url(
        application_id=payload.application_id,
        role_id=snapshot["role_id"],
        candidate_id=payload.candidate_id,
    )
    # Extract the token suffix for WhatsApp button param.
    token = form_url.rsplit("/", 1)[-1]

    email_template = {
        "invite": "screening_invite",
        "reminder": "screening_reminder",
        "final_reminder": "screening_reminder",
    }[payload.kind]

    failures: list[str] = []
    sent_email = sent_whatsapp = sent_sms = False

    # Email -- always.
    if snapshot["candidate_email"]:
        er = await send_email(
            to=snapshot["candidate_email"],
            template=email_template,
            variables={
                "candidate_name": snapshot["candidate_name"],
                "role_title": snapshot["role_title"],
                "form_url": form_url,
                "application_id": str(payload.application_id),
                "expires_at": expires_at.strftime("%d %b %Y, %H:%M UTC"),
                "is_final": payload.kind == "final_reminder",
            },
            tags={"stage": "screening", "kind": payload.kind},
        )
        sent_email = er.success
        if not er.success:
            failures.append(f"email:{er.error or er.status_code}")
    else:
        failures.append("email:no_candidate_email")

    # WhatsApp -- if phone valid.
    if snapshot["candidate_phone"] and is_valid_indian_mobile(snapshot["candidate_phone"]):
        wr = await send_whatsapp_template(
            to_phone=snapshot["candidate_phone"],
            template_name=_WHATSAPP_TEMPLATES[payload.kind],
            body_params=[snapshot["candidate_name"] or "there", snapshot["role_title"]],
            button_url_param=token,
        )
        sent_whatsapp = wr.success
        if not wr.success:
            failures.append(f"whatsapp:{wr.error or wr.status_code}")

    # SMS -- only for the final reminder.
    if payload.kind == "final_reminder":
        if snapshot["candidate_phone"] and is_valid_indian_mobile(snapshot["candidate_phone"]):
            sr = await send_sms(
                to_phone=snapshot["candidate_phone"],
                variables={
                    "name": (snapshot["candidate_name"] or "Candidate")[:30],
                    "role": snapshot["role_title"][:30],
                    "url": form_url,
                },
            )
            sent_sms = sr.success
            if not sr.success:
                failures.append(f"sms:{sr.error or sr.status_code}")

    # Audit + status update in one transaction.
    async with session_scope() as session:
        if payload.kind == "invite":
            candidate = await get_candidate(session, payload.candidate_id)
            if candidate is not None:
                candidate.status = CandidateStatus.SCREENING_SENT.value
            await update_application_status(
                session,
                payload.application_id,
                ApplicationStatus.SCREENING_IN_PROGRESS,
            )

        await log_audit(
            session,
            action=f"screening_{payload.kind}_sent",
            actor="agent",
            candidate_id=payload.candidate_id,
            application_id=payload.application_id,
            details={
                "kind": payload.kind,
                "email": sent_email,
                "whatsapp": sent_whatsapp,
                "sms": sent_sms,
                "failures": failures,
                "form_url_expires_at": expires_at.isoformat(),
                "sent_at": datetime.now(tz=UTC).isoformat(),
            },
        )

    return ScreeningSendResult(
        sent_email=sent_email,
        sent_whatsapp=sent_whatsapp,
        sent_sms=sent_sms,
        form_url=form_url,
        failures=failures,
    )


# ---------------------------------------------------------------------------
# Cold-pool park (Day 10 no-response)
# ---------------------------------------------------------------------------


@dataclass
class ParkColdPoolInput:
    candidate_id: UUID
    application_id: UUID
    reason: str = "no_screening_response_10d"


@activity.defn(name="park_cold_pool")
async def park_cold_pool_activity(payload: ParkColdPoolInput) -> None:
    async with session_scope() as session:
        candidate = await get_candidate(session, payload.candidate_id)
        if candidate is not None:
            candidate.status = CandidateStatus.COLD.value
        await update_application_status(
            session, payload.application_id, ApplicationStatus.COLD
        )
        await log_audit(
            session,
            action="parked_cold_pool",
            actor="agent",
            candidate_id=payload.candidate_id,
            application_id=payload.application_id,
            details={"reason": payload.reason},
        )


@activity.defn(name="send_screening")
async def send_screening_activity(payload: ScreeningSendInput) -> ScreeningSendResult:
    return await run_send_screening(payload)
