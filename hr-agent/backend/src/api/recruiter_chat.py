"""Recruiter-side chat API.

Endpoints (all under ``/v2/recruiter-chat``, dashboard-key authed):

    GET    /conversations                  -> list conversations for actor
    POST   /conversations                  -> create new conversation
    GET    /conversations/{id}             -> conversation header + messages
    DELETE /conversations/{id}             -> archive
    PATCH  /conversations/{id}             -> rename
    POST   /conversations/{id}/messages    -> queue user message (SSE picks up)
    GET    /conversations/{id}/stream      -> SSE; runs an agent turn per
                                              inbound message
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.api.auth import require_recruiter, require_viewer
from src.db.connection import get_redis, session_scope
from src.db.repositories import recruiter_chat as repo
from src.db.repositories.recruiter_chat import hash_actor
from src.recruiter_agent.runner import run_recruiter_turn
from src.db.base import Role, RolePipelineStage
from src.db.repositories import artifact as artifact_repo
from src.db.repositories import organization as org_repo
from src.db.repositories import role_pipeline_stage as stage_repo
from src.models.artifacts import ArtifactStatus, RoleDraftContent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v2/recruiter-chat", tags=["recruiter-chat"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ConversationSummary(BaseModel):
    id: UUID
    title: str | None
    archived: bool
    created_at: datetime
    updated_at: datetime


class ConversationDetail(BaseModel):
    id: UUID
    title: str | None
    archived: bool
    created_at: datetime
    updated_at: datetime
    messages: list[dict[str, Any]]


class CreateConversationBody(BaseModel):
    title: str | None = Field(default=None, max_length=255)


class RenameBody(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class SendMessageBody(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _actor(
    x_dashboard_key: str | None = Header(default=None, alias="X-Dashboard-Key"),
    key_query: str | None = Query(default=None, alias="key"),
) -> tuple[str, str]:
    """Resolve actor for both header-authed (POST) and query-authed (SSE).

    EventSource cannot send custom headers, so the SSE handler accepts a
    ``?key=...`` query param. Other endpoints require ``X-Dashboard-Key``.
    Both paths run through the same key validator.
    """
    from src.api.auth import _load_keys  # type: ignore[attr-defined]

    raw = (x_dashboard_key or key_query or "").strip()
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing dashboard key")
    keys = _load_keys()
    role = keys.get(raw)
    if role is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid dashboard key")
    return hash_actor(raw), role


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("/conversations", response_model=list[ConversationSummary], dependencies=[Depends(require_viewer)])
async def list_my_conversations(
    actor: tuple[str, str] = Depends(_actor),
    include_archived: bool = Query(default=False),
) -> list[ConversationSummary]:
    actor_hash, _ = actor
    async with session_scope() as session:
        rows = await repo.list_conversations(
            session, actor_hash=actor_hash, include_archived=include_archived
        )
    return [
        ConversationSummary(
            id=c.id,
            title=c.title,
            archived=c.archived,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in rows
    ]


@router.post("/conversations", response_model=ConversationSummary, dependencies=[Depends(require_viewer)])
async def create_conversation(
    body: Annotated[CreateConversationBody, Body()],
    actor: tuple[str, str] = Depends(_actor),
) -> ConversationSummary:
    actor_hash, role = actor
    async with session_scope() as session:
        conv = await repo.create_conversation(
            session, actor_hash=actor_hash, actor_role=role, title=body.title
        )
    return ConversationSummary(
        id=conv.id,
        title=conv.title,
        archived=conv.archived,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetail,
    dependencies=[Depends(require_viewer)],
)
async def get_conversation_detail(
    conversation_id: UUID,
    actor: tuple[str, str] = Depends(_actor),
) -> ConversationDetail:
    actor_hash, _ = actor
    async with session_scope() as session:
        conv = await repo.get_conversation(
            session, conversation_id=conversation_id, actor_hash=actor_hash
        )
        if conv is None:
            raise HTTPException(404, "conversation_not_found")
        msgs = await repo.list_messages(session, conversation_id)
    return ConversationDetail(
        id=conv.id,
        title=conv.title,
        archived=conv.archived,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=[
            {
                "sequence": m.sequence,
                "role": m.role,
                "content": m.content,
                "tool_name": m.tool_name,
                "tool_calls": m.tool_calls,
                "tool_result": m.tool_result,
                "attachments": m.attachments,
                "created_at": m.created_at,
            }
            for m in msgs
        ],
    )


@router.delete("/conversations/{conversation_id}", dependencies=[Depends(require_recruiter)])
async def delete_conversation(
    conversation_id: UUID,
    actor: tuple[str, str] = Depends(_actor),
) -> dict:
    actor_hash, _ = actor
    async with session_scope() as session:
        ok = await repo.archive(
            session, conversation_id=conversation_id, actor_hash=actor_hash
        )
    if not ok:
        raise HTTPException(404, "conversation_not_found")
    return {"ok": True}


class NudgeToggleBody(BaseModel):
    enabled: bool


@router.patch(
    "/conversations/{conversation_id}/nudges",
    dependencies=[Depends(require_recruiter)],
)
async def toggle_nudges(
    conversation_id: UUID,
    body: Annotated[NudgeToggleBody, Body()],
    actor: tuple[str, str] = Depends(_actor),
) -> dict:
    actor_hash, _ = actor
    async with session_scope() as session:
        conv = await repo.get_conversation(
            session, conversation_id=conversation_id, actor_hash=actor_hash
        )
        if conv is None:
            raise HTTPException(404, "conversation_not_found")
        await repo.update_state(
            session,
            conversation_id,
            state_patch={"nudges_enabled": bool(body.enabled)},
        )
    return {"ok": True, "nudges_enabled": bool(body.enabled)}


@router.patch("/conversations/{conversation_id}", dependencies=[Depends(require_recruiter)])
async def rename_conversation(
    conversation_id: UUID,
    body: Annotated[RenameBody, Body()],
    actor: tuple[str, str] = Depends(_actor),
) -> dict:
    actor_hash, _ = actor
    async with session_scope() as session:
        ok = await repo.rename(
            session,
            conversation_id=conversation_id,
            actor_hash=actor_hash,
            title=body.title,
        )
    if not ok:
        raise HTTPException(404, "conversation_not_found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Artifacts (editable side-panel: role draft etc.)
# ---------------------------------------------------------------------------


class ArtifactUpdateBody(BaseModel):
    content: dict[str, Any]
    title: str | None = Field(default=None, max_length=255)


def _artifact_payload(art: Any) -> dict[str, Any]:
    return {
        "id": str(art.id),
        "conversation_id": str(art.conversation_id),
        "type": art.type,
        "status": art.status,
        "title": art.title,
        "content": art.content,
        "version": art.version,
    }


async def _owned_artifact(session, artifact_id: UUID, actor_hash: str):
    art = await artifact_repo.get(session, artifact_id)
    if art is None:
        raise HTTPException(404, "artifact_not_found")
    conv = await repo.get_conversation(
        session, conversation_id=art.conversation_id, actor_hash=actor_hash
    )
    if conv is None:
        raise HTTPException(404, "artifact_not_found")
    return art


@router.get(
    "/conversations/{conversation_id}/artifact",
    dependencies=[Depends(require_viewer)],
)
async def get_active_artifact(
    conversation_id: UUID,
    actor: tuple[str, str] = Depends(_actor),
) -> dict:
    actor_hash, _ = actor
    async with session_scope() as session:
        conv = await repo.get_conversation(
            session, conversation_id=conversation_id, actor_hash=actor_hash
        )
        if conv is None:
            raise HTTPException(404, "conversation_not_found")
        art = await artifact_repo.get_active_for_conversation(session, conversation_id)
        return {"artifact": _artifact_payload(art) if art else None}


@router.patch("/artifacts/{artifact_id}", dependencies=[Depends(require_recruiter)])
async def update_artifact(
    artifact_id: UUID,
    body: Annotated[ArtifactUpdateBody, Body()],
    actor: tuple[str, str] = Depends(_actor),
) -> dict:
    """Human edit from the panel: replace the artifact content (+ bump version)."""
    actor_hash, _ = actor
    async with session_scope() as session:
        await _owned_artifact(session, artifact_id, actor_hash)
        art = await artifact_repo.update_content(
            session, artifact_id, body.content, title=body.title
        )
        return {"ok": True, "artifact": _artifact_payload(art)}


@router.post("/artifacts/{artifact_id}/apply", dependencies=[Depends(require_recruiter)])
async def apply_artifact(
    artifact_id: UUID,
    actor: tuple[str, str] = Depends(_actor),
) -> dict:
    """Apply a role_draft: create the Role (+ evaluation_spec), seed the pipeline
    stages, and generate the assignment. Marks the artifact applied."""
    actor_hash, _ = actor
    async with session_scope() as session:
        art = await _owned_artifact(session, artifact_id, actor_hash)
        if art.type != "role_draft":
            raise HTTPException(400, "unsupported_artifact_type")
        if art.status == ArtifactStatus.APPLIED.value:
            raise HTTPException(409, "already_applied")
        try:
            draft = RoleDraftContent.model_validate(art.content or {})
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"invalid_role_draft: {e}")
        if not draft.title or not draft.jd_text:
            raise HTTPException(422, "title_and_jd_required")

        org = await org_repo.get_default(session)
        org_id = org.id if org else None
        role = Role(
            org_id=org_id,
            title=draft.title,
            jd_text=draft.jd_text,
            screening_questions=[],
            scoring_rubric={},
            interviewer_panel=[],
            ctc_min_lpa=draft.ctc_min_lpa,
            ctc_max_lpa=draft.ctc_max_lpa,
            location=draft.location,
            remote_policy=draft.remote_policy,
            max_notice_days=draft.max_notice_days,
            evaluation_spec=draft.evaluation_spec.model_dump(mode="json"),
            company_context=draft.company_context.model_dump(mode="json"),
        )
        # Extract assignment info from artifact; brief will be LLM-generated
        # via ensure_role_assignment, not stored raw.
        _captured_brief = (draft.assignment.brief or "").strip() if draft.assignment else ""
        _captured_instructions = (draft.assignment.instructions or "").strip() if draft.assignment else ""
        _assignment_n_problems = draft.assignment.n_problems if draft.assignment else 2
        _assignment_time_budget = draft.assignment.time_budget_hours if draft.assignment else 6
        _assignment_deadline_days = draft.assignment.deadline_days if draft.assignment else 7
        role.status = "open"
        session.add(role)
        await session.flush()
        role_id = role.id

        # Prepend mandatory pre-stages (intake, parse, fit) that always run
        # automatically, then append the draft's pipeline from voice_screen onwards.
        _mandatory_pipeline = [
            {"stage_key": "intake", "stage_type": "intake", "label": "Intake", "mode": "auto"},
            {"stage_key": "parse", "stage_type": "parse", "label": "Resume Parse", "mode": "auto"},
            {"stage_key": "fit", "stage_type": "fit", "label": "Fit Score", "mode": "auto"},
        ]
        seen_keys: set[str] = set()
        for pos, entry in enumerate(_mandatory_pipeline):
            seen_keys.add(entry["stage_key"])
            session.add(
                RolePipelineStage(
                    role_id=role_id,
                    org_id=org_id,
                    position=pos,
                    **entry,
                )
            )
        _pos = len(_mandatory_pipeline)
        if draft.pipeline:
            for st in draft.pipeline:
                if st.stage_key in seen_keys:
                    continue
                seen_keys.add(st.stage_key)
                session.add(
                    RolePipelineStage(
                        role_id=role_id,
                        org_id=org_id,
                        position=_pos,
                        stage_type=str(st.stage_type),
                        stage_key=st.stage_key,
                        label=st.label,
                        mode=str(st.mode),
                        is_enabled=st.is_enabled,
                        config=st.config.model_dump(mode="json"),
                        eval_spec=st.eval_spec.model_dump(mode="json"),
                    )
                )
                _pos += 1
        else:
            await stage_repo.seed_default(session, role_id=role_id, org_id=org_id)

        await artifact_repo.set_status(session, artifact_id, ArtifactStatus.APPLIED)
        conversation_id = art.conversation_id
        role_title = draft.title

    # Generate a proper structured assignment via LLM when the pipeline
    # has an assignment stage. The user's brief from the artifact is passed
    # as seed requirements so the LLM produces relevant problems + a real PDF.
    _assignment_generated = False
    _has_assignment_stage = bool(
        draft.pipeline and any(
            str(st.stage_type) == "assignment" and st.is_enabled
            for st in draft.pipeline
        )
    ) if draft.pipeline else False
    if _has_assignment_stage:
        from src.recruiter_agent.tools import ensure_role_assignment
        try:
            _gen = await ensure_role_assignment(
                role_id=str(role_id),
                n_problems=_assignment_n_problems,
                time_budget_hours=_assignment_time_budget,
                deadline_days=_assignment_deadline_days,
                source="artifact_apply",
                user_brief=_captured_brief or None,
            )
            _assignment_generated = _gen.get("ok", False)
        except Exception:  # noqa: BLE001
            logger.warning("assignment auto-gen failed for role %s", role_id)

        # If the artifact had custom instructions, overwrite LLM-generated ones.
        if _captured_instructions and _assignment_generated:
            async with session_scope() as session:
                r = await session.get(Role, UUID(role_id))
                if r is not None:
                    r.assignment_instructions = _captured_instructions

    status_line = "It's open for applications."
    if _assignment_generated:
        if _captured_brief:
            status_line += " Take-home generated from your requirements."
        else:
            status_line += " Take-home assignment auto-generated."
    follow_up = (
        f"Created '{role_title}'. {status_line} "
        "Want me to draft a LinkedIn post for this role?"
    )
    try:
        async with session_scope() as session:
            await repo.append_message(
                session,
                conversation_id=conversation_id,
                role="assistant",
                content=follow_up,
            )
    except Exception:  # noqa: BLE001
        logger.warning("failed to persist apply follow-up message", exc_info=True)

    return {
        "ok": True,
        "role_id": str(role_id),
        "role_url": f"/roles/{role_id}",
        "status": "open",
        "follow_up": follow_up,
    }


# ---------------------------------------------------------------------------
# Send message + SSE stream
# ---------------------------------------------------------------------------


def _cancel_key(conv: UUID) -> str:
    return f"recruiter-chat:{conv}:cancel"


def _inbox_key(conv: UUID) -> str:
    return f"recruiter-chat:{conv}:inbox"


# ---------------------------------------------------------------------------
# Phase 7: file upload (PDF, DOCX, text) for the composer
# ---------------------------------------------------------------------------


@router.post(
    "/conversations/{conversation_id}/upload",
    dependencies=[Depends(require_recruiter)],
)
async def upload_file(
    conversation_id: UUID,
    file: Annotated[UploadFile, File(...)],
    actor: tuple[str, str] = Depends(_actor),
) -> dict:
    actor_hash, _ = actor
    async with session_scope() as session:
        conv = await repo.get_conversation(
            session, conversation_id=conversation_id, actor_hash=actor_hash
        )
        if conv is None:
            raise HTTPException(404, "conversation_not_found")

    if file.size and file.size > 25 * 1024 * 1024:
        raise HTTPException(413, "file_too_large")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty_file")

    from src.config import get_settings as _gs
    from src.services.file_storage import upload_blob

    settings = _gs()
    safe = (file.filename or "upload.bin").replace("/", "_").replace("\\", "_")
    key = f"recruiter-chat/{conversation_id}/{datetime.now(UTC):%Y%m%dT%H%M%S}_{safe}"
    await upload_blob(
        bucket=settings.r2_bucket_resumes,
        key=key,
        content=raw,
        content_type=file.content_type or "application/octet-stream",
    )
    return {
        "file_ref": key,
        "filename": safe,
        "size": len(raw),
        "content_type": file.content_type,
    }


# ---------------------------------------------------------------------------
# Phase 8: cancel + edit-and-resend
# ---------------------------------------------------------------------------


@router.post(
    "/conversations/{conversation_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_recruiter)],
)
async def cancel_stream(
    conversation_id: UUID,
    actor: tuple[str, str] = Depends(_actor),
) -> dict:
    """Set the cancel flag in Redis. The runner checks this between hops +
    streaming chunks; if set, persists partial output and ends cleanly.
    """
    actor_hash, _ = actor
    async with session_scope() as session:
        conv = await repo.get_conversation(
            session, conversation_id=conversation_id, actor_hash=actor_hash
        )
        if conv is None:
            raise HTTPException(404, "conversation_not_found")
    await get_redis().setex(_cancel_key(conversation_id), 30, "1")
    return {"ok": True}


class EditMessageBody(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


@router.patch(
    "/conversations/{conversation_id}/messages/{sequence}",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_recruiter)],
)
async def edit_and_resend(
    conversation_id: UUID,
    sequence: int,
    body: Annotated[EditMessageBody, Body()],
    actor: tuple[str, str] = Depends(_actor),
) -> dict:
    """Tombstone the target user message + everything after, then enqueue a
    fresh user message with the new content.
    """
    actor_hash, _ = actor
    from sqlalchemy import update

    from src.db.base import RecruiterMessage

    async with session_scope() as session:
        conv = await repo.get_conversation(
            session, conversation_id=conversation_id, actor_hash=actor_hash
        )
        if conv is None:
            raise HTTPException(404, "conversation_not_found")
        await session.execute(
            update(RecruiterMessage)
            .where(RecruiterMessage.conversation_id == conversation_id)
            .where(RecruiterMessage.sequence >= sequence)
            .values(tombstoned=True)
        )

    # Enqueue replacement.
    await get_redis().rpush(
        _inbox_key(conversation_id),
        json.dumps({"content": body.content, "ts": datetime.now(UTC).isoformat()}),
    )
    return {"ok": True}


class ConfirmBody(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    accept: bool = True
    edited_args: dict[str, Any] | None = None


class ToolResultBody(BaseModel):
    tool_use_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=4000)


@router.post(
    "/conversations/{conversation_id}/tool-result",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_recruiter)],
)
async def send_tool_result(
    conversation_id: UUID,
    body: Annotated[ToolResultBody, Body()],
    actor: tuple[str, str] = Depends(_actor),
) -> dict:
    """Receive a give_choice tool_result from the frontend."""
    actor_hash, _ = actor
    async with session_scope() as session:
        conv = await repo.get_conversation(
            session, conversation_id=conversation_id, actor_hash=actor_hash
        )
        if conv is None:
            raise HTTPException(404, "conversation_not_found")
        # Persist the tool result message.
        await repo.append_message(
            session,
            conversation_id=conversation_id,
            role="tool",
            tool_name="give_choice",
            tool_calls=[{"id": body.tool_use_id}],
            tool_result=body.content,
        )
    # Wake the SSE loop so it picks up the new tool_result and runs the next turn.
    redis = get_redis()
    await redis.rpush(
        _inbox_key(conversation_id),
        json.dumps({"content": "__quick_reply_wake__", "ts": datetime.now(UTC).isoformat()}),
    )
    await redis.ltrim(_inbox_key(conversation_id), -50, -1)
    return {"queued": True}


@router.post(
    "/conversations/{conversation_id}/confirm",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_recruiter)],
)
async def confirm_pending(
    conversation_id: UUID,
    body: Annotated[ConfirmBody, Body()],
    actor: tuple[str, str] = Depends(_actor),
) -> dict:
    """Resolve a pending confirm card.

    Accepting enqueues a synthetic user message ``__pulse_confirm__:<id>`` to
    the inbox; the SSE handler picks it up and the runner short-circuits to
    execute the saved tool call. Rejecting deletes the pending entry and
    enqueues a plain message so the agent can acknowledge cleanly.
    """
    actor_hash, _ = actor
    async with session_scope() as session:
        conv = await repo.get_conversation(
            session, conversation_id=conversation_id, actor_hash=actor_hash
        )
        if conv is None:
            raise HTTPException(404, "conversation_not_found")

    redis = get_redis()
    if body.accept:
        if body.edited_args:
            confirm_key = f"recruiter-confirm:{conversation_id}:{body.request_id}"
            raw = await redis.get(confirm_key)
            if raw:
                payload = json.loads(raw)
                payload["args"].update(body.edited_args)
                await redis.setex(confirm_key, 600, json.dumps(payload))
        await redis.rpush(
            _inbox_key(conversation_id),
            json.dumps(
                {
                    "content": f"__pulse_confirm__:{body.request_id}",
                    "ts": datetime.now(UTC).isoformat(),
                }
            ),
        )
    else:
        # Drop the Redis pending entry + tell the agent.
        await redis.delete(f"recruiter-confirm:{conversation_id}:{body.request_id}")
        await redis.rpush(
            _inbox_key(conversation_id),
            json.dumps(
                {
                    "content": f"I cancelled the pending action ({body.request_id}). Move on.",
                    "ts": datetime.now(UTC).isoformat(),
                }
            ),
        )
    return {"ok": True}


@router.post(
    "/conversations/{conversation_id}/messages",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_recruiter)],
)
async def send_message(
    conversation_id: UUID,
    body: Annotated[SendMessageBody, Body()],
    actor: tuple[str, str] = Depends(_actor),
) -> dict:
    actor_hash, _ = actor
    async with session_scope() as session:
        conv = await repo.get_conversation(
            session, conversation_id=conversation_id, actor_hash=actor_hash
        )
        if conv is None:
            raise HTTPException(404, "conversation_not_found")

    redis = get_redis()
    await redis.rpush(
        _inbox_key(conversation_id),
        json.dumps({"content": body.content, "ts": datetime.now(UTC).isoformat()}),
    )
    await redis.ltrim(_inbox_key(conversation_id), -50, -1)
    return {"queued": True}


def _sse(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event, default=str)}\n\n".encode("utf-8")


async def _sse_loop(conversation_id: UUID, actor_hash: str):
    """Yield SSE events. Two sources:

    1. ``recruiter-chat:{id}:inbox`` Redis list (user messages) -> runs an
       agent turn.
    2. ``recruiter-chat:{id}:nudge`` Redis pubsub (proactive nudges) -> emits
       an attachment event directly.
    """
    redis = get_redis()
    nudge_client = redis  # reuse same connection pool
    pubsub = nudge_client.pubsub()
    await pubsub.subscribe(f"recruiter-chat:{conversation_id}:nudge")
    yield b": connected\n\n"

    # Drain any messages already queued before this stream connected. The
    # frontend's first send + new-conversation flow can land a message in
    # the inbox a few ms before the SSE handler attaches; without this
    # eager drain, that message would sit waiting for the next BLPOP cycle.
    while True:
        item = await redis.lpop(_inbox_key(conversation_id))
        if item is None:
            break
        try:
            payload = json.loads(item)
        except json.JSONDecodeError:
            continue
        content = (payload.get("content") or "").strip()
        if not content:
            continue
        async for ev in run_recruiter_turn(
            conversation_id=conversation_id, user_message=content
        ):
            yield _sse(ev)

    async def _drain_pubsub():
        """Yields nudge events without blocking the inbox loop."""
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
        if msg is None:
            return None
        data = msg.get("data")
        if not data:
            return None
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None

    try:
        while True:
            # Tight loop: 1s blocking pop on inbox, then check nudge pubsub.
            item = await redis.blpop(_inbox_key(conversation_id), timeout=1)

            # Always check for nudges between pops.
            nudge = await _drain_pubsub()
            if nudge is not None:
                yield _sse(
                    {
                        "type": "attachment",
                        "kind": (nudge.get("attachment") or {}).get("kind", "nudge-card"),
                        "data": (nudge.get("attachment") or {}).get("data"),
                        "raw": nudge.get("attachment"),
                        "_nudge": True,
                        "content": nudge.get("content"),
                    }
                )

            if item is None:
                # Heartbeat every ~25 ticks (~25s).
                yield b": keepalive\n\n"
                continue
            _, raw = item
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            content = (payload.get("content") or "").strip()
            if not content:
                continue
            async for ev in run_recruiter_turn(
                conversation_id=conversation_id, user_message=content
            ):
                yield _sse(ev)
    except asyncio.CancelledError:
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning("recruiter sse loop error: %s", exc)
        yield _sse({"type": "error", "message": "stream_failed"})
    finally:
        try:
            await pubsub.unsubscribe(f"recruiter-chat:{conversation_id}:nudge")
            await pubsub.aclose()
        except Exception:  # noqa: BLE001
            pass


@router.get("/conversations/{conversation_id}/stream")
async def stream(
    conversation_id: UUID,
    actor: tuple[str, str] = Depends(_actor),
) -> StreamingResponse:
    actor_hash, _ = actor
    # Verify ownership before streaming.
    async with session_scope() as session:
        conv = await repo.get_conversation(
            session, conversation_id=conversation_id, actor_hash=actor_hash
        )
        if conv is None:
            raise HTTPException(404, "conversation_not_found")
    return StreamingResponse(
        _sse_loop(conversation_id, actor_hash),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
