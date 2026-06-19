"""Auto-progression: template-driven pipeline engine.

When a role has ``pipeline_template``, the engine reads the ordered step list
and fires the next action based on the candidate's current stage.  When NULL,
falls back to the legacy hardcoded flow for backward compatibility.

This module never blocks the calling activity -- every dispatch goes through
the Arq queue (with BackgroundTasks fallback). Every action emits a
``progressed`` audit row so HR can read the agent's reasoning later.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from src.db.base import Application, Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.v1_application import set_stage
from src.models.v1 import CallKind, PipelineStage
from src.services.events import publish_event
from src.services.queue import enqueue

logger = logging.getLogger(__name__)


def _voice_globally_enabled() -> bool:
    try:
        from src.config import get_settings
        return bool(get_settings().enable_voice_screening)
    except Exception:
        return False


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


def _scheduling_cfg(rubric: dict | list | None) -> dict[str, Any]:
    if not isinstance(rubric, dict):
        return {}
    s = rubric.get("scheduling")
    return s if isinstance(s, dict) else {}


async def auto_progress(*, application_id: UUID) -> str:
    """Look at the application's current stage and trigger the next thing.

    Returns one of: "fired:<action>" | "skipped:<reason>" | "noop".
    """

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            return "skipped:application_missing"
        if (app.status or "").lower() in {"rejected", "withdrawn"}:
            return "skipped:application_not_active"
        role = await session.get(Role, app.role_id) if app.role_id else None
        if role is not None and (role.status or "").lower() not in {"open"}:
            return f"skipped:role_status_{role.status}"
        rubric = role.scoring_rubric if role else None
        agentic = _agentic_cfg(rubric)
        scheduling = _scheduling_cfg(rubric)
        template = getattr(role, "pipeline_template", None) if role else None

        if agentic.get("auto_progress") is False:
            return "skipped:auto_progress_off"

        stage = PipelineStage(app.current_stage)

    # ── Template-driven flow ──────────────────────────────────────────
    if template and isinstance(template, list) and len(template) > 0:
        decision = await _template_progress(
            application_id=application_id,
            stage=stage,
            template=template,
            agentic=agentic,
            scheduling=scheduling,
        )
    else:
        # ── Legacy hardcoded flow (backward compat) ───────────────────
        decision = await _legacy_progress(
            application_id=application_id,
            stage=stage,
            agentic=agentic,
            scheduling=scheduling,
        )

    if decision != "noop":
        async with session_scope() as session:
            await log_audit(
                session,
                application_id=application_id,
                action="auto_progress_fired",
                actor="agent",
                details={"from_stage": stage.value, "decision": decision, "template": bool(template)},
            )
        await publish_event(
            application_id,
            event="auto_progressed",
            data={"from_stage": stage.value, "decision": decision},
        )
    return decision


# ── Template-driven progression ───────────────────────────────────────

async def _template_progress(
    *,
    application_id: UUID,
    stage: PipelineStage,
    template: list[str],
    agentic: dict[str, Any],
    scheduling: dict[str, Any],
) -> str:
    """Use the role's pipeline_template to decide what to fire next."""
    from src.services.pipeline_templates import STEP_REGISTRY, find_current_step, get_next_step

    # Handle confirmation calls on meeting-scheduled stages
    if stage in {
        PipelineStage.TECHNICAL_MEETING_SCHEDULED,
        PipelineStage.CEO_MEETING_SCHEDULED,
        PipelineStage.HR_MEETING_SCHEDULED,
    } and agentic.get("voice_confirmation_enabled", True) and _voice_calls_enabled():
        return await _fire_confirmation_call(application_id, stage)

    # Handle joining details call on hired
    if stage == PipelineStage.HIRED and agentic.get("voice_joining_call_enabled", True) and _voice_calls_enabled():
        return await _fire_joining_details_call(application_id)

    # Find which template step the candidate just completed
    current_step_id = find_current_step(template, stage)
    if current_step_id is None:
        logger.debug("template progress: stage %s not mapped to any step for %s", stage, application_id)
        return "noop"

    next_step_id = get_next_step(template, current_step_id)
    if next_step_id is None:
        return "noop"

    next_step = STEP_REGISTRY.get(next_step_id)
    if next_step is None:
        logger.warning("template progress: unknown step %s for %s", next_step_id, application_id)
        return "noop"

    return await _fire_step(application_id, next_step, scheduling)


