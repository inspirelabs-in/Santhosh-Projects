"""Advanced analytics endpoints — time-to-hire, source effectiveness,
drop-off analysis, pipeline velocity, interviewer calibration.

Goes beyond the basic funnel counts in services/metrics.py.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, cast, desc, extract, func, select, Float
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_viewer
from src.db.base import Application, AuditLog, Candidate, Interview, Role
from src.db.connection import session_scope

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/analytics", tags=["analytics"])


# ---------------------------------------------------------------------------
# Time-to-hire
# ---------------------------------------------------------------------------


class StageTimingItem(BaseModel):
    stage: str
    avg_hours: float
    median_hours: float | None = None
    p90_hours: float | None = None
    count: int


class TimeToHireResponse(BaseModel):
    avg_days_overall: float | None
    by_role: dict[str, float]
    by_stage: list[StageTimingItem]
    since_days: int


@router.get("/time-to-hire", response_model=TimeToHireResponse)
async def time_to_hire(
    _: Annotated[str, Depends(require_viewer)],
    since_days: int = Query(90, ge=7, le=365),
    role_id: UUID | None = None,
) -> TimeToHireResponse:
    """Time from application to hire/reject, broken by stage and role."""
    cutoff = datetime.now(tz=UTC) - timedelta(days=since_days)

    async with session_scope() as session:
        # Overall average days to terminal state
        terminal = ("rejected", "hired", "withdrawn")
        base_filter = [
            Application.created_at >= cutoff,
            Application.status.in_(terminal),
        ]
        if role_id:
            base_filter.append(Application.role_id == role_id)

        duration_expr = extract(
            "epoch",
            Application.updated_at - Application.created_at,
        ) / 86400.0

        avg_overall = await session.scalar(
            select(func.avg(duration_expr))
            .where(and_(*base_filter))
        )

        # By role
        role_rows = (
            await session.execute(
                select(
                    Role.title,
                    func.avg(duration_expr).label("avg_days"),
                )
                .join(Role, Role.id == Application.role_id)
                .where(and_(*base_filter))
                .group_by(Role.title)
                .order_by(desc("avg_days"))
            )
        ).all()
        by_role = {title: round(float(avg), 2) for title, avg in role_rows if avg}

        # By stage transitions (from audit log)
        stage_pairs = (
            await session.execute(
                select(
                    AuditLog.action,
                    func.avg(
                        extract("epoch", AuditLog.created_at - Application.created_at) / 3600.0
                    ).label("avg_hours"),
                    func.count().label("cnt"),
                )
                .join(Application, Application.id == AuditLog.application_id)
                .where(
                    and_(
                        AuditLog.created_at >= cutoff,
                        AuditLog.action.in_([
                            "fit_scored",
                            "screening_invite_sent",
                            "screening_submitted",
                            "screening_evaluated",
                            "assignment_sent",
                            "assignment_submitted",
                            "journey_report_generated",
                            "tech_review_requested",
                            "hr_override",
                        ]),
                    )
                )
                .group_by(AuditLog.action)
            )
        ).all()

        by_stage = [
            StageTimingItem(
                stage=action,
                avg_hours=round(float(avg_h), 1) if avg_h else 0,
                count=int(cnt),
            )
            for action, avg_h, cnt in stage_pairs
        ]

    return TimeToHireResponse(
        avg_days_overall=round(float(avg_overall), 2) if avg_overall else None,
        by_role=by_role,
        by_stage=sorted(by_stage, key=lambda x: x.avg_hours),
        since_days=since_days,
    )


# ---------------------------------------------------------------------------
# Source effectiveness
# ---------------------------------------------------------------------------


class SourceStat(BaseModel):
    source: str
    total_applications: int
    screened: int
    passed_screening: int
    hired: int
    conversion_rate: float
    avg_fit_score: float | None


@router.get("/source-effectiveness", response_model=list[SourceStat])
async def source_effectiveness(
    _: Annotated[str, Depends(require_viewer)],
    since_days: int = Query(90, ge=7, le=365),
) -> list[SourceStat]:
    """Which candidate sources produce the best conversion rates?"""
    cutoff = datetime.now(tz=UTC) - timedelta(days=since_days)

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(
                    func.coalesce(Candidate.source_channel, "unknown").label("src"),
                    func.count(Application.id).label("total"),
                    func.sum(case(
                        (Application.screening_score.isnot(None), 1),
                        else_=0,
                    )).label("screened"),
                    func.sum(case(
                        (Application.screening_score >= 60, 1),
                        else_=0,
                    )).label("passed"),
                    func.sum(case(
                        (Application.status == "hired", 1),
                        else_=0,
                    )).label("hired"),
                    func.avg(cast(Application.fit_score, Float)).label("avg_fit"),
                )
                .join(Candidate, Candidate.id == Application.candidate_id)
                .where(Application.created_at >= cutoff)
                .group_by("src")
                .order_by(desc("total"))
            )
        ).all()

        return [
            SourceStat(
                source=str(src),
                total_applications=int(total),
                screened=int(screened or 0),
                passed_screening=int(passed or 0),
                hired=int(hired or 0),
                conversion_rate=round(int(hired or 0) / int(total), 4) if total else 0.0,
                avg_fit_score=round(float(avg_fit), 1) if avg_fit else None,
            )
            for src, total, screened, passed, hired, avg_fit in rows
        ]


# ---------------------------------------------------------------------------
# Drop-off analysis
# ---------------------------------------------------------------------------


class DropOffStage(BaseModel):
    stage: str
    entered: int
    exited_to_next: int
    dropped: int
    drop_rate: float


@router.get("/drop-off", response_model=list[DropOffStage])
async def drop_off_analysis(
    _: Annotated[str, Depends(require_viewer)],
    since_days: int = Query(90, ge=7, le=365),
    role_id: UUID | None = None,
) -> list[DropOffStage]:
    """Where in the funnel are candidates dropping off?"""
    cutoff = datetime.now(tz=UTC) - timedelta(days=since_days)

    stage_order = [
        "applied", "screening_sent", "screening_submitted",
        "screening_evaluated", "assignment_sent", "assignment_submitted",
        "report_ready", "technical_pending_approval", "hired",
    ]

    async with session_scope() as session:
        # Count audit log entries per stage transition
        filters = [AuditLog.created_at >= cutoff]
        if role_id:
            filters.append(Application.role_id == role_id)

        stage_counts: dict[str, int] = {}
        for stage in stage_order:
            count = await session.scalar(
                select(func.count(func.distinct(Application.id)))
                .join(AuditLog, AuditLog.application_id == Application.id, isouter=True)
                .where(
                    and_(
                        Application.created_at >= cutoff,
                        Application.current_stage.in_(
                            stage_order[stage_order.index(stage):]
                        ),
                        *(
                            [Application.role_id == role_id] if role_id else []
                        ),
                    )
                )
            )
            stage_counts[stage] = int(count or 0)

        results: list[DropOffStage] = []
        for i, stage in enumerate(stage_order[:-1]):
            entered = stage_counts.get(stage, 0)
            next_stage = stage_order[i + 1]
            exited = stage_counts.get(next_stage, 0)
            dropped = max(entered - exited, 0)
            results.append(
                DropOffStage(
                    stage=stage,
                    entered=entered,
                    exited_to_next=exited,
                    dropped=dropped,
                    drop_rate=round(dropped / entered, 4) if entered > 0 else 0.0,
                )
            )
        return results


# ---------------------------------------------------------------------------
# Role pipeline health
# ---------------------------------------------------------------------------


class RolePipelineHealth(BaseModel):
    role_id: UUID
    role_title: str
    status: str
    total_applicants: int
    in_screening: int
    in_assignment: int
    in_review: int
    hired: int
    rejected: int
    avg_fit_score: float | None
    avg_days_in_pipeline: float | None
    stale_count: int


@router.get("/role-health", response_model=list[RolePipelineHealth])
async def role_pipeline_health(
    _: Annotated[str, Depends(require_viewer)],
) -> list[RolePipelineHealth]:
    """Health of each open role's pipeline — counts, scores, staleness."""
    stale_cutoff = datetime.now(tz=UTC) - timedelta(days=7)

    async with session_scope() as session:
        roles = (await session.scalars(
            select(Role).where(Role.status == "open")
        )).all()

        results: list[RolePipelineHealth] = []
        for role in roles:
            base = select(Application).where(Application.role_id == role.id)

            total = int(await session.scalar(
                select(func.count()).select_from(base.subquery())
            ) or 0)

            screening_stages = ("screening_sent", "screening_submitted")
            in_screening = int(await session.scalar(
                select(func.count()).where(
                    and_(Application.role_id == role.id,
                         Application.current_stage.in_(screening_stages))
                )
            ) or 0)

            assignment_stages = ("assignment_sent", "assignment_submitted")
            in_assignment = int(await session.scalar(
                select(func.count()).where(
                    and_(Application.role_id == role.id,
                         Application.current_stage.in_(assignment_stages))
                )
            ) or 0)

            review_stages = ("report_ready", "technical_pending_approval",
                             "ceo_pending_approval", "needs_hr_review")
            in_review = int(await session.scalar(
                select(func.count()).where(
                    and_(Application.role_id == role.id,
                         Application.current_stage.in_(review_stages))
                )
            ) or 0)

            hired = int(await session.scalar(
                select(func.count()).where(
                    and_(Application.role_id == role.id,
                         Application.status == "hired")
                )
            ) or 0)

            rejected = int(await session.scalar(
                select(func.count()).where(
                    and_(Application.role_id == role.id,
                         Application.status == "rejected")
                )
            ) or 0)

            avg_fit = await session.scalar(
                select(func.avg(cast(Application.fit_score, Float))).where(
                    and_(Application.role_id == role.id,
                         Application.fit_score.isnot(None))
                )
            )

            avg_days = await session.scalar(
                select(func.avg(
                    extract("epoch", Application.updated_at - Application.created_at) / 86400.0
                )).where(Application.role_id == role.id)
            )

            stale = int(await session.scalar(
                select(func.count()).where(
                    and_(
                        Application.role_id == role.id,
                        Application.updated_at < stale_cutoff,
                        Application.status.notin_(("rejected", "hired", "withdrawn")),
                    )
                )
            ) or 0)

            results.append(RolePipelineHealth(
                role_id=role.id,
                role_title=role.title,
                status=role.status,
                total_applicants=total,
                in_screening=in_screening,
                in_assignment=in_assignment,
                in_review=in_review,
                hired=hired,
                rejected=rejected,
                avg_fit_score=round(float(avg_fit), 1) if avg_fit else None,
                avg_days_in_pipeline=round(float(avg_days), 1) if avg_days else None,
                stale_count=stale,
            ))

        return sorted(results, key=lambda r: r.stale_count, reverse=True)


