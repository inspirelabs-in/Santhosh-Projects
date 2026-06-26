"""Auto-progression: the role-pipeline-driven engine.

Progression is driven by the **role's configured pipeline**
(``role_pipeline_stages``): the candidate's ``current_stage_key`` is mapped to the
next enabled stage, and the engine either fires that stage's activity (``auto``)
or parks the candidate and raises a distinct ``requires_action`` item (``manual``).
The pure decision lives in ``services/pipeline_engine.py`` (unit-tested); this
module is the IO shell that resolves state, dispatches activities, and parks gates.

Each manual gate parks at its OWN ``stage_key`` with its OWN action event
(``review_assessment`` / ``schedule_interview`` / ``hire_or_reject``), so the
overloaded ``NEEDS_HR_REVIEW`` state -- which let "schedule the HR round" trigger
"re-send the assignment" -- is gone.

This never blocks the calling activity: every dispatch goes through the Arq queue
(with an inline fallback), and every decision writes an audit row + a domain event
so HR can read the agent's reasoning later.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from src.db.base import Application, Role
from src.db.connection import session_scope
from src.db.events import emit_event
from src.db.repositories import role_pipeline_stage as stage_repo
from src.db.repositories.audit import log_audit
from src.db.repositories.v1_application import set_stage
from src.models.events import ActionType, EventType
from src.models.pipeline import StageStatus
from src.models.v1 import CallKind, PipelineStage
from src.services.events import publish_event
from src.services.pipeline_engine import (
    Plan,
    StageAction,
    StageView,
    plan_transition,
)
from src.services.queue import enqueue

logger = logging.getLogger(__name__)


def _voice_calls_enabled() -> bool:
    try:
        from src.config import get_settings
        s = get_settings()
        return bool(s.enable_voice_screening or s.enable_voice_calls)
    except Exception:
        return False


def _agentic_cfg(rubric: dict | list | None) -> dict[str, Any]:
    if not isinstance(rubric, dict):
        return {}
    a = rubric.get("agentic")
    return a if isinstance(a, dict) else {}


# Map a manual gate's park action to the inbox action type + a human-readable note.
_GATE_EVENT: dict[StageAction, tuple[str, str]] = {
    StageAction.PARK_REVIEW: (ActionType.REVIEW_ASSESSMENT.value, "review the candidate's work"),
    StageAction.PARK_SCHEDULE: (ActionType.SCHEDULE_INTERVIEW.value, "schedule the interview"),
    StageAction.PARK_DECISION: (ActionType.HIRE_OR_REJECT.value, "make the hire / reject call"),
    StageAction.PARK_MANUAL: (ActionType.REVIEW_ASSESSMENT.value, "trigger this stage manually"),
}


async def auto_progress(*, application_id: UUID) -> str:
    """Advance the application to the next step of its role's pipeline.

    Returns one of: "fired:<action>" | "parked:<stage_key>" | "skipped:<reason>"
    | "done" | "noop".
    """
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            return "skipped:application_missing"
        if (app.status or "").lower() in {"rejected", "withdrawn"}:
            return "skipped:application_not_active"
        if app.role_id is None:
            return "skipped:no_role"
        role = await session.get(Role, app.role_id)
        if role is not None and (role.status or "").lower() not in {"open"}:
            return f"skipped:role_status_{role.status}"

        agentic = _agentic_cfg(role.scoring_rubric if role else None)
        if agentic.get("auto_progress") is False:
            return "skipped:auto_progress_off"

        # Resolve the candidate's position in the role's pipeline. current_stage_key
        # is dual-written by set_stage(); fall back to mapping the legacy stage.
        current_key = app.current_stage_key
        if current_key is None:
            from src.services.state_machine import legacy_stage_to_key

            current_key, _ = legacy_stage_to_key(PipelineStage(app.current_stage))

        rows = await stage_repo.for_role(session, app.role_id, enabled_only=False)
        stages = [StageView.from_row(r) for r in rows]
        plan = plan_transition(stages, current_key)
        from_key = current_key
        role_id = app.role_id

    # Confirmation / joining calls sit ON a scheduled/hired stage (voice
    # side-effects, not pipeline moves) -- handle those before normal progression.
    side = await _maybe_fire_voice_side_effects(application_id, agentic)
    if side is not None:
        return side

    decision = await _execute_plan(application_id, plan, role_id=role_id)

    if decision not in {"noop", "done"}:
        async with session_scope() as session:
            await log_audit(
                session,
                application_id=application_id,
                action="auto_progress_fired",
                actor="agent",
                details={"from_stage_key": from_key, "decision": decision, "to": plan.reason},
            )
        await publish_event(
            application_id,
            event="auto_progressed",
            data={"from_stage_key": from_key, "decision": decision},
        )
    return decision


async def _maybe_fire_voice_side_effects(
    application_id: UUID, agentic: dict[str, Any]
) -> str | None:
    """Confirmation / joining-details calls keyed off the LEGACY stage (these are
    voice side-effects that sit on a scheduled/hired stage, not pipeline moves).
    Returns a decision string if one fired, else None so normal progression runs."""
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            return None
        stage = PipelineStage(app.current_stage)

    if stage in {
        PipelineStage.TECHNICAL_MEETING_SCHEDULED,
        PipelineStage.CEO_MEETING_SCHEDULED,
        PipelineStage.HR_MEETING_SCHEDULED,
    } and agentic.get("voice_confirmation_enabled", True) and _voice_calls_enabled():
        return await _fire_confirmation_call(application_id, stage)

    if stage == PipelineStage.HIRED and agentic.get(
        "voice_joining_call_enabled", True
    ) and _voice_calls_enabled():
        return await _fire_joining_details_call(application_id)

    return None


async def _execute_plan(application_id: UUID, plan: Plan, *, role_id: UUID) -> str:
    """Carry out the engine's decision: fire an auto stage, or park a manual gate."""
    if plan.action == StageAction.NOOP:
        return "noop"
    if plan.action == StageAction.DONE:
        return "done"

    assert plan.stage is not None  # FIRE_*/PARK_* always carry a stage

    # V2 owns the cursor: advance current_stage_key to the stage being fired BEFORE
    # dispatching it. Progression no longer depends on the dispatch activity's
    # legacy set_stage() dual-writing the key -- that dual-write was the race
    # (V1->V2-migration bug #1, the re-sent assignment). The dispatch is just the
    # side-effect of being AT this stage.
    if plan.action in (
        StageAction.FIRE_VOICE_SCREEN,
        StageAction.FIRE_ASSIGNMENT,
        StageAction.FIRE_SCREENING,
        StageAction.FIRE_OFFER,
    ):
        async with session_scope() as session:
            app = await session.get(Application, application_id, with_for_update=True)
            if app is not None:
                app.current_stage_key = plan.stage.stage_key
                app.stage_status = str(StageStatus.ACTIVE)

    if plan.action == StageAction.FIRE_VOICE_SCREEN:
        return await _fire_voice_screen(application_id)
    if plan.action == StageAction.FIRE_ASSIGNMENT:
        return await _fire_assessment(application_id)
    if plan.action == StageAction.FIRE_SCREENING:
        return await _fire_screening(application_id)
    if plan.action == StageAction.FIRE_OFFER:
        return await _fire_offer(application_id)

    # Manual gates: park at THIS stage_key with a distinct, resolvable action item.
    return await _park_gate(application_id, plan, role_id=role_id)


