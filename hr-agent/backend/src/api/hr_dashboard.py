"""HR dashboard: read-only journey view for candidates in the HR round.

Mirrors the CEO dashboard surface but scoped to HR-stage applications. Reuses
the CEODetail shape via direct delegation to ceo_dashboard.get_application.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from src.api.auth import require_recruiter
from src.api.ceo_dashboard import (
    CEODetail,
    CEOListItem,
    get_application as _ceo_get_application,
)
from src.db.base import Application, Candidate, Role
from src.db.connection import session_scope
from src.models.v1 import PipelineStage

router = APIRouter(prefix="/dashboard/hr", tags=["hr-dashboard"])

_HR_RELEVANT_STAGES = {
    PipelineStage.HR_MEETING_SCHEDULED.value,
    PipelineStage.HR_MEETING_IN_PROGRESS.value,
    PipelineStage.HR_MEETING_COMPLETED.value,
    PipelineStage.HR_EVALUATED.value,
    PipelineStage.HIRED.value,
}


@router.get("/applications", response_model=list[CEOListItem])
async def list_applications(
    _: Annotated[str, Depends(require_recruiter)],
) -> list[CEOListItem]:
    async with session_scope() as session:
        stmt = (
            select(Application, Candidate, Role)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .outerjoin(Role, Role.id == Application.role_id)
            .where(Application.current_stage.in_(_HR_RELEVANT_STAGES))
            .order_by(Application.updated_at.desc())
        )
        rows = (await session.execute(stmt)).all()
        return [
            CEOListItem(
                application_id=app.id,
                candidate_id=cand.id,
                candidate_name=cand.name,
                role_title=role.title if role is not None else None,
                current_stage=app.current_stage,
                fit_score=app.fit_score,
                has_brief=bool(app.journey_report),
                updated_at=app.updated_at,
            )
            for app, cand, role in rows
        ]


@router.get("/applications/{application_id}", response_model=CEODetail)
async def get_application(
    application_id: UUID,
    actor: Annotated[str, Depends(require_recruiter)],
) -> CEODetail:
    # Direct call bypasses the CEO endpoint's Depends gate; we've already
    # gated this route with require_recruiter. The data shape is identical.
    async with session_scope() as session:
        if await session.get(Application, application_id) is None:
            raise HTTPException(404, "application not found")
    return await _ceo_get_application(application_id, _=actor)
