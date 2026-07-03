"""Inbound call webhook — handles candidates calling back.

When a candidate calls the number that previously called them, the telephony
provider (Twilio/ElevenLabs) fires a webhook here. We:
1. Look up candidate by caller phone number
2. If known candidate with active application: queue outbound callback + SMS ack
3. If unknown: send SMS "we'll call you back"
4. Emit supervisor event for tracking

This does NOT answer the call live — it receives the inbound-call notification
and dispatches an outbound callback through the existing voice pipeline.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, select

from src.config import get_settings
from src.constants.statuses import TERMINAL_APPLICATION_STATUSES
from src.db.base import Application, Candidate
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.voice_call import create_voice_call
from src.models.v1 import CallKind
from src.services.dedup import normalise_phone
from src.services.voice_context import build_candidate_context
from src.services.voice_prompts import build_voice_prompt
from src.services.typed_event_bus import EventType
from src.services.typed_event_bus import publish_event as publish_supervisor_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/inbound-call", tags=["inbound-call-webhooks"])
_settings = get_settings()


class InboundCallPayload(BaseModel):
    caller_phone: str = Field(..., description="Caller number in E.164")
    called_phone: str = Field("", description="Number that was called")
    call_sid: str = Field("", description="Provider call ID for dedup")
    provider: str = Field("twilio", description="twilio|elevenlabs")


def _verify_twilio_signature(
    request: Request, body: bytes, signature: str | None
) -> bool:
    secret = _settings.twilio_auth_token
    if not secret:
        return False
    url = str(request.url)
    expected = hmac.new(
        secret.encode(), url.encode() + body, hashlib.sha1
    ).hexdigest()
    return hmac.compare_digest(expected, signature or "")


@router.post("", status_code=status.HTTP_200_OK)
async def handle_inbound_call(
    request: Request,
    x_twilio_signature: str | None = Header(None),
) -> dict[str, Any]:
    body_bytes = await request.body()

    if _settings.twilio_auth_token and not _verify_twilio_signature(
        request, body_bytes, x_twilio_signature
    ):
        raise HTTPException(status_code=403, detail="Invalid signature")

    import json
    try:
        data = json.loads(body_bytes)
    except json.JSONDecodeError:
        from urllib.parse import parse_qs
        qs = parse_qs(body_bytes.decode())
        data = {
            "caller_phone": qs.get("From", [qs.get("Caller", [""])[0]])[0],
            "called_phone": qs.get("To", [qs.get("Called", [""])[0]])[0],
            "call_sid": qs.get("CallSid", [""])[0],
            "provider": "twilio",
        }

    payload = InboundCallPayload(**data)
    normalised = normalise_phone(payload.caller_phone)

    if not normalised:
        logger.warning("inbound call: no parseable phone in %s", payload.caller_phone)
        return {"action": "ignored", "reason": "no_valid_phone"}

    async with session_scope() as session:
        candidate = (
            await session.execute(
                select(Candidate).where(Candidate.phone == normalised)
            )
        ).scalar_one_or_none()

        if not candidate:
            logger.info("inbound call from unknown number %s", normalised[:6] + "***")
            await publish_supervisor_event(
                session,
                EventType.CANDIDATE_MESSAGE_RECEIVED,
                payload={
                    "channel": "phone_inbound",
                    "caller_phone": normalised,
                    "intent": "unknown_caller",
                    "action_taken": "sms_ack",
                },
                dedup_extra=f"inbound-call-unknown-{payload.call_sid or normalised}",
            )
            try:
                from src.channels.sms import send_sms
                await send_sms(
                    phone=payload.caller_phone,
                    message="Thanks for calling! Our recruitment team will get back to you shortly.",
                )
            except Exception:
                logger.exception("Failed to send SMS ack to unknown caller")
            return {"action": "sms_ack", "candidate": "unknown"}

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

        await log_audit(
            session,
            application_id=app_id,
            action="inbound_call_received",
            details={
                "caller_phone": normalised,
                "call_sid": payload.call_sid,
                "stage_at_call": stage,
            },
        )

        await publish_supervisor_event(
            session,
            EventType.CANDIDATE_MESSAGE_RECEIVED,
            application_id=app_id,
            candidate_id=candidate.id,
            payload={
                "channel": "phone_inbound",
                "caller_phone": normalised,
                "candidate_name": candidate.name,
                "current_stage": stage,
                "intent": "callback_request",
                "action_taken": "queued_callback",
            },
            dedup_extra=f"inbound-call-{payload.call_sid or normalised}-{datetime.now(UTC).strftime('%Y%m%d%H')}",
        )

        if active_app and stage not in TERMINAL_APPLICATION_STATUSES:
            try:
                from src.activities.v1_voice_screening import dispatch_voice_screening
                await dispatch_voice_screening(application_id=active_app.id)
                logger.info(
                    "queued callback for candidate %s (app %s)",
                    candidate.id, active_app.id,
                )
            except Exception:
                logger.exception("Failed to dispatch callback for %s", candidate.id)
                try:
                    from src.channels.sms import send_sms
                    await send_sms(
                        phone=payload.caller_phone,
                        message="Thanks for calling! We'll reach out to you shortly regarding your application.",
                    )
                except Exception:
                    logger.exception("SMS fallback also failed")

    return {
        "action": "callback_queued",
        "candidate_id": str(candidate.id) if candidate else None,
        "application_id": str(app_id) if app_id else None,
    }


@router.post("/initiation", status_code=status.HTTP_200_OK)
async def elevenlabs_initiation_webhook(
    request: Request,
) -> dict[str, Any]:
    """ElevenLabs conversation initiation webhook for inbound calls.

    When a candidate calls the ElevenLabs-assigned phone number, ElevenLabs
    POSTs here to fetch per-call overrides (system_prompt, dynamic_variables).
    We identify the caller, build full candidate context, and return the
    override payload so the agent answers live with accurate data.
    """

    settings = get_settings()
    if settings.inbound_call_mode != "live_agent":
        raise HTTPException(
            status_code=400,
            detail="inbound live agent not enabled; set INBOUND_CALL_MODE=live_agent",
        )

    import json
    body_bytes = await request.body()
    try:
        data = json.loads(body_bytes)
    except json.JSONDecodeError:
        raise HTTPException(400, "invalid JSON")

    caller_phone = data.get("from_number") or data.get("caller_phone") or ""
    conversation_id = data.get("conversation_id") or ""
    normalised = normalise_phone(caller_phone)

    company_name = settings.voice_agent_company_name

    async with session_scope() as session:
        candidate = None
        active_app = None

        if normalised:
            candidate = (
                await session.execute(
                    select(Candidate).where(Candidate.phone == normalised)
                )
            ).scalar_one_or_none()

        if candidate:
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

        if not candidate or not active_app:
            # Unknown caller — return generic prompt
            system_prompt = (
                f"You are an AI assistant for {company_name}'s recruitment team. "
                "A caller has reached our recruitment line but we cannot identify "
                "their application. Be polite, collect their name and the role "
                "they applied for, and let them know HR will follow up via email. "
                "Keep the call under 2 minutes."
            )
            first_message = (
                f"Hello, thank you for calling {company_name}. "
                "I wasn't able to find your application right away. "
                "Could you tell me your name and which role you applied for? "
                "I'll make sure our HR team follows up with you."
            )

            await log_audit(
                session,
                action="inbound_call_initiation_unknown",
                details={
                    "caller_phone": normalised or caller_phone,
                    "conversation_id": conversation_id,
                },
            )

            return {
                "conversation_config_override": {
                    "agent": {
                        "prompt": {"prompt": system_prompt},
                        "first_message": first_message,
                        "language": "en",
                    },
                },
                "dynamic_variables": {
                    "company_name": company_name,
                    "mode": "general_query",
                },
            }

        # Known candidate — build full context
        ctx = await build_candidate_context(
            session, active_app.id, company_name
        )
        system_prompt, first_message = build_voice_prompt(
            kind=CallKind.GENERAL_QUERY, context=ctx, attempt_no=1
        )

        # Create a VoiceCall row so post-call webhook can find it
        voice_row = await create_voice_call(
            session,
            application_id=active_app.id,
            candidate_phone=normalised,
            questions=[],
            scheduled_at=None,
            attempt_no=1,
            provider="elevenlabs",
            call_kind=CallKind.GENERAL_QUERY.value,
        )

        await log_audit(
            session,
            candidate_id=candidate.id,
            application_id=active_app.id,
            action="inbound_call_initiation",
            details={
                "caller_phone": normalised,
                "conversation_id": conversation_id,
                "voice_call_id": str(voice_row.id),
                "current_stage": active_app.current_stage,
            },
        )

        voice_call_id = voice_row.id
        app_id = active_app.id
        candidate_id = candidate.id
        candidate_name_str = candidate.name
        current_stage = active_app.current_stage

        await publish_supervisor_event(
            session,
            EventType.CANDIDATE_MESSAGE_RECEIVED,
            application_id=app_id,
            candidate_id=candidate_id,
            payload={
                "channel": "phone_inbound",
                "caller_phone": normalised,
                "candidate_name": candidate_name_str,
                "current_stage": current_stage,
                "intent": "live_inbound_call",
                "voice_call_id": str(voice_call_id),
            },
            dedup_extra=f"inbound-init-{conversation_id or normalised}",
        )

    return {
        "conversation_config_override": {
            "agent": {
                "prompt": {"prompt": system_prompt},
                "first_message": first_message,
                "language": "en",
            },
        },
        "dynamic_variables": {
            "candidate_name": ctx.candidate_name,
            "role_title": ctx.role_title,
            "company_name": company_name,
            "application_id": str(app_id),
            "voice_call_id": str(voice_call_id),
            "mode": "general_query",
        },
    }
