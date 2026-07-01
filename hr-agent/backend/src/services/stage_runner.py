"""Generic stage-runner — the single, verdict-aware driver of a candidate's flow.

This is the runtime face of the per-role pipeline. Where ``pipeline_engine`` is the
pure planner ("given these stages + cursor, what's next?") and ``auto_progress`` is
the IO shell that fires/parks that plan, ``stage_runner`` is the thin layer the
**evaluators** call: they record *what happened* (a verdict on the stage that just
finished) and ask the runner to move the candidate forward. No evaluator names its
successor stage -- the engine decides -- so ANY configured pipeline (1 stage or 9,
any order, each auto|manual) runs generically. This is what kills the hardcoded
tech->ceo->hr->offer tail (P-0).

Contract for an evaluator:

    await advance_candidate(
        application_id=app_id,
        completed_stage_key=current_key,   # the stage that just produced a result
        verdict=StageVerdict.PASS,         # pass -> advance, fail -> reject, on_going -> hold
        result_ref={"score": 82},          # optional pointer to the result
    )

- ``verdict=PASS``  -> record it, then ``auto_progress`` advances to the next enabled
  stage in the role's pipeline (fires it if auto, parks + raises a ``requires_action``
  event if manual).
- ``verdict=FAIL``  -> consult the stage's mode: AUTO -> reject + send rejection
  email via ``_auto_reject_with_email``; MANUAL -> park for HR with score/threshold
  in the requires_action payload so HR can pass or reject from the inbox.
- ``verdict=ON_GOING`` -> record it and STOP (e.g. an interview was scheduled, a call
  placed; the next event -- analysis/eval -- will call back with PASS/FAIL).

Stage handlers (for stages the runner *fires*, e.g. email_filter/fit when auto) live
in ``STAGE_HANDLERS`` keyed by ``stage_type``; the engine + auto_progress already own
voice/assignment/offer dispatch, so this registry only needs the inline-fireable
stages that previously had no home (email_filter, fit).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from src.db.base import Application, Role
from src.db.connection import session_scope
from src.db.repositories import v1_application as app_repo
from src.models.candidate import ApplicationStatus
from src.models.pipeline import StageVerdict

logger = logging.getLogger(__name__)

# Terminal states — a finished candidate must never be advanced, parked, or
# re-notified. The reject/withdraw action owns the transition INTO these states
# (via _auto_reject_with_email); this guard just stops any later event
# (e.g. a re-delivered meeting-analysis webhook) from resurrecting them.
# "hired" has no ApplicationStatus member — it lives only as a cursor sentinel.
_TERMINAL_STATUSES = frozenset({ApplicationStatus.REJECTED, ApplicationStatus.WITHDRAWN})
_TERMINAL_CURSORS = frozenset({"rejected", "hired"})


def _is_terminal(app: Application) -> bool:
    """True when the application is already rejected/withdrawn/hired."""
    return app.status in _TERMINAL_STATUSES or (app.current_stage_key or "") in _TERMINAL_CURSORS


async def _get_stage_threshold(application_id: UUID, stage_key: str) -> int | None:
    """Return the per-stage pass_threshold from the role's pipeline stage eval_spec,
    or None when unset (falls back to the global config knob inside route_score)."""
    from src.db.repositories import role_pipeline_stage as stage_repo
    from src.models.pipeline import StageEvalSpec

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None or app.role_id is None:
            return None
        row = await stage_repo.get_stage(session, app.role_id, stage_key)
        if row is None:
            return None
        raw = row.eval_spec
        if not raw:
            return None
        try:
            spec = StageEvalSpec.model_validate(raw)
            return spec.pass_threshold
        except Exception:
            return None


async def _auto_reject_with_email(
    application_id: UUID,
    completed_stage_key: str,
    score: float | int | None,
    threshold: int | None,
) -> None:
    """Set pipeline cursor to REJECTED, send the rejection email, and emit the
    auto_rejected event.  run_rejection() already writes ApplicationStatus.REJECTED
    and CandidateStatus.REJECTED, so we only need the cursor/event here.
    """
    from src.activities.rejection import RejectionInput, run_rejection
    from src.db.events import emit_event
    from src.db.repositories.audit import log_audit
    from src.db.repositories.v1_application import set_stage
    from src.models.events import EventType
    from src.models.pipeline import StageStatus
    from src.models.v1 import PipelineStage
    from src.services.events import publish_event

    # Fetch candidate_id before entering the session that sets the stage cursor.
    candidate_id: UUID | None = None
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            return
        candidate_id = app.candidate_id

    # 1. Send the rejection email + set ApplicationStatus / CandidateStatus.
    #    run_rejection() is self-contained and must run BEFORE we finalize the
    #    pipeline cursor so that any observer sees a consistent state afterwards.
    try:
        await run_rejection(
            RejectionInput(
                candidate_id=candidate_id,
                application_id=application_id,
                stage=completed_stage_key,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "auto-reject email failed for %s at stage %s: %s",
            application_id, completed_stage_key, exc,
        )

    # 2. Advance the V2 pipeline cursor to REJECTED and emit events.
    #    We do NOT call set_stage(REJECTED) here because set_stage is legacy-only
    #    (see V1->V2-migration note in v1_application.py).  Instead we write
    #    current_stage_key + stage_status directly, matching the convention used
    #    by _park_for_review / _park_gate in this file.
    async with session_scope() as session:
        app = await session.get(Application, application_id, with_for_update=True)
        if app is None:
            return
        app.current_stage_key = "rejected"
        app.stage_status = str(StageStatus.FAILED)
        await emit_event(
            session,
            type=EventType.STAGE_CHANGED,
            org_id=app.org_id,
            application_id=application_id,
            role_id=app.role_id,
            payload={
                "to": "rejected",
                "from": completed_stage_key,
                "status": str(StageStatus.FAILED),
                "reason": "auto_rejected",
                "score": score,
                "threshold": threshold,
            },
            actor="agent",
        )
        await log_audit(
            session,
            application_id=application_id,
            action="auto_rejected",
            actor="agent",
            details={
                "stage_key": completed_stage_key,
                "score": score,
                "threshold": threshold,
                "reason": f"stage '{completed_stage_key}' verdict=fail",
            },
        )

    try:
        await publish_event(
            application_id,
            event="auto_rejected",
            data={"stage_key": completed_stage_key, "score": score, "threshold": threshold},
        )
    except Exception:  # noqa: BLE001
        pass


async def advance_candidate(
    *,
    application_id: UUID,
    completed_stage_key: str | None = None,
    verdict: StageVerdict | str = StageVerdict.PASS,
    score: float | int | None = None,
    result_ref: Any | None = None,
) -> str:
    """Record the completed stage's verdict, then move the candidate forward.

    Returns a short decision string (mirrors auto_progress): "advanced:<...>",
    "rejected:<stage>", "held:<stage>", "skipped:<reason>".
    """
    # Terminal guard: never advance/park a finished candidate. A late event
    # (re-delivered webhook, retry) must not resurrect a rejected/hired app.
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            return "skipped:missing"
        if _is_terminal(app):
            logger.info(
                "advance_candidate skipped: %s already terminal (status=%s cursor=%s)",
                application_id, app.status, app.current_stage_key,
            )
            return f"skipped:terminal:{app.current_stage_key or app.status}"

    # If a raw 0-100 score is supplied, the shared router decides the verdict
    # (pass/needs_review/reject) -- one consistent rule for every stage. The
    # prompt never sees the threshold; routing lives here.
    # Per-stage threshold override: read the completed stage's eval_spec so a
    # role can set a higher/lower bar than the global default, while None falls
    # back to the global knob inside route_score.
    if score is not None:
        from src.services.evaluation import route_score

        per_stage_threshold = (
            await _get_stage_threshold(application_id, completed_stage_key)
            if completed_stage_key
            else None
        )
        verdict = route_score(score, threshold=per_stage_threshold)
    verdict_val = verdict.value if isinstance(verdict, StageVerdict) else str(verdict)

    # 1. Record the verdict on the stage that just finished (idempotent overlay write).
    if completed_stage_key:
        async with session_scope() as session:
            await app_repo.record_stage_verdict(
                session,
                application_id,
                completed_stage_key,
                verdict=verdict_val,
                result_ref=result_ref,
            )

    # 2. ON_GOING -> hold. The next event (analysis/eval) will call back.
    if verdict_val == StageVerdict.ON_GOING.value:
        return f"held:{completed_stage_key or 'current'}"

    # 2b. NEEDS_REVIEW -> park at this stage for a human. Never auto-advance and
    # never auto-reject a borderline candidate; HR's pass/reject drives the move.
    if verdict_val == StageVerdict.NEEDS_REVIEW.value:
        await _park_for_review(application_id, completed_stage_key, result_ref)
        return f"needs_review:{completed_stage_key or 'current'}"

    # 3. FAIL -> consult the completed stage's mode.
    #    AUTO (or no mode — treat as auto for mandatory auto stages like fit):
    #      immediately reject + send email.
    #    MANUAL: park for HR with score + threshold in the payload so HR can
    #      decide; the resolve-review endpoint will call back with PASS/FAIL and
    #      the FAIL path will then send the email.
    if verdict_val == StageVerdict.FAIL.value:
        stage_mode: str = "auto"  # default: auto-reject when mode is unset
        if completed_stage_key:
            from src.db.repositories import role_pipeline_stage as _stage_repo

            async with session_scope() as session:
                _app = await session.get(Application, application_id)
                if _app is not None and _app.role_id is not None:
                    _row = await _stage_repo.get_stage(
                        session, _app.role_id, completed_stage_key
                    )
                    if _row is not None and _row.mode:
                        stage_mode = _row.mode

        if stage_mode == "manual":
            # Park for HR: include score + threshold so the UI can show context.
            per_stage_threshold_display = (
                await _get_stage_threshold(application_id, completed_stage_key)
                if completed_stage_key
                else None
            )
            await _park_for_review(
                application_id,
                completed_stage_key,
                {
                    **(result_ref or {}),
                    "score": score,
                    "threshold": per_stage_threshold_display,
                    "verdict": "fail",
                    "note": "scored below threshold — HR can pass or reject",
                },
            )
            return f"parked_fail_manual:{completed_stage_key}"

        # mode == "auto" (or unset): auto-reject + email.
        per_stage_threshold_auto = (
            await _get_stage_threshold(application_id, completed_stage_key)
            if completed_stage_key
            else None
        )
        await _auto_reject_with_email(
            application_id,
            completed_stage_key or "unknown",
            score,
            per_stage_threshold_auto,
        )
        return f"rejected:{completed_stage_key}"

    # 4. PASS -> advance through the role's pipeline (engine decides the next move).
    from src.services.auto_progress import auto_progress

    decision = await auto_progress(application_id=application_id)
    return f"advanced:{decision}"


async def complete_assignment_submission(
    application_id: UUID,
    *,
    overall_score: int | None = None,
    result_ref: Any | None = None,
) -> str:
    """The candidate submitted their take-home. Decide what "done" means for THIS
    role's pipeline -- the one place the "auto-send on entry, review on submit" rule
    lives, so the careers form (pipeline/v1) and the agentic apply path stay in sync:

    - If ``overall_score`` is provided (from AssignmentParseResult), route through
      the shared scored path: the stage's mode (auto/manual) and the per-stage
      threshold gate auto-reject or park exactly like voice/meeting/fit stages.
    - If ``overall_score`` is None (LLM produced no score), fall back to the
      NEEDS_REVIEW park so a human reviews it -- never auto-reject without a score.
    """
    if overall_score is not None:
        return await advance_candidate(
            application_id=application_id,
            completed_stage_key="assignment",
            score=overall_score,
            result_ref=result_ref,
        )

    # No score -- park for HR review (NEEDS_REVIEW path).
    return await advance_candidate(
        application_id=application_id,
        completed_stage_key="assignment",
        verdict=StageVerdict.NEEDS_REVIEW,
        result_ref=result_ref,
    )


# ---------------------------------------------------------------------------
# Inline-stage recorders. email_filter (runs at mail_ingest) and fit (runs at
# intake) are INLINE: the engine walks past them, so they are not "fired" during
# forward progression. These helpers RECORD their per-candidate verdict so a JD
# whose pipeline includes them has real, trackable stages (e.g. a 1-stage
# ``[email_filter]`` pipeline). Called from the intake path (pipeline/v1).
# ---------------------------------------------------------------------------


async def record_email_filter_passed(application_id: UUID) -> None:
    """Record that the inbound funnel accepted this application as a real
    application (the funnel already ran in mail_ingest). Makes ``email_filter`` a
    visible, passed stage in the candidate's stage view."""
    async with session_scope() as session:
        # Only record if the role actually has an email_filter stage.
        app = await session.get(Application, application_id)
        if app is None or app.role_id is None:
            return
        if not await _role_has_stage(session, app.role_id, "email_filter"):
            return
        await app_repo.record_stage_verdict(
            session, application_id, "email_filter",
            verdict=StageVerdict.PASS.value,
            result_ref={"source": "intake_funnel"},
        )


