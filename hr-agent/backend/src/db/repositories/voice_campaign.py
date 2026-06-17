"""Repository functions for voice campaigns."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import VoiceCampaign, VoiceCall


async def create_campaign(
    session: AsyncSession,
    *,
    call_kind: str,
    name: str,
    target_application_ids: list[str],
    role_id: UUID | None = None,
    max_concurrent: int = 10,
    dispatch_rate_per_minute: int = 5,
    context_template: dict[str, Any] | None = None,
    created_by: str | None = None,
) -> VoiceCampaign:
    row = VoiceCampaign(
        call_kind=call_kind,
        name=name,
        target_application_ids=target_application_ids,
        total_calls=len(target_application_ids),
        role_id=role_id,
        max_concurrent=max_concurrent,
        dispatch_rate_per_minute=dispatch_rate_per_minute,
        context_template=context_template,
        created_by=created_by,
    )
    session.add(row)
    await session.flush()
    return row


async def get_campaign(session: AsyncSession, campaign_id: UUID) -> VoiceCampaign | None:
    return await session.get(VoiceCampaign, campaign_id)


async def start_campaign(session: AsyncSession, campaign_id: UUID) -> None:
    row = await session.get(VoiceCampaign, campaign_id)
    if row is None:
        raise ValueError(f"campaign {campaign_id} not found")
    row.status = "dispatching"
    row.started_at = datetime.now(UTC)


async def pause_campaign(session: AsyncSession, campaign_id: UUID) -> None:
    row = await session.get(VoiceCampaign, campaign_id)
    if row is None:
        raise ValueError(f"campaign {campaign_id} not found")
    row.status = "paused"


async def cancel_campaign(session: AsyncSession, campaign_id: UUID) -> None:
    row = await session.get(VoiceCampaign, campaign_id)
    if row is None:
        raise ValueError(f"campaign {campaign_id} not found")
    row.status = "cancelled"
    await session.execute(
        update(VoiceCall)
        .where(
            VoiceCall.campaign_id == campaign_id,
            VoiceCall.status.in_(["pending", "dialing"]),
            VoiceCall.provider_call_id.is_(None),
        )
        .values(status="failed", error="campaign_cancelled")
    )


async def complete_campaign(session: AsyncSession, campaign_id: UUID) -> None:
    row = await session.get(VoiceCampaign, campaign_id)
    if row is None:
        return
    row.status = "completed"
    row.completed_at = datetime.now(UTC)


async def get_campaign_progress(
    session: AsyncSession, campaign_id: UUID
) -> dict[str, int]:
    rows = (
        await session.execute(
            select(VoiceCall.status, func.count())
            .where(VoiceCall.campaign_id == campaign_id)
            .group_by(VoiceCall.status)
        )
    ).all()
    return {status: count for status, count in rows}


async def count_in_flight(session: AsyncSession, campaign_id: UUID) -> int:
    result = await session.execute(
        select(func.count()).where(
            VoiceCall.campaign_id == campaign_id,
            VoiceCall.status.in_(["pending", "dialing", "in_progress"]),
        )
    )
    return result.scalar_one()


async def get_dispatching_campaigns(session: AsyncSession) -> list[UUID]:
    """Return IDs of all campaigns in 'dispatching' status."""
    rows = (
        await session.execute(
            select(VoiceCampaign.id).where(VoiceCampaign.status == "dispatching")
        )
    ).scalars().all()
    return list(rows)


async def get_undispatched_targets(
    session: AsyncSession,
    campaign_id: UUID,
    target_app_ids: list[str],
    limit: int = 10,
) -> list[UUID]:
    """Return application IDs from the target list that don't yet have a VoiceCall for this campaign."""
    if not target_app_ids:
        return []
    target_uuids = [UUID(aid) for aid in target_app_ids]
    already_dispatched = (
        await session.execute(
            select(VoiceCall.application_id).where(
                VoiceCall.campaign_id == campaign_id,
            )
        )
    ).scalars().all()
    dispatched_set = set(already_dispatched)
    remaining = [uid for uid in target_uuids if uid not in dispatched_set]
    return remaining[:limit]


async def update_campaign_counters(
    session: AsyncSession, campaign_id: UUID
) -> None:
    progress = await get_campaign_progress(session, campaign_id)
    row = await session.get(VoiceCampaign, campaign_id)
    if row is None:
        return
    row.completed_calls = progress.get("completed", 0)
    row.failed_calls = (
        progress.get("failed", 0)
        + progress.get("no_answer", 0)
        + progress.get("declined", 0)
    )