# ---------------------------------------------------------------------------
# Interviewer stats
# ---------------------------------------------------------------------------


class InterviewerStat(BaseModel):
    interviewer_email: str
    interviews_conducted: int
    avg_score: float | None
    hire_rate: float
    recommendations: dict[str, int]


@router.get("/interviewer-stats", response_model=list[InterviewerStat])
async def interviewer_stats(
    _: Annotated[str, Depends(require_viewer)],
    since_days: int = Query(90, ge=7, le=365),
) -> list[InterviewerStat]:
    """Interviewer calibration — who's scoring how, hire/no-hire distribution."""
    cutoff = datetime.now(tz=UTC) - timedelta(days=since_days)

    async with session_scope() as session:
        rows = (
            await session.scalars(
                select(Interview)
                .where(
                    and_(
                        Interview.created_at >= cutoff,
                        Interview.feedback.isnot(None),
                    )
                )
            )
        ).all()

        by_email: dict[str, dict] = {}
        for iv in rows:
            fb = iv.feedback or {}
            email = fb.get("hr_email", "unknown")
            if email not in by_email:
                by_email[email] = {
                    "count": 0,
                    "scores": [],
                    "recs": {},
                }
            by_email[email]["count"] += 1
            if fb.get("overall_score") is not None:
                by_email[email]["scores"].append(fb["overall_score"])
            rec = fb.get("recommendation", "unknown")
            by_email[email]["recs"][rec] = by_email[email]["recs"].get(rec, 0) + 1

        results: list[InterviewerStat] = []
        for email, data in by_email.items():
            scores = data["scores"]
            hire_count = data["recs"].get("hire", 0)
            results.append(
                InterviewerStat(
                    interviewer_email=email,
                    interviews_conducted=data["count"],
                    avg_score=round(sum(scores) / len(scores), 1) if scores else None,
                    hire_rate=round(hire_count / data["count"], 3) if data["count"] else 0,
                    recommendations=data["recs"],
                )
            )
        return sorted(results, key=lambda x: x.interviews_conducted, reverse=True)
