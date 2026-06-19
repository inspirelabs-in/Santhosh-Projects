"""Role-pipeline-stage repository: the per-role configurable pipeline.

Each row is one ordered stage of a role's pipeline. Helpers to read the active
pipeline, find the next enabled stage, and seed the default pipeline onto a new
role (so roles created after the migration also get a pipeline).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import RolePipelineStage
from src.models.pipeline import DEFAULT_PIPELINE


async def for_role(
    session: AsyncSession, role_id: UUID, *, enabled_only: bool = True
) -> list[RolePipelineStage]:
    stmt = select(RolePipelineStage).where(RolePipelineStage.role_id == role_id)
    if enabled_only:
        stmt = stmt.where(RolePipelineStage.is_enabled.is_(True))
    stmt = stmt.order_by(RolePipelineStage.position)
    return list(await session.scalars(stmt))


async def get_stage(
    session: AsyncSession, role_id: UUID, stage_key: str
) -> RolePipelineStage | None:
    return await session.scalar(
        select(RolePipelineStage).where(
            RolePipelineStage.role_id == role_id,
            RolePipelineStage.stage_key == stage_key,
        )
    )


async def next_stage(
    session: AsyncSession, role_id: UUID, current_stage_key: str | None
) -> RolePipelineStage | None:
    """The next enabled stage after ``current_stage_key``. If ``current_stage_key``
    is None, returns the first enabled stage. None when at/after the last stage."""
    stages = await for_role(session, role_id, enabled_only=True)
    if not stages:
        return None
    if current_stage_key is None:
        return stages[0]
    for i, stage in enumerate(stages):
        if stage.stage_key == current_stage_key:
            return stages[i + 1] if i + 1 < len(stages) else None
    return None


async def seed_default(
    session: AsyncSession, *, role_id: UUID, org_id: UUID | None = None
) -> list[RolePipelineStage]:
    """Seed the default pipeline onto a role that has none. Idempotent: returns
    the existing pipeline untouched if one already exists. Call this on role
    creation."""
    existing = await for_role(session, role_id, enabled_only=False)
    if existing:
        return existing
    created: list[RolePipelineStage] = []
    for position, spec in enumerate(DEFAULT_PIPELINE):
        row = RolePipelineStage(
            role_id=role_id,
            org_id=org_id,
            position=position,
            stage_type=spec["stage_type"],
            stage_key=spec["stage_key"],
            label=spec["label"],
            mode=spec["mode"],
        )
        session.add(row)
        created.append(row)
    await session.flush()
    return created
