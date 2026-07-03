"""Read-only view of server-side settings for the dashboard Settings page.

Never expose secrets. This endpoint returns only flags, thresholds, and
non-secret identifiers. It DOES return whether a key is configured (bool)
but never the key value itself.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.auth import require_recruiter, require_viewer
from src.config import get_settings
from src.db.repositories.audit import log_audit
from src.llm.prompt_manager import invalidate_cache as invalidate_prompt_cache, get_prompt_sources
from src.services.rate_limit import snapshot

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard/settings", tags=["settings"])

# ---------------------------------------------------------------------------
# Prompt registry — names used across all activities / agents
# ---------------------------------------------------------------------------

PROMPT_REGISTRY: list[dict[str, str]] = [
    {"name": "voice_screen_gen", "label": "Voice Screen — Question Generation", "stage": "voice_screen"},
    {"name": "voice_screen_eval", "label": "Voice Screen — Evaluation", "stage": "voice_screen"},
    {"name": "fit_score", "label": "Fit Score", "stage": "fit_score"},
    {"name": "parse_resume", "label": "Resume Parsing", "stage": "parse"},
    {"name": "classify_email", "label": "Email Classification", "stage": "intake"},
    {"name": "screening_gen", "label": "Screening — Question Generation", "stage": "screening"},
    {"name": "screening_eval", "label": "Screening — Evaluation", "stage": "screening"},
    {"name": "rejection_message", "label": "Rejection Message", "stage": "rejection"},
    {"name": "meeting_analysis", "label": "Meeting Analysis", "stage": "interview"},
    {"name": "ceo_brief", "label": "CEO Brief", "stage": "ceo_interview"},
    {"name": "journey_report", "label": "Journey Report", "stage": "report"},
    {"name": "interview_report", "label": "Interview Report", "stage": "report"},
    {"name": "assignment_parse", "label": "Assignment Parsing", "stage": "assignment"},
    {"name": "extract_turn", "label": "Chat — Extract Turn Data", "stage": "chat_agent"},
    {"name": "tailored_qs", "label": "Chat — Tailored Questions", "stage": "chat_agent"},
    {"name": "assignment_gen", "label": "Assignment Generation", "stage": "assignment"},
    {"name": "jd_generation", "label": "JD Generation (Pulse)", "stage": "assignment"},
]


def _get_langfuse():
    from langfuse import Langfuse
    s = get_settings()
    if not (s.langfuse_public_key and s.langfuse_secret_key):
        return None
    return Langfuse(
        public_key=s.langfuse_public_key,
        secret_key=s.langfuse_secret_key,
        host=s.langfuse_host,
    )


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
        "pi_tests_enabled": s.pi_tests_enabled,
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


# ---------------------------------------------------------------------------
# Prompt management endpoints
# ---------------------------------------------------------------------------


@router.get("/prompts/status")
async def prompt_status(
    _: Annotated[str, Depends(require_viewer)],
) -> dict[str, Any]:
    from src.llm.client import _langfuse_enabled
    return {
        "langfuse_tracing_enabled": _langfuse_enabled,
        "prompt_sources": get_prompt_sources(),
    }


@router.get("/prompts")
async def list_prompts(
    _: Annotated[str, Depends(require_viewer)],
) -> list[dict[str, Any]]:
    """List all prompts with their current content and source."""
    lf = _get_langfuse()
    results = []
    for entry in PROMPT_REGISTRY:
        item: dict[str, Any] = {
            "name": entry["name"],
            "label": entry["label"],
            "stage": entry["stage"],
            "source": "unknown",
            "version": None,
            "content": "",
            "labels": [],
        }
        if lf:
            try:
                p = lf.get_prompt(entry["name"], label="production", type="text")
                item["source"] = "langfuse"
                item["version"] = p.version
                item["content"] = p.prompt
                item["labels"] = list(p.labels) if p.labels else ["production"]
            except Exception:
                item["source"] = "not_in_langfuse"
        results.append(item)
    return results


@router.get("/prompts/{prompt_name}")
async def get_prompt_detail(
    prompt_name: str,
    _: Annotated[str, Depends(require_viewer)],
) -> dict[str, Any]:
    """Get a single prompt's content and metadata."""
    lf = _get_langfuse()
    if not lf:
        raise HTTPException(status_code=503, detail="Langfuse not configured")
    try:
        p = lf.get_prompt(prompt_name, label="production", type="text")
        return {
            "name": p.name,
            "version": p.version,
            "labels": list(p.labels) if p.labels else [],
            "content": p.prompt,
        }
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Prompt '{prompt_name}' not found: {e}")


