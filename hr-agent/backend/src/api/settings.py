"""Read-only view of server-side settings for the dashboard Settings page.

Never expose secrets. This endpoint returns only flags, thresholds, and
non-secret identifiers. It DOES return whether a key is configured (bool)
but never the key value itself.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from src.api.auth import require_viewer
from src.config import get_settings
from src.services.rate_limit import snapshot

router = APIRouter(prefix="/dashboard/settings", tags=["settings"])


@router.get("")
async def get_ui_settings(
    _: Annotated[str, Depends(require_viewer)],
) -> dict[str, Any]:
    s = get_settings()
    usage = await snapshot()
    return {
        "app_env": s.app_env,
        "app_base_url": s.app_base_url,
        "llm_provider": s.llm_provider,
        "llm_model_fast": s.llm_model_fast,
        "llm_model_smart": s.llm_model_smart,
        "screening_url_ttl_days": s.screening_url_ttl_days,
        "data_retention_days_default": s.data_retention_days_default,
        "data_retention_days_talent_pool": s.data_retention_days_talent_pool,
        "dpo_contact_email": s.dpo_contact_email,
        "auto_approve_green_tier": s.auto_approve_green_tier,
        "enable_whatsapp": s.enable_whatsapp,
        "enable_sms_reminders": s.enable_sms_reminders,
        "channel_configured": {
            "openai": bool(s.openai_api_key),
            "elevenlabs_voice": bool(s.elevenlabs_api_key and s.elevenlabs_agent_id),
            "graph_email_send": bool(
                s.graph_tenant_id and s.graph_client_id and s.graph_client_secret
            ),
            "smtp_outbound": bool(s.smtp_host),
            "resend_fallback": bool(s.resend_api_key),
            "readai_meeting_bot": bool(s.read_ai_webhook_secret or s.read_ai_api_key),
            "imap_inbound": bool(s.mail_inboxes),
            "object_storage": bool(s.r2_endpoint_url and s.r2_access_key_id),
            "emotion_service": bool(s.emotion_model_endpoint and s.emotion_api_key),
            "langfuse_tracing": bool(s.langfuse_public_key and s.langfuse_secret_key),
            "sentry_errors": bool(s.sentry_dsn),
            "teams_alerts": bool(s.teams_webhook_alerts),
        },
        "pipeline_readiness": {
            "email_provider": s.email_provider,
            "meeting_bot_provider": s.meeting_bot_provider,
            "ready_for_prod_email": bool(
                s.graph_tenant_id and s.graph_client_id and s.graph_client_secret
            ),
            "ready_for_meeting_capture": bool(s.read_ai_webhook_secret),
            "ready_for_calendar_scheduling": bool(
                s.graph_tenant_id and s.graph_client_id and s.graph_client_secret
                and s.graph_organiser_email
            ),
        },
        "llm_limits": {
            "daily_call_limit": s.llm_daily_call_limit,
            "daily_token_limit": s.llm_daily_token_limit,
            "per_candidate_monthly_limit": s.llm_per_candidate_monthly_limit,
            "rate_limit_per_minute": s.llm_rate_limit_per_minute,
            "daily_budget_usd": s.llm_daily_budget_usd,
        },
        "llm_usage_today": {
            "calls_last_minute": usage.minute_calls,
            "calls_today": usage.day_calls,
            "tokens_today": usage.day_tokens,
            "usd_spent_today": round(usage.day_usd_spent, 4),
        },
    }


@router.get("/whoami")
async def whoami(
    role: Annotated[str, Depends(require_viewer)],
) -> dict[str, str]:
    return {"role": role}
