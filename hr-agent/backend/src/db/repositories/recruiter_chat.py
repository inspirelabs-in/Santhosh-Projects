"""Recruiter conversation + message repository.

Mirror of ``conversation.py`` but for recruiter-side chats. Locks via
SELECT FOR UPDATE on the conversation row to keep ``messages.sequence``
collision-free under concurrent multi-tab use.
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import RecruiterConversation, RecruiterMessage


def hash_actor(dashboard_key: str) -> str:
    return hashlib.sha256(dashboard_key.encode("utf-8")).hexdigest()


async def list_conversations(
    session: AsyncSession,
    *,
    actor_hash: str,
    include_archived: bool = False,
    limit: int = 50,
) -> list[RecruiterConversation]:
    q = (
        select(RecruiterConversation)
        .where(RecruiterConversation.actor_hash == actor_hash)
        .order_by(desc(RecruiterConversation.updated_at))
        .limit(limit)
    )
    if not include_archived:
        q = q.where(RecruiterConversation.archived.is_(False))
    return list((await session.execute(q)).scalars().all())


async def create_conversation(
    session: AsyncSession,
    *,
    actor_hash: str,
    actor_role: str,
    title: str | None = None,
) -> RecruiterConversation:
    conv = RecruiterConversation(
        actor_hash=actor_hash,
        actor_role=actor_role,
        title=title or "New chat",
    )
    session.add(conv)
    await session.flush()
    return conv


async def get_conversation(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    actor_hash: str,
) -> RecruiterConversation | None:
    conv = await session.get(RecruiterConversation, conversation_id)
    if conv is None or conv.actor_hash != actor_hash:
        return None
    return conv


async def archive(
    session: AsyncSession, *, conversation_id: UUID, actor_hash: str
) -> bool:
    conv = await get_conversation(
        session, conversation_id=conversation_id, actor_hash=actor_hash
    )
    if conv is None:
        return False
    conv.archived = True
    return True


async def rename(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    actor_hash: str,
    title: str,
) -> bool:
    conv = await get_conversation(
        session, conversation_id=conversation_id, actor_hash=actor_hash
    )
    if conv is None:
        return False
    conv.title = title.strip()[:255]
    return True


async def update_state(
    session: AsyncSession,
    conversation_id: UUID,
    *,
    state_patch: dict[str, Any] | None = None,
) -> None:
    conv = await session.get(RecruiterConversation, conversation_id)
    if conv is None:
        return
    if state_patch:
        merged = dict(conv.state or {})
        merged.update(state_patch)
        conv.state = merged


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


async def list_messages(
    session: AsyncSession,
    conversation_id: UUID,
    *,
    include_tombstoned: bool = False,
) -> list[RecruiterMessage]:
    q = (
        select(RecruiterMessage)
        .where(RecruiterMessage.conversation_id == conversation_id)
        .order_by(RecruiterMessage.sequence.asc())
    )
    if not include_tombstoned:
        q = q.where(RecruiterMessage.tombstoned.is_(False))
    return list((await session.execute(q)).scalars().all())


async def _next_sequence(
    session: AsyncSession, conversation_id: UUID
) -> int:
    # Lock the conversation row for the rest of the txn.
    await session.execute(
        select(RecruiterConversation.id)
        .where(RecruiterConversation.id == conversation_id)
        .with_for_update()
    )
    last = (
        await session.execute(
            select(RecruiterMessage.sequence)
            .where(RecruiterMessage.conversation_id == conversation_id)
            .order_by(RecruiterMessage.sequence.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
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
    attachments: list | dict | None = None,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    latency_ms: int | None = None,
) -> RecruiterMessage:
    seq = await _next_sequence(session, conversation_id)
    msg = RecruiterMessage(
        conversation_id=conversation_id,
        sequence=seq,
        role=role,
        content=content,
        tool_name=tool_name,
        tool_calls=tool_calls,
        tool_result=tool_result,
        attachments=attachments,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
    )
    session.add(msg)
    await session.flush()
    return msg
