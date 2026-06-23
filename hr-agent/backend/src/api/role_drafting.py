"""HR-facing endpoints for chat-driven role creation.

  POST /agentic/roles/chat            -- one conversational turn
  GET  /agentic/roles/defaults        -- panel + tz + duration suggestions
                                         derived from previously-saved roles
  POST /agentic/roles/linkedin-post   -- generate LinkedIn copy from a draft

The chat endpoint is stateless on the server side -- the frontend keeps
the message history + current draft and ships them on every turn. Keeps
horizontal scaling simple and lets a recruiter close the tab and resume
later by replaying the cached state.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from src.activities.v1_role_drafting import (
    ChatMessage,
    LinkedInPost,
    RoleDraftEnvelope,
    SectionBody,
    chat_role_draft,
    generate_linkedin_post,
    rewrite_section,
)
from src.api.auth import require_recruiter
from src.db.base import Role
from src.db.connection import session_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agentic/roles", tags=["agentic-roles"])


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


class ChatTurnBody(BaseModel):
    user_message: str = Field(min_length=1)
    history: list[ChatMessage] = Field(default_factory=list)
    draft: dict[str, Any] = Field(default_factory=dict)
    memory_override: dict[str, Any] | None = None  # optional, frontend can prepend


@router.post("/chat", response_model=RoleDraftEnvelope)
async def chat(
    body: ChatTurnBody,
    _: Annotated[str, Depends(require_recruiter)],
) -> RoleDraftEnvelope:
    memory = body.memory_override or await _build_role_memory()
    try:
        result = await chat_role_draft(
            user_message=body.user_message,
            history=body.history,
            current_draft=body.draft,
            memory=memory,
        )
    except Exception as e:  # noqa: BLE001
        from src.config import get_settings as _gs

        provider = (_gs().llm_provider or "llm").lower()
        msg = str(e)
        low = msg.lower()
        if "ratelimiterror" in low or "rate limit" in low or "exceeded your current quota" in low:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"{provider.capitalize()} rate-limit / quota hit — wait a minute, top up the account, "
                f"or switch LLM_PROVIDER + LLM_MODEL_FAST/SMART in backend/.env. Raw: {msg[:160]}",
            )
        if "authenticationerror" in low or "invalid api key" in low or "incorrect api key" in low:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"{provider.capitalize()} rejected the API key — check the matching *_API_KEY in backend/.env.",
            )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"LLM call failed ({provider}): {msg[:200]}",
        )

    result = await _maybe_generate_assignment(result, body.draft)
    return result


async def _maybe_generate_assignment(
    result: RoleDraftEnvelope,
    prev_draft: dict[str, Any],
) -> RoleDraftEnvelope:
    """Auto-generate assignment problems when JD first appears in draft.

    Runs once: when the response draft has jd_text + title but the
    previous draft did not have a generated assignment yet. The result
    is injected into both ``result.assignment`` (for the frontend card)
    and ``result.draft`` (so it round-trips on subsequent turns).
    """
    draft = result.draft or {}
    jd_text = (draft.get("jd_text") or "").strip()
    title = (draft.get("title") or "").strip()

    if not jd_text or not title:
        return result

    if prev_draft.get("_assignment_generated"):
        result.assignment = prev_draft.get("_assignment_data")
        draft["_assignment_generated"] = True
        draft["_assignment_data"] = prev_draft.get("_assignment_data")
        return result

    try:
        from src.agent.generators import gen_assignment

        synthetic_id = uuid4()
        brief = await gen_assignment(
            role_title=title,
            jd_text=jd_text,
            time_budget_hours=int(draft.get("assignment_deadline_days") or 6),
            deadline_days=int(draft.get("assignment_deadline_days") or 7),
            application_id=synthetic_id,
            candidate_id=synthetic_id,
        )
        payload = brief.model_dump()
        result.assignment = payload
        draft["_assignment_generated"] = True
        draft["_assignment_data"] = payload
        draft["assignment_brief"] = payload.get("brief_md")
    except Exception:
        logger.warning("auto assignment generation failed", exc_info=True)

    return result


# ---------------------------------------------------------------------------
# Defaults memory -- pull common values from existing roles
# ---------------------------------------------------------------------------


class RoleDefaults(BaseModel):
    common_remote_policy: str | None
    common_locations: list[str]
    common_ctc_min: float | None
    common_ctc_max: float | None


@router.get("/defaults", response_model=RoleDefaults)
async def role_defaults(
    _: Annotated[str, Depends(require_recruiter)],
) -> RoleDefaults:
    return await _build_role_memory_typed()


async def _build_role_memory() -> dict[str, Any]:
    typed = await _build_role_memory_typed()
    return typed.model_dump(mode="json")


async def _build_role_memory_typed() -> RoleDefaults:
    locations: Counter[str] = Counter()
    remotes: Counter[str] = Counter()
    ctc_mins: list[float] = []
    ctc_maxs: list[float] = []

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Role).order_by(desc(Role.created_at)).limit(50)
            )
        ).scalars().all()

    for r in rows:
        if r.location:
            locations[r.location] += 1
        if r.remote_policy:
            remotes[r.remote_policy] += 1
        if r.ctc_min_lpa is not None:
            ctc_mins.append(float(r.ctc_min_lpa))
        if r.ctc_max_lpa is not None:
            ctc_maxs.append(float(r.ctc_max_lpa))

    def top(c: Counter[str], n: int = 5) -> list[str]:
        return [k for k, _ in c.most_common(n)]

    return RoleDefaults(
        common_remote_policy=remotes.most_common(1)[0][0] if remotes else None,
        common_locations=top(locations),
        common_ctc_min=min(ctc_mins) if ctc_mins else None,
        common_ctc_max=max(ctc_maxs) if ctc_maxs else None,
    )


# ---------------------------------------------------------------------------
# LinkedIn post
# ---------------------------------------------------------------------------


class SectionRewriteBody(BaseModel):
    section: str
    current_body: str
    role_context: dict[str, Any] = Field(default_factory=dict)
    user_directive: str | None = None


@router.post("/section-rewrite", response_model=SectionBody)
async def section_rewrite(
    body: SectionRewriteBody,
    _: Annotated[str, Depends(require_recruiter)],
) -> SectionBody:
    if not body.section.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "section is required")
    return await rewrite_section(
        section=body.section,
        current_body=body.current_body,
        role_context=body.role_context,
        user_directive=body.user_directive,
    )


class LinkedInPostBody(BaseModel):
    title: str
    jd_text: str
    location: str | None = None
    remote_policy: str | None = None
    ctc_min_lpa: float | None = None
    ctc_max_lpa: float | None = None
    apply_url: str | None = None


@router.post("/linkedin-post", response_model=LinkedInPost)
async def linkedin_post(
    body: LinkedInPostBody,
    _: Annotated[str, Depends(require_recruiter)],
) -> LinkedInPost:
    if not body.title.strip() or not body.jd_text.strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "title and jd_text are required"
        )
    return await generate_linkedin_post(
        title=body.title,
        jd_text=body.jd_text,
        location=body.location,
        remote_policy=body.remote_policy,
        ctc_min_lpa=body.ctc_min_lpa,
        ctc_max_lpa=body.ctc_max_lpa,
        apply_url=body.apply_url,
    )
