"""Pipeline state machine — the single writer of an application's stage.

``transition()`` is the ONLY function that should set
``applications.current_stage_key`` / ``stage_status``. It validates the move
against the application's role pipeline (``role_pipeline_stages``), updates the
application, and emits a ``stage_changed`` domain event (which pushes a live UI
update). ``advance()`` moves to the next enabled stage in the role's pipeline.

This coexists with the legacy ``current_stage`` column + ``auto_progress`` during
the cut-over. Nothing here changes existing behaviour until callers adopt it —
wiring the pipeline onto these functions is the Module-A task.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import Application
from src.db.events import emit_event
from src.db.repositories import role_pipeline_stage as stage_repo
from src.models.events import EventType
from src.models.pipeline import StageStatus

logger = logging.getLogger(__name__)


# Stage-1 cutover: map a legacy PipelineStage value to the new
# (stage_key, stage_status) so set_stage() can dual-write the new columns while
# the legacy current_stage stays authoritative. Mirrors the CASE map in
# migration 0031. Returns (None, None) for unmapped values.
_LEGACY_STAGE_KEY: dict[str, tuple[str, str]] = {
    "applied": ("intake", "active"),
    "screening_sent": ("screening", "active"),
    "screening_submitted": ("screening", "in_progress"),
    "screening_evaluated": ("screening", "completed"),
    "needs_hr_review": ("assessment_review", "parked"),
    "assignment_sent": ("assignment", "active"),
    "assignment_submitted": ("assignment", "completed"),
    "assessment_invited": ("assignment", "active"),
    "report_ready": ("assessment_review", "active"),
    "assessment_completed": ("assessment_review", "active"),
    "assessment_pending_review": ("assessment_review", "active"),
    "assessment_evaluated": ("assessment_review", "completed"),
    "voice_screen_scheduled": ("voice_screen", "scheduled"),
    "voice_screen_in_progress": ("voice_screen", "in_progress"),
    "voice_screen_callback_requested": ("voice_screen", "scheduled"),
    "voice_screen_completed": ("voice_screen", "completed"),
    "voice_screen_evaluated": ("voice_screen", "completed"),
    "technical_meeting_scheduled": ("technical", "scheduled"),
    "technical_meeting_in_progress": ("technical", "in_progress"),
    "technical_meeting_completed": ("technical", "completed"),
    "technical_evaluated": ("technical", "completed"),
    "technical_pending_approval": ("technical", "completed"),
    "ceo_meeting_scheduled": ("ceo", "scheduled"),
    "ceo_meeting_in_progress": ("ceo", "in_progress"),
    "ceo_meeting_completed": ("ceo", "completed"),
    "ceo_pending_approval": ("ceo", "completed"),
    "hr_meeting_scheduled": ("hr", "scheduled"),
    "hr_meeting_in_progress": ("hr", "in_progress"),
    "hr_meeting_completed": ("hr", "completed"),
    "hr_evaluated": ("hr", "completed"),
    "hired": ("offer", "passed"),
    "rejected": ("decision", "failed"),
}


def legacy_stage_to_key(stage: object) -> tuple[str | None, str | None]:
    """Map a legacy PipelineStage (or its string value) to (stage_key, status)."""
    val = getattr(stage, "value", None) or str(stage)
    return _LEGACY_STAGE_KEY.get(val, (None, None))


class InvalidTransition(Exception):
    """Raised when a stage move isn't permitted by the role's pipeline."""


async def transition(
    session: AsyncSession,
    application: Application,
    to_stage_key: str,
    *,
    actor: str = "system",
    reason: str | None = None,
    status: StageStatus | str = StageStatus.ACTIVE,
    validate: bool = True,
) -> Application:
    """Move ``application`` to ``to_stage_key``.

    Validates that the target stage exists in (and is enabled for) the role's
    pipeline. Pass ``validate=False`` to force a move (parking, terminal states,
    admin overrides). Emits a ``stage_changed`` event after the write succeeds.
    """
    if application.role_id is None:
        raise InvalidTransition("application has no role; cannot resolve a pipeline")

    target = await stage_repo.get_stage(session, application.role_id, to_stage_key)
    if target is None:
        raise InvalidTransition(
            f"stage '{to_stage_key}' is not in role {application.role_id}'s pipeline"
        )
    if validate and not target.is_enabled:
        raise InvalidTransition(f"stage '{to_stage_key}' is disabled for this role")

    from_key = application.current_stage_key
    application.current_stage_key = to_stage_key
    application.stage_status = str(status)

    await emit_event(
        session,
        type=EventType.STAGE_CHANGED,
        org_id=application.org_id,
        application_id=application.id,
        role_id=application.role_id,
        payload={
            "from": from_key,
            "to": to_stage_key,
            "status": str(status),
            "reason": reason,
        },
        actor=actor,
    )
    return application


async def advance(
    session: AsyncSession,
    application: Application,
    *,
    actor: str = "system",
    reason: str | None = None,
    status: StageStatus | str = StageStatus.ACTIVE,
) -> Application | None:
    """Advance to the NEXT enabled stage in the role's pipeline. Returns None if
    already at the last stage (nothing to advance to)."""
    nxt = await stage_repo.next_stage(
        session, application.role_id, application.current_stage_key
    )
    if nxt is None:
        return None
    return await transition(
        session, application, nxt.stage_key, actor=actor, reason=reason, status=status
    )
