"""V1 activity: send assignment email to a clear-pass candidate.

Uses role.assignment_brief / assignment_instructions / assignment_deadline_days
set by HR at role creation. The problem-statement doc (if HR uploaded one) is
attached to the email -- no presigned links.
"""

from __future__ import annotations

import logging
import mimetypes
from datetime import UTC, datetime, timedelta
from uuid import UUID

from src.channels.email import EmailAttachment, send_email
from src.config import get_settings
from src.db.base import Candidate, Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.services.file_storage import download
from src.services.screening_url import generate_apply_token

logger = logging.getLogger(__name__)
_settings = get_settings()


async def send_assignment_email(
    *,
    application_id: UUID,
    candidate_id: UUID,
    role_id: UUID,
) -> dict:
    async with session_scope() as session:
        role = await session.get(Role, role_id)
        candidate = await session.get(Candidate, candidate_id)
        if role is None or candidate is None:
            raise ValueError("role or candidate not found")
        if not role.assignment_brief and not role.assignment_problem_doc_key:
            raise ValueError(
                f"role {role_id} has no assignment_brief AND no problem doc; "
                "HR must set at least one"
            )
        # Default brief text when only the doc was uploaded (common case --
        # HR attaches a PDF and skips the brief textarea).
        effective_brief = (
            role.assignment_brief
            or "Please find the attached problem statement. Submit your work via the link below."
        )
        if candidate.email is None:
            raise ValueError(f"candidate {candidate_id} has no email")

        deadline_days = role.assignment_deadline_days or 7
        deadline_at = datetime.now(UTC) + timedelta(days=deadline_days)

        token = generate_apply_token(
            application_id=application_id,
            action="assignment",
            ttl_days=deadline_days,
        )
        upload_url = f"{_settings.frontend_base_url.rstrip('/')}/apply/{token}"

        attachments: list[EmailAttachment] = []
        attached_filename: str | None = None
        attachment_error: str | None = None
        attached_size: int | None = None
        if role.assignment_problem_doc_key:
            try:
                blob = await download(
                    _settings.r2_bucket_resumes, role.assignment_problem_doc_key
                )
                fname = role.assignment_problem_filename or "problem-statement"
                ctype, _ = mimetypes.guess_type(fname)
                attachments.append(
                    EmailAttachment(
                        filename=fname,
                        content=blob,
                        content_type=ctype or "application/octet-stream",
                    )
                )
                attached_filename = fname
                attached_size = len(blob)
                logger.info(
                    "attaching problem doc %s (%d bytes, %s) to assignment email",
                    fname, attached_size, ctype or "octet-stream",
                )
            except Exception as e:  # noqa: BLE001
                attachment_error = str(e)[:200]
                logger.warning("failed to attach problem doc: %s", e)
        else:
            attachment_error = "role has no assignment_problem_doc_key; HR must upload one"
            logger.warning(
                "role %s has no problem doc; assignment email going out without attachment",
                role_id,
            )

        result = await send_email(
            to=candidate.email,
            template="assignment_invite",
            variables={
                "candidate_name": candidate.name,
                "role_title": role.title,
                "assignment_brief": effective_brief,
                "assignment_instructions": role.assignment_instructions or "",
                "deadline_days": deadline_days,
                "deadline_at": deadline_at.strftime("%Y-%m-%d"),
                "upload_url": upload_url,
                "problem_doc_filename": attached_filename,
                "application_id": str(application_id),
            },
            tags={"type": "assignment_invite", "application_id": str(application_id)},
            attachments=attachments,
        )

        await log_audit(
            session,
            candidate_id=candidate_id,
            application_id=application_id,
            action="assignment_sent",
            actor="agent",
            details={
                "email_provider": result.provider,
                "message_id": result.message_id,
                "deadline_at": deadline_at.isoformat(),
                "success": result.success,
                "problem_doc_attached": attached_filename,
                "problem_doc_bytes": attached_size,
                "attachment_error": attachment_error,
            },
        )
        return {
            "success": result.success,
            "deadline_at": deadline_at.isoformat(),
            "upload_url": upload_url,
        }
