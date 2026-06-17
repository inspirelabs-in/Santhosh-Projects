"""Voice campaign API: create, start, pause, cancel, and track bulk call campaigns."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from src.config import get_settings
from src.db.base import Application, Candidate
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.voice_campaign import (
    cancel_campaign,
    create_campaign,
    get_campaign,
    get_campaign_progress,
    pause_campaign,
    start_campaign,
)
from src.models.v1 import CallKind

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agentic/voice-campaigns", tags=["voice-campaigns"])


class CreateCampaignBody(BaseModel):
    call_kind: str = Field(..., description="One of: status_update, meeting_schedule, joining_details, screening")
    application_ids: list[UUID] = Field(..., min_length=1, max_length=500)
    name: str = Field(..., max_length=255)
    role_id: UUID | None = None
    max_concurrent: int = Field(default=10, ge=1, le=100)
    dispatch_rate_per_minute: int = Field(default=5, ge=1, le=60)


class CampaignResponse(BaseModel):
    id: UUID
    status: str
    call_kind: str
    name: str
    total_calls: int
    completed_calls: int
    failed_calls: int


class CampaignDetailResponse(CampaignResponse):
    progress: dict[str, int] = {}
    max_concurrent: int = 10


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_voice_campaign(body: CreateCampaignBody) -> CampaignResponse:
    """Create a campaign with target application IDs. No VoiceCall rows created yet."""

    settings = get_settings()
    if not (settings.enable_voice_calls or settings.enable_voice_screening):
        raise HTTPException(400, "voice calls not enabled")

    try:
        kind = CallKind(body.call_kind)
    except ValueError:
        raise HTTPException(400, f"invalid call_kind: {body.call_kind}")

    async with session_scope() as session:
        valid_ids: list[str] = []
        skipped = 0
        for app_id in body.application_ids:
            app = await session.get(Application, app_id)
            if app is None:
                skipped += 1
                continue
            candidate = await session.get(Candidate, app.candidate_id)
            if candidate is None or not candidate.phone:
                skipped += 1
                continue
            valid_ids.append(str(app_id))

        campaign = await create_campaign(
            session,
            call_kind=kind.value,
            name=body.name,
            target_application_ids=valid_ids,
            role_id=body.role_id,
            max_concurrent=min(body.max_concurrent, settings.voice_campaign_max_concurrent),
            dispatch_rate_per_minute=min(body.dispatch_rate_per_minute, settings.voice_campaign_dispatch_rate_per_minute),
            created_by="hr",
        )
        campaign_id = campaign.id
        total = len(valid_ids)

        await log_audit(
            session,
            application_id=None,
            action="voice_campaign_created",
            actor="hr",
            details={
                "campaign_id": str(campaign_id),
                "call_kind": kind.value,
                "total": total,
                "skipped": skipped,
            },
        )

    return CampaignResponse(
        id=campaign_id,
        status="draft",
        call_kind=kind.value,
        name=body.name,
        total_calls=total,
        completed_calls=0,
        failed_calls=0,
    )


@router.post("/{campaign_id}/start")
async def start_voice_campaign(campaign_id: UUID) -> CampaignResponse:
    async with session_scope() as session:
        campaign = await get_campaign(session, campaign_id)
        if campaign is None:
            raise HTTPException(404, "campaign not found")
        if campaign.status not in ("draft", "paused"):
            raise HTTPException(400, f"cannot start campaign in status: {campaign.status}")
        await start_campaign(session, campaign_id)
        resp = CampaignResponse(
            id=campaign_id,
            status="dispatching",
            call_kind=campaign.call_kind,
            name=campaign.name,
            total_calls=campaign.total_calls,
            completed_calls=campaign.completed_calls,
            failed_calls=campaign.failed_calls,
        )

    return resp


@router.get("/{campaign_id}")
async def get_voice_campaign(campaign_id: UUID) -> CampaignDetailResponse:
    async with session_scope() as session:
        campaign = await get_campaign(session, campaign_id)
        if campaign is None:
            raise HTTPException(404, "campaign not found")
        progress = await get_campaign_progress(session, campaign_id)
        resp = CampaignDetailResponse(
            id=campaign_id,
            status=campaign.status,
            call_kind=campaign.call_kind,
            name=campaign.name,
            total_calls=campaign.total_calls,
            completed_calls=campaign.completed_calls,
            failed_calls=campaign.failed_calls,
            progress=progress,
            max_concurrent=campaign.max_concurrent,
        )

    return resp


@router.post("/{campaign_id}/pause")
async def pause_voice_campaign(campaign_id: UUID) -> CampaignResponse:
    async with session_scope() as session:
        campaign = await get_campaign(session, campaign_id)
        if campaign is None:
            raise HTTPException(404, "campaign not found")
        if campaign.status != "dispatching":
            raise HTTPException(400, f"cannot pause campaign in status: {campaign.status}")
        await pause_campaign(session, campaign_id)
        resp = CampaignResponse(
            id=campaign_id,
            status="paused",
            call_kind=campaign.call_kind,
            name=campaign.name,
            total_calls=campaign.total_calls,
            completed_calls=campaign.completed_calls,
            failed_calls=campaign.failed_calls,
        )

    return resp


@router.post("/{campaign_id}/cancel")
async def cancel_voice_campaign(campaign_id: UUID) -> CampaignResponse:
    async with session_scope() as session:
        campaign = await get_campaign(session, campaign_id)
        if campaign is None:
            raise HTTPException(404, "campaign not found")
        if campaign.status in ("completed", "cancelled"):
            raise HTTPException(400, f"campaign already {campaign.status}")
        await cancel_campaign(session, campaign_id)
        resp = CampaignResponse(
            id=campaign_id,
            status="cancelled",
            call_kind=campaign.call_kind,
            name=campaign.name,
            total_calls=campaign.total_calls,
            completed_calls=campaign.completed_calls,
            failed_calls=campaign.failed_calls,
        )

    return resp
