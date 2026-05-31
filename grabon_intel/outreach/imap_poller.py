"""IMAP poller — pulls new replies, classifies, emits `reply.received`.

Replaces Smartlead reply webhook. Uses `aioimaplib` for native asyncio
IMAP without blocking the event loop.

State: stores last-seen UID per (host, folder) in `events` (cheap
sentinel topic `imap.cursor`) so subsequent polls only fetch new mail.

Classification: same prompt schema as the old Smartlead webhook —
single LLM call returns {intent, confidence, reason}.
"""
from __future__ import annotations

import email
import email.policy
from dataclasses import dataclass
from email.message import Message
from typing import Any

import aioimaplib
from sqlalchemy import text

from ..config import get_settings
from ..db import session as session_ctx
from ..events import emit
from ..llm import Tier, complete
from ..logging import get_logger

log = get_logger(__name__)

_CURSOR_TOPIC = "imap.cursor"


@dataclass(slots=True)
class PollResult:
    fetched: int
    classified: int
    last_uid: str | None


async def _last_uid(host: str, folder: str) -> int:
    async with session_ctx() as s:
        row = (
            await s.execute(
                text(
                    "SELECT (payload->>'last_uid')::bigint FROM events "
                    "WHERE topic = :t AND payload->>'host' = :h "
                    "AND payload->>'folder' = :f ORDER BY created_at DESC LIMIT 1"
                ),
                {"t": _CURSOR_TOPIC, "h": host, "f": folder},
            )
        ).first()
    return int(row[0]) if row and row[0] is not None else 0


async def _save_uid(host: str, folder: str, uid: int) -> None:
    async with session_ctx() as s:
        await emit(
            s,
            topic=_CURSOR_TOPIC,
            brand_id=None,
            payload={"host": host, "folder": folder, "last_uid": uid},
        )


def _extract_text(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                try:
                    return part.get_content()  # type: ignore[no-any-return]
                except Exception:
                    pass
    try:
        return msg.get_content()  # type: ignore[no-any-return]
    except Exception:
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            try:
                return payload.decode("utf-8", errors="replace")
            except Exception:
                return ""
        return str(payload or "")


async def _classify(reply_text: str) -> dict[str, Any]:
    if not reply_text.strip():
        return {"intent": "neutral", "confidence": 0.0, "reason": "empty"}
    prompt = (
        "Classify this email reply into one of: interested, objection, "
        "ooo, unsubscribe, wrong_person, scheduling, neutral. "
        "Return strict JSON {intent, confidence(0-1), reason}.\n\n"
        f"Reply text:\n{reply_text[:2000]}"
    )
    res = await complete(
        tier=Tier.FAST, prompt=prompt, json_mode=True, max_output_tokens=200, temperature=0.0
    )
    import json as _json

    try:
        return _json.loads(res.content)
    except Exception:
        log.warning("imap.classify_parse_failed", raw=res.content[:200])
        return {"intent": "neutral", "confidence": 0.0, "reason": "parse_failed"}


async def _resolve_brand(from_email: str | None) -> int | None:
    if not from_email or "@" not in from_email:
        return None
    rd = from_email.split("@", 1)[1].lower().strip().removeprefix("www.")
    async with session_ctx() as s:
        row = (
            await s.execute(
                text("SELECT id FROM brands WHERE root_domain = :rd LIMIT 1"),
                {"rd": rd},
            )
        ).first()
    return int(row[0]) if row else None


async def poll_replies(*, max_messages: int = 50) -> PollResult:
    s = get_settings()
    if not s.inbound_imap_host or not s.inbound_imap_user:
        return PollResult(fetched=0, classified=0, last_uid=None)

    client = aioimaplib.IMAP4_SSL(host=s.inbound_imap_host, port=s.inbound_imap_port, timeout=20)
    await client.wait_hello_from_server()
    await client.login(s.inbound_imap_user, s.inbound_imap_pass.get_secret_value())
    await client.select(s.inbound_imap_folder)

    last_uid = await _last_uid(s.inbound_imap_host, s.inbound_imap_folder)
    search_criterion = f"UID {last_uid + 1}:*" if last_uid else "ALL"
    typ, search_data = await client.uid_search(search_criterion)
    if typ != "OK" or not search_data:
        await client.logout()
        return PollResult(fetched=0, classified=0, last_uid=None)
    uids_raw = (search_data[0] or b"").decode().split()
    new_uids = [int(u) for u in uids_raw if u.isdigit() and int(u) > last_uid][:max_messages]

    fetched = 0
    classified = 0
    max_uid_seen = last_uid
    for uid in new_uids:
        typ, data = await client.uid("fetch", str(uid), "(BODY.PEEK[])")
        if typ != "OK" or not data or len(data) < 2:
            continue
        raw = data[1]
        if isinstance(raw, list):
            raw = raw[0] if raw else b""
        if not isinstance(raw, (bytes, bytearray)):
            continue
        msg = email.message_from_bytes(bytes(raw), policy=email.policy.default)
        fetched += 1
        from_addr = email.utils.parseaddr(msg.get("From") or "")[1]
        body_txt = _extract_text(msg)
        classification = await _classify(body_txt)
        classified += 1
        brand_id = await _resolve_brand(from_addr)
        in_reply_to = msg.get("In-Reply-To")
        async with session_ctx() as ses:
            await emit(
                ses,
                topic="reply.received",
                brand_id=brand_id,
                payload={
                    "source": "imap",
                    "uid": uid,
                    "from": from_addr,
                    "subject": msg.get("Subject"),
                    "in_reply_to": in_reply_to,
                    "classification": classification,
                    "preview": body_txt[:500],
                },
            )
        if uid > max_uid_seen:
            max_uid_seen = uid

    if max_uid_seen > last_uid:
        await _save_uid(s.inbound_imap_host, s.inbound_imap_folder, max_uid_seen)

    await client.logout()
    return PollResult(fetched=fetched, classified=classified, last_uid=str(max_uid_seen) if max_uid_seen else None)