async def _fire_step(application_id: UUID, step: Any, scheduling: dict[str, Any]) -> str:
    """Dispatch the action for a given pipeline step."""
    from src.services.pipeline_templates import StepDef

    if not isinstance(step, StepDef):
        return "noop"

    if step.action == "fit_score":
        return "noop"  # fit_score runs during intake, not via auto_progress

    if step.action == "voice_screen":
        return await _fire_voice_screen(application_id)

    if step.action == "chat_screen":
        return await _fire_chat_screen(application_id)

    if step.action in {"assessment", "cognitive_test"}:
        return await _fire_assessment(application_id)

    if step.action == "meeting" and step.is_meeting and step.meeting_round:
        return await _fire_meeting(application_id, step.meeting_round)

    if step.action == "reference_check":
        return await _fire_manual_step(application_id, "reference_check")

    if step.action == "background_check":
        return await _fire_manual_step(application_id, "background_check")

    if step.action == "offer":
        return await _fire_manual_step(application_id, "offer")

    return "noop"


async def _fire_manual_step(application_id: UUID, step_name: str) -> str:
    """For steps that need HR action, park at needs_hr_review with context."""
    async with session_scope() as session:
        await set_stage(session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True)
        await log_audit(
            session,
            application_id=application_id,
            action=f"awaiting_{step_name}",
            actor="agent",
            details={"step": step_name, "reason": "manual_step_requires_hr"},
        )
    await publish_event(
        application_id,
        event=f"awaiting_{step_name}",
        data={"step": step_name},
    )
    return f"fired:awaiting_{step_name}"

async def _fire_chat_screen(application_id: UUID) -> str:
    """Dispatch chat-based screening for the application."""
    queued = await enqueue("run_apply_to_chat", str(application_id))
    if queued:
        return "fired:chat_screen"
    try:
        from src.pipeline.v1 import run_apply_to_chat
        await run_apply_to_chat(application_id=application_id)
        return "fired:chat_screen_inline"
    except Exception as exc:
        logger.warning("auto chat screen failed for %s: %s", application_id, exc)
        return f"fallback:chat_screen_failed:{exc}"[:80]

# ── Legacy hardcoded flow ─────────────────────────────────────────────

async def _legacy_progress(
    *,
    application_id: UUID,
    stage: PipelineStage,
    agentic: dict[str, Any],
    scheduling: dict[str, Any],
) -> str:
    """Original hardcoded stage branching for roles without pipeline_template."""
    decision = "noop"

    if stage in {
        PipelineStage.APPLIED,
        PipelineStage.SCREENING_EVALUATED,
        PipelineStage.REPORT_READY,
    } and (agentic.get("voice_screening_enabled") or _voice_globally_enabled()):
        decision = await _fire_voice_screen(application_id)

    elif stage == PipelineStage.VOICE_SCREEN_EVALUATED:
        decision = await _fire_assessment(application_id)

    elif stage == PipelineStage.ASSESSMENT_EVALUATED:
        decision = await _fire_meeting(application_id, "technical")

    elif stage == PipelineStage.TECHNICAL_EVALUATED:
        decision = await _fire_meeting(application_id, "ceo")

    elif stage == PipelineStage.CEO_MEETING_COMPLETED:
        decision = await _fire_meeting(application_id, "hr")

    elif stage in {
        PipelineStage.TECHNICAL_MEETING_SCHEDULED,
        PipelineStage.CEO_MEETING_SCHEDULED,
        PipelineStage.HR_MEETING_SCHEDULED,
    } and (agentic.get("voice_confirmation_enabled", True) and _voice_calls_enabled()):
        decision = await _fire_confirmation_call(application_id, stage)

    elif stage == PipelineStage.HIRED and agentic.get("voice_joining_call_enabled", True) and _voice_calls_enabled():
        decision = await _fire_joining_details_call(application_id)

    return decision