async def _park_gate(application_id: UUID, plan: Plan, *, role_id: UUID) -> str:
    """Park the candidate at a manual gate stage and raise a distinct
    ``requires_action`` domain event so the right inbox card shows up (review vs
    schedule vs decision) -- never the wrong one."""
    stage = plan.stage
    assert stage is not None
    action_type, note = _GATE_EVENT.get(
        plan.action, (ActionType.REVIEW_ASSESSMENT.value, "needs attention")
    )

    # An interview parks as "scheduled-pending"; other gates as "parked".
    status = (
        StageStatus.SCHEDULED
        if plan.action == StageAction.PARK_SCHEDULE
        else StageStatus.PARKED
    )

    async with session_scope() as session:
        app = await session.get(Application, application_id, with_for_update=True)
        if app is None:
            return "skipped:application_missing"
        app.current_stage_key = stage.stage_key
        app.stage_status = str(status)

        await emit_event(
            session,
            type=EventType.STAGE_CHANGED,
            org_id=app.org_id,
            application_id=application_id,
            role_id=role_id,
            payload={
                "to": stage.stage_key,
                "stage_type": stage.stage_type,
                "status": str(status),
                "label": stage.label,
            },
            actor="agent",
        )
        # The actionable inbox item -- distinct per gate type.
        await emit_event(
            session,
            type=action_type,
            org_id=app.org_id,
            application_id=application_id,
            role_id=role_id,
            payload={
                "stage_key": stage.stage_key,
                "stage_type": stage.stage_type,
                "label": stage.label,
                "note": note,
            },
            actor="agent",
            requires_action=True,
        )
        await log_audit(
            session,
            application_id=application_id,
            action=f"parked_{action_type}",
            actor="agent",
            details={"stage_key": stage.stage_key, "stage_type": stage.stage_type, "note": note},
        )

    return f"parked:{stage.stage_key}"


