"""V2 chat API: candidate-facing conversation with the hiring agent.

Endpoints (all under ``/v2/chat``):
    GET  /{token}              -> resolve token, return chat header context
                                   (candidate name, role title, stage, history)
    POST /{token}/messages     -> persist user message + kick off agent turn
    GET  /{token}/stream       -> SSE; tokens + state changes (also pushes the
                                   opening greeting on first connect)
    POST /{token}/upload       -> upload an assignment artifact (multipart)
    POST /{token}/submit       -> finalize assignment submission

The token's ``action`` claim must be ``"chat"`` (issued at apply-time by the
new pre-warm hook).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import (
    APIRouter,
    Body,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.agent.runner import run_turn
from src.config import get_settings
from src.db.base import Application, Candidate, Role
from src.db.connection import session_scope
from src.db.repositories import (
    assignment as assignment_repo,
    conversation as conversation_repo,
    screening_answer as screening_repo,
)
from src.db.repositories.audit import log_audit
from src.db.repositories.v1_application import set_stage
from src.models.v1 import PipelineStage
from src.services.events import publish_event
from src.services.request_rate_limit import enforce_rate_limit
from src.services.screening_url import (
    InvalidScreeningToken,
    verify_apply_token,
)
from src.services.file_storage import upload_blob

logger = logging.getLogger(__name__)
_settings = get_settings()

router = APIRouter(prefix="/v2/chat", tags=["chat"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decode(token: str):
    try:
        claims = verify_apply_token(token)
    except InvalidScreeningToken as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid_token: {e}") from e
    if claims.action != "chat":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "token_action_mismatch")
    return claims


async def _resolve_or_create_conversation(application_id: UUID) -> UUID:
    async with session_scope() as session:
        conv = await conversation_repo.get_or_create_conversation(
            session, application_id=application_id
        )
        return conv.id


# ---------------------------------------------------------------------------
# Wire-level models
# ---------------------------------------------------------------------------


class MessageOut(BaseModel):
    sequence: int
    role: str
    content: str | None
    created_at: datetime


class ChatHeader(BaseModel):
    application_id: UUID
    conversation_id: UUID
    role_title: str
    candidate_name: str | None
    stage: str
    history: list[MessageOut]
    expires_at: datetime
    role_location: str | None = None
    # Restored on refresh so the candidate sees the brief + submission UI
    # without waiting for a fresh SSE ``brief`` event.
    assignment: dict[str, Any] | None = None


class SendMessageBody(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class SubmitAssignmentBody(BaseModel):
    submission_url: str | None = Field(default=None, max_length=1000)
    submission_text: str | None = Field(default=None, max_length=20000)


# ---------------------------------------------------------------------------
# GET /{token} -- header / resume context
# ---------------------------------------------------------------------------


@router.get("/{token}", response_model=ChatHeader)
async def get_header(token: str) -> ChatHeader:
    claims = _decode(token)
    async with session_scope() as session:
        app = await session.get(Application, claims.application_id)
        if app is None:
            raise HTTPException(404, "application_not_found")
        role = await session.get(Role, app.role_id) if app.role_id else None
        candidate = await session.get(Candidate, app.candidate_id)

        conv = await conversation_repo.get_or_create_conversation(
            session, application_id=app.id
        )
        msgs = await conversation_repo.list_messages(session, conv.id)
        assignment_row = await assignment_repo.get(session, app.id)

    assignment_payload: dict[str, Any] | None = None
    if assignment_row and assignment_row.brief_md and assignment_row.problems:
        assignment_payload = {
            "brief_md": assignment_row.brief_md,
            "problems": assignment_row.problems,
            "submission_format": assignment_row.submission_format,
            "evaluation_rubric": assignment_row.evaluation_rubric,
        }

    return ChatHeader(
        application_id=app.id,
        conversation_id=conv.id,
        role_title=role.title if role else "Role",
        candidate_name=candidate.name if candidate else None,
        stage=conv.stage,
        history=[
            MessageOut(
                sequence=m.sequence,
                role=m.role,
                content=m.content,
                created_at=m.created_at,
            )
            for m in msgs
            if m.role in ("user", "assistant")
        ],
        expires_at=claims.expires_at,
        role_location=role.location if role else None,
        assignment=assignment_payload,
    )


# ---------------------------------------------------------------------------
# POST /{token}/messages -- send a user message; reply is streamed via /stream
# ---------------------------------------------------------------------------


@router.post("/{token}/messages", status_code=status.HTTP_202_ACCEPTED)
async def send_message(
    token: str,
    request: Request,
    body: Annotated[SendMessageBody, Body()],
) -> dict[str, Any]:
    claims = _decode(token)
    await enforce_rate_limit(request, "chat_send", limit=20, window_seconds=60)

    conversation_id = await _resolve_or_create_conversation(claims.application_id)

    # Push the inbound text to the per-conversation in-memory queue so the
    # /stream handler can pick it up. The queue is a Redis list so that a
    # candidate sending while the SSE handler is reconnecting still gets
    # their reply.
    from src.db.connection import get_redis

    redis = get_redis()
    await redis.rpush(
        f"chat:{conversation_id}:inbox",
        json.dumps({"content": body.content, "ts": datetime.now(UTC).isoformat()}),
    )
    # Cap inbox at 50 to prevent abuse.
    await redis.ltrim(f"chat:{conversation_id}:inbox", -50, -1)

    return {"queued": True, "conversation_id": str(conversation_id)}


# ---------------------------------------------------------------------------
# GET /{token}/stream -- SSE; runs an agent turn whenever an inbox item arrives
# ---------------------------------------------------------------------------


def _sse(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event, default=str)}\n\n".encode("utf-8")


async def _sse_loop(token: str):
    claims = _decode(token)
    application_id = claims.application_id
    conversation_id = await _resolve_or_create_conversation(application_id)

    from src.db.connection import get_redis

    redis = get_redis()
    inbox_key = f"chat:{conversation_id}:inbox"
    state_key = f"chat:{conversation_id}:opened"

    yield b": connected\n\n"

    # On first connect for this conversation, push an opening greeting (no
    # user message yet). Subsequent reconnects skip this so the candidate
    # doesn't see a duplicate greeting.
    first_open = await redis.set(state_key, "1", nx=True, ex=86400)
    if first_open:
        async for ev in run_turn(conversation_id=conversation_id, user_message=None):
            yield _sse(ev)

    # Poll the inbox. BLPOP gives us blocking semantics with a 25s timeout
    # so we can ship periodic SSE comments to keep proxies from cutting
    # the connection.
    try:
        while True:
            item = await redis.blpop(inbox_key, timeout=25)
            if item is None:
                yield b": keepalive\n\n"
                continue
            _, raw = item
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            user_text = (payload.get("content") or "").strip()
            if not user_text:
                continue
            async for ev in run_turn(
                conversation_id=conversation_id, user_message=user_text
            ):
                yield _sse(ev)
    except asyncio.CancelledError:
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning("chat sse loop error: %s", exc)
        yield _sse({"type": "error", "message": "stream_failed"})


@router.get("/{token}/stream")
async def stream(token: str) -> StreamingResponse:
    return StreamingResponse(
        _sse_loop(token),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# POST /{token}/upload -- assignment file upload
# ---------------------------------------------------------------------------


@router.post("/{token}/upload")
async def upload_artifact(
    token: str,
    request: Request,
    file: Annotated[UploadFile, File(...)],
    label: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    claims = _decode(token)
    await enforce_rate_limit(request, "chat_upload", limit=10, window_seconds=600)

    _ALLOWED_UPLOAD_TYPES = {
        "application/pdf", "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip", "application/x-zip-compressed",
        "text/plain", "text/csv", "text/markdown",
        "image/png", "image/jpeg", "image/gif", "image/webp",
        "video/mp4", "video/webm",
    }
    if file.size and file.size > 25 * 1024 * 1024:
        raise HTTPException(413, "file_too_large")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty_file")
    ct = (file.content_type or "application/octet-stream").lower().split(";")[0].strip()
    if ct not in _ALLOWED_UPLOAD_TYPES:
        raise HTTPException(400, f"file_type_not_allowed: {ct}")

    import re as _re
    safe_name = _re.sub(r"[^a-zA-Z0-9._-]", "_", file.filename or "submission.bin")[:200]
    key = f"assignments/{claims.application_id}/{datetime.now(UTC):%Y%m%dT%H%M%S}_{safe_name}"
    await upload_blob(
        bucket=_settings.r2_bucket_resumes,
        key=key,
        content=raw,
        content_type=file.content_type or "application/octet-stream",
    )

    async with session_scope() as session:
        await assignment_repo.attach_submission(
            session,
            claims.application_id,
            submission_r2_keys=[key],
        )

    await publish_event(
        claims.application_id,
        event="assignment_artifact_uploaded",
        data={"r2_key": key, "filename": safe_name, "label": label},
    )
    return {"ok": True, "r2_key": key, "filename": safe_name}


# ---------------------------------------------------------------------------
# POST /{token}/submit -- finalize submission
# ---------------------------------------------------------------------------


@router.post("/{token}/submit")
async def submit_assignment(
    token: str,
    request: Request,
    body: Annotated[SubmitAssignmentBody, Body()],
) -> dict[str, Any]:
    claims = _decode(token)
    await enforce_rate_limit(request, "chat_submit", limit=5, window_seconds=600)

    if not body.submission_url and not body.submission_text:
        # Files alone (uploaded earlier) are also valid.
        async with session_scope() as session:
            existing = await assignment_repo.get(session, claims.application_id)
            if not existing or not (existing.submission_r2_keys or []):
                raise HTTPException(400, "submission_empty")

    async with session_scope() as session:
        await assignment_repo.attach_submission(
            session,
            claims.application_id,
            submission_url=body.submission_url,
            submission_text=body.submission_text,
        )
        conv = await conversation_repo.get_conversation_by_application(
            session, claims.application_id
        )
        if conv is not None:
            await conversation_repo.update_state(session, conv.id, stage="submitted")
        # Advance V1 pipeline so recruiter dashboard reflects the new state.
        try:
            await set_stage(
                session, claims.application_id, PipelineStage.ASSIGNMENT_SUBMITTED
            )
        except Exception:  # noqa: BLE001
            # Already past this stage on a re-submission; non-fatal.
            pass
        app = await session.get(Application, claims.application_id)
        if app is not None:
            await log_audit(
                session,
                application_id=app.id,
                candidate_id=app.candidate_id,
                action="chat_assignment_submitted",
                actor="agent",
                details={
                    "has_url": bool(body.submission_url),
                    "has_text": bool(body.submission_text),
                },
            )

    await publish_event(
        claims.application_id,
        event="assignment_submitted",
        data={"has_url": bool(body.submission_url), "has_text": bool(body.submission_text)},
    )
    return {"ok": True}
