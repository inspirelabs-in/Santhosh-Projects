"""Conversational role drafting activity.

Each chat turn rebuilds the role draft from the user's free-text and the
current snapshot. Called from ``api/role_drafting.py``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from src.llm.client import get_llm_client
from src.llm.prompt_manager import get_prompt
from src.llm.prompts.role_drafting import (
    LINKEDIN_POST_SYSTEM,
    LINKEDIN_POST_TURN_TEMPLATE,
    LINKEDIN_POST_VERSION,
    ROLE_DRAFT_SYSTEM,
    ROLE_DRAFT_TURN_TEMPLATE,
    ROLE_DRAFT_VERSION,
    SECTION_REWRITE_SYSTEM,
    SECTION_REWRITE_TEMPLATE,
    SECTION_REWRITE_VERSION,
)

logger = logging.getLogger(__name__)


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class RoleDraftEnvelope(BaseModel):
    """LLM-validated shape returned each turn."""

    message: str
    draft: dict[str, Any] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)
    quick_replies: list[str] = Field(default_factory=list)
    ready_to_save: bool = False
    assignment: dict[str, Any] | None = None


def _format_conversation(messages: list[ChatMessage], *, max_msgs: int = 14, per_msg_cap: int = 1500) -> str:
    if not messages:
        return "(no prior messages)"
    return "\n".join(
        f"{m.role.upper()}: {m.content[:per_msg_cap]}" for m in messages[-max_msgs:]
    )


async def chat_role_draft(
    *,
    user_message: str,
    history: list[ChatMessage],
    current_draft: dict[str, Any],
    memory: dict[str, Any] | None = None,
) -> RoleDraftEnvelope:
    prompt = ROLE_DRAFT_TURN_TEMPLATE.format(
        memory_json=json.dumps(memory or {}, ensure_ascii=False)[:3000],
        draft_json=json.dumps(current_draft or {}, ensure_ascii=False)[:12000],
        conversation=_format_conversation(history),
        user_message=user_message[:3000],
    )
    client = get_llm_client()
    result = await client.complete(
        prompt=prompt,
        response_model=RoleDraftEnvelope,
        model=client.smart,
        trace_name="role_draft_chat",
        prompt_version=ROLE_DRAFT_VERSION,
        system=get_prompt("role_draft_system", fallback=ROLE_DRAFT_SYSTEM),
        max_tokens=2000,
    )
    return result.parsed


# ---------------------------------------------------------------------------
# LinkedIn post generator
# ---------------------------------------------------------------------------


class LinkedInPost(BaseModel):
    post_text: str
    hashtags: list[str] = Field(default_factory=list)
    headline: str


class SectionBody(BaseModel):
    body: str


async def rewrite_section(
    *,
    section: str,
    current_body: str,
    role_context: dict[str, Any],
    user_directive: str | None,
) -> SectionBody:
    """Redraft a single JD section. Used by the inline 'edit with AI' UX."""

    prompt = SECTION_REWRITE_TEMPLATE.format(
        section=section,
        current_body=current_body[:4000],
        role_context=json.dumps(role_context, ensure_ascii=False)[:3000],
        user_directive=(user_directive or "(none)")[:600],
    )
    client = get_llm_client()
    result = await client.complete(
        prompt=prompt,
        response_model=SectionBody,
        model=client.smart,
        trace_name="role_section_rewrite",
        prompt_version=SECTION_REWRITE_VERSION,
        system=get_prompt("section_rewrite_system", fallback=SECTION_REWRITE_SYSTEM),
        max_tokens=900,
    )
    return result.parsed


async def generate_linkedin_post(
    *,
    title: str,
    jd_text: str,
    location: str | None,
    remote_policy: str | None,
    ctc_min_lpa: float | None,
    ctc_max_lpa: float | None,
    apply_url: str | None,
) -> LinkedInPost:
    prompt = LINKEDIN_POST_TURN_TEMPLATE.format(
        title=title,
        location=location or "not specified",
        remote_policy=remote_policy or "n/a",
        ctc_min_lpa=ctc_min_lpa if ctc_min_lpa is not None else "n/a",
        ctc_max_lpa=ctc_max_lpa if ctc_max_lpa is not None else "n/a",
        jd_text=(jd_text or "")[:5000],
        apply_url=apply_url or "(not provided)",
    )
    client = get_llm_client()
    result = await client.complete(
        prompt=prompt,
        response_model=LinkedInPost,
        model=client.smart,
        trace_name="linkedin_post",
        prompt_version=LINKEDIN_POST_VERSION,
        system=get_prompt("linkedin_post_system", fallback=LINKEDIN_POST_SYSTEM),
        max_tokens=900,
    )
    return result.parsed
