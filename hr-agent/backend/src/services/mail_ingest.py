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
from src.classifiers.candidate_intent import classify_candidate_intent
from src.config import get_settings
from src.db.base import Application, Candidate, ProcessedMessage, Role
from src.db.connection import session_scope
from src.db.repositories.role import list_open_roles
from src.db.events import emit_event
from src.db.repositories import organization as org_repo
from src.models.candidate import IntakePayload, SourceChannel
from src.models.events import ActionType
from src.pipeline.v1 import run_apply_to_screening
from src.services.email_filter import InboundDecision, InboundKind, classify_inbound
from src.services.imap_inbox import (
    InboundMessage,
    InboxConfig,
    baseline_inbox,
    fetch_new,
    load_inboxes_from_env,
)

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

    Strategy (two-pass):
      1. Try matching against the **subject line only** first.  The subject
         is the strongest signal — "Application for Product Owner" should
         match "Product Owner" regardless of what's in the body/resume.
      2. Only if the subject yields no match, widen to subject + body.
      3. Within each pass: require ALL significant (non-stop) title tokens
         to appear.  Longest title wins ties.
    """
    if not subject and not body:
        return None
    import re as _re

    # Grammatical filler ONLY. Seniority + role-type words (junior/senior/
    # engineer/developer/manager/...) are the SIGNAL that distinguishes similar
    # roles, so they are kept, not stopped. (Stopping them made "Junior Full
    # Stack" and "Full Stack Engineer" both collapse to [full, stack] and match
    # arbitrarily.)
    _STOP = {"the", "for", "a", "an", "at", "to", "of", "role", "position",
             "job", "opening", "vacancy", "application", "applying", "apply",
             "applicant", "regarding", "re", "fwd"}

    async with session_scope() as session:
        roles = await list_open_roles(session)

        def _find_match(haystack_text: str) -> UUID | None:
            haystack_tokens = set(_re.findall(r"[a-z0-9]+", haystack_text.lower()))
            candidates: list[tuple[int, UUID]] = []
            for role in roles:
                title = (role.title or "").strip().lower()
                if not title:
                    continue
                title_tokens = [t for t in _re.findall(r"[a-z0-9]+", title)]
                if not title_tokens:
                    continue
                # Require ALL meaningful title tokens (incl. seniority/role-type)
                # to be present. Drop only grammatical filler -- but never drop
                # everything.
                required = [t for t in title_tokens if t not in _STOP] or title_tokens
                if all(t in haystack_tokens for t in required):
                    candidates.append((len(required), role.id))
            if not candidates:
                return None
            # Most-specific full match wins (a longer fully-matched title beats a
            # shorter one it contains, e.g. "Senior Backend Engineer" over
            # "Backend Engineer").
            candidates.sort(key=lambda x: x[0], reverse=True)
            # Ambiguous: the top two match equally specifically (e.g. the subject
            # names both "Junior Full Stack" and "Full Stack Engineer") -> do NOT
            # guess. Return None so the application is created unassigned for HR to
            # route, instead of being silently sent to the wrong role.
            if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
                return None
            return candidates[0][1]

        if subject:
            match = _find_match(subject)
            if match:
                return match

        if body:
            return _find_match(f"{subject or ''} {body}")
        return None


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
    is_referral: bool = False,
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

    # Pass the forwarder email for dedup but leave name blank — the resume
    # parser overwrites identity from the resume. Setting sender_name here
    # causes a visible flash in the UI (forwarder name → real name).
    payload = IntakePayload(
        source_channel=SourceChannel.EMAIL,
        sender_email=msg.from_email,
        sender_name=None,
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
            "is_referral": is_referral,
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
            send_ack_post_parse=not result.acknowledgement_sent,
        )
    )
    pipeline_task.add_done_callback(_on_pipeline_done)


async def _match_application_by_thread(
    session, msg: InboundMessage
) -> Application | None:
    """Find the application a reply belongs to via the thread it references.

    A candidate's reply carries In-Reply-To / References pointing at the
    Message-ID of an email WE sent. We persist our outbound Message-IDs on
    ``email_sends.provider_message_id`` with their ``application_id``, so we can
    map the reply to the EXACT application instead of guessing "most recent"
    (the NB-12 misroute). Returns None if the thread can't be resolved.
    """
    from src.db.base import EmailSend

    refs = msg.referenced_message_ids
    if not refs:
        return None
    row = (
        await session.execute(
            select(EmailSend)
            .where(EmailSend.provider_message_id.in_(refs))
            .where(EmailSend.application_id.is_not(None))
            .order_by(EmailSend.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None or row.application_id is None:
        return None
    return await session.get(Application, row.application_id)


async def _route_reply(msg: InboundMessage) -> None:
    """Route an inbound reply to the right application as a DURABLE event.

    Thread-match first (correct application); fall back to the sender's most
    recent active application only when the thread can't be resolved. Emits a
    durable ``candidate_email_reply`` domain event (requires_action) so the reply
    survives reloads and lands in the inbox -- replacing the dead supervisor bus
    that silently dropped replies (NB-12).
    """
    from sqlalchemy import select as sa_select

    if not msg.from_email:
        return
    try:
        async with session_scope() as session:
            app = await _match_application_by_thread(session, msg)
            matched_by = "thread"

            if app is None:
                # Fallback: known sender's most recent active application.
                candidate = (
                    await session.execute(
                        sa_select(Candidate)
                        .where(Candidate.email == msg.from_email.lower())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if candidate is None:
                    logger.info(
                        "reply %s from unknown sender %s and no thread match -- ignored",
                        msg.message_id, msg.from_email,
                    )
                    return
                app = (
                    await session.execute(
                        sa_select(Application)
                        .where(Application.candidate_id == candidate.id)
                        .where(Application.status.notin_(("rejected", "hired", "withdrawn")))
                        .order_by(Application.updated_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if app is None:
                    logger.info(
                        "reply %s: sender %s has no active application -- ignored",
                        msg.message_id, msg.from_email,
                    )
                    return
                matched_by = "sender_recent"

            has_attachment = any(
                a.content and _is_resume_attachment(a.filename, a.content_type)
                for a in msg.attachments
            )
            classify_text = f"{msg.subject or ''}\n{msg.body_text or ''}".strip()
            intent_result = await classify_candidate_intent(
                classify_text,
                has_attachments=has_attachment,
                current_stage=app.current_stage,
                application_id=app.id,
                candidate_id=app.candidate_id,
            )
            requires_action = intent_result.intent.value in {
                "withdrawal", "reschedule_request", "question",
            }
            await emit_event(
                session,
                type=ActionType.CANDIDATE_EMAIL_REPLY.value,
                org_id=getattr(app, "org_id", None),
                application_id=app.id,
                role_id=app.role_id,
                payload={
                    "from_email": msg.from_email,
                    "subject": (msg.subject or "")[:200],
                    "body_preview": (msg.body_text or "")[:500],
                    "has_attachment": has_attachment,
                    "message_id": msg.message_id,
                    "in_reply_to": msg.in_reply_to,
                    "matched_by": matched_by,
                    "channel": "email",
                    "intent": intent_result.intent.value,
                    "intent_confidence": intent_result.confidence,
                    "urgency": intent_result.urgency,
                    "extracted_details": intent_result.extracted_details,
                },
                actor="candidate",
                requires_action=requires_action,
            )
            logger.info(
                "candidate reply routed app=%s via=%s intent=%s action=%s",
                app.id, matched_by, intent_result.intent.value, requires_action,
            )
    except Exception:
        logger.exception("failed to route candidate reply for %s", msg.message_id)


async def _classify_inbound(msg: InboundMessage) -> InboundDecision:
    """Run the pure funnel with this org's domains + open-role titles."""
    has_resume = any(
        a.content and _is_resume_attachment(a.filename, a.content_type)
        for a in msg.attachments
    )
    async with session_scope() as session:
        org_domains = await org_repo.get_email_domains(session)
        roles = await list_open_roles(session)
        role_titles = [r.title for r in roles if r.title]
    return classify_inbound(
        subject=msg.subject,
        from_email=msg.from_email,
        has_reply_headers=msg.has_reply_headers,
        has_resume_attachment=has_resume,
        org_domains=org_domains,
        open_role_titles=role_titles,
    )


