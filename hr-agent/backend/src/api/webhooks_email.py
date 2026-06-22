"""Email push webhook — Microsoft Graph change notifications.

Supplements (does NOT replace) the IMAP mail poller. Graph subscriptions
expire after ~3 days, so a renewal cron is required. If the subscription
lapses, IMAP polling is the floor — email still flows, just with latency.

Two request types:
1. Subscription validation: POST with validationToken query param → echo it
2. Change notification: POST with notification payload → fetch message → funnel → durable route

Reply handling is durable + thread-correct here (NB-12); NEW-applicant
ingestion from Graph is intentionally deferred to the IMAP poller (the
reliable floor that also pulls attachments), so this path never double-
ingests and never blocks an application's resume from being processed.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from src.config import get_settings
from src.db.base import ProcessedMessage
from src.db.connection import session_scope

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

        msg = _inbound_from_graph(msg_data, message_id)
        if not msg.from_email:
            continue

        # Idempotency: the IMAP poller and this webhook share processed_messages,
        # keyed by RFC Message-ID, so the same mail is never handled twice.
        async with session_scope() as session:
            if await session.get(ProcessedMessage, msg.message_id) is not None:
                continue

        from src.services.email_filter import classify_inbound
        from src.services.mail_ingest import _classify_inbound, _route_reply

        decision = await _classify_inbound(msg)
        logger.info(
            "graph mail %s funnel -> %s (%s)",
            msg.message_id, decision.kind.value, decision.reason,
        )

        # Replies are routed durably + thread-correct here (NB-12). New
        # applications + internal/ignore are left to the IMAP poller (the
        # reliable floor that also fetches attachments), so we never double-
        # ingest from the push channel.
        from src.services.email_filter import InboundKind
        if decision.kind is InboundKind.REPLY:
            await _route_reply(msg)
            async with session_scope() as session:
                session.add(
                    ProcessedMessage(
                        message_id=msg.message_id,
                        application_id=None,
                        mail_source="graph_push:reply",
                    )
                )
            processed += 1

    return {"status": "ok", "processed": processed}


def _inbound_from_graph(msg_data: dict, message_id: str):
    """Adapt a Graph message payload into the shared InboundMessage shape so the
    push channel reuses the exact same funnel + reply router as the IMAP poller."""
    from src.services.imap_inbox import InboundMessage

    sender_email = (
        msg_data.get("from", {}).get("emailAddress", {}).get("address", "")
    ).lower().strip() or None
    sender_name = (
        msg_data.get("from", {}).get("emailAddress", {}).get("name", "")
    ) or None
    subject = msg_data.get("subject") or None
    body_preview = msg_data.get("bodyPreview") or None

    in_reply_to = None
    references: list[str] = []
    for h in msg_data.get("internetMessageHeaders", []) or []:
        name = (h.get("name") or "").lower()
        value = h.get("value") or ""
        if name == "in-reply-to":
            toks = [tok for tok in value.replace(",", " ").split() if tok.strip()]
            in_reply_to = toks[0] if toks else None
        elif name == "references":
            references = [tok for tok in value.replace(",", " ").split() if tok.strip()]

    rfc_id = msg_data.get("internetMessageId") or f"graph-{message_id}"
    return InboundMessage(
        inbox_label="graph_push",
        source="graph_push",
        message_id=rfc_id,
        from_email=sender_email,
        from_name=sender_name,
        subject=subject,
        body_text=body_preview,
        received_at=msg_data.get("receivedDateTime"),
        attachments=[],
        in_reply_to=in_reply_to,
        references=references,
    )


async def _fetch_graph_message(message_id: str) -> dict | None:
    import httpx
    token = await _get_graph_token()
    if not token:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"https://graph.microsoft.com/v1.0/me/messages/{message_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "$select": "from,subject,bodyPreview,receivedDateTime,internetMessageId,internetMessageHeaders"
            },
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
