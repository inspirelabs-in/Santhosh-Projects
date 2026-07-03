"""SMS inbound webhook — MSG91 delivery reports + inbound replies.

MSG91 posts delivery status updates and (if enabled) inbound replies
to a configured webhook URL. We:
1. Look up candidate by phone
2. Classify intent
3. Emit supervisor event
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import and_, select

from src.classifiers.candidate_intent import classify_candidate_intent
from src.config import get_settings
from src.constants.statuses import TERMINAL_APPLICATION_STATUSES
from src.db.base import Application, Candidate
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.services.dedup import normalise_phone
from src.services.typed_event_bus import EventType
from src.services.typed_event_bus import publish_event as publish_supervisor_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/sms", tags=["sms-webhooks"])
_settings = get_settings()


@router.post("", status_code=status.HTTP_200_OK)
async def handle_inbound_sms(request: Request) -> dict[str, Any]:
    import json

    body_bytes = await request.body()
    try:
        data = json.loads(body_bytes)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type_raw = data.get("type", "")

    if event_type_raw in ("DLR", "delivery", "sent", "rejected", "failed"):
        logger.debug("SMS delivery report: %s", event_type_raw)
        return {"status": "ok", "type": "delivery_report"}

    sender_phone = data.get("from") or data.get("sender") or data.get("mobile") or ""
    message_body = data.get("body") or data.get("message") or data.get("text") or ""
    message_id = data.get("requestId") or data.get("message_id") or ""

    normalised = normalise_phone(sender_phone)
    if not normalised or not message_body:
        logger.info("SMS webhook: no usable phone/body in payload")
        return {"status": "ok", "action": "ignored"}

    async with session_scope() as session:
        candidate = (
            await session.execute(
                select(Candidate).where(Candidate.phone == normalised)
            )
        ).scalar_one_or_none()

        if not candidate:
            logger.info("SMS from unknown number %s", normalised[:6] + "***")
            await publish_supervisor_event(
                session,
                EventType.CANDIDATE_MESSAGE_RECEIVED,
                payload={
                    "channel": "sms",
                    "from_phone": normalised,
                    "message_preview": message_body[:200],
                    "intent": "unknown_sender",
                    "sms_message_id": message_id,
                },
                dedup_extra=f"sms-unknown-{message_id or normalised}",
            )
            return {"status": "ok", "candidate": "unknown"}

        active_app = (
            await session.execute(
                select(Application).where(
                    and_(
                        Application.candidate_id == candidate.id,
                        Application.status.notin_(TERMINAL_APPLICATION_STATUSES),
                    )
                ).order_by(Application.updated_at.desc())
            )
        ).scalar_first()

        app_id = active_app.id if active_app else None
        stage = active_app.current_stage if active_app else None

        intent_result = await classify_candidate_intent(
            message_body,
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
            action="sms_received",
            details={
                "from_phone": normalised,
                "message_preview": message_body[:200],
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
                "channel": "sms",
                "message_preview": message_body[:200],
                "intent": intent_result.intent.value,
                "intent_confidence": intent_result.confidence,
                "urgency": intent_result.urgency,
                "extracted_details": intent_result.extracted_details,
                "current_stage": stage,
                "sms_message_id": message_id,
            },
            dedup_extra=f"sms-{message_id or normalised}-{datetime.now(UTC).strftime('%Y%m%d%H%M')}",
        )

    return {
        "status": "ok",
        "candidate_id": str(candidate.id),
        "intent": intent_result.intent.value,
    }
