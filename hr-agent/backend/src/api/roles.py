"""Role management (CRUD). Consumed by the Roles section of the dashboard."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from src.api.auth import require_recruiter, require_viewer
from src.config import get_settings
from src.db.base import Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.models.candidate import RemotePolicy, RoleStatus
from src.services.file_storage import download, presigned_get_url, upload_resume

_settings = get_settings()

router = APIRouter(prefix="/dashboard/roles", tags=["roles"])
careers_router = APIRouter(prefix="/careers", tags=["careers"])


class RoleRead(BaseModel):
    id: UUID
    title: str
    jd_text: str
    screening_questions: list[dict[str, Any]]
    scoring_rubric: dict[str, Any]
    cut_line: int
    interviewer_panel: list[dict[str, Any]]
    status: str
    ctc_min_lpa: float | None
    ctc_max_lpa: float | None
    max_notice_days: int | None
    location: str | None
    remote_policy: str | None
    # V1 assignment fields
    assignment_brief: str | None
    assignment_instructions: str | None
    assignment_deadline_days: int
    assignment_problem_doc_filename: str | None
    has_problem_doc: bool
    pi_cognitive_url: str | None
    created_at: datetime


class RoleWrite(BaseModel):
    title: str = Field(..., min_length=2, max_length=255)
    jd_text: str = Field(..., min_length=10)
    screening_questions: list[dict[str, Any]] = Field(default_factory=list)
    scoring_rubric: dict[str, Any] = Field(default_factory=dict)
    cut_line: int = Field(60, ge=0, le=100)
    interviewer_panel: list[dict[str, Any]] = Field(default_factory=list)
    ctc_min_lpa: float | None = None
    ctc_max_lpa: float | None = None
    max_notice_days: int | None = None
    location: str | None = None
    remote_policy: RemotePolicy | None = None
    status: RoleStatus = RoleStatus.OPEN
    # V1 assignment fields (HR sets these at role creation)
    assignment_brief: str | None = None
    assignment_instructions: str | None = None
    assignment_deadline_days: int = Field(7, ge=1, le=60)
    pi_cognitive_url: str | None = None


def _to_read(r: Role) -> RoleRead:
    return RoleRead(
        id=r.id,
        title=r.title,
        jd_text=r.jd_text,
        screening_questions=list(r.screening_questions or []),
        scoring_rubric=dict(r.scoring_rubric or {}),
        cut_line=r.cut_line,
        interviewer_panel=list(r.interviewer_panel or []),
        status=r.status,
        ctc_min_lpa=r.ctc_min_lpa,
        ctc_max_lpa=r.ctc_max_lpa,
        max_notice_days=r.max_notice_days,
        location=r.location,
        remote_policy=r.remote_policy,
        assignment_brief=r.assignment_brief,
        assignment_instructions=r.assignment_instructions,
        assignment_deadline_days=r.assignment_deadline_days,
        assignment_problem_doc_filename=r.assignment_problem_filename,
        has_problem_doc=bool(r.assignment_problem_doc_key),
        pi_cognitive_url=r.pi_cognitive_url,
        created_at=r.created_at,
    )


@router.get("", response_model=list[RoleRead])
async def list_roles(
    _: Annotated[str, Depends(require_viewer)],
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(100, le=500),
) -> list[RoleRead]:
    async with session_scope() as session:
        stmt = select(Role).order_by(desc(Role.created_at)).limit(limit)
        if status_filter:
            stmt = stmt.where(Role.status == status_filter)
        rows = (await session.scalars(stmt)).all()
        return [_to_read(r) for r in rows]


@router.get("/{role_id}", response_model=RoleRead)
async def get_role(
    role_id: UUID,
    _: Annotated[str, Depends(require_viewer)],
) -> RoleRead:
    async with session_scope() as session:
        role = await session.get(Role, role_id)
        if role is None:
            raise HTTPException(status_code=404, detail="Role not found")
        return _to_read(role)


@router.post("", response_model=RoleRead, status_code=status.HTTP_201_CREATED)
async def create_role(
    payload: RoleWrite,
    _: Annotated[str, Depends(require_recruiter)],
) -> RoleRead:
    async with session_scope() as session:
        role = Role(
            title=payload.title,
            jd_text=payload.jd_text,
            screening_questions=payload.screening_questions,
            scoring_rubric=payload.scoring_rubric,
            cut_line=payload.cut_line,
            interviewer_panel=payload.interviewer_panel,
            ctc_min_lpa=payload.ctc_min_lpa,
            ctc_max_lpa=payload.ctc_max_lpa,
            max_notice_days=payload.max_notice_days,
            location=payload.location,
            remote_policy=payload.remote_policy.value if payload.remote_policy else None,
            status=payload.status.value,
            assignment_brief=payload.assignment_brief,
            assignment_instructions=payload.assignment_instructions,
            assignment_deadline_days=payload.assignment_deadline_days,
            pi_cognitive_url=payload.pi_cognitive_url,
        )
        session.add(role)
        await session.flush()
        await log_audit(
            session,
            action="role_created",
            actor="dashboard",
            details={"role_id": str(role.id), "title": role.title, "status": role.status},
        )
        return _to_read(role)


@router.put("/{role_id}", response_model=RoleRead)
async def update_role(
    role_id: UUID,
    payload: RoleWrite,
    _: Annotated[str, Depends(require_recruiter)],
) -> RoleRead:
    async with session_scope() as session:
        role = await session.get(Role, role_id)
        if role is None:
            raise HTTPException(status_code=404, detail="Role not found")
        before = {
            "title": role.title,
            "status": role.status,
            "cut_line": role.cut_line,
        }
        role.title = payload.title
        role.jd_text = payload.jd_text
        role.screening_questions = payload.screening_questions
        role.scoring_rubric = payload.scoring_rubric
        role.cut_line = payload.cut_line
        role.interviewer_panel = payload.interviewer_panel
        role.ctc_min_lpa = payload.ctc_min_lpa
        role.ctc_max_lpa = payload.ctc_max_lpa
        role.max_notice_days = payload.max_notice_days
        role.location = payload.location
        role.remote_policy = payload.remote_policy.value if payload.remote_policy else None
        role.status = payload.status.value
        role.assignment_brief = payload.assignment_brief
        role.assignment_instructions = payload.assignment_instructions
        role.assignment_deadline_days = payload.assignment_deadline_days
        role.pi_cognitive_url = payload.pi_cognitive_url
        await log_audit(
            session,
            action="role_updated",
            actor="dashboard",
            details={"role_id": str(role.id), "before": before, "after": {"title": role.title, "status": role.status, "cut_line": role.cut_line}},
        )
        return _to_read(role)


@router.post("/{role_id}/pause", response_model=RoleRead)
async def pause_role(
    role_id: UUID,
    _: Annotated[str, Depends(require_recruiter)],
) -> RoleRead:
    async with session_scope() as session:
        role = await session.get(Role, role_id)
        if role is None:
            raise HTTPException(status_code=404, detail="Role not found")
        role.status = RoleStatus.PAUSED.value
        await log_audit(session, action="role_paused", actor="dashboard", details={"role_id": str(role.id)})
        return _to_read(role)


@router.post("/{role_id}/reopen", response_model=RoleRead)
async def reopen_role(
    role_id: UUID,
    _: Annotated[str, Depends(require_recruiter)],
) -> RoleRead:
    async with session_scope() as session:
        role = await session.get(Role, role_id)
        if role is None:
            raise HTTPException(status_code=404, detail="Role not found")
        role.status = RoleStatus.OPEN.value
        await log_audit(session, action="role_reopened", actor="dashboard", details={"role_id": str(role.id)})
        return _to_read(role)


@router.post("/{role_id}/problem-doc", response_model=RoleRead)
async def upload_problem_doc(
    role_id: UUID,
    _: Annotated[str, Depends(require_recruiter)],
    file: Annotated[UploadFile, File()],
) -> RoleRead:
    """HR uploads the Word document with the problem statement for this role."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty_file")
    filename = file.filename or "problem-statement.docx"
    async with session_scope() as session:
        role = await session.get(Role, role_id)
        if role is None:
            raise HTTPException(status_code=404, detail="role_not_found")
        stored = await upload_resume(
            candidate_id=role_id,  # reuse key namespace; role_id prefixes the path
            filename=f"roles/{role_id}/{filename}",
            content=content,
            content_type=file.content_type or "application/octet-stream",
        )
        role.assignment_problem_doc_key = stored.key
        role.assignment_problem_filename = filename
        await log_audit(
            session,
            action="role_problem_doc_uploaded",
            actor="dashboard",
            details={"role_id": str(role_id), "filename": filename, "size": len(content)},
        )
        return _to_read(role)


