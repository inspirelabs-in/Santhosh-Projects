"""Auto-progression: after every successful stage transition, fire the next.

Reads ``role.scoring_rubric.agentic.auto_progress`` (defaults to true). When
on, the agent advances candidates through the entire pipeline -- recruiters
only intervene when something parks at ``needs_hr_review``.

Mapping (current_stage -> action):

  applied / screening_evaluated / report_ready -> dispatch voice screen
  voice_screen_evaluated:pass                  -> dispatch assessment
  assessment_evaluated:pass                    -> schedule technical meeting
  technical_evaluated:pass                     -> schedule CEO meeting
  ceo_meeting_completed:pass                   -> schedule HR meeting
  any verdict=clear_reject                     -> auto-reject (if enabled)

This module never blocks the calling activity -- every dispatch goes
through the Arq queue (with BackgroundTasks fallback). Every action emits
a ``progressed`` audit row so HR can read the agent's reasoning later.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from src.db.base import Application, Role
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.v1_application import set_stage
from src.models.v1 import PipelineStage
from src.services.events import publish_event
from src.services.queue import enqueue

logger = logging.getLogger(__name__)


def _voice_globally_enabled() -> bool:
    try:
        from src.config import get_settings
        return bool(get_settings().enable_voice_screening)
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
        # Never fire side effects (dial, email assignment) on a rejected /
        # withdrawn application -- HR has already decided this candidate is
        # out. Belt-and-braces: status check + stage check.
        if (app.status or "").lower() in {"rejected", "withdrawn"}:
            return "skipped:application_not_active"
        role = await session.get(Role, app.role_id) if app.role_id else None
        # Same guard for the role: closed / archived roles must not generate
        # outbound calls or emails to their candidates.
        if role is not None and (role.status or "").lower() not in {"open"}:
            return f"skipped:role_status_{role.status}"
        rubric = role.scoring_rubric if role else None
        agentic = _agentic_cfg(rubric)
        scheduling = _scheduling_cfg(rubric)

        if agentic.get("auto_progress") is False:
            return "skipped:auto_progress_off"

        stage = PipelineStage(app.current_stage)

    # ---- branch by stage ---------------------------------------------------
    decision = "noop"

    if stage in {
        PipelineStage.APPLIED,
        PipelineStage.SCREENING_EVALUATED,
        PipelineStage.REPORT_READY,
    } and (agentic.get("voice_screening_enabled") or _voice_globally_enabled()):
        decision = await _fire_voice_screen(application_id)

    elif stage == PipelineStage.VOICE_SCREEN_EVALUATED:
        decision = await _fire_assessment(application_id)

    elif stage == PipelineStage.ASSESSMENT_EVALUATED and scheduling.get("enabled"):
        decision = await _fire_meeting(application_id, "technical")

    elif stage == PipelineStage.TECHNICAL_EVALUATED and scheduling.get("enabled"):
        decision = await _fire_meeting(application_id, "ceo")

    elif stage == PipelineStage.CEO_MEETING_COMPLETED and scheduling.get("enabled"):
        decision = await _fire_meeting(application_id, "hr")

    if decision != "noop":
        async with session_scope() as session:
            await log_audit(
                session,
                application_id=application_id,
                action="auto_progress_fired",
                actor="agent",
                details={"from_stage": stage.value, "decision": decision},
            )
        await publish_event(
            application_id,
            event="auto_progressed",
            data={"from_stage": stage.value, "decision": decision},
        )
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
    """Schedule a retry for a failed voice dispatch instead of parking immediately.

    Tries up to 2 deferred retries (5min, 15min) before falling back to HR review.
    """
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
        from src.services.fallback_manager import voice_to_chat_fallback

        result = await voice_to_chat_fallback(application_id, error)
        return f"fallback:{result}"[:80]

    delay = 300 * (2 ** (attempt - 2))  # 5min, 10min, 20min
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
        from src.services.fallback_manager import voice_to_chat_fallback

        result = await voice_to_chat_fallback(application_id, f"{error} (arq unavailable for retry)")
        return f"fallback:{result}"[:80]

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
        # Arq disabled -- can't defer inline (would block). Park for HR
        # to dispatch manually from the dashboard.
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
    except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
        logger.warning("auto assessment dispatch failed for %s: %s", application_id, exc)
        from src.services.fallback_manager import llm_fallback

        await llm_fallback(application_id, None, "assessment_dispatch", str(exc))
        return f"fallback:assessment_parked:{exc}"[:80]


async def _fire_meeting(application_id: UUID, round: str) -> str:
    queued = await enqueue("schedule_meeting", str(application_id), round=round)
    if queued:
        return f"fired:meeting_{round}"
    try:
        from src.activities.v1_schedule_meeting import schedule_meeting

        await schedule_meeting(application_id=application_id, round=round)
        return f"fired:meeting_{round}_inline"
    except Exception as exc:  # noqa: BLE001
        logger.warning("auto schedule %s failed for %s: %s", round, application_id, exc)
        async with session_scope() as session:
            await set_stage(
                session, application_id, PipelineStage.NEEDS_HR_REVIEW, force=True
            )
            await log_audit(
                session,
                application_id=application_id,
                action=f"meeting_{round}_schedule_failed",
                actor="agent",
                details={"error": str(exc)[:300]},
            )
        return f"fallback:meeting_{round}_parked"