class PromptUpdate(BaseModel):
    content: str
    commit_message: str = ""


@router.put("/prompts/{prompt_name}")
async def update_prompt(
    prompt_name: str,
    payload: PromptUpdate,
    _: Annotated[str, Depends(require_recruiter)],
) -> dict[str, Any]:
    """Update a prompt in Langfuse (creates a new version)."""
    lf = _get_langfuse()
    if not lf:
        raise HTTPException(status_code=503, detail="Langfuse not configured")
    known = {e["name"] for e in PROMPT_REGISTRY}
    if prompt_name not in known:
        raise HTTPException(status_code=400, detail=f"Unknown prompt: {prompt_name}")
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="Prompt content cannot be empty")
    try:
        p = lf.create_prompt(
            name=prompt_name,
            prompt=payload.content,
            labels=["production", "latest"],
            type="text",
            commit_message=payload.commit_message or f"Updated via dashboard",
        )
        invalidate_prompt_cache(prompt_name)
        logger.info("Prompt '%s' updated to version %s", prompt_name, p.version)
        return {
            "name": p.name,
            "version": p.version,
            "labels": list(p.labels) if p.labels else [],
            "status": "ok",
        }
    except Exception as e:
        logger.error("Failed to update prompt '%s': %s", prompt_name, e)
        raise HTTPException(status_code=500, detail=f"Failed to update prompt: {e}")


@router.post("/prompts/refresh")
async def refresh_prompts(
    _: Annotated[str, Depends(require_viewer)],
) -> dict[str, str]:
    """Flush cached Langfuse prompts so next LLM call picks up latest edits."""
    invalidate_prompt_cache()
    return {"status": "ok", "detail": "Prompt cache cleared"}


# ---------------------------------------------------------------------------
# Org settings
# ---------------------------------------------------------------------------


class OrgSettingsWrite(BaseModel):
    name: str | None = None
    hiring_persona: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None


@router.get("/org")
async def get_org(
    _: Annotated[str, Depends(require_viewer)],
) -> dict[str, Any]:
    from src.db.connection import session_scope
    from src.db.repositories.organization import get_default

    async with session_scope() as session:
        org = await get_default(session)
        if org is None:
            raise HTTPException(status_code=404, detail="No org configured")
        return {
            "id": str(org.id),
            "name": org.name,
            "slug": org.slug,
            "hiring_persona": org.hiring_persona or {},
            "settings": org.settings or {},
        }


@router.put("/org")
async def update_org(
    payload: OrgSettingsWrite,
    _: Annotated[str, Depends(require_recruiter)],
) -> dict[str, Any]:
    from src.db.connection import session_scope
    from src.db.repositories.organization import get_default

    async with session_scope() as session:
        org = await get_default(session)
        if org is None:
            raise HTTPException(status_code=404, detail="No org configured")
        if payload.name is not None:
            org.name = payload.name
        if payload.hiring_persona is not None:
            org.hiring_persona = payload.hiring_persona
        if payload.settings is not None:
            org.settings = payload.settings
        await log_audit(
            session,
            action="org_updated",
            actor="dashboard",
            details={
                "fields": [
                    k for k, v in payload.model_dump(exclude_none=True).items()
                ]
            },
        )
        return {
            "id": str(org.id),
            "name": org.name,
            "slug": org.slug,
            "hiring_persona": org.hiring_persona or {},
            "settings": org.settings or {},
        }


@router.get("/whoami")
async def whoami(
    role: Annotated[str, Depends(require_viewer)],
) -> dict[str, str]:
    return {"role": role}
