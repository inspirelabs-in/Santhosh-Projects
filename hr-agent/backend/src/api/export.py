"""CSV/JSON export endpoints for candidates, applications, and audit data.

All exports stream rows to avoid memory issues on large datasets.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, desc, select

from src.api.auth import require_recruiter
from src.db.base import Application, AuditLog, Candidate, CandidateProfileRow, Role
from src.db.connection import session_scope
from src.services.request_rate_limit import enforce_rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/export", tags=["export"])


def _csv_response(rows: list[dict[str, Any]], filename: str) -> StreamingResponse:
    if not rows:
        return StreamingResponse(
            iter(["No data"]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/candidates")
async def export_candidates(
    request: Request,
    _: Annotated[str, Depends(require_recruiter)],
    format: Literal["csv", "json"] = "csv",
    since_days: int = Query(90, ge=1, le=365),
    role_id: UUID | None = None,
    status: str | None = None,
) -> Any:
    """Export candidate + application data as CSV or JSON."""
    await enforce_rate_limit(request, "export", limit=3, window_seconds=300)
    logger.info("export/candidates requested (since_days=%d, format=%s)", since_days, format)
    cutoff = datetime.now(tz=UTC) - timedelta(days=since_days)

    async with session_scope() as session:
        filters = [Application.created_at >= cutoff]
        if role_id:
            filters.append(Application.role_id == role_id)
        if status:
            filters.append(Application.status == status)

        rows = (
            await session.execute(
                select(Application, Candidate, Role, CandidateProfileRow)
                .join(Candidate, Candidate.id == Application.candidate_id)
                .join(Role, Role.id == Application.role_id, isouter=True)
                .join(
                    CandidateProfileRow,
                    and_(
                        CandidateProfileRow.candidate_id == Candidate.id,
                    ),
                    isouter=True,
                )
                .where(and_(*filters))
                .order_by(desc(Application.created_at))
                .limit(5000)
            )
        ).all()

        seen_apps: set[UUID] = set()
        data: list[dict[str, Any]] = []
        for app, cand, role, profile in rows:
            if app.id in seen_apps:
                continue
            seen_apps.add(app.id)

            parsed = (profile.parsed_data or {}) if profile else {}
            skills = parsed.get("skills") or parsed.get("top_skills") or []
            if isinstance(skills, list):
                skills = ", ".join(str(s) for s in skills[:10])

            work = parsed.get("work_history") or parsed.get("experience") or []
            current_title = ""
            if isinstance(work, list) and work and isinstance(work[0], dict):
                current_title = work[0].get("title", "")

            data.append({
                "application_id": str(app.id),
                "candidate_name": cand.name or "",
                "candidate_email": cand.email or "",
                "candidate_phone": cand.phone or "",
                "role": role.title if role else "",
                "source": cand.source_channel or "",
                "status": app.status,
                "current_stage": app.current_stage,
                "fit_score": app.fit_score or "",
                "fit_tier": app.fit_tier or "",
                "screening_score": app.screening_score or "",
                "current_title": current_title,
                "skills": skills,
                "experience_years": parsed.get("total_experience_years", ""),
                "location": parsed.get("location", ""),
                "applied_at": app.created_at.isoformat() if app.created_at else "",
                "updated_at": app.updated_at.isoformat() if app.updated_at else "",
            })

    if format == "json":
        return data

    ts = datetime.now(tz=UTC).strftime("%Y%m%d")
    return _csv_response(data, f"candidates_export_{ts}.csv")


@router.get("/audit")
async def export_audit(
    request: Request,
    _: Annotated[str, Depends(require_recruiter)],
    since_days: int = Query(30, ge=1, le=365),
    action: str | None = None,
) -> Any:
    """Export audit log as CSV."""
    await enforce_rate_limit(request, "export", limit=3, window_seconds=300)
    logger.info("export/audit requested (since_days=%d)", since_days)
    cutoff = datetime.now(tz=UTC) - timedelta(days=since_days)

    async with session_scope() as session:
        filters = [AuditLog.created_at >= cutoff]
        if action:
            filters.append(AuditLog.action == action)

        rows = (
            await session.scalars(
                select(AuditLog)
                .where(and_(*filters))
                .order_by(desc(AuditLog.id))
                .limit(10000)
            )
        ).all()

        data = [
            {
                "id": r.id,
                "candidate_id": str(r.candidate_id) if r.candidate_id else "",
                "application_id": str(r.application_id) if r.application_id else "",
                "action": r.action,
                "actor": r.actor,
                "model_version": r.model_version or "",
                "prompt_version": r.prompt_version or "",
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ]

    ts = datetime.now(tz=UTC).strftime("%Y%m%d")
    return _csv_response(data, f"audit_export_{ts}.csv")


@router.get("/pipeline-summary")
async def export_pipeline_summary(
    _: Annotated[str, Depends(require_recruiter)],
) -> Any:
    """Export per-role pipeline summary as CSV for management reporting."""
    async with session_scope() as session:
        roles = (await session.scalars(select(Role))).all()

        data: list[dict[str, Any]] = []
        for role in roles:
            from sqlalchemy import func as sqlfunc
            apps = (
                await session.execute(
                    select(
                        Application.status,
                        sqlfunc.count().label("cnt"),
                    )
                    .where(Application.role_id == role.id)
                    .group_by(Application.status)
                )
            ).all()
            status_map = {s: c for s, c in apps}
            total = sum(status_map.values())

            data.append({
                "role": role.title,
                "role_status": role.status,
                "total_applicants": total,
                "active": status_map.get("active", 0) + status_map.get("scored", 0)
                    + status_map.get("shortlisted", 0) + status_map.get("screening_in_progress", 0),
                "hired": status_map.get("hired", 0),
                "rejected": status_map.get("rejected", 0),
                "cold": status_map.get("cold", 0),
                "withdrawn": status_map.get("withdrawn", 0),
            })

    ts = datetime.now(tz=UTC).strftime("%Y%m%d")
    return _csv_response(data, f"pipeline_summary_{ts}.csv")
