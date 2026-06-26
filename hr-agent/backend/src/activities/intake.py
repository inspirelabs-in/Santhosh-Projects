"""Stage 1: Intake.

Idempotent, auditable entrypoint for every candidate in the system. Runs as
a Temporal activity (when called from the workflow) *and* is safe to call
directly from the webhook handler for the synchronous "create + ack"
response path. The webhook normally just kicks off a workflow which then
invokes this activity.

Responsibilities:
  1. Dedupe on email / phone / LinkedIn.
  2. Upsert candidate + create application row.
  3. Persist uploaded resume files to R2 (if any raw content provided).
  4. Write a `ConsentArtifact` with the privacy notice version shown.
  5. Send the auto-acknowledgement email via Resend (within 30s SLA).
  6. Append an `audit_log` entry.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from uuid import UUID

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)

from temporalio import activity

from src.channels.email import send_email
from src.db.connection import session_scope
from sqlalchemy import select

from src.db.base import Application
from src.db.repositories.audit import log_audit
from src.db.repositories.candidate import (
    create_application,
    upsert_candidate,
)
from src.models.candidate import (
    ApplicationStatus,
    IntakePayload,
    IntakeResult,
)
from src.services.consent import (
    PRIVACY_NOTICE_TEXT_V1,
    PRIVACY_NOTICE_VERSION,
    capture_consent,
)
from src.services.dedup import find_duplicate_candidate
from src.services.file_storage import upload_resume

logger = logging.getLogger(__name__)


async def run_intake(payload: IntakePayload) -> IntakeResult:
    """Execute intake end-to-end. Idempotent via dedup."""
    was_duplicate = False

    async with session_scope() as session:
        # 1. Dedup
        existing_id = await find_duplicate_candidate(
            session,
            email=payload.sender_email,
            phone=payload.sender_phone,
            linkedin_url=None,  # candidate hasn't given LinkedIn yet; resolved in Stage 3
            name=payload.sender_name,
            city=None,
        )
        # Only treat as duplicate if candidate exists AND has an application for
        # the same role.  The same person applying for a different role is NOT a
        # duplicate — they still get consent capture + acknowledgement email.
        same_role_dup = False
        if existing_id is not None and payload.role_id_hint is not None:
            existing = await session.scalar(
                select(Application).where(
                    Application.candidate_id == existing_id,
                    Application.role_id == payload.role_id_hint,
                )
            )
            same_role_dup = existing is not None
        was_duplicate = same_role_dup

        # 2. Upsert candidate
        candidate = await upsert_candidate(
            session,
            email=payload.sender_email,
            name=payload.sender_name,
            phone=payload.sender_phone,
            linkedin_url=None,
            source_channel=payload.source_channel,
            existing_id=existing_id,
        )

        # 3. Create application
        application = await create_application(
            session,
            candidate_id=candidate.id,
            role_id=payload.role_id_hint,
            status=ApplicationStatus.ACTIVE,
        )

        # 4. Store raw attachments to R2 (if bytes were included in raw_payload)
        stored_keys: list[str] = list(payload.attachment_keys)
        raw_files = payload.raw_payload.get("_files", []) if payload.raw_payload else []
        for entry in raw_files:
            # entry: {"filename": str, "content_b64": str, "content_type": str | None}
            try:
                import base64

                content = base64.b64decode(entry["content_b64"])
            except Exception as e:  # noqa: BLE001 -- surface parse errors
                logger.warning("Skipping malformed attachment: %s", e)
                continue
            stored = await upload_resume(
                candidate_id=candidate.id,
                filename=entry.get("filename", "resume.bin"),
                content=content,
                content_type=entry.get("content_type"),
            )
            stored_keys.append(stored.key)

        # 5. Consent artifact
        if not was_duplicate:
            await capture_consent(
                session,
                candidate_id=candidate.id,
                consent_text=payload.consent_text_shown or PRIVACY_NOTICE_TEXT_V1,
                ip_address=payload.consent_ip_address,
            )
            candidate.consent_captured_at = datetime.now(tz=UTC)
            candidate.consent_text_shown = payload.consent_text_shown or PRIVACY_NOTICE_TEXT_V1

        # Extract URLs from body so HR can see candidate-shared links.
        body_preview = (payload.body_text or "")[:4000]
        body_links = list(dict.fromkeys(_URL_RE.findall(payload.body_text or "")))[:30]

        # 6. Audit -- record the intake event itself (no-ack yet, done below)
        await log_audit(
            session,
            action="intake_completed",
            actor="agent",
            candidate_id=candidate.id,
            application_id=application.id,
            details={
                "source": payload.source_channel.value,
                "was_duplicate": was_duplicate,
                "body_preview": body_preview,
                "body_links": body_links,
                "forwarder_email": payload.raw_payload.get("forwarder_email") if payload.raw_payload else None,
                "forwarder_name": payload.raw_payload.get("forwarder_name") if payload.raw_payload else None,
                "files_stored": stored_keys,
                "role_id_hint": str(payload.role_id_hint) if payload.role_id_hint else None,
                "consent_version": PRIVACY_NOTICE_VERSION,
                # Email-specific fields (present only when ingested from inbound mail).
                "mail_source": payload.raw_payload.get("mail_source") if payload.raw_payload else None,
                "subject": payload.subject or ((payload.raw_payload or {}).get("subject")),
                "received_at": payload.raw_payload.get("received_at") if payload.raw_payload else None,
                "message_id": payload.raw_payload.get("message_id") if payload.raw_payload else None,
            },
        )

        candidate_id = candidate.id
        application_id = application.id
        candidate_email = candidate.email
        candidate_name = candidate.name

    # 7. Auto-acknowledgement (outside the transaction so an email failure
    #    doesn't roll back the intake). Re-open a session purely for auditing.
    # For referral emails (HR forwards a candidate's resume) msg.from_email is
    # HR's address, not the candidate's. We skip the ack here; run_apply_to_screening
    # sends it after parse_resume extracts the real candidate email.
    forwarder_email = (
        payload.raw_payload.get("forwarder_email") if payload.raw_payload else None
    )
    forwarder_name = (
        payload.raw_payload.get("forwarder_name") if payload.raw_payload else None
    )
    is_referral = bool(
        (payload.raw_payload or {}).get("is_referral")
    )
    ack_to = None if is_referral else (candidate_email or forwarder_email)
    ack_sent = False
    if ack_to and not was_duplicate:
        role_title = payload.raw_payload.get("role_title") or "the role you applied for"
        result = await send_email(
            to=ack_to,
            template="acknowledgement",
            variables={
                "candidate_name": candidate_name or forwarder_name or "Applicant",
                "role_title": role_title,
                "reference_id": str(application_id),
                "received_at": datetime.now(tz=UTC).strftime("%d %b %Y, %H:%M UTC"),
            },
            tags={"stage": "intake", "candidate_id": str(candidate_id)},
        )
        ack_sent = result.success

        async with session_scope() as session:
            await log_audit(
                session,
                action="acknowledgement_sent" if ack_sent else "acknowledgement_failed",
                actor="agent",
                candidate_id=candidate_id,
                application_id=application_id,
                details={
                    "to": ack_to,
                    "candidate_email": candidate_email,
                    "forwarder_email": forwarder_email,
                    "message_id": result.message_id,
                    "status_code": result.status_code,
                    "error": result.error,
                },
            )

    return IntakeResult(
        candidate_id=candidate_id,
        application_id=application_id,
        acknowledgement_sent=ack_sent,
        consent_captured=not was_duplicate,
        files_stored=stored_keys,
        was_duplicate=was_duplicate,
    )


# ---------------------------------------------------------------------------
# Temporal wrapper
# ---------------------------------------------------------------------------


@activity.defn(name="intake")
async def intake_activity(payload: IntakePayload) -> IntakeResult:
    return await run_intake(payload)