async def record_fit_verdict(
    application_id: UUID, *, tier: str, result_ref: Any | None = None
) -> None:
    """Record the fit stage's verdict from its tier (RED=fail, else pass). Called
    by the intake fit path so ``fit`` shows as a processed stage with a verdict."""
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None or app.role_id is None:
            return
        if not await _role_has_stage(session, app.role_id, "fit"):
            return
        verdict = StageVerdict.FAIL if (tier or "").lower() == "red" else StageVerdict.PASS
        await app_repo.record_stage_verdict(
            session, application_id, "fit",
            verdict=verdict.value,
            result_ref=result_ref or {"tier": tier},
        )


async def _role_has_stage(session, role_id: UUID, stage_key: str) -> bool:
    from src.db.repositories import role_pipeline_stage as _stage_repo

    return await _stage_repo.get_stage(session, role_id, stage_key) is not None


async def _park_for_review(
    application_id: UUID, completed_stage_key: str | None, result_ref: Any | None
) -> None:
    """Park a borderline candidate at the stage that just scored and raise a
    ``needs_review`` requires_action item for HR. Mirrors auto_progress._park_gate
    but for a SCORED stage (not a configured human gate): the candidate stays put
    until HR confirms pass (advance) or reject. Independent of the stage's
    auto|manual mode -- a review-band score always needs a human."""
    from src.db.base import Application
    from src.db.events import emit_event
    from src.db.repositories.audit import log_audit
    from src.models.events import ActionType, EventType
    from src.models.pipeline import StageStatus
    from src.services.events import publish_event

    async with session_scope() as session:
        app = await session.get(Application, application_id, with_for_update=True)
        if app is None:
            return
        # Atomic terminal guard under the row lock: if the candidate was rejected
        # (or hired) between the caller's read and this write, do NOT re-park or
        # re-emit needs_review events. This is the race-proof stop for the
        # "rejected candidate keeps bouncing back to needs_hr_review" bug.
        if _is_terminal(app):
            logger.info(
                "_park_for_review skipped: %s already terminal (status=%s cursor=%s)",
                application_id, app.status, app.current_stage_key,
            )
            return
        if completed_stage_key:
            app.current_stage_key = completed_stage_key
        app.stage_status = str(StageStatus.PARKED)

        await emit_event(
            session,
            type=EventType.STAGE_CHANGED,
            org_id=app.org_id,
            application_id=application_id,
            role_id=app.role_id,
            payload={"to": completed_stage_key, "status": str(StageStatus.PARKED), "reason": "needs_review"},
            actor="agent",
        )
        await emit_event(
            session,
            type=ActionType.NEEDS_REVIEW.value,
            org_id=app.org_id,
            application_id=application_id,
            role_id=app.role_id,
            payload={
                "stage_key": completed_stage_key,
                "result_ref": result_ref,
                "note": "borderline score -- HR confirms pass or reject",
            },
            actor="agent",
            requires_action=True,
        )
        await log_audit(
            session,
            application_id=application_id,
            action="parked_needs_review",
            actor="agent",
            details={"stage_key": completed_stage_key, "result_ref": result_ref},
        )

    try:
        await publish_event(
            application_id, event="needs_review", data={"stage_key": completed_stage_key}
        )
    except Exception:  # noqa: BLE001
        pass