@router.get("/{role_id}/problem-doc/download")
async def download_problem_doc(
    role_id: UUID,
    _: Annotated[str, Depends(require_viewer)],
):
    async with session_scope() as session:
        role = await session.get(Role, role_id)
        if role is None or not role.assignment_problem_doc_key:
            raise HTTPException(status_code=404, detail="no_problem_doc")
        content = await download(_settings.r2_bucket_resumes, role.assignment_problem_doc_key)
        filename = role.assignment_problem_filename or "problem-statement.docx"
        return StreamingResponse(
            iter([content]),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


@router.post("/{role_id}/close", response_model=RoleRead)
async def close_role(
    role_id: UUID,
    _: Annotated[str, Depends(require_recruiter)],
) -> RoleRead:
    async with session_scope() as session:
        role = await session.get(Role, role_id)
        if role is None:
            raise HTTPException(status_code=404, detail="Role not found")
        role.status = RoleStatus.FILLED.value
        await log_audit(session, action="role_closed", actor="dashboard", details={"role_id": str(role.id)})
        return _to_read(role)


class CareersRoleItem(BaseModel):
    id: UUID
    title: str
    location: str | None = None
    remote_policy: str | None = None
    department: str | None = None
    description: str | None = None
    status: str


@careers_router.get("/roles", response_model=list[CareersRoleItem])
async def careers_list_roles() -> list[CareersRoleItem]:
    async with session_scope() as session:
        rows = (
            await session.scalars(
                select(Role)
                .where(Role.status == "open")
                .order_by(desc(Role.created_at))
            )
        ).all()
        return [
            CareersRoleItem(
                id=r.id,
                title=r.title,
                location=r.location,
                remote_policy=r.remote_policy,
                description=r.jd_text[:500] if r.jd_text else None,
                status=r.status,
            )
            for r in rows
        ]
