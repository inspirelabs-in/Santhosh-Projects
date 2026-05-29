"""V1 application repository: state-machine transitions + JSONB setters.

Concentrates all writes to the new V1 columns so activities stay thin.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import Application
from src.models.v1 import PipelineStage, can_transition


class InvalidTransition(RuntimeError):
    pass


async def get_stage(session: AsyncSession, application_id: UUID) -> PipelineStage:
    app = await session.get(Application, application_id)
    if app is None:
        raise ValueError(f"application {application_id} not found")
    return PipelineStage(app.current_stage)


async def set_stage(
    session: AsyncSession,
    application_id: UUID,
    new_stage: PipelineStage,
    *,
    force: bool = False,
) -> None:
    # SELECT ... FOR UPDATE prevents concurrent transitions on the same row
    app = await session.get(Application, application_id, with_for_update=True)
    if app is None:
        raise ValueError(f"application {application_id} not found")
    current = PipelineStage(app.current_stage)
    if not force and not can_transition(current, new_stage):
        raise InvalidTransition(f"{current} -> {new_stage} not allowed")
    app.current_stage = new_stage.value


async def save_screening_questions(
    session: AsyncSession, application_id: UUID, questions: dict | list[Any]
) -> None:
    app = await session.get(Application, application_id)
    if app is None:
        raise ValueError(f"application {application_id} not found")
    app.screening_questions = questions


async def save_screening_evaluation(
    session: AsyncSession, application_id: UUID, evaluation: dict
) -> None:
    app = await session.get(Application, application_id)
    if app is None:
        raise ValueError(f"application {application_id} not found")
    app.screening_evaluation = evaluation
    if "overall_score" in evaluation:
        app.screening_score = int(evaluation["overall_score"])


async def save_assignment_submission(
    session: AsyncSession, application_id: UUID, submission: dict
) -> None:
    app = await session.get(Application, application_id)
    if app is None:
        raise ValueError(f"application {application_id} not found")
    app.assignment_submission = submission


async def save_admin_review(
    session: AsyncSession,
    application_id: UUID,
    *,
    round_key: str,
    payload: dict,
) -> None:
    """Merge an admin review payload under ``admin_review[round_key]``."""
    app = await session.get(Application, application_id)
    if app is None:
        raise ValueError(f"application {application_id} not found")
    current = dict(app.admin_review or {})
    current[round_key] = payload
    app.admin_review = current


async def save_journey_report(
    session: AsyncSession, application_id: UUID, markdown: str
) -> None:
    app = await session.get(Application, application_id)
    if app is None:
        raise ValueError(f"application {application_id} not found")
    app.journey_report = markdown