async def _process_one(msg: InboundMessage) -> None:
    if not msg.message_id:
        return
    if await _already_processed(msg.message_id):
        return
    if not msg.from_email:
        logger.info("skip mail %s: no From email", msg.message_id)
        await _mark_processed(msg.message_id, None, f"{msg.source}:skipped_no_from")
        return

    # Single deterministic funnel decides what this mail IS before we act.
    decision = await _classify_inbound(msg)
    logger.info(
        "mail %s funnel -> %s (%s) signals=%s",
        msg.message_id, decision.kind.value, decision.reason, decision.signals,
    )

    if decision.kind is InboundKind.REPLY:
        # A reply is never a new application: thread-route it to the right
        # application as a durable event (NB-12). Never re-intake.
        await _route_reply(msg)
        await _mark_processed(msg.message_id, None, f"{msg.source}:reply")
        return

    if decision.kind is InboundKind.INTERNAL:
        await _mark_processed(msg.message_id, None, f"{msg.source}:internal")
        return

    if decision.kind is InboundKind.IGNORE:
        # External non-application (newsletter / vendor / spam). Record so it is
        # never reprocessed; do not ingest.
        await _mark_processed(msg.message_id, None, f"{msg.source}:ignored_not_application")
        return

    # decision.kind is APPLICATION -> ingest. Referrals (internal forwarder) are
    # flagged on the application's raw_payload + the processed-source marker.
    is_referral = decision.is_referral
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
            is_referral=is_referral,
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
                is_referral=is_referral,
            )

    # Mark the whole message processed once all its resumes are dispatched.
    # application_id=None is fine — the PK is message_id for dedup.
    source = f"{msg.source}:referral" if is_referral else msg.source
    await _mark_processed(msg.message_id, None, source)


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
        "mail poller: STARTED — %d inbox(es) (%s), interval=%ss, gate=deterministic-funnel",
        len(inboxes),
        ", ".join(i.label for i in inboxes),
        interval,
    )
    POLLER_STATUS["running"] = True
    POLLER_STATUS["gate"] = "deterministic_funnel"
    while True:
        for cfg in inboxes:
            await _poll_inbox_once(cfg)
        POLLER_STATUS["last_poll_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
        await asyncio.sleep(interval)
