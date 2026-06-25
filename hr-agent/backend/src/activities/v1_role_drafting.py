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
from src.llm.model_registry import Stage, model_for
from src.llm.prompt_manager import get_prompt
from src.llm.prompts.role_drafting import (
    ROLE_DRAFT_SYSTEM,
    ROLE_DRAFT_TURN_TEMPLATE,
    ROLE_DRAFT_VERSION,
    SECTION_REWRITE_SYSTEM,
    SECTION_REWRITE_TEMPLATE,
    SECTION_REWRITE_VERSION,
)
from src.services.linkedin_format import jd_to_linkedin_text

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
    from src.config import get_settings as _gs
    _company = _gs().voice_agent_company_name
    _sys = get_prompt("role_draft_system", fallback=ROLE_DRAFT_SYSTEM)
    try:
        _sys = _sys.format(company_name=_company)
    except (KeyError, IndexError):
        _sys = _sys.replace("{company_name}", _company)

    client = get_llm_client()
    result = await client.complete(
        prompt=prompt,
        response_model=RoleDraftEnvelope,
        model=model_for(Stage.ROLE_DRAFT_CHAT),
        trace_name="role_draft_chat",
        prompt_version=ROLE_DRAFT_VERSION,
        system=_sys,
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
        model=model_for(Stage.ROLE_SECTION_REWRITE),
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
    # The JD is already the LinkedIn post (vibey, human, with How-to-apply block).
    # Deterministically strip markdown so it renders cleanly on LinkedIn.
    # No LLM call needed -- the content stays 100% faithful to what was drafted.
    post_text = jd_to_linkedin_text(jd_text)

    # Derive headline from the first non-empty line of the plain-text post,
    # falling back to the role title.
    headline = title
    for line in post_text.splitlines():
        stripped = line.strip()
        if stripped:
            headline = stripped
            break

    return LinkedInPost(
        post_text=post_text,
        hashtags=[],
        headline=headline,
    )
