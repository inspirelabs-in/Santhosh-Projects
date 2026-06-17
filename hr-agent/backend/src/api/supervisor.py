"""Supervisor inbox API: review, approve, and reject shadow-mode proposals.

HR uses these endpoints to see what the supervisor would do and either
approve (triggering execution) or reject (recording the override for
future accuracy tracking).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from src.api.auth import require_recruiter, require_viewer
from src.db.base import SupervisorAction, SupervisorEvent
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.services.typed_event_bus import EventType
from src.services.typed_event_bus import publish_event as publish_supervisor_event

router = APIRouter(prefix="/supervisor", tags=["supervisor"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ActionSummary(BaseModel):
    id: str
    event_id: str
    application_id: str | None
    action_type: str
    reasoning: str | None
    confidence: float | None
    mode: str
    executed: bool
    execution_result: dict | None
    approved_by: str | None
    rejected_by: str | None
    created_at: str


class ActionListResponse(BaseModel):
    actions: list[ActionSummary]
    total: int


class EventSummary(BaseModel):
    id: str
    event_type: str
    application_id: str | None
    status: str
    payload: dict
    created_at: str


class EventListResponse(BaseModel):
    events: list[EventSummary]
    total: int


class AccuracyReport(BaseModel):
    total_proposals: int
    approved: int
    rejected: int
    pending: int
    executed: int
    approval_rate: float | None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/actions", response_model=ActionListResponse)
async def list_actions(
    _: Annotated[str, Depends(require_viewer)],
    status: str | None = Query(None, description="pending|approved|rejected|executed"),
    application_id: UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ActionListResponse:
    """List supervisor action proposals. Default: pending (unreviewed)."""
    async with session_scope() as session:
        stmt = select(SupervisorAction).order_by(SupervisorAction.created_at.desc())

        if status == "pending":
            stmt = stmt.where(
                SupervisorAction.approved_by.is_(None),
                SupervisorAction.rejected_by.is_(None),
                SupervisorAction.executed.is_(False),
            )
        elif status == "approved":
            stmt = stmt.where(SupervisorAction.approved_by.isnot(None))
        elif status == "rejected":
            stmt = stmt.where(SupervisorAction.rejected_by.isnot(None))
        elif status == "executed":
            stmt = stmt.where(SupervisorAction.executed.is_(True))

        if application_id:
            stmt = stmt.where(SupervisorAction.application_id == application_id)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = await session.scalar(count_stmt) or 0

        rows = (await session.execute(stmt.offset(offset).limit(limit))).scalars().all()

    return ActionListResponse(
        actions=[
            ActionSummary(
                id=str(a.id),
                event_id=str(a.event_id),
                application_id=str(a.application_id) if a.application_id else None,
                action_type=a.action_type,
                reasoning=a.reasoning,
                confidence=a.confidence,
                mode=a.mode,
                executed=a.executed,
                execution_result=a.execution_result,
                approved_by=a.approved_by,
                rejected_by=a.rejected_by,
                created_at=str(a.created_at),
            )
            for a in rows
        ],
        total=total,
    )


class ReviewBody(BaseModel):
    note: str | None = None


@router.post("/actions/{action_id}/approve")
async def approve_action(
    action_id: UUID,
    body: ReviewBody,
    actor: Annotated[str, Depends(require_recruiter)],
) -> dict[str, Any]:
    """Approve a shadow-mode proposal and execute it."""
    async with session_scope() as session:
        action = await session.get(SupervisorAction, action_id)
        if action is None:
            raise HTTPException(status_code=404, detail="action not found")
        if action.approved_by or action.rejected_by:
            raise HTTPException(status_code=409, detail="action already reviewed")

        action.approved_by = actor
        action.approved_at = datetime.now(UTC)

        # Execute through tool registry
        from src.supervisor.engine import ProposedAction, _execute_via_registry
        proposed = ProposedAction(
            action_type=action.action_type,
            target_stage=action.action_params.get("target_stage"),
            params={k: v for k, v in action.action_params.items() if k != "target_stage"},
            reasoning=action.reasoning or "",
            confidence=action.confidence or 0.5,
        )
        exec_result = await _execute_via_registry(proposed)
        action.executed = exec_result.get("success", False)
        action.execution_result = exec_result

        await log_audit(
            session,
            application_id=action.application_id,
            candidate_id=action.candidate_id,
            action="supervisor_action_approved",
            actor=actor,
            details={
                "action_id": str(action_id),
                "action_type": action.action_type,
                "note": body.note,
                "execution_result": exec_result,
            },
        )

        # Emit HR_DECISION_MADE so supervisor can chain follow-up actions
        await publish_supervisor_event(
            session,
            EventType.HR_DECISION_MADE,
            application_id=action.application_id,
            candidate_id=action.candidate_id,
            payload={
                "decision": "approved",
                "action_type": action.action_type,
                "actor": actor,
                "note": body.note,
            },
            dedup_extra=f"approve-{action_id}",
        )

    return {
        "status": "approved",
        "executed": action.executed,
        "execution_result": exec_result,
    }


@router.post("/actions/{action_id}/reject")
async def reject_action(
    action_id: UUID,
    body: ReviewBody,
    actor: Annotated[str, Depends(require_recruiter)],
) -> dict[str, str]:
    """Reject a shadow-mode proposal."""
    async with session_scope() as session:
        action = await session.get(SupervisorAction, action_id)
        if action is None:
            raise HTTPException(status_code=404, detail="action not found")
        if action.approved_by or action.rejected_by:
            raise HTTPException(status_code=409, detail="action already reviewed")

        action.rejected_by = actor
        action.rejected_at = datetime.now(UTC)

        await log_audit(
            session,
            application_id=action.application_id,
            candidate_id=action.candidate_id,
            action="supervisor_action_rejected",
            actor=actor,
            details={
                "action_id": str(action_id),
                "action_type": action.action_type,
                "note": body.note,
            },
        )

        await publish_supervisor_event(
            session,
            EventType.HR_DECISION_MADE,
            application_id=action.application_id,
            candidate_id=action.candidate_id,
            payload={
                "decision": "rejected",
                "action_type": action.action_type,
                "actor": actor,
                "note": body.note,
            },
            dedup_extra=f"reject-{action_id}",
        )

    return {"status": "rejected"}


@router.get("/events", response_model=EventListResponse)
async def list_events(
    _: Annotated[str, Depends(require_viewer)],
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> EventListResponse:
    """List supervisor events."""
    async with session_scope() as session:
        stmt = select(SupervisorEvent).order_by(SupervisorEvent.created_at.desc())
        if status:
            stmt = stmt.where(SupervisorEvent.status == status)
        stmt = stmt.limit(limit)

        count_stmt = select(func.count()).select_from(
            select(SupervisorEvent).where(
                SupervisorEvent.status == status if status else True
            ).subquery()
        )
        total = await session.scalar(count_stmt) or 0
        rows = (await session.execute(stmt)).scalars().all()

    return EventListResponse(
        events=[
            EventSummary(
                id=str(e.id),
                event_type=e.event_type,
                application_id=str(e.application_id) if e.application_id else None,
                status=e.status,
                payload=e.payload or {},
                created_at=str(e.created_at),
            )
            for e in rows
        ],
        total=total,
    )


@router.get("/accuracy-report", response_model=AccuracyReport)
async def accuracy_report(
    _: Annotated[str, Depends(require_viewer)],
) -> AccuracyReport:
    """Shadow mode accuracy: approve/reject ratios."""
    async with session_scope() as session:
        total = await session.scalar(
            select(func.count()).select_from(SupervisorAction)
        ) or 0
        approved = await session.scalar(
            select(func.count()).select_from(
                select(SupervisorAction).where(SupervisorAction.approved_by.isnot(None)).subquery()
            )
        ) or 0
        rejected = await session.scalar(
            select(func.count()).select_from(
                select(SupervisorAction).where(SupervisorAction.rejected_by.isnot(None)).subquery()
            )
        ) or 0
        executed = await session.scalar(
            select(func.count()).select_from(
                select(SupervisorAction).where(SupervisorAction.executed.is_(True)).subquery()
            )
        ) or 0

    reviewed = approved + rejected
    return AccuracyReport(
        total_proposals=total,
        approved=approved,
        rejected=rejected,
        pending=total - reviewed,
        executed=executed,
        approval_rate=round(approved / reviewed, 3) if reviewed > 0 else None,
    )


# ---------------------------------------------------------------------------
# A/B Experiments
# ---------------------------------------------------------------------------


class CreateExperimentPayload(BaseModel):
    name: str
    description: str = ""
    variants: list[dict[str, Any]]
    target_stage: str | None = None
    target_event_types: list[str] | None = None
    sample_rate: float = 1.0


class ExperimentResponse(BaseModel):
    name: str
    status: str
    description: str
    variant_count: int
    sample_rate: float
    target_stage: str | None = None


@router.post("/experiments", status_code=201)
async def create_experiment(
    payload: CreateExperimentPayload,
    _: Annotated[str, Depends(require_recruiter)],
) -> dict[str, Any]:
    """Create a new A/B experiment for supervisor decisions."""
    from src.services.ab_testing import create_experiment as _create

    async with session_scope() as session:
        exp = await _create(
            session,
            name=payload.name,
            description=payload.description,
            variants=payload.variants,
            target_stage=payload.target_stage,
            target_event_types=payload.target_event_types,
            sample_rate=payload.sample_rate,
        )
    return {
        "ok": True,
        "experiment": exp.name,
        "id": str(exp.id),
        "variant_count": len(exp.variants),
    }


@router.get("/experiments", response_model=list[ExperimentResponse])
async def list_experiments(
    _: Annotated[str, Depends(require_viewer)],
) -> list[ExperimentResponse]:
    """List all A/B experiments."""
    from src.services.ab_testing import ExperimentRow

    if ExperimentRow is None:
        return []

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(ExperimentRow).order_by(ExperimentRow.created_at.desc())
            )
        ).scalars().all()

    return [
        ExperimentResponse(
            name=r.name,
            status=r.status,
            description=r.description or "",
            variant_count=len(r.variants or []),
            sample_rate=r.sample_rate or 1.0,
            target_stage=r.target_stage,
        )
        for r in rows
    ]


@router.get("/experiments/{experiment_name}/results")
async def experiment_results(
    experiment_name: str,
    _: Annotated[str, Depends(require_viewer)],
) -> dict[str, Any]:
    """Get outcome metrics per variant for an experiment."""
    from src.services.ab_testing import get_experiment_results

    async with session_scope() as session:
        return await get_experiment_results(session, experiment_name)


@router.post("/experiments/{experiment_name}/conclude")
async def conclude_experiment(
    experiment_name: str,
    _: Annotated[str, Depends(require_recruiter)],
    notes: str = "",
) -> dict[str, Any]:
    """Conclude an experiment and stop variant assignment."""
    from src.services.ab_testing import conclude_experiment as _conclude

    async with session_scope() as session:
        exp = await _conclude(session, experiment_name, notes=notes)
    if not exp:
        raise HTTPException(status_code=404, detail="experiment not found")
    return {"ok": True, "experiment": exp.name, "status": exp.status}