# ---------------------------------------------------------------------------
# Auto-reject helper -- called from each evaluator on clear_reject verdicts
# ---------------------------------------------------------------------------


async def auto_reject_if_configured(*, application_id: UUID, reason: str) -> bool:
    """Reject the application if ``agentic.auto_reject_clear_reject`` is on.

    Returns True if the rejection happened, False otherwise.
    """

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            return False
        role = await session.get(Role, app.role_id) if app.role_id else None
        agentic = _agentic_cfg(role.scoring_rubric if role else None)
        if not agentic.get("auto_reject_clear_reject"):
            return False
        await set_stage(session, application_id, PipelineStage.REJECTED, force=True)
        await log_audit(
            session,
            application_id=application_id,
            action="auto_rejected",
            actor="agent",
            details={"reason": reason[:300]},
        )
    await publish_event(
        application_id,
        event="auto_rejected",
        data={"reason": reason[:300]},
    )
    return True


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


async def _fire_meeting(application_id: UUID, round: str) -> str:
    """Meeting scheduling is now CHAT-DRIVEN — the recruiter books meetings via
    Pulse (``schedule_meeting`` tool) with the panel + time they choose.

    The legacy auto panel-availability / smart-scheduler / direct-booking chain
    was fragile (slot-finding, dead GMeet stubs, confirmation calls, panel
    email ping-pong) and is intentionally disabled — see the commented block
    below for the previous behaviour. Instead we park the candidate for manual
    scheduling and nudge the recruiter in chat so they pick it up.
    """
    async with session_scope() as session:
        await set_stage(
            session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True
        )
        await log_audit(
            session,
            application_id=application_id,
            action=f"meeting_{round}_awaiting_chat_scheduling",
            actor="agent",
            details={
                "round": round,
                "note": "auto-scheduling disabled; recruiter schedules via Pulse chat",
            },
        )
    try:
        from src.services.events import publish_event

        await publish_event(
            application_id,
            event="meeting_scheduling_needed",
            data={"round": round},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "meeting_scheduling_needed publish failed (best-effort): %s", exc
        )
    return f"parked:meeting_{round}_awaiting_chat"

    # ── LEGACY AUTO-SCHEDULER (disabled — kept for reference) ──────────────────
    # Primary: emails panel members a confirmation link, auto-books once all
    # respond. Fallback: smart scheduler (Graph), then legacy direct booking.
    #
    # try:
    #     from src.services.panel_availability import initiate_panel_availability
    #     queued = await enqueue(
    #         "panel_availability_request", str(application_id), round=round
    #     )
    #     if queued:
    #         return f"fired:panel_availability_{round}"
    #     await initiate_panel_availability(application_id=application_id, round=round)
    #     return f"fired:panel_availability_{round}_inline"
    # except Exception as exc:
    #     logger.warning(
    #         "panel availability %s failed for %s: %s, falling back to smart scheduler",
    #         round, application_id, exc,
    #     )
    #     try:
    #         from src.services.smart_scheduler import initiate_smart_schedule
    #         await initiate_smart_schedule(application_id=application_id, round=round)
    #         return f"fired:smart_meeting_{round}_fallback"
    #     except Exception as exc2:
    #         logger.warning("smart schedule also failed for %s: %s, trying legacy", application_id, exc2)
    #         try:
    #             from src.activities.v1_schedule_meeting import schedule_meeting
    #             await schedule_meeting(application_id=application_id, round=round)
    #             return f"fired:meeting_{round}_legacy_fallback"
    #         except Exception as exc3:
    #             logger.warning("all scheduling failed for %s: %s", application_id, exc3)
    #             async with session_scope() as session:
    #                 await set_stage(session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True)
    #                 await log_audit(
    #                     session,
    #                     application_id=application_id,
    #                     action=f"meeting_{round}_schedule_failed",
    #                     actor="agent",
    #                     details={
    #                         "panel_avail_error": str(exc)[:200],
    #                         "smart_error": str(exc2)[:200],
    #                         "legacy_error": str(exc3)[:200],
    #                     },
    #                 )
    #             return f"fallback:meeting_{round}_parked"


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
