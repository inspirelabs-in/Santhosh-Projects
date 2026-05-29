"""Conversation + message repository for the V2 chat-first flow.

One conversation per application. Messages are append-only with a monotonic
``sequence`` per conversation. State machine moves through:

    intake -> screening -> assignment -> submitted -> completed

Stage values are validated by callers; this repo never enforces order so the
agent can override (e.g. fast-track a strong candidate skipping logistics).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import Conversation, Message


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


async def get_or_create_conversation(
    session: AsyncSession,
    *,
    application_id: UUID,
    initial_state: dict[str, Any] | None = None,
) -> Conversation:
    """Idempotent. Returns existing conversation if one exists for this app.

    Uses an ``ON CONFLICT DO NOTHING`` insert + select so concurrent callers
    (the email link click + the pre-warm task) cannot race-create two rows.
    """
    stmt = (
        pg_insert(Conversation)
        .values(application_id=application_id, state=initial_state or {})
        .on_conflict_do_nothing(index_elements=["application_id"])
    )
    await session.execute(stmt)
    result = await session.execute(
        select(Conversation).where(Conversation.application_id == application_id)
    )
    conv = result.scalar_one()
    return conv


async def get_conversation_by_id(
    session: AsyncSession, conversation_id: UUID
) -> Conversation | None:
    return await session.get(Conversation, conversation_id)


async def get_conversation_by_application(
    session: AsyncSession, application_id: UUID
) -> Conversation | None:
    result = await session.execute(
        select(Conversation).where(Conversation.application_id == application_id)
    )
    return result.scalar_one_or_none()


async def update_state(
    session: AsyncSession,
    conversation_id: UUID,
    *,
    state_patch: dict[str, Any] | None = None,
    stage: str | None = None,
) -> None:
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise ValueError(f"conversation {conversation_id} not found")
    if state_patch:
        merged = dict(conv.state or {})
        merged.update(state_patch)
        conv.state = merged
    if stage is not None:
        conv.stage = stage


async def set_prewarmed(
    session: AsyncSession,
    conversation_id: UUID,
    *,
    questions: dict | list | None = None,
    assignment: dict | None = None,
) -> None:
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise ValueError(f"conversation {conversation_id} not found")
    if questions is not None:
        conv.prewarmed_questions = questions
    if assignment is not None:
        conv.prewarmed_assignment = assignment


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


async def list_messages(
    session: AsyncSession, conversation_id: UUID
) -> list[Message]:
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.sequence.asc())
    )
    return list(result.scalars().all())


async def next_sequence(
    session: AsyncSession, conversation_id: UUID
) -> int:
    """Compute the next sequence number for a conversation.

    Locks the conversation row for the rest of the transaction so two
    concurrent ``append_message`` calls (e.g. multi-tab candidate session)
    cannot land on the same sequence + violate ``uq_messages_conv_seq``.
    The lock is released at commit / rollback.
    """
    # Row-lock the conversation. ``SELECT 1 ... FOR UPDATE`` is enough to
    # acquire the lock; we don't actually need the row contents.
    await session.execute(
        select(Conversation.id)
        .where(Conversation.id == conversation_id)
        .with_for_update()
    )
    result = await session.execute(
        select(Message.sequence)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.sequence.desc())
        .limit(1)
    )
    last = result.scalar_one_or_none()
    return (last or 0) + 1


async def append_message(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    role: str,
    content: str | None = None,
    tool_name: str | None = None,
    tool_calls: list | dict | None = None,
    tool_result: dict | list | None = None,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    latency_ms: int | None = None,
) -> Message:
    seq = await next_sequence(session, conversation_id)
    msg = Message(
        conversation_id=conversation_id,
        sequence=seq,
        role=role,
        content=content,
        tool_name=tool_name,
        tool_calls=tool_calls,
        tool_result=tool_result,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
    )
    session.add(msg)
    await session.flush()
    return msg
