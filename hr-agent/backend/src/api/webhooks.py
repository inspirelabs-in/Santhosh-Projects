"""Inbound webhook endpoints.

V1: careers-form POST runs intake synchronously (dedup + ack) then kicks off
the V1 pipeline (parse resume -> generate screening -> email) via FastAPI
BackgroundTasks. No Temporal.
"""

from __future__ import annotations

import base64
import html
import logging
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)

from sqlalchemy import select

from src.activities.intake import run_intake
from src.db.base import Role
from src.db.connection import session_scope
from src.models.candidate import IntakePayload, IntakeResult, SourceChannel
from src.pipeline.v1 import run_apply_to_screening
from src.services.request_rate_limit import enforce_rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post(
    "/careers-form",
    response_model=IntakeResult,
    status_code=status.HTTP_201_CREATED,
    summary="Careers page form submission (V1 primary intake).",
)
async def careers_form(
    request: Request,
    background: BackgroundTasks,
    name: Annotated[str, Form()],
    email: Annotated[str, Form()],
    phone: Annotated[str | None, Form()] = None,
    role_id: Annotated[UUID | None, Form()] = None,
    role_title: Annotated[str | None, Form()] = None,
    body_text: Annotated[str | None, Form()] = None,
    consent_given: Annotated[bool, Form()] = False,
    consent_text_shown: Annotated[str | None, Form()] = None,
    resume: Annotated[UploadFile | None, File()] = None,
) -> IntakeResult:
    await enforce_rate_limit(request, "careers_form", limit=5, window_seconds=60)

    if not consent_given:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Explicit consent is required to process your application.",
        )

    if role_id is not None:
        async with session_scope() as session:
            role = await session.get(Role, role_id)
            if role is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Role not found.",
                )
            if role.status not in ("open",):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"This role is no longer accepting applications (status: {role.status}).",
                )

    if resume is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Resume is required. Please upload a resume.",
        )

    files: list[dict[str, str | None]] = []
    content = await resume.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded resume is empty.",
        )
    files.append(
            {
                "filename": resume.filename or "resume.bin",
                "content_b64": base64.b64encode(content).decode("ascii"),
                "content_type": resume.content_type,
            }
        )

    client_ip = request.client.host if request.client else None
    name = html.escape(name.strip())

    payload = IntakePayload(
        source_channel=SourceChannel.FORM,
        sender_email=email,
        sender_name=name,
        sender_phone=phone,
        body_text=body_text,
        role_id_hint=role_id,
        consent_text_shown=consent_text_shown,
        consent_ip_address=client_ip,
        raw_payload={
            "_files": files,
            "role_title": role_title,
            "consent_ip": client_ip,
        },
    )

    try:
        result = await run_intake(payload)
    except Exception:
        logger.exception("Intake failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Intake failed. Please try again in a moment.",
        )

    # Kick off V1 pipeline in background.
    resume_key = result.files_stored[0] if result.files_stored else None
    resume_filename = resume.filename if resume else None
    background.add_task(
        run_apply_to_screening,
        application_id=result.application_id,
        candidate_id=result.candidate_id,
        role_id=role_id,
        resume_r2_key=resume_key,
        resume_filename=resume_filename,
    )

    return result
