"""Email push webhook — Microsoft Graph change notifications.

Supplements (does NOT replace) the IMAP mail poller. Graph subscriptions
expire after ~3 days, so a renewal cron is required. If the subscription
lapses, IMAP polling is the floor — email still flows, just with latency.

Two request types:
1. Subscription validation: POST with validationToken query param → echo it
2. Change notification: POST with notification payload → fetch message → classify → emit
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from src.config import get_settings
from src.db.base import Candidate
from src.db.connection import session_scope
from src.services.typed_event_bus import EventType
from src.services.typed_event_bus import publish_event as publish_supervisor_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/email", tags=["email-webhooks"])
_settings = get_settings()


@router.post("", status_code=status.HTTP_200_OK)
async def handle_graph_notification(
    request: Request,
    validationToken: str | None = Query(None),
) -> Any:
    if validationToken is not None:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(content=validationToken, status_code=200)

    import json
    body_bytes = await request.body()
    try:
        payload = json.loads(body_bytes)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    notifications = payload.get("value", [])
    processed = 0

    for notification in notifications:
        client_state = notification.get("clientState", "")
        if _settings.graph_webhook_client_state and client_state != _settings.graph_webhook_client_state:
            logger.warning("Graph notification clientState mismatch")
            continue

        resource = notification.get("resource", "")
        change_type = notification.get("changeType", "")
        subscription_id = notification.get("subscriptionId", "")

        if change_type != "created":
            continue

        message_id = resource.split("/")[-1] if "/" in resource else resource

        try:
            msg_data = await _fetch_graph_message(message_id)
        except Exception:
            logger.exception("Failed to fetch Graph message %s", message_id)
            continue

        if not msg_data:
            continue

        sender_email = (
            msg_data.get("from", {}).get("emailAddress", {}).get("address", "")
        ).lower().strip()
        subject = msg_data.get("subject", "")
        body_preview = msg_data.get("bodyPreview", "")

        if not sender_email:
            continue

        async with session_scope() as session:
            candidate = (
                await session.execute(
                    select(Candidate).where(Candidate.email == sender_email)
                )
            ).scalar_one_or_none()

            if not candidate:
                logger.debug("Graph email from non-candidate %s", sender_email)
                continue

            from sqlalchemy import and_
            from src.db.base import Application
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

            from src.classifiers.candidate_intent import classify_candidate_intent
            intent_text = f"Subject: {subject}\n\n{body_preview}"
            intent_result = await classify_candidate_intent(
                intent_text,
                current_stage=stage,
                application_id=app_id,
                candidate_id=candidate.id,
            )

            event_type = EventType.CANDIDATE_MESSAGE_RECEIVED
            if intent_result.intent.value == "withdrawal":
                event_type = EventType.CANDIDATE_WITHDRAWAL

            await publish_supervisor_event(
                session,
                event_type,
                application_id=app_id,
                candidate_id=candidate.id,
                payload={
                    "channel": "email_push",
                    "sender_email": sender_email,
                    "subject": subject[:200],
                    "message_preview": body_preview[:300],
                    "intent": intent_result.intent.value,
                    "intent_confidence": intent_result.confidence,
                    "urgency": intent_result.urgency,
                    "extracted_details": intent_result.extracted_details,
                    "current_stage": stage,
                    "graph_message_id": message_id,
                },
                dedup_extra=f"email-push-{message_id}",
            )
            processed += 1

    return {"status": "ok", "processed": processed}


async def _fetch_graph_message(message_id: str) -> dict | None:
    import httpx
    token = await _get_graph_token()
    if not token:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"https://graph.microsoft.com/v1.0/me/messages/{message_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"$select": "from,subject,bodyPreview,receivedDateTime"},
        )
        if resp.status_code != 200:
            logger.warning("Graph message fetch failed: %d", resp.status_code)
            return None
        return resp.json()


async def _get_graph_token() -> str | None:
    if not _settings.graph_client_id or not _settings.graph_client_secret:
        logger.warning("Graph credentials not configured")
        return None
    import httpx
    tenant = _settings.graph_tenant_id or "common"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            data={
                "client_id": _settings.graph_client_id,
                "client_secret": _settings.graph_client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
        )
        if resp.status_code != 200:
            logger.warning("Graph token fetch failed: %d", resp.status_code)
            return None
        return resp.json().get("access_token")
