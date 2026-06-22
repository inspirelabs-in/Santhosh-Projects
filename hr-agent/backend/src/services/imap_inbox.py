"""Minimal IMAP reader for V1 mail ingestion.

Uses stdlib `imaplib` inside `asyncio.to_thread`. Returns a list of
`InboundMessage`s with headers, plain-text body, and attachments.
Marks fetched messages as \\Seen so the next poll skips them.
"""

from __future__ import annotations

import asyncio
import email
import imaplib
import json
import logging
import os
from dataclasses import dataclass, field
from email.header import decode_header
from email.message import Message
from typing import Any

from src.config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()


@dataclass
class InboxConfig:
    label: str
    host: str
    port: int
    user: str
    password: str
    source: str = "imap_gmail"
    folder: str = "INBOX"
    use_ssl: bool = True


@dataclass
class MailAttachment:
    filename: str
    content: bytes
    content_type: str | None = None


@dataclass
class InboundMessage:
    inbox_label: str
    source: str
    message_id: str
    from_email: str | None
    from_name: str | None
    subject: str | None
    body_text: str | None
    received_at: str | None
    attachments: list[MailAttachment] = field(default_factory=list)
    # Threading headers (RFC 5322). Populated for replies; empty for fresh mail.
    # ``in_reply_to`` is the parent Message-ID; ``references`` is the full chain.
    # These drive reply detection + thread-routing in the ingest funnel (NB-12).
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)

    @property
    def has_reply_headers(self) -> bool:
        return bool(self.in_reply_to or self.references)

    @property
    def referenced_message_ids(self) -> list[str]:
        """All parent Message-IDs this mail threads onto, newest-intent first."""
        out: list[str] = []
        if self.in_reply_to:
            out.append(self.in_reply_to)
        for r in self.references:
            if r and r not in out:
                out.append(r)
        return out


def load_inboxes_from_env() -> list[InboxConfig]:
    raw = _settings.mail_inboxes
    if not raw:
        return []
    try:
        arr = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        logger.warning("MAIL_INBOXES not valid JSON: %s", e)
        return []
    out: list[InboxConfig] = []
    for obj in arr:
        try:
            out.append(
                InboxConfig(
                    label=obj["label"],
                    host=obj["host"],
                    port=int(obj.get("port", 993)),
                    user=obj["user"],
                    password=obj["password"],
                    source=obj.get("source", "imap_gmail"),
                    folder=obj.get("folder", "INBOX"),
                    use_ssl=bool(obj.get("use_ssl", True)),
                )
            )
        except KeyError as e:
            logger.warning("skipping mailbox %r: missing key %s", obj, e)
    return out


