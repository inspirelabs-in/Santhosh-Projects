"""Role management (CRUD). Consumed by the Roles section of the dashboard."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Annotated, Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from src.api.auth import require_recruiter, require_viewer
from src.config import get_settings
from src.db.base import PanelMember, Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.models.candidate import RemotePolicy, RoleStatus
from src.models.scheduling import AvailabilityWindow, RoleScheduling
from src.services.file_storage import download, presigned_get_url, upload_resume
from src.services.scheduling import (
    _graph_busy_intervals,
    find_candidate_slot,
)

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
    pipeline_template: list[str] | None
    screening_modality: str
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
    pipeline_template: list[str] | None = None


class RolePatch(BaseModel):
    """Partial update — only fields present in the JSON body are written."""

    model_config = {"extra": "ignore"}

    title: str | None = None
    jd_text: str | None = None
    screening_questions: list[dict[str, Any]] | None = None
    scoring_rubric: dict[str, Any] | None = None
    cut_line: int | None = None
    interviewer_panel: list[dict[str, Any]] | None = None
    ctc_min_lpa: float | None = None
    ctc_max_lpa: float | None = None
    max_notice_days: int | None = None
    location: str | None = None
    remote_policy: str | None = None
    status: str | None = None
    assignment_brief: str | None = None
    assignment_instructions: str | None = None
    assignment_deadline_days: int | None = None
    pipeline_template: list[str] | None = None
    screening_modality: str | None = None


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
        pipeline_template=r.pipeline_template,
        screening_modality=r.screening_modality,
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
            pipeline_template=payload.pipeline_template,
        )
        if payload.pipeline_template:
            from src.services.pipeline_templates import validate_template
            errors = validate_template(payload.pipeline_template)
            if errors:
                raise HTTPException(status_code=422, detail={"pipeline_template_errors": errors})
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
        if payload.pipeline_template:
            from src.services.pipeline_templates import validate_template
            errors = validate_template(payload.pipeline_template)
            if errors:
                raise HTTPException(status_code=422, detail={"pipeline_template_errors": errors})
        role.pipeline_template = payload.pipeline_template
        await log_audit(
            session,
            action="role_updated",
            actor="dashboard",
            details={"role_id": str(role.id), "before": before, "after": {"title": role.title, "status": role.status, "cut_line": role.cut_line}},
        )
        return _to_read(role)


@router.patch("/{role_id}", response_model=RoleRead)
async def patch_role(
    role_id: UUID,
    payload: RolePatch,
    _: Annotated[str, Depends(require_recruiter)],
) -> RoleRead:
    """Partial update — only fields present in the request body are written."""
    async with session_scope() as session:
        role = await session.get(Role, role_id)
        if role is None:
            raise HTTPException(status_code=404, detail="Role not found")
        supplied = payload.model_fields_set
        changed: dict[str, Any] = {}
        for field in supplied:
            val = getattr(payload, field)
            if field == "pipeline_template" and val is not None:
                from src.services.pipeline_templates import validate_template
                errors = validate_template(val)
                if errors:
                    raise HTTPException(status_code=422, detail={"pipeline_template_errors": errors})
            if field == "remote_policy" and val is not None:
                val = val if val in ("onsite", "hybrid", "remote") else None
            if field == "status" and val is not None:
                val = val if val in ("open", "paused", "filled", "cancelled") else role.status
            if field == "screening_modality":
                val = "voice"
            setattr(role, field, val)
            changed[field] = val
        await log_audit(
            session,
            action="role_updated",
            actor="dashboard",
            details={"role_id": str(role.id), "fields": list(changed.keys())},
        )
        return _to_read(role)


@router.get("/pipeline/presets")
async def get_pipeline_presets(
    _: Annotated[str, Depends(require_viewer)],
) -> dict[str, Any]:
    from src.services.pipeline_templates import PRESETS, STEP_REGISTRY
    steps = [
        {
            "id": s.id,
            "label": s.label,
            "category": s.category,
            "is_meeting": s.is_meeting,
        }
        for s in STEP_REGISTRY.values()
    ]
    return {"steps": steps, "presets": PRESETS}


@router.get("/pipeline/availability")
async def check_availability(
    _: Annotated[str, Depends(require_viewer)],
    emails: str = Query(..., description="Comma-separated panel emails"),
    duration_minutes: int = Query(45, ge=15, le=240),
    horizon_days: int = Query(10, ge=1, le=30),
) -> dict:
    """Check free/busy availability for specific panel members.

    Returns available windows of ``duration_minutes`` length within
    business hours (weekdays 11:00-18:00 IST) for the next
    ``horizon_days``.
    """
    panel_emails = [e.strip() for e in emails.split(",") if e.strip()]
    if not panel_emails:
        raise HTTPException(status_code=422, detail="No valid emails provided")

    tz = ZoneInfo("Asia/Kolkata")
    now_utc = datetime.now(timezone.utc)
    start_utc = now_utc
    end_utc = now_utc + timedelta(days=horizon_days)

    # Get organiser email for Graph call
    try:
        from src.services.online_meeting import get_organiser_email
        organiser = get_organiser_email() or panel_emails[0]
    except Exception:  # noqa: BLE001
        organiser = panel_emails[0]

    busy = await _graph_busy_intervals(
        panel_emails=panel_emails,
        start_utc=start_utc,
        end_utc=end_utc,
        organiser_email=organiser,
    )
    graph_available = busy is not None
    busy_blocks: list[tuple[datetime, datetime]] = busy or []

    # Generate candidate windows: weekdays 11:00-18:00 IST
    duration = timedelta(minutes=duration_minutes)
    available_slots: list[dict[str, str]] = []
    now_local = now_utc.astimezone(tz)
    for d in range(horizon_days):
        day_local = (now_local + timedelta(days=d)).date()
        if day_local.weekday() >= 5:  # skip weekends
            continue
        day_start = datetime.combine(day_local, time(11, 0), tzinfo=tz).astimezone(timezone.utc)
        day_end = datetime.combine(day_local, time(18, 0), tzinfo=tz).astimezone(timezone.utc)
        cursor = max(day_start, start_utc)
        # Round up to next 30-min boundary
        if cursor.minute % 30 != 0:
            cursor = cursor + timedelta(minutes=30 - cursor.minute % 30)
            cursor = cursor.replace(second=0, microsecond=0)
        while cursor + duration <= day_end:
            slot_end = cursor + duration
            collides = any(bs < slot_end and be > cursor for bs, be in busy_blocks)
            if not collides:
                available_slots.append({
                    "start": cursor.isoformat().replace("+00:00", "Z"),
                    "end": slot_end.isoformat().replace("+00:00", "Z"),
                })
            cursor += timedelta(minutes=30)
            if len(available_slots) >= 100:
                break
        if len(available_slots) >= 100:
            break

    return {
        "panel_emails": panel_emails,
        "duration_minutes": duration_minutes,
        "horizon_days": horizon_days,
        "graph_connected": graph_available,
        "available_slots": available_slots,
    }


@router.post("/{role_id}/availability")
async def check_panel_availability(
    role_id: UUID,
    _: Annotated[str, Depends(require_viewer)],
) -> dict:
    """Check panel availability for each interview round of a role.

    Loads the role's scheduling config and, for each round (technical,
    ceo, hr), resolves panel emails (with PanelMember fallback) and
    finds the next available slot.
    """
    async with session_scope() as session:
        role = await session.get(Role, role_id)
        if role is None:
            raise HTTPException(status_code=404, detail="Role not found")

        cfg = RoleScheduling.from_role_rubric(role.scoring_rubric)
        rounds_result: dict[str, dict[str, Any]] = {}

        for round_name in ("technical", "ceo", "hr"):
            round_cfg = cfg.rounds.get(round_name)  # type: ignore[arg-type]
            if round_cfg is None:
                rounds_result[round_name] = {
                    "panel_emails": [],
                    "next_slot": None,
                    "status": "not_configured",
                }
                continue

            # Panel email fallback: workspace PanelMember directory
            if not round_cfg.panel_emails:
                members = (
                    await session.execute(
                        select(PanelMember).where(
                            PanelMember.role_type == round_name,
                            PanelMember.is_active.is_(True),
                        )
                    )
                ).scalars().all()
                if members:
                    round_cfg = round_cfg.model_copy(
                        update={"panel_emails": [m.email for m in members]}
                    )

            # Default availability windows fallback (weekday 11-18 IST)
            if not round_cfg.windows:
                round_cfg = round_cfg.model_copy(
                    update={
                        "windows": [
                            AvailabilityWindow(
                                days=[0, 1, 2, 3, 4],
                                start_hhmm="11:00",
                                end_hhmm="18:00",
                            )
                        ]
                    }
                )

            if not round_cfg.panel_emails:
                rounds_result[round_name] = {
                    "panel_emails": [],
                    "next_slot": None,
                    "status": "not_configured",
                }
                continue

            slot = await find_candidate_slot(
                session,
                round_cfg=round_cfg,
                panel_tz=cfg.panel_timezone,
                horizon_days=cfg.horizon_business_days,
                min_lead_hours=cfg.min_lead_hours,
            )

            if slot is None:
                rounds_result[round_name] = {
                    "panel_emails": list(round_cfg.panel_emails),
                    "next_slot": None,
                    "status": "no_slots",
                }
            else:
                rounds_result[round_name] = {
                    "panel_emails": list(round_cfg.panel_emails),
                    "next_slot": slot.start.isoformat().replace("+00:00", "Z"),
                    "status": "available",
                }

    return {"rounds": rounds_result}


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
        from src.services.typed_event_bus import EventType, publish_event
        from sqlalchemy import select
        from src.db.base import Application
        active_apps = (await session.execute(
            select(Application.id, Application.candidate_id)
            .where(Application.role_id == role_id)
            .where(Application.status == "active")
        )).all()
        for app_id, cand_id in active_apps:
            await publish_event(
                session, EventType.ROLE_FROZEN,
                application_id=app_id, candidate_id=cand_id,
                payload={"role_id": str(role_id), "role_title": role.title},
                dedup_extra=f"role_paused:{role_id}",
            )
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
    inline: bool = False,
):
    import mimetypes

    async with session_scope() as session:
        role = await session.get(Role, role_id)
        if role is None or not role.assignment_problem_doc_key:
            raise HTTPException(status_code=404, detail="no_problem_doc")
        content = await download(_settings.r2_bucket_resumes, role.assignment_problem_doc_key)
        filename = role.assignment_problem_filename or "problem-statement.docx"
        mime, _ = mimetypes.guess_type(filename)
        media_type = mime or "application/octet-stream"
        disposition = "inline" if inline else "attachment"
        return StreamingResponse(
            iter([content]),
            media_type=media_type,
            headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
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
