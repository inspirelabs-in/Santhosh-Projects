"""V1 mail ingestion bridge: InboundMessage -> IntakePayload -> V1 pipeline.

Runs as an asyncio task spawned from FastAPI lifespan. Polls every inbox
every `mail_poll_interval_seconds`. Deduplicates via `processed_messages`
table so restarts don't double-process.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from uuid import UUID

from sqlalchemy import select

from src.activities.intake import run_intake
from src.config import get_settings
from src.db.base import ProcessedMessage, Role
from src.db.connection import session_scope
from src.db.repositories.role import list_open_roles
from src.models.candidate import IntakePayload, SourceChannel
from src.pipeline.v1 import run_apply_to_screening
from src.services.imap_inbox import (
    InboundMessage,
    InboxConfig,
    baseline_inbox,
    fetch_new,
    load_inboxes_from_env,
)

# Only mails whose subject starts with this prefix (case-insensitive) are
# treated as job applications. Everything else is left read but ignored.
SUBJECT_REQUIRED_PREFIX = "application"

logger = logging.getLogger(__name__)
_settings = get_settings()


async def _already_processed(message_id: str) -> bool:
    async with session_scope() as session:
        existing = await session.get(ProcessedMessage, message_id)
        return existing is not None


async def _mark_processed(message_id: str, application_id: UUID | None, source: str) -> None:
    async with session_scope() as session:
        row = ProcessedMessage(
            message_id=message_id,
            application_id=application_id,
            mail_source=source,
        )
        session.add(row)


async def _match_role(subject: str | None, body: str | None) -> UUID | None:
    """Match an open role to an inbound mail by title.

    Strategy:
      1. Lowercase + tokenize subject and body (alpha-numeric runs).
      2. For each open role, check whether ALL non-trivial title tokens
         appear in the haystack as whole words (word-boundary match).
      3. When multiple roles match, return the role with the LONGEST title
         (most specific). This means "Senior Data Engineer" wins over a
         bare "Engineer" role on a mail subject containing the full phrase.
    """
    if not subject and not body:
        return None
    import re as _re

    haystack = f"{subject or ''} {body or ''}".lower()
    haystack_tokens = set(_re.findall(r"[a-z0-9]+", haystack))
    # Stop-tokens that match almost any mail and produce false positives.
    _STOP = {"engineer", "developer", "manager", "lead", "junior", "senior",
             "analyst", "the", "for", "role", "position", "job", "a", "an"}

    async with session_scope() as session:
        roles = await list_open_roles(session)
        candidates: list[tuple[int, UUID]] = []
        for role in roles:
            title = (role.title or "").strip().lower()
            if not title:
                continue
            title_tokens = [t for t in _re.findall(r"[a-z0-9]+", title)]
            if not title_tokens:
                continue
            significant = [t for t in title_tokens if t not in _STOP]
            # If the title is entirely stop-tokens (rare), fall back to all
            # title tokens but require ALL to be present.
            required = significant or title_tokens
            if all(t in haystack_tokens for t in required):
                # Score = number of title tokens that matched (longer titles win).
                candidates.append((len(title_tokens), role.id))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]


def _is_resume_attachment(filename: str | None, content_type: str | None) -> bool:
    fn = (filename or "").lower()
    if fn.endswith((".pdf", ".doc", ".docx", ".rtf", ".txt")):
        return True
    ct = (content_type or "").lower()
    if ct in (
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/rtf",
        "text/plain",
    ):
        return True
    return False


async def _ingest_one_resume(
    msg: InboundMessage,
    *,
    resume_bytes: bytes | None,
    resume_filename: str | None,
    resume_ct: str | None,
    index: int,
    role_id: UUID | None,
) -> None:
    """Create ONE fresh candidate + application for one resume.

    Identity (name, email, phone) is intentionally left null at intake; the
    parse_resume activity backfills from the resume text. This ensures two
    resumes forwarded from the same inbox address become two independent
    candidates.
    """
    files = []
    if resume_bytes and resume_filename:
        files.append(
            {
                "filename": resume_filename,
                "content_b64": base64.b64encode(resume_bytes).decode("ascii"),
                "content_type": resume_ct,
            }
        )

    # Pass the forwarder email/name to intake. The resume parser ultimately
    # overwrites identity from the resume (resume wins), but giving intake a
    # non-null email lets ``find_duplicate_candidate`` actually deduplicate
    # repeat forwards from the same address before parse runs. Without this
    # every forward creates a fresh candidate row.
    payload = IntakePayload(
        source_channel=SourceChannel.EMAIL,
        sender_email=msg.from_email,
        sender_name=msg.from_name,
        sender_phone=None,
        subject=msg.subject,
        body_text=msg.body_text,
        role_id_hint=role_id,
        consent_text_shown=(
            "Consent by sending your resume to the careers inbox, per the "
            "privacy notice at grabon.in/careers."
        ),
        consent_ip_address=None,
        raw_payload={
            "_files": files,
            "mail_source": msg.source,
            "message_id": msg.message_id,
            "inbox_label": msg.inbox_label,
            "received_at": msg.received_at,
            "forwarder_email": msg.from_email,
            "forwarder_name": msg.from_name,
            "attachment_index": index,
        },
    )

    try:
        result = await run_intake(payload)
    except Exception:
        logger.exception(
            "mail intake failed for %s (attachment #%d)", msg.message_id, index
        )
        return

    resume_key = result.files_stored[0] if result.files_stored else None

    def _on_pipeline_done(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error(
                "pipeline task failed for application %s: %s",
                result.application_id, exc, exc_info=exc,
            )

    pipeline_task = asyncio.create_task(
        run_apply_to_screening(
            application_id=result.application_id,
            candidate_id=result.candidate_id,
            role_id=role_id,
            resume_r2_key=resume_key,
            resume_filename=resume_filename,
        )
    )
    pipeline_task.add_done_callback(_on_pipeline_done)


async def _process_one(msg: InboundMessage) -> None:
    if not msg.message_id:
        return
    if await _already_processed(msg.message_id):
        return
    if not msg.from_email:
        logger.info("skip mail %s: no From email", msg.message_id)
        await _mark_processed(msg.message_id, None, f"{msg.source}:skipped_no_from")
        return
    subject = (msg.subject or "").strip().lower()
    if not subject.startswith(SUBJECT_REQUIRED_PREFIX):
        logger.info(
            "skip mail %s: subject does not start with %r (got %r)",
            msg.message_id, SUBJECT_REQUIRED_PREFIX, msg.subject,
        )
        await _mark_processed(
            msg.message_id, None, f"{msg.source}:skipped_subject_mismatch"
        )
        return

    role_id = await _match_role(msg.subject, msg.body_text)

    resume_attachments = [
        a for a in msg.attachments
        if a.content and _is_resume_attachment(a.filename, a.content_type)
    ]

    if not resume_attachments:
        # No resume attached — treat the mail body as the application.
        logger.info(
            "mail %s has no resume attachment; creating single application from body",
            msg.message_id,
        )
        await _ingest_one_resume(
            msg,
            resume_bytes=None,
            resume_filename=None,
            resume_ct=None,
            index=0,
            role_id=role_id,
        )
    else:
        logger.info(
            "mail %s has %d resume attachment(s); creating one application per resume",
            msg.message_id, len(resume_attachments),
        )
        for i, att in enumerate(resume_attachments):
            await _ingest_one_resume(
                msg,
                resume_bytes=att.content,
                resume_filename=att.filename,
                resume_ct=att.content_type,
                index=i,
                role_id=role_id,
            )

    # Mark the whole message processed once all its resumes are dispatched.
    # application_id=None is fine — the PK is message_id for dedup.
    await _mark_processed(msg.message_id, None, msg.source)


# Shared state so the /healthz endpoint can surface poller status.
POLLER_STATUS: dict[str, Any] = {
    "running": False,
    "inboxes": [],
    "last_poll_at": None,
    "last_error": None,
    "fetched_total": 0,
    "processed_total": 0,
}


async def _poll_inbox_once(cfg: InboxConfig) -> None:
    try:
        messages = await fetch_new(cfg, max_messages=_settings.mail_max_messages_per_poll)
        POLLER_STATUS["fetched_total"] += len(messages)
        if messages:
            logger.info("mail poller [%s]: fetched %d new message(s)", cfg.label, len(messages))
        else:
            logger.debug("mail poller [%s]: no new messages", cfg.label)
    except Exception as e:  # noqa: BLE001
        logger.exception("fetch_new failed for inbox %s", cfg.label)
        POLLER_STATUS["last_error"] = f"[{cfg.label}] {type(e).__name__}: {e}"
        return
    for m in messages:
        try:
            await _process_one(m)
            POLLER_STATUS["processed_total"] += 1
            logger.info(
                "mail poller [%s]: processed message_id=%s from=%s",
                cfg.label, m.message_id, m.from_email,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("process_one failed for %s", m.message_id)
            POLLER_STATUS["last_error"] = f"process {m.message_id}: {type(e).__name__}: {e}"


async def run_mail_poller() -> None:
    """Long-running task. Cancel via asyncio.Task.cancel() on shutdown."""
    import datetime as _dt
    inboxes = load_inboxes_from_env()
    POLLER_STATUS["inboxes"] = [i.label for i in inboxes]
    if not inboxes:
        logger.warning("mail poller: no inboxes configured (MAIL_INBOXES empty); loop idle")
        POLLER_STATUS["running"] = True
        POLLER_STATUS["last_error"] = "MAIL_INBOXES not configured"
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            return
    interval = max(15, int(_settings.mail_poll_interval_seconds or 60))
    # Baseline: mark existing unread mail as read so only NEW arrivals are picked up.
    for cfg in inboxes:
        try:
            skipped = await baseline_inbox(cfg)
            logger.info(
                "mail poller [%s]: baseline done, %d existing unread marked as seen",
                cfg.label, skipped,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("mail poller [%s]: baseline failed", cfg.label)
            POLLER_STATUS["last_error"] = f"[{cfg.label}] baseline: {type(e).__name__}: {e}"
    logger.info(
        "mail poller: STARTED — %d inbox(es) (%s), interval=%ss, subject_filter=%r",
        len(inboxes),
        ", ".join(i.label for i in inboxes),
        interval,
        SUBJECT_REQUIRED_PREFIX,
    )
    POLLER_STATUS["running"] = True
    POLLER_STATUS["subject_filter"] = SUBJECT_REQUIRED_PREFIX
    while True:
        for cfg in inboxes:
            await _poll_inbox_once(cfg)
        POLLER_STATUS["last_poll_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
        await asyncio.sleep(interval)
