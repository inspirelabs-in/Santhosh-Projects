"""Arq job wrappers around the agentic activities.

Arq jobs receive ``ctx`` as their first argument and must use JSON-friendly
parameter types (UUIDs come in as strings). Each wrapper hydrates the
typed inputs and delegates to the canonical activity in ``src/activities``.

Keeping these wrappers thin means the activities stay testable without arq.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select

from src.activities.v1_ceo_brief import generate_ceo_brief as _generate_ceo_brief
from src.activities.v1_dispatch_assessment import dispatch_assessment as _dispatch_assessment
from src.activities.v1_dispatch_meeting_bot import dispatch_meeting_bot as _dispatch_meeting_bot
from src.activities.v1_evaluate_voice_call import evaluate_voice_call as _evaluate_voice_call
from src.activities.v1_meeting_analysis import analyze_meeting as _analyze_meeting
from src.activities.v1_schedule_meeting import schedule_meeting as _schedule_meeting
from src.activities.v1_dispatch_voice_call import dispatch_voice_call as _dispatch_voice_call
from src.activities.v1_voice_screening import dispatch_voice_screening as _dispatch_voice_screening
from src.config import get_settings
from src.constants.external import ELEVENLABS_API_BASE
from src.constants.timers import (
    ASSIGNMENT_DEADLINE_REMINDER_WINDOW_HOURS,
    MEETING_ANALYSIS_STUCK_CUTOFF_MINUTES,
    MEETING_IN_CALL_STUCK_CUTOFF_HOURS,
    POLL_DUE_CALLBACKS_HORIZON_SECONDS,
    PRUNE_OLD_ARTIFACTS_MIN_RETENTION_DAYS,
    RECONCILE_STUCK_MEETINGS_CRON_MINUTES,
    RECONCILE_STUCK_VOICE_CALLS_CRON_MINUTES,
    VOICE_CALL_EVAL_RETRY_CUTOFF_MINUTES,
    VOICE_CALL_NO_PICKUP_CUTOFF_MINUTES,
    VOICE_CALL_OVER_RUNTIME_GRACE_SECONDS,
)
from src.db.base import VoiceCall
from src.db.connection import session_scope
from src.models.v1 import CallKind, VoiceCallStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Voice screening
# ---------------------------------------------------------------------------


async def dispatch_voice_screening(
    ctx: dict[str, Any],
    application_id: str,
    *,
    attempt_no: int | None = None,
    scheduled_at_iso: str | None = None,
) -> str:
    scheduled_at = (
        datetime.fromisoformat(scheduled_at_iso) if scheduled_at_iso else None
    )
    voice_call_id = await _dispatch_voice_screening(
        application_id=UUID(application_id),
        attempt_no=attempt_no,
        scheduled_at=scheduled_at,
    )
    return str(voice_call_id)


async def dispatch_voice_call(
    ctx: dict[str, Any],
    application_id: str,
    *,
    call_kind: str = "screening",
    attempt_no: int | None = None,
    scheduled_at_iso: str | None = None,
    campaign_id: str | None = None,
) -> str:
    scheduled_at = (
        datetime.fromisoformat(scheduled_at_iso) if scheduled_at_iso else None
    )
    voice_call_id = await _dispatch_voice_call(
        application_id=UUID(application_id),
        call_kind=CallKind(call_kind),
        attempt_no=attempt_no,
        scheduled_at=scheduled_at,
        campaign_id=UUID(campaign_id) if campaign_id else None,
    )
    return str(voice_call_id)


async def evaluate_voice_call(
    ctx: dict[str, Any],
    voice_call_id: str,
) -> dict[str, Any]:
    score = await _evaluate_voice_call(
        voice_call_id=UUID(voice_call_id),
    )
    return score.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------


async def dispatch_assessment(
    ctx: dict[str, Any],
    application_id: str,
) -> None:
    await _dispatch_assessment(application_id=UUID(application_id))


# ---------------------------------------------------------------------------
# Meetings
# ---------------------------------------------------------------------------


async def dispatch_meeting_bot(
    ctx: dict[str, Any],
    application_id: str,
    round: str,
    teams_join_url: str,
    scheduled_at_iso: str,
    interview_id: str | None = None,
) -> str:
    meeting_id = await _dispatch_meeting_bot(
        application_id=UUID(application_id),
        round=round,
        teams_join_url=teams_join_url,
        scheduled_at=datetime.fromisoformat(scheduled_at_iso),
        interview_id=UUID(interview_id) if interview_id else None,
    )
    return str(meeting_id)


async def analyze_meeting(ctx: dict[str, Any], meeting_session_id: str) -> dict[str, Any]:
    analysis = await _analyze_meeting(meeting_session_id=UUID(meeting_session_id))
    return analysis.model_dump(mode="json")


async def schedule_meeting(
    ctx: dict[str, Any],
    application_id: str,
    *,
    round: str,
    avoid_iso: list[str] | None = None,
    attempt_no: int = 1,
) -> str:
    avoid: list[datetime] = []
    if avoid_iso:
        for s in avoid_iso:
            try:
                avoid.append(datetime.fromisoformat(s))
            except ValueError:
                continue
    meeting_id = await _schedule_meeting(
        application_id=UUID(application_id),
        round=round,
        avoid_starts=avoid or None,
        attempt_no=attempt_no,
    )
    return str(meeting_id)


async def schedule_meeting_reattempt(
    ctx: dict[str, Any],
    application_id: str,
    *,
    voice_call_id: str | None = None,
    requested_at_iso: str | None = None,
    round: str = "technical",
) -> str | None:
    """Re-pick a slot after the candidate rejected the previous one."""
    try:
        meeting_id = await _schedule_meeting(
            application_id=UUID(application_id),
            round=round,
        )
        return str(meeting_id)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# CEO brief
# ---------------------------------------------------------------------------


async def generate_ceo_brief(ctx: dict[str, Any], application_id: str) -> str:
    return await _generate_ceo_brief(application_id=UUID(application_id))


# ---------------------------------------------------------------------------
# Smart meeting scheduling
# ---------------------------------------------------------------------------


async def smart_schedule_meeting(ctx: dict[str, Any], application_id: str, *, round: str) -> str:
    from src.services.smart_scheduler import initiate_smart_schedule

    ms_id = await initiate_smart_schedule(application_id=UUID(application_id), round=round)
    return f"initiated:{ms_id}"


async def panel_availability_request(ctx: dict[str, Any], application_id: str, *, round: str) -> str:
    from src.services.panel_availability import initiate_panel_availability

    ms_id = await initiate_panel_availability(application_id=UUID(application_id), round=round)
    return f"initiated:{ms_id}"


# ---------------------------------------------------------------------------
# Cron: scan for due voice-screen callbacks and re-dispatch.
# ---------------------------------------------------------------------------


async def poll_due_callbacks(ctx: dict[str, Any]) -> int:
    """Find ``voice_calls`` rows whose callback_at has elapsed and re-dispatch.

    Runs on a fixed schedule via ``WorkerSettings.cron_jobs``. Each due row
    is bumped to attempt_no+1 by ``_dispatch_voice_screening`` and the
    original row stays in ``callback_requested`` for audit history.
    """

    settings = get_settings()
    if not settings.enable_voice_screening:
        return 0

    now = datetime.now(UTC)
    horizon = now + timedelta(seconds=POLL_DUE_CALLBACKS_HORIZON_SECONDS)
    dispatched = 0

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(VoiceCall)
                .where(
                    VoiceCall.status == VoiceCallStatus.CALLBACK_REQUESTED.value,
                    VoiceCall.callback_at.isnot(None),
                    VoiceCall.callback_at <= horizon,
                )
                .order_by(VoiceCall.callback_at.asc())
                .limit(20)
            )
        ).scalars().all()
        candidates = [
            (row.application_id, row.attempt_no, row.id, getattr(row, "call_kind", "screening"))
            for row in rows
        ]

    for application_id, attempt_no, original_id, call_kind in candidates:
        if attempt_no >= settings.voice_agent_max_callback_attempts:
            logger.info(
                "voice call %s exceeded max callback attempts (%d); skipping",
                original_id,
                settings.voice_agent_max_callback_attempts,
            )
            continue
        try:
            if call_kind == "screening":
                await _dispatch_voice_screening(
                    application_id=application_id,
                    attempt_no=attempt_no + 1,
                    scheduled_at=now,
                )
            else:
                await _dispatch_voice_call(
                    application_id=application_id,
                    call_kind=CallKind(call_kind),
                    attempt_no=attempt_no + 1,
                    scheduled_at=now,
                )
            dispatched += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "callback redispatch failed for application %s: %s",
                application_id,
                exc,
            )

    return dispatched


# ---------------------------------------------------------------------------
# Cron: campaign dispatcher tick — scans all dispatching campaigns.
# ---------------------------------------------------------------------------


async def campaign_dispatch_tick(ctx: dict[str, Any]) -> dict[str, Any]:
    """Scan all dispatching campaigns and dispatch one tick for each."""
    from src.activities.v1_campaign_dispatcher import campaign_dispatch_tick_all

    return await campaign_dispatch_tick_all()


# ---------------------------------------------------------------------------
# Cron: merged tick — combines poll_due_callbacks + campaign_dispatch_tick
# into a single cron registration to cut Redis/arq scheduling overhead.
#
# Both originals were "every 15s" pollers, staggered ~7s apart only to spread
# load (poll_due_callbacks at :00/:15/:30/:45, campaign_dispatch_tick at
# :07/:22/:37/:52). Merged, that stagger is pointless, so this job fires once
# every 15s (:00/:15/:30/:45) and runs BOTH bodies each tick — preserving each
# job's original 4x/min frequency. We deliberately do NOT re-check the exact
# second here: with poll_delay raised to 10s, arq picks a cron up to ~10s after
# its scheduled second (jobs are enqueued with _defer_until=next_run and only
# collected on the next poll iteration), so datetime.now().second would rarely
# match an exact set and would skip the bodies. Each body is wrapped in its own
# try/except so a failure in one can never block the other.
# ---------------------------------------------------------------------------


async def voice_and_campaign_tick(ctx: dict[str, Any]) -> dict[str, Any]:
    """Run poll_due_callbacks + campaign_dispatch_tick bodies every tick from a
    single merged cron registration (each at its original 4x/min frequency)."""
    result: dict[str, Any] = {}

    try:
        result["poll_due_callbacks"] = await poll_due_callbacks(ctx)
    except Exception:
        logger.exception("voice_and_campaign_tick: poll_due_callbacks failed")

    try:
        result["campaign_dispatch_tick"] = await campaign_dispatch_tick(ctx)
    except Exception:
        logger.exception("voice_and_campaign_tick: campaign_dispatch_tick failed")

    return result


# ---------------------------------------------------------------------------
# Cron: reconcile stuck voice calls.
# ---------------------------------------------------------------------------


async def reconcile_stuck_voice_calls(ctx: dict[str, Any]) -> int:
    """Pull conversation status from ElevenLabs for rows stuck in dialing /
    in_progress beyond the call-duration cap. Webhooks can be missed (ngrok
    restart, network blip, ElevenLabs delivery failure). Without this, the row
    sits "live" forever and no downstream pipeline step runs.
    """
    import httpx

    from src.api.webhooks_voice import (
        _extract_callback,
        _extract_callback_from_analysis,
        _extract_callback_natural,
        _fetch_and_store_recording,
        _store_transcript,
        _transcript_text,
        _zip_answers,
        ElTranscriptTurn,
    )
    from src.db.repositories.audit import log_audit
    from src.db.repositories.v1_application import set_stage
    from src.db.repositories.voice_call import (
        mark_failed,
        save_call_completion,
        save_callback_request,
    )
    from src.models.v1 import PipelineStage
    from src.services.queue import enqueue
    from src.services.voice_window import clamp_to_call_window

    settings = get_settings()
    if not settings.enable_voice_screening or not settings.elevenlabs_api_key:
        return 0

    # A call is "stuck" if it never started ringing within 5 min, or if it
    # started but exceeded the max call duration with no ended_at. Webhook
    # delivery failures are the usual cause.
    now = datetime.now(UTC)
    no_pickup_cutoff = now - timedelta(minutes=VOICE_CALL_NO_PICKUP_CUTOFF_MINUTES)
    over_runtime_cutoff = now - timedelta(
        seconds=settings.voice_agent_max_call_seconds + VOICE_CALL_OVER_RUNTIME_GRACE_SECONDS
    )
    reconciled = 0

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(VoiceCall)
                .where(
                    VoiceCall.status.in_(
                        [
                            VoiceCallStatus.DIALING.value,
                            VoiceCallStatus.IN_PROGRESS.value,
                        ]
                    ),
                    VoiceCall.provider_call_id.isnot(None),
                )
                .order_by(VoiceCall.created_at.asc())
                .limit(20)
            )
        ).scalars().all()
        rows = [
            r
            for r in rows
            if (r.started_at is None and r.created_at < no_pickup_cutoff)
            or (r.started_at is not None and r.started_at < over_runtime_cutoff)
        ]
        targets = [
            (
                r.id,
                r.application_id,
                r.provider_call_id,
                r.questions,
                r.attempt_no,
            )
            for r in rows
        ]

    if not targets:
        return 0

    headers = {"xi-api-key": settings.elevenlabs_api_key}
    base = f"{ELEVENLABS_API_BASE}/convai/conversations"

    for voice_call_id, application_id, provider_call_id, questions, attempt_no in targets:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(f"{base}/{provider_call_id}", headers=headers)
                if resp.status_code == 404:
                    async with session_scope() as session:
                        await mark_failed(
                            session,
                            voice_call_id,
                            error="elevenlabs conversation not found (reconcile)",
                            status=VoiceCallStatus.FAILED,
                        )
                    continue
                resp.raise_for_status()
                conv = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "reconcile fetch failed for %s: %s", provider_call_id, exc
            )
            continue

        conv_status = (conv.get("status") or "").lower()
        if conv_status in ("in_progress", "processing", "started", "initiated", ""):
            # Still live on provider side or never connected -- skip.
            # "initiated" means the call was placed but never bridged; let it
            # age out via the no-answer retry path instead of faking a completion.
            continue

        # Build typed turn list once.
        turns = [
            ElTranscriptTurn(
                role=t.get("role", "user"),
                message=t.get("message"),
                time_in_call_secs=t.get("time_in_call_secs"),
            )
            for t in (conv.get("transcript") or [])
            if isinstance(t, dict)
        ]
        analysis = conv.get("analysis") if isinstance(conv.get("analysis"), dict) else None
        metadata = conv.get("metadata") if isinstance(conv.get("metadata"), dict) else {}
        duration_sec = metadata.get("call_duration_secs")

        # Determine if this was a callback request.
        callback_at_raw, callback_reason = _extract_callback(turns)
        if callback_at_raw is None:
            callback_at_raw, analysis_reason = _extract_callback_from_analysis(analysis)
            if callback_reason is None:
                callback_reason = analysis_reason
        if callback_at_raw is None:
            callback_at_raw, natural_reason = _extract_callback_natural(turns)
            if callback_reason is None:
                callback_reason = natural_reason

        if callback_at_raw is not None:
            callback_at, _ = clamp_to_call_window(callback_at_raw)
            async with session_scope() as session:
                await save_callback_request(
                    session,
                    voice_call_id,
                    callback_at=callback_at,
                    reason=callback_reason,
                )
                await set_stage(
                    session,
                    application_id,
                    PipelineStage.VOICE_SCREEN_CALLBACK_REQUESTED,
                    force=True,
                )
                await log_audit(
                    session,
                    application_id=application_id,
                    action="voice_call_callback_requested_reconcile",
                    actor="agent",
                    details={
                        "voice_call_id": str(voice_call_id),
                        "callback_at": callback_at.isoformat(),
                    },
                )
            await enqueue(
                "dispatch_voice_screening",
                str(application_id),
                attempt_no=attempt_no + 1,
                scheduled_at_iso=callback_at.isoformat(),
                _defer_until=callback_at,
                _job_id=f"voice-callback-{voice_call_id}-{attempt_no + 1}",
            )
            reconciled += 1
            continue

        # Guard: if ElevenLabs returned a terminal status but the call never
        # actually connected (0 transcript turns, 0 or missing duration), this
        # is a telephony failure — not a real screening. Mark as no-answer so
        # the retry/HR-review path handles it instead of sending empty data to
        # the LLM evaluator.
        if not turns and (duration_sec is None or duration_sec == 0):
            async with session_scope() as session:
                await mark_failed(
                    session,
                    voice_call_id,
                    error=f"call never connected (reconcile: status={conv_status}, 0 turns, dur=0)",
                    status=VoiceCallStatus.NO_ANSWER,
                )
                await log_audit(
                    session,
                    application_id=application_id,
                    action="voice_call_never_connected_reconcile",
                    actor="agent",
                    details={
                        "voice_call_id": str(voice_call_id),
                        "conversation_id": provider_call_id,
                        "conv_status": conv_status,
                    },
                )
            reconciled += 1
            continue

        # Otherwise: treat as completed. Persist transcript + recording + answers.
        transcript_text = _transcript_text(turns)
        transcript_key = await _store_transcript(
            application_id, voice_call_id, transcript_text
        )
        recording_key = await _fetch_and_store_recording(
            application_id, voice_call_id, provider_call_id
        )
        answers_jsonable = _zip_answers(questions, turns)
        async with session_scope() as session:
            await save_call_completion(
                session,
                voice_call_id,
                answers=answers_jsonable,
                transcript_r2_key=transcript_key,
                recording_r2_key=recording_key,
                duration_sec=duration_sec,
                ended_at=datetime.now(UTC),
            )
            await set_stage(
                session,
                application_id,
                PipelineStage.VOICE_SCREEN_COMPLETED,
                force=True,
            )
            await log_audit(
                session,
                application_id=application_id,
                action="voice_call_completed_reconcile",
                actor="agent",
                details={
                    "voice_call_id": str(voice_call_id),
                    "conversation_id": provider_call_id,
                    "duration_sec": duration_sec,
                    "answer_count": len(answers_jsonable),
                },
            )
        await enqueue("evaluate_voice_call", str(voice_call_id))
        reconciled += 1

    # Phase 2: pick up calls that completed (transcript saved) but evaluation
    # failed — they sit in voice_screen_completed with no verdict indefinitely.
    eval_retry_cutoff = now - timedelta(minutes=VOICE_CALL_EVAL_RETRY_CUTOFF_MINUTES)
    async with session_scope() as session:
        from src.db.base import Application as _App

        stuck_evaluated = (
            await session.execute(
                select(VoiceCall)
                .join(_App, _App.id == VoiceCall.application_id)
                .where(
                    VoiceCall.status == VoiceCallStatus.COMPLETED.value,
                    VoiceCall.transcript_r2_key.isnot(None),
                    VoiceCall.overall_score.is_(None),
                    VoiceCall.ended_at < eval_retry_cutoff,
                )
                .limit(10)
            )
        ).scalars().all()

    for vc in stuck_evaluated:
        logger.info(
            "reconcile: retrying evaluation for voice_call %s (completed but no verdict)",
            vc.id,
        )
        queued = await enqueue("evaluate_voice_call", str(vc.id))
        if queued:
            reconciled += 1

    return reconciled


# ---------------------------------------------------------------------------
# Cron: assignment deadline reminders.
# ---------------------------------------------------------------------------


async def assignment_deadline_reminders(ctx: dict[str, Any]) -> int:
    """Find assignments due within 24h that haven't been submitted and nudge."""
    from sqlalchemy import and_

    from src.db.base import Application, AssignmentRow, Candidate, Role
    from src.db.repositories.audit import log_audit

    now = datetime.now(UTC)
    reminder_window = timedelta(hours=ASSIGNMENT_DEADLINE_REMINDER_WINDOW_HOURS)
    reminded = 0

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(AssignmentRow, Application, Candidate, Role)
                .join(Application, Application.id == AssignmentRow.application_id)
                .join(Candidate, Candidate.id == Application.candidate_id)
                .join(Role, Role.id == Application.role_id, isouter=True)
                .where(
                    and_(
                        AssignmentRow.submitted_at.is_(None),
                        AssignmentRow.deadline_at.isnot(None),
                        AssignmentRow.deadline_at <= now + reminder_window,
                        AssignmentRow.deadline_at > now,
                        AssignmentRow.reminder_sent_at.is_(None),
                        Application.current_stage.in_(("assignment_sent",)),
                    )
                )
            )
        ).all()

        for assignment, app, candidate, role in rows:
            if not candidate.email:
                continue

            hours_left = max(0, (assignment.deadline_at - now).total_seconds() / 3600)
            try:
                from src.channels.email import send_email

                await send_email(
                    to=candidate.email,
                    template="assignment_deadline_reminder",
                    variables={
                        "candidate_name": candidate.name or "there",
                        "role_title": role.title if role else "the position",
                        "hours_remaining": round(hours_left),
                        "deadline": assignment.deadline_at.strftime("%B %d, %Y at %I:%M %p UTC"),
                    },
                    tags={"type": "assignment_reminder", "application_id": str(app.id)},
                    idempotency_key=f"assignment-reminder-{assignment.id}",
                    application_id=str(app.id),
                    candidate_id=str(candidate.id),
                )
                assignment.reminder_sent_at = now
                await log_audit(
                    session,
                    application_id=app.id,
                    candidate_id=candidate.id,
                    action="assignment_deadline_reminder_sent",
                    actor="agent",
                    details={
                        "hours_remaining": round(hours_left, 1),
                        "deadline": assignment.deadline_at.isoformat(),
                    },
                )
                reminded += 1
            except Exception as exc:
                logger.warning(
                    "assignment reminder failed for %s: %s", app.id, exc
                )

    return reminded