def _decode(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            logger.warning("UTF-8 decode failed, falling back to latin-1 (data may be lossy)")
            return raw.decode("latin-1", errors="replace")
    if isinstance(raw, str):
        return raw
    return str(raw)


def _mime_header(val: Any) -> str:
    if not val:
        return ""
    parts = decode_header(val)
    out: list[str] = []
    for text, enc in parts:
        if isinstance(text, bytes):
            try:
                out.append(text.decode(enc or "utf-8", errors="replace"))
            except Exception:  # noqa: BLE001
                out.append(text.decode("latin-1", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def _split_from(from_header: str) -> tuple[str | None, str | None]:
    from_header = (from_header or "").strip()
    if not from_header:
        return None, None
    if "<" in from_header and ">" in from_header:
        name = from_header.split("<", 1)[0].strip().strip('"')
        addr = from_header.split("<", 1)[1].split(">", 1)[0].strip().lower()
        return addr, name or None
    return from_header.lower(), None


def _split_references(raw: str | None) -> list[str]:
    """Parse a References / In-Reply-To header into a list of Message-IDs.

    Both headers hold whitespace-separated ``<id@host>`` tokens. We keep the
    angle brackets so the values match the form we persist for our own sends.
    """
    if not raw:
        return []
    return [tok for tok in raw.replace(",", " ").split() if tok.strip()]


def _extract_body_and_attachments(msg: Message) -> tuple[str, list[MailAttachment]]:
    body: list[str] = []
    atts: list[MailAttachment] = []

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            cdisp = str(part.get("Content-Disposition") or "")
            if "attachment" in cdisp.lower() or "inline" in cdisp.lower() and part.get_filename():
                filename = _mime_header(part.get_filename() or "attachment.bin")
                content = part.get_payload(decode=True) or b""
                if content:
                    atts.append(
                        MailAttachment(filename=filename, content=content, content_type=ctype)
                    )
                continue
            if ctype == "text/plain" and "attachment" not in cdisp.lower():
                payload = part.get_payload(decode=True) or b""
                body.append(_decode(payload))
    else:
        payload = msg.get_payload(decode=True) or b""
        body.append(_decode(payload))

    return "\n".join(b for b in body if b).strip(), atts


def _baseline_sync(cfg: InboxConfig) -> int:
    """Mark every currently-UNSEEN message as \\Seen. Called once on startup so
    only mails arriving AFTER startup are processed. Returns count skipped."""
    conn = imaplib.IMAP4_SSL(cfg.host, cfg.port) if cfg.use_ssl else imaplib.IMAP4(cfg.host, cfg.port)
    try:
        conn.login(cfg.user, cfg.password)
        conn.select(cfg.folder)
        typ, data = conn.search(None, "UNSEEN")
        if typ != "OK":
            return 0
        ids = data[0].split()
        for i in ids:
            conn.store(i, "+FLAGS", "\\Seen")
        return len(ids)
    finally:
        try:
            conn.logout()
        except Exception as _logout_err:  # noqa: BLE001
            logger.debug("IMAP logout failed (non-fatal): %s", _logout_err)


async def baseline_inbox(cfg: InboxConfig) -> int:
    return await asyncio.to_thread(_baseline_sync, cfg)


def _fetch_sync(cfg: InboxConfig, max_messages: int) -> list[InboundMessage]:
    if cfg.use_ssl:
        conn = imaplib.IMAP4_SSL(cfg.host, cfg.port)
    else:
        conn = imaplib.IMAP4(cfg.host, cfg.port)
    try:
        conn.login(cfg.user, cfg.password)
        conn.select(cfg.folder)
        typ, data = conn.search(None, "UNSEEN")
        if typ != "OK":
            return []
        ids = data[0].split()
        out: list[InboundMessage] = []
        for i in ids[:max_messages]:
            typ, msg_data = conn.fetch(i, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            if not isinstance(raw, (bytes, bytearray)):
                continue
            msg = email.message_from_bytes(raw)
            subject = _mime_header(msg.get("Subject"))
            from_email, from_name = _split_from(_mime_header(msg.get("From")))
            message_id = _mime_header(msg.get("Message-ID")) or f"imap-{cfg.label}-{i.decode()}"
            received = _mime_header(msg.get("Date"))
            in_reply_to_list = _split_references(_mime_header(msg.get("In-Reply-To")))
            references = _split_references(_mime_header(msg.get("References")))
            body, atts = _extract_body_and_attachments(msg)
            out.append(
                InboundMessage(
                    inbox_label=cfg.label,
                    source=cfg.source,
                    message_id=message_id,
                    from_email=from_email,
                    from_name=from_name,
                    subject=subject or None,
                    body_text=body or None,
                    received_at=received or None,
                    attachments=atts,
                    in_reply_to=in_reply_to_list[0] if in_reply_to_list else None,
                    references=references,
                )
            )
            # Mark read so we don't reprocess.
            conn.store(i, "+FLAGS", "\\Seen")
        return out
    finally:
        try:
            conn.logout()
        except Exception as _logout_err:  # noqa: BLE001
            logger.debug("IMAP logout failed (non-fatal): %s", _logout_err)


async def fetch_new(cfg: InboxConfig, max_messages: int = 25) -> list[InboundMessage]:
    return await asyncio.to_thread(_fetch_sync, cfg, max_messages)
