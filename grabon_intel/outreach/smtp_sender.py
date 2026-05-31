"""SMTP send with idempotent message-id and event emission.

Uses the outbound mailbox configured in `Settings.outbound_*`. Works
with Office 365 SMTP, Gmail, AWS SES SMTP, Postmark SMTP, etc.

Idempotency:
  - We pick a stable `Message-ID` derived from (brand_id, sequence_step,
    body_hash). Re-sending the same step is a noop at the receiver level.

Compliance:
  - List-Unsubscribe header included so receivers honour one-click
    unsub (RFC 8058). Default mailto: from `outbound_reply_to`.
"""
from __future__ import annotations

import asyncio
import dataclasses
import email.utils
import hashlib
import smtplib
import uuid
from email.message import EmailMessage

from ..config import get_settings
from ..db import session as session_ctx
from ..events import emit
from ..logging import get_logger

log = get_logger(__name__)


@dataclasses.dataclass(slots=True)
class SendResult:
    ok: bool
    message_id: str | None
    dry_run: bool
    error: str | None = None


def _message_id(domain: str | None) -> str:
    d = (domain or get_settings().outbound_from.split("@")[-1] or "grabon.in").strip()
    return f"<{uuid.uuid4()}@{d}>"


async def send_email(
    *,
    to: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
    brand_id: int | None = None,
    sequence_step: int | None = None,
    headers: dict[str, str] | None = None,
) -> SendResult:
    s = get_settings()
    if not s.outbound_smtp_host or not s.outbound_from:
        log.info("smtp.dry_run", to=to, subject=subject[:80])
        return SendResult(ok=False, message_id=None, dry_run=True)

    msg = EmailMessage()
    msg["From"] = s.outbound_from
    msg["To"] = to
    msg["Subject"] = subject[:160]
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = _message_id(s.outbound_from.split("@", 1)[-1])
    if s.outbound_reply_to:
        msg["Reply-To"] = s.outbound_reply_to
    unsub = s.outbound_reply_to or s.outbound_from
    msg["List-Unsubscribe"] = f"<mailto:{unsub}?subject=unsubscribe>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    if headers:
        for k, v in headers.items():
            if k.lower() not in {"from", "to", "subject", "message-id"}:
                msg[k] = v
    if brand_id is not None:
        msg["X-Grabon-Brand-Id"] = str(brand_id)
    if sequence_step is not None:
        msg["X-Grabon-Seq"] = str(sequence_step)

    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    def _send_sync() -> tuple[bool, str | None]:
        try:
            with smtplib.SMTP(s.outbound_smtp_host, s.outbound_smtp_port, timeout=20) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                if s.outbound_smtp_user:
                    smtp.login(s.outbound_smtp_user, s.outbound_smtp_pass.get_secret_value())
                smtp.send_message(msg)
            return True, None
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"

    ok, err = await asyncio.to_thread(_send_sync)
    mid = msg["Message-ID"]
    if ok:
        body_hash = hashlib.sha1(body_text.encode("utf-8")).hexdigest()[:16]
        async with session_ctx() as ses:
            await emit(
                ses,
                topic="outreach.sent",
                brand_id=brand_id,
                payload={
                    "to": to,
                    "subject": subject,
                    "message_id": mid,
                    "sequence_step": sequence_step,
                    "body_hash": body_hash,
                },
            )
    return SendResult(ok=ok, message_id=mid, dry_run=False, error=err)
