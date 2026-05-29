"""Workspace panel-member directory.

CRUD endpoints behind /dashboard/panels. HR maintains a small list of
interviewers (technical/hr/ceo) once; roles reference them when
configuring per-round panels.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from src.api.auth import require_recruiter, require_viewer
from src.db.base import PanelMember
from src.db.connection import session_scope

router = APIRouter(prefix="/dashboard/panels", tags=["panels"])


RoleTypeLiteral = Literal["technical", "hr", "ceo"]
CalendarProviderLiteral = Literal["microsoft", "google", "none"]


class PanelMemberCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    role_type: RoleTypeLiteral
    job_title: str | None = None
    timezone: str = "Asia/Kolkata"
    calendar_provider: CalendarProviderLiteral = "microsoft"
    calendar_id: str | None = None
    notes: str | None = None


class PanelMemberUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    email: EmailStr | None = None
    role_type: RoleTypeLiteral | None = None
    job_title: str | None = None
    timezone: str | None = None
    calendar_provider: CalendarProviderLiteral | None = None
    calendar_id: str | None = None
    notes: str | None = None
    is_active: bool | None = None


class PanelMemberOut(BaseModel):
    id: UUID
    name: str
    email: str
    role_type: str
    job_title: str | None
    timezone: str
    calendar_provider: str
    calendar_id: str | None
    is_active: bool
    notes: str | None
    created_at: datetime
    updated_at: datetime


def _to_out(row: PanelMember) -> PanelMemberOut:
    # Legacy "founder" rows surface as "ceo" so existing data still works
    # after we collapsed the panel taxonomy.
    role_type = "ceo" if row.role_type == "founder" else row.role_type
    return PanelMemberOut(
        id=row.id,
        name=row.name,
        email=row.email,
        role_type=role_type,
        job_title=row.job_title,
        timezone=row.timezone,
        calendar_provider=row.calendar_provider,
        calendar_id=row.calendar_id,
        is_active=row.is_active,
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("", response_model=list[PanelMemberOut])
async def list_panel_members(
    _: Annotated[str, Depends(require_viewer)],
    role_type: RoleTypeLiteral | None = None,
    include_inactive: bool = False,
) -> list[PanelMemberOut]:
    async with session_scope() as session:
        stmt = select(PanelMember).order_by(PanelMember.role_type, PanelMember.name)
        if role_type:
            stmt = stmt.where(PanelMember.role_type == role_type)
        if not include_inactive:
            stmt = stmt.where(PanelMember.is_active.is_(True))
        rows = (await session.execute(stmt)).scalars().all()
        return [_to_out(r) for r in rows]


@router.post("", response_model=PanelMemberOut, status_code=status.HTTP_201_CREATED)
async def create_panel_member(
    body: PanelMemberCreate,
    _: Annotated[str, Depends(require_recruiter)],
) -> PanelMemberOut:
    async with session_scope() as session:
        existing = (
            await session.execute(
                select(PanelMember).where(PanelMember.email == body.email)
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"panel member with email {body.email} already exists",
            )
        row = PanelMember(
            name=body.name,
            email=body.email,
            role_type=body.role_type,
            job_title=body.job_title,
            timezone=body.timezone,
            calendar_provider=body.calendar_provider,
            calendar_id=body.calendar_id,
            notes=body.notes,
        )
        session.add(row)
        await session.flush()
        return _to_out(row)


@router.patch("/{member_id}", response_model=PanelMemberOut)
async def update_panel_member(
    member_id: UUID,
    body: PanelMemberUpdate,
    _: Annotated[str, Depends(require_recruiter)],
) -> PanelMemberOut:
    async with session_scope() as session:
        row = await session.get(PanelMember, member_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "panel member not found")
        update = body.model_dump(exclude_unset=True)
        for key, val in update.items():
            setattr(row, key, val)
        await session.flush()
        return _to_out(row)


@router.delete("/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_panel_member(
    member_id: UUID,
    _: Annotated[str, Depends(require_recruiter)],
) -> None:
    """Soft-delete by flipping is_active to false. Hard-delete only when no
    role references this email so historical meetings stay intact."""
    async with session_scope() as session:
        row = await session.get(PanelMember, member_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "panel member not found")
        row.is_active = False