# ---------------------------------------------------------------------------
# Cron: pipeline SLA monitor.
# Detects applications stalled past per-stage thresholds and raises a
# ``pipeline_alerts`` row so the dashboard can surface them. Idempotent --
# refreshes the existing unresolved row instead of duplicating.
# ---------------------------------------------------------------------------


_STAGE_SLA_HOURS: dict[str, float] = {
    "applied": 1,
    "resume_parsed": 1,
    "fit_scored": 1,
    "screening_sent": 72,
    "screening_submitted": 1,
    "screening_evaluated": 6,
    "voice_screen_scheduled": 4,
    "voice_screen_in_progress": 1,
    "voice_screen_completed": 6,
    "voice_screen_evaluated": 6,
    "assessment_sent": 168,  # 7 days
    "assessment_submitted": 6,
    "assessment_evaluated": 24,
    "technical_meeting_scheduled": 168,
    "technical_pending_approval": 48,
    "ceo_meeting_scheduled": 168,
    "ceo_pending_approval": 48,
    "hr_meeting_scheduled": 168,
    "hr_evaluated": 48,
    "needs_hr_review": 48,
    "report_ready": 24,
}


async def pipeline_sla_monitor(ctx: dict[str, Any]) -> int:
    """Scan applications, raise/refresh alerts for those past stage SLA."""
    from sqlalchemy import and_

    from src.db.base import Application, PipelineAlert

    raised = 0
    now = datetime.now(UTC)

    async with session_scope() as session:
        terminal = {"rejected", "offer_extended", "offer_accepted", "offer_declined", "withdrawn"}
        apps = (
            await session.execute(
                select(Application).where(~Application.current_stage.in_(terminal))
            )
        ).scalars().all()

        for app in apps:
            sla = _STAGE_SLA_HOURS.get(app.current_stage)
            if sla is None:
                continue
            stage_age = (now - (app.updated_at or app.created_at)).total_seconds() / 3600
            if stage_age < sla:
                continue
            existing = (
                await session.execute(
                    select(PipelineAlert).where(
                        and_(
                            PipelineAlert.application_id == app.id,
                            PipelineAlert.alert_type == "stuck_in_stage",
                            PipelineAlert.resolved_at.is_(None),
                        )
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.stage != app.current_stage:
                    existing.resolved_at = now
                    session.add(
                        PipelineAlert(
                            application_id=app.id,
                            alert_type="stuck_in_stage",
                            stage=app.current_stage,
                            hours_stuck=stage_age,
                            details={"sla_hours": sla},
                        )
                    )
                    raised += 1
                else:
                    existing.hours_stuck = stage_age
                continue
            session.add(
                PipelineAlert(
                    application_id=app.id,
                    alert_type="stuck_in_stage",
                    stage=app.current_stage,
                    hours_stuck=stage_age,
                    details={"sla_hours": sla},
                )
            )
            raised += 1

    return raised


# ---------------------------------------------------------------------------
# Cron: storage pruning. Deletes recordings + transcripts older than the
# tenant's retention setting.
# ---------------------------------------------------------------------------


async def prune_old_artifacts(ctx: dict[str, Any]) -> int:
    """Delete voice/meeting recordings + transcripts past retention. Best-effort."""
    from src.db.base import VoiceCall, MeetingSession
    from src.services.file_storage import delete as delete_blob

    settings = get_settings()
    days = max(PRUNE_OLD_ARTIFACTS_MIN_RETENTION_DAYS, settings.data_retention_days_default)
    cutoff = datetime.now(UTC) - timedelta(days=days)
    deleted = 0
    bucket = settings.r2_bucket_resumes

    from sqlalchemy import or_ as _or
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(VoiceCall).where(
                    VoiceCall.created_at < cutoff,
                    _or(
                        VoiceCall.recording_r2_key.isnot(None),
                        VoiceCall.transcript_r2_key.isnot(None),
                    ),
                )
            )
        ).scalars().all()
        for row in rows:
            for key_attr in ("recording_r2_key", "transcript_r2_key"):
                k = getattr(row, key_attr, None)
                if not k:
                    continue
                try:
                    await delete_blob(bucket=bucket, key=k)
                    setattr(row, key_attr, None)
                    deleted += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("prune voice artifact failed key=%s: %s", k, exc)

        meetings = (
            await session.execute(
                select(MeetingSession).where(
                    MeetingSession.created_at < cutoff,
                    _or(
                        MeetingSession.recording_r2_key.isnot(None),
                        MeetingSession.transcript_r2_key.isnot(None),
                    ),
                )
            )
        ).scalars().all()
        for row in meetings:
            for key_attr in ("recording_r2_key", "transcript_r2_key"):
                k = getattr(row, key_attr, None)
                if not k:
                    continue
                try:
                    await delete_blob(bucket=bucket, key=k)
                    setattr(row, key_attr, None)
                    deleted += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("prune meeting artifact failed key=%s: %s", k, exc)

    return deleted


# ---------------------------------------------------------------------------
# Cron: reconcile stuck meetings.
# Mirrors reconcile_stuck_voice_calls for the meeting pipeline. Two cases:
# 1. bot_status='done' + transcript saved but no analysis (analyzer crash/timeout)
# 2. bot_status='in_call' for > 3 hours (bot never reported back)
# ---------------------------------------------------------------------------


async def reconcile_stuck_meetings(ctx: dict[str, Any]) -> int:
    from src.db.base import MeetingSession
    from src.db.repositories.meeting_session import mark_failed as meeting_mark_failed
    from src.db.repositories.audit import log_audit
    from src.services.queue import enqueue

    settings = get_settings()
    now = datetime.now(UTC)
    reconciled = 0

    # Case 1: transcript done, analysis never ran
    analysis_cutoff = now - timedelta(minutes=MEETING_ANALYSIS_STUCK_CUTOFF_MINUTES)
    async with session_scope() as session:
        stuck_analyzed = (
            await session.execute(
                select(MeetingSession).where(
                    MeetingSession.bot_status == "done",
                    MeetingSession.transcript_r2_key.isnot(None),
                    MeetingSession.overall_score.is_(None),
                    MeetingSession.ended_at < analysis_cutoff,
                ).limit(10)
            )
        ).scalars().all()

    for ms in stuck_analyzed:
        logger.info(
            "reconcile meeting: retrying analysis for session=%s (done but no verdict)",
            ms.id,
        )
        queued = await enqueue("analyze_meeting", str(ms.id))
        if queued:
            reconciled += 1

    # Case 2: in_call for too long — bot never reported completion
    in_call_cutoff = now - timedelta(hours=MEETING_IN_CALL_STUCK_CUTOFF_HOURS)
    async with session_scope() as session:
        stuck_in_call = (
            await session.execute(
                select(MeetingSession).where(
                    MeetingSession.bot_status == "in_call",
                    MeetingSession.started_at < in_call_cutoff,
                ).limit(10)
            )
        ).scalars().all()

    for ms in stuck_in_call:
        logger.warning(
            "reconcile meeting: session=%s stuck in_call since %s — marking failed",
            ms.id,
            ms.started_at,
        )
        async with session_scope() as session:
            await meeting_mark_failed(
                session,
                ms.id,
                error=f"stuck_in_call >3h (reconcile, started={ms.started_at})",
            )
            await log_audit(
                session,
                application_id=ms.application_id,
                action="meeting_stuck_in_call_reconcile",
                actor="agent",
                details={
                    "meeting_session_id": str(ms.id),
                    "started_at": ms.started_at.isoformat() if ms.started_at else None,
                },
            )
        reconciled += 1

    return reconciled


# ---------------------------------------------------------------------------
# Cron: merged tick — combines reconcile_stuck_voice_calls +
# reconcile_stuck_meetings into a single cron registration to cut Redis/arq
# scheduling overhead.
#
# The two original crons fire on disjoint minute-sets (voice reconciler at
# :03/:18/:33/:48, meeting reconciler at :10/:25/:40/:55, each every 15
# minutes). To keep each job running at EXACTLY its original cadence under
# one registration, this merged job is scheduled at the union of both
# minute-sets, then internally re-checks the current minute against each
# job's own original set before invoking that job's body. Each body is
# wrapped in its own try/except so a failure in one can never block the
# other.
#
# run_at_startup: the original voice-call reconciler ran at startup
# (run_at_startup=True); the meeting reconciler did not. arq's run_at_startup
# only affects the very first firing after worker boot -- it runs that one
# job immediately (regardless of minute), then falls back to the normal
# minute-based schedule for every later run (see arq.worker.run_cron: a cron
# job with run_at_startup=True gets `next_run = <boot time>` once, then
# `calculate_next()` takes over exactly like any other cron job).
#
# The merged cron below is registered with run_at_startup=True, so arq gives
# it one immediate boot-time firing (arbitrary minute) followed by firings
# strictly on the union-of-minutes schedule. A process-local flag tracks
# "has this worker process fired this cron yet" so the boot-time firing
# unconditionally runs the voice-call body (mirroring its original
# run_at_startup=True) regardless of which minute the worker happened to
# start on, while the meeting reconciler -- which never had run_at_startup --
# only runs on its own matching minutes.
# ---------------------------------------------------------------------------

_voice_and_meetings_cron_started = False


async def reconcile_stuck_voice_and_meetings(ctx: dict[str, Any]) -> dict[str, Any]:
    """Run reconcile_stuck_voice_calls + reconcile_stuck_meetings bodies on
    their original independent cadences from a single merged cron registration."""
    global _voice_and_meetings_cron_started

    now = datetime.now(UTC)
    minute = now.minute
    result: dict[str, Any] = {}

    is_boot_firing = not _voice_and_meetings_cron_started
    _voice_and_meetings_cron_started = True

    is_voice_minute = minute in RECONCILE_STUCK_VOICE_CALLS_CRON_MINUTES
    is_meeting_minute = minute in RECONCILE_STUCK_MEETINGS_CRON_MINUTES

    if is_voice_minute or is_boot_firing:
        try:
            result["reconcile_stuck_voice_calls"] = await reconcile_stuck_voice_calls(ctx)
        except Exception:
            logger.exception(
                "reconcile_stuck_voice_and_meetings: reconcile_stuck_voice_calls failed"
            )

    if is_meeting_minute:
        try:
            result["reconcile_stuck_meetings"] = await reconcile_stuck_meetings(ctx)
        except Exception:
            logger.exception(
                "reconcile_stuck_voice_and_meetings: reconcile_stuck_meetings failed"
            )

    return result
