"""Recruiter-artifact repository: structured, editable artifacts per conversation.

The agent writes/updates artifacts here; the frontend reads the active one to
render the editable side panel. Human edits and agent edits both go through
``update_content`` (which bumps ``version``).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import RecruiterArtifact
from src.models.artifacts import ArtifactStatus


async def create(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    type: str,
    content: dict,
    org_id: UUID | None = None,
    application_id: UUID | None = None,
    title: str | None = None,
) -> RecruiterArtifact:
    art = RecruiterArtifact(
        conversation_id=conversation_id,
        org_id=org_id,
        application_id=application_id,
        type=str(type),
        status=ArtifactStatus.DRAFT.value,
        title=title,
        content=content or {},
        version=1,
    )
    session.add(art)
    await session.flush()
    return art


async def get(session: AsyncSession, artifact_id: UUID) -> RecruiterArtifact | None:
    return await session.get(RecruiterArtifact, artifact_id)


async def get_active_for_conversation(
    session: AsyncSession, conversation_id: UUID
) -> RecruiterArtifact | None:
    """The most recent artifact for a conversation, preferring an open draft
    if one exists, otherwise the most recently updated artifact (applied or
    dismissed). Returns None only when the conversation has never had an
    artifact — this allows the panel to show applied data on revisit."""
    # Try draft first
    draft = await session.scalar(
        select(RecruiterArtifact)
        .where(
            RecruiterArtifact.conversation_id == conversation_id,
            RecruiterArtifact.status == ArtifactStatus.DRAFT.value,
        )
        .order_by(RecruiterArtifact.updated_at.desc())
        .limit(1)
    )
    if draft is not None:
        return draft
    # Fall back to the latest applied/dismissed artifact
    return await session.scalar(
        select(RecruiterArtifact)
        .where(RecruiterArtifact.conversation_id == conversation_id)
        .order_by(RecruiterArtifact.updated_at.desc())
        .limit(1)
    )


async def list_for_conversation(
    session: AsyncSession, conversation_id: UUID, *, limit: int = 50
) -> list[RecruiterArtifact]:
    result = await session.scalars(
        select(RecruiterArtifact)
        .where(RecruiterArtifact.conversation_id == conversation_id)
        .order_by(RecruiterArtifact.created_at.desc())
        .limit(limit)
    )
    return list(result)


async def update_content(
    session: AsyncSession,
    artifact_id: UUID,
    content: dict,
    *,
    title: str | None = None,
) -> RecruiterArtifact | None:
    """Replace the artifact content (human or agent edit) and bump the version."""
    art = await session.get(RecruiterArtifact, artifact_id)
    if art is None:
        return None
    art.content = content or {}
    if title is not None:
        art.title = title
    art.version = (art.version or 1) + 1
    return art


async def set_status(
    session: AsyncSession, artifact_id: UUID, status: ArtifactStatus | str
) -> RecruiterArtifact | None:
    art = await session.get(RecruiterArtifact, artifact_id)
    if art is not None:
        art.status = str(status)
    return art