# ---------------------------------------------------------------------------
# Auto-reject helper -- SUPERSEDED by stage_runner._auto_reject_with_email
# ---------------------------------------------------------------------------


async def auto_reject_if_configured(*, application_id: UUID, reason: str) -> bool:
    """Thin pass-through to the centralised gate in stage_runner.

    The original feature-flag default-off behaviour (``auto_reject_clear_reject``)
    has been retired. All scored FAIL verdicts are now routed through
    ``stage_runner._auto_reject_with_email`` which consults the stage's *mode*
    (auto/manual) and always sends the rejection email. This shim remains only so
    that any external caller that was not updated yet does not blow up with an
    ImportError; it delegates immediately and always returns True on success.
    """
    from src.services.stage_runner import _auto_reject_with_email

    try:
        await _auto_reject_with_email(
            application_id,
            completed_stage_key="unknown",
            score=None,
            threshold=None,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("auto_reject_if_configured shim failed for %s: %s", application_id, exc)
        return False


# ---------------------------------------------------------------------------
# Dispatch helpers -- enqueue first, fall back to direct activity import.
# ---------------------------------------------------------------------------


async def _retry_voice_dispatch(application_id: UUID, error: str) -> str:
    from datetime import UTC, datetime, timedelta

    from src.config import get_settings
    from src.db.base import VoiceCall
    from src.db.repositories.voice_call import next_attempt_no
    from src.services.voice_window import clamp_to_call_window

    async with session_scope() as session:
        attempt = await next_attempt_no(session, application_id)

    settings = get_settings()
    max_dispatch_retries = min(settings.voice_agent_max_noanswer_attempts, 3)
    if attempt > max_dispatch_retries:
        async with session_scope() as session:
            await set_stage(session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True)
            await log_audit(
                session,
                application_id=application_id,
                action="voice_dispatch_exhausted",
                actor="agent",
                details={
                    "attempts": attempt,
                    "max_retries": max_dispatch_retries,
                    "original_error": error[:300],
                    "action_required": "All voice call attempts failed. HR can re-trigger voice screening manually.",
                },
            )
        await publish_event(
            application_id,
            event="voice_dispatch_exhausted",
            data={"attempts": attempt, "error": error[:200]},
        )
        return "parked:voice_retries_exhausted_hr_review"

    delay = 300 * (2 ** (attempt - 2))
    retry_at, _ = clamp_to_call_window(datetime.now(UTC) + timedelta(seconds=delay))

    async with session_scope() as session:
        await log_audit(
            session,
            application_id=application_id,
            action="voice_dispatch_retry_scheduled",
            actor="agent",
            details={
                "attempt": attempt,
                "retry_at": retry_at.isoformat(),
                "original_error": error[:300],
            },
        )

    queued = await enqueue(
        "dispatch_voice_screening",
        str(application_id),
        attempt_no=attempt,
        scheduled_at_iso=retry_at.isoformat(),
        _defer_until=retry_at,
        _job_id=f"voice-dispatch-retry-{application_id}-{attempt}",
    )
    if not queued:
        async with session_scope() as session:
            await set_stage(session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True)
            await log_audit(
                session,
                application_id=application_id,
                action="voice_dispatch_no_queue",
                actor="agent",
                details={
                    "original_error": error[:300],
                    "action_required": "Voice retry could not be queued. HR can re-trigger manually.",
                },
            )
        return "parked:voice_no_arq_hr_review"

    await publish_event(
        application_id,
        event="voice_dispatch_retry",
        data={"attempt": attempt, "retry_at": retry_at.isoformat()},
    )
    return f"fired:voice_dispatch_retry_{attempt}_at_{retry_at.isoformat()}"[:80]


async def _fire_voice_screen(application_id: UUID) -> str:
    from datetime import UTC, datetime

    from src.services.voice_window import clamp_to_call_window

    now = datetime.now(UTC)
    when, clamped = clamp_to_call_window(now)
    if clamped:
        async with session_scope() as session:
            await log_audit(
                session,
                application_id=application_id,
                action="voice_call_deferred_quiet_hours",
                actor="agent",
                details={
                    "requested_at": now.isoformat(),
                    "deferred_to": when.isoformat(),
                    "reason": "outside_call_window",
                },
            )
        queued = await enqueue(
            "dispatch_voice_screening",
            str(application_id),
            scheduled_at_iso=when.isoformat(),
            _defer_until=when,
            _job_id=f"voice-deferred-{application_id}-{int(when.timestamp())}",
        )
        if queued:
            return f"fired:voice_screen_deferred_to_{when.isoformat()}"[:80]
        async with session_scope() as session:
            await set_stage(
                session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True
            )
        return "skipped:voice_quiet_hours_no_arq"

    queued = await enqueue("dispatch_voice_screening", str(application_id))
    if queued:
        return "fired:voice_screen"
    try:
        from src.activities.v1_voice_screening import dispatch_voice_screening

        await dispatch_voice_screening(application_id=application_id)
        return "fired:voice_screen_inline"
    except Exception as exc:
        logger.warning("auto voice dispatch failed for %s: %s", application_id, exc)
        from src.services.circuit_breaker import ELEVENLABS, record_failure

        await record_failure(ELEVENLABS, str(exc))
        return await _retry_voice_dispatch(application_id, str(exc))


async def _fire_assessment(application_id: UUID) -> str:
    from src.config import get_settings as _gs
    if not _gs().enable_assessment_round:
        logger.info("auto assessment skipped for %s: ENABLE_ASSESSMENT_ROUND=false", application_id)
        return "skipped:assessment_round_disabled"
    queued = await enqueue("dispatch_assessment", str(application_id))
    if queued:
        return "fired:assessment"
    try:
        from src.activities.v1_dispatch_assessment import dispatch_assessment

        await dispatch_assessment(application_id=application_id)
        return "fired:assessment_inline"
    except Exception as exc:
        logger.warning("auto assessment dispatch failed for %s: %s", application_id, exc)
        from src.services.fallback_manager import llm_fallback

        await llm_fallback(application_id, None, "assessment_dispatch", str(exc))
        return f"fallback:assessment_parked:{exc}"[:80]


async def _fire_screening(application_id: UUID) -> str:
    """Dispatch the written/resume screening stage (optional; a role can add a
    ``screening`` stage before voice). Generates + emails the questionnaire.

    [TO_FIX] SM-3: this enqueues ``run_apply_to_screening_stage`` (no such Arq job)
    and the inline fallback imports ``dispatch_screening_stage`` (doesn't exist) -> a
    role that adds a written ``screening`` stage parks at NEEDS_HR_REVIEW. The default
    pipeline uses voice as the screen, so this is unhit today. Screening is OUT OF
    SCOPE for now (voice is the mandated screen); wire a real screening dispatcher or
    reject a ``screening`` stage at JD-config time when revisited.
    """
    queued = await enqueue("run_apply_to_screening_stage", str(application_id))
    if queued:
        return "fired:screening"
    try:
        from src.activities.v1_generate_screening import dispatch_screening_stage

        await dispatch_screening_stage(application_id=application_id)
        return "fired:screening_inline"
    except (ImportError, AttributeError):
        # No standalone screening dispatcher wired (default pipeline uses voice as
        # the screen). Park rather than crash so a custom screening stage is
        # visible to HR instead of silently stalling.
        logger.warning(
            "screening stage requested for %s but no dispatcher is wired -- parking",
            application_id,
        )
        async with session_scope() as session:
            await set_stage(
                session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True
            )
        return "parked:screening_no_dispatcher"
    except Exception as exc:  # noqa: BLE001
        logger.warning("auto screening dispatch failed for %s: %s", application_id, exc)
        return f"fallback:screening_failed:{exc}"[:80]


async def _fire_offer(application_id: UUID) -> str:
    """Dispatch the offer stage (when the role's offer stage is auto). Generates
    and emails the offer; ``generate_offer`` sets the candidate to HIRED."""
    queued = await enqueue("generate_offer", str(application_id))
    if queued:
        return "fired:offer"
    try:
        from src.activities.offer import generate_offer

        await generate_offer(application_id=application_id, approved_by="agent")
        return "fired:offer_inline"
    except Exception as exc:  # noqa: BLE001
        logger.warning("auto offer dispatch failed for %s: %s", application_id, exc)
        return f"fallback:offer_failed:{exc}"[:80]


async def _fire_confirmation_call(application_id: UUID, stage: PipelineStage) -> str:
    from datetime import UTC, datetime, timedelta
    from src.services.voice_window import clamp_to_call_window

    when = datetime.now(UTC) + timedelta(minutes=2)
    when, _ = clamp_to_call_window(when)

    queued = await enqueue(
        "dispatch_voice_call",
        str(application_id),
        call_kind="confirmation",
        scheduled_at_iso=when.isoformat(),
        _defer_until=when,
        _job_id=f"voice-confirm-{application_id}-{stage.value}",
    )
    if queued:
        return f"fired:confirmation_call_{stage.value}"
    try:
        from src.activities.v1_dispatch_voice_call import dispatch_voice_call
        await dispatch_voice_call(
            application_id=application_id,
            call_kind=CallKind.CONFIRMATION,
            attempt_no=1,
            scheduled_at=when,
        )
        return f"fired:confirmation_call_{stage.value}_inline"
    except Exception as exc:
        logger.warning("auto confirmation call failed for %s: %s", application_id, exc)
        return f"skipped:confirmation_call_failed:{exc}"[:80]


async def _fire_joining_details_call(application_id: UUID) -> str:
    from datetime import UTC, datetime, timedelta
    from src.services.voice_window import clamp_to_call_window

    when = datetime.now(UTC) + timedelta(minutes=5)
    when, _ = clamp_to_call_window(when)

    queued = await enqueue(
        "dispatch_voice_call",
        str(application_id),
        call_kind="joining_details",
        scheduled_at_iso=when.isoformat(),
        _defer_until=when,
        _job_id=f"voice-joining-{application_id}",
    )
    if queued:
        return "fired:joining_details_call"
    try:
        from src.activities.v1_dispatch_voice_call import dispatch_voice_call
        await dispatch_voice_call(
            application_id=application_id,
            call_kind=CallKind.JOINING_DETAILS,
            attempt_no=1,
            scheduled_at=when,
        )
        return "fired:joining_details_call_inline"
    except Exception as exc:
        logger.warning("auto joining details call failed for %s: %s", application_id, exc)
        return f"skipped:joining_call_failed:{exc}"[:80]
