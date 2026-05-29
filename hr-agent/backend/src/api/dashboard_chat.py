"""HR dashboard read-only view of a candidate's V2 chat conversation.

The candidate-facing chat endpoints (``/v2/chat/...``) are token-scoped so a
recruiter cannot reach them. This router exposes the same data via the
existing dashboard-key auth so HR can audit the screening + assignment
flow without impersonating the candidate.

Live updates piggy-back on the existing per-application Redis pubsub
(``services/events.py``); the frontend listens via ``useApplicationEvents``
and refetches when ``chat_message`` / ``chat_stage_change`` events fire.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.auth import require_viewer
from src.db.base import Application
from src.db.connection import session_scope
from src.db.repositories import (
    assignment as assignment_repo,
    conversation as conversation_repo,
    screening_answer as screening_repo,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard/v2/applications", tags=["dashboard-chat"])


class DashboardMessage(BaseModel):
    sequence: int
    role: str
    content: str | None
    created_at: datetime
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None


class DashboardScreeningAnswers(BaseModel):
    tailored_q1: str | None = None
    tailored_a1: str | None = None
    tailored_q2: str | None = None
    tailored_a2: str | None = None
    current_ctc_lpa: float | None = None
    expected_ctc_lpa: float | None = None
    notice_period_days: int | None = None
    willing_to_relocate: bool | None = None
    composite_score: int | None = None
    knock_out_triggered: bool = False
    knock_out_reason: str | None = None
    submitted_at: datetime | None = None
    evaluation: dict[str, Any] | None = None


class DashboardAssignment(BaseModel):
    brief_md: str | None = None
    problems: list[dict[str, Any]] | None = None
    submission_format: dict[str, Any] | None = None
    evaluation_rubric: dict[str, Any] | None = None
    submission_url: str | None = None
    submission_text: str | None = None
    submission_r2_keys: list[str] | None = None
    submitted_at: datetime | None = None
    score: int | None = None
    evaluation: dict[str, Any] | None = None


class DashboardConversationView(BaseModel):
    application_id: UUID
    conversation_id: UUID
    stage: str
    created_at: datetime
    updated_at: datetime
    messages: list[DashboardMessage]
    screening: DashboardScreeningAnswers | None = None
    assignment: DashboardAssignment | None = None


@router.get(
    "/{application_id}/conversation",
    response_model=DashboardConversationView | None,
    dependencies=[Depends(require_viewer)],
)
async def get_conversation(application_id: UUID) -> DashboardConversationView | None:
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            return None
        conv = await conversation_repo.get_conversation_by_application(
            session, application_id
        )
        if conv is None:
            return None
        msgs = await conversation_repo.list_messages(session, conv.id)
        screening_row = await screening_repo.get(session, application_id)
        assignment_row = await assignment_repo.get(session, application_id)

    return DashboardConversationView(
        application_id=application_id,
        conversation_id=conv.id,
        stage=conv.stage,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=[
            DashboardMessage(
                sequence=m.sequence,
                role=m.role,
                content=m.content,
                created_at=m.created_at,
                model=m.model,
                input_tokens=m.input_tokens,
                output_tokens=m.output_tokens,
                latency_ms=m.latency_ms,
            )
            for m in msgs
        ],
        screening=(
            DashboardScreeningAnswers(
                tailored_q1=screening_row.tailored_q1,
                tailored_a1=screening_row.tailored_a1,
                tailored_q2=screening_row.tailored_q2,
                tailored_a2=screening_row.tailored_a2,
                current_ctc_lpa=float(screening_row.current_ctc_lpa)
                if screening_row.current_ctc_lpa is not None
                else None,
                expected_ctc_lpa=float(screening_row.expected_ctc_lpa)
                if screening_row.expected_ctc_lpa is not None
                else None,
                notice_period_days=screening_row.notice_period_days,
                willing_to_relocate=screening_row.willing_to_relocate,
                composite_score=screening_row.composite_score,
                knock_out_triggered=screening_row.knock_out_triggered,
                knock_out_reason=screening_row.knock_out_reason,
                submitted_at=screening_row.submitted_at,
                evaluation=screening_row.evaluation,
            )
            if screening_row is not None
            else None
        ),
        assignment=(
            DashboardAssignment(
                brief_md=assignment_row.brief_md,
                problems=assignment_row.problems,
                submission_format=assignment_row.submission_format,
                evaluation_rubric=assignment_row.evaluation_rubric,
                submission_url=assignment_row.submission_url,
                submission_text=assignment_row.submission_text,
                submission_r2_keys=assignment_row.submission_r2_keys,
                submitted_at=assignment_row.submitted_at,
                score=assignment_row.score,
                evaluation=assignment_row.evaluation,
            )
            if assignment_row is not None
            else None
        ),
    )
