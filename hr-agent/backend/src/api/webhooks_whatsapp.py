"""WhatsApp inbound webhook — Meta Cloud API.

Handles two flows:
1. GET: Meta webhook verification (echoes hub.challenge)
2. POST: Incoming messages from candidates via WhatsApp

Every inbound message is: phone lookup → intent classification → supervisor event.
HMAC-SHA256 verified via X-Hub-Signature-256 against whatsapp_app_secret.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import and_, select

from src.classifiers.candidate_intent import classify_candidate_intent
from src.config import get_settings
from src.db.base import Application, Candidate
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.services.dedup import normalise_phone
from src.services.typed_event_bus import EventType
from src.services.typed_event_bus import publish_event as publish_supervisor_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp-webhooks"])
_settings = get_settings()


def _verify_meta_signature(body: bytes, signature_header: str | None) -> bool:
    secret = _settings.whatsapp_app_secret
    if not secret:
        logger.warning("whatsapp_app_secret not set — skipping HMAC verification")
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature_header)


@router.get("", status_code=status.HTTP_200_OK)
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
) -> Any:
    if hub_mode == "subscribe" and hub_verify_token == _settings.whatsapp_webhook_verify_token:
        logger.info("WhatsApp webhook verified")
        return int(hub_challenge) if hub_challenge else ""
    raise HTTPException(status_code=403, detail="Verification failed")


def _extract_messages(payload: dict) -> list[dict]:
    messages = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                messages.append({
                    "from_phone": msg.get("from", ""),
                    "message_id": msg.get("id", ""),
                    "timestamp": msg.get("timestamp", ""),
                    "type": msg.get("type", "text"),
                    "body": (msg.get("text", {}) or {}).get("body", ""),
                })
    return messages


@router.post("", status_code=status.HTTP_200_OK)
async def handle_inbound_whatsapp(request: Request) -> dict[str, str]:
    body_bytes = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")

    if not _verify_meta_signature(body_bytes, signature):
        raise HTTPException(status_code=403, detail="Invalid signature")

    import json
    try:
        payload = json.loads(body_bytes)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    messages = _extract_messages(payload)
    if not messages:
        return {"status": "ok", "processed": "0"}

    processed = 0
    for msg in messages:
        normalised = normalise_phone(msg["from_phone"])
        if not normalised:
            continue

        body_text = msg["body"]
        if not body_text:
            continue

        async with session_scope() as session:
            candidate = (
                await session.execute(
                    select(Candidate).where(Candidate.phone == normalised)
                )
            ).scalar_one_or_none()

            if not candidate:
                logger.info("WhatsApp from unknown number %s", normalised[:6] + "***")
                await publish_supervisor_event(
                    session,
                    EventType.CANDIDATE_MESSAGE_RECEIVED,
                    payload={
                        "channel": "whatsapp",
                        "from_phone": normalised,
                        "message_preview": body_text[:200],
                        "intent": "unknown_sender",
                        "wa_message_id": msg["message_id"],
                    },
                    dedup_extra=f"wa-unknown-{msg['message_id']}",
                )
                continue

            active_app = (
                await session.execute(
                    select(Application).where(
                        and_(
                            Application.candidate_id == candidate.id,
                            Application.status.notin_(("rejected", "hired", "withdrawn")),
                        )
                    ).order_by(Application.updated_at.desc())
                )
            ).scalar_first()

            app_id = active_app.id if active_app else None
            stage = active_app.current_stage if active_app else None

            intent_result = await classify_candidate_intent(
                body_text,
                current_stage=stage,
                application_id=app_id,
                candidate_id=candidate.id,
            )

            event_type = EventType.CANDIDATE_MESSAGE_RECEIVED
            if intent_result.intent.value == "withdrawal":
                event_type = EventType.CANDIDATE_WITHDRAWAL

            await log_audit(
                session,
                application_id=app_id,
                action="whatsapp_message_received",
                details={
                    "from_phone": normalised,
                    "message_preview": body_text[:200],
                    "intent": intent_result.intent.value,
                    "confidence": intent_result.confidence,
                },
            )

            await publish_supervisor_event(
                session,
                event_type,
                application_id=app_id,
                candidate_id=candidate.id,
                payload={
                    "channel": "whatsapp",
                    "message_preview": body_text[:200],
                    "intent": intent_result.intent.value,
                    "intent_confidence": intent_result.confidence,
                    "urgency": intent_result.urgency,
                    "extracted_details": intent_result.extracted_details,
                    "current_stage": stage,
                    "wa_message_id": msg["message_id"],
                },
                dedup_extra=f"wa-{msg['message_id']}",
            )
            processed += 1

    return {"status": "ok", "processed": str(processed)}
