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
- ``verdict=FAIL``  -> record it, reject the application (+ rejection email on the
  auto lane via the existing ``auto_reject_if_configured`` / rejection path).
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
from src.models.pipeline import StageVerdict

logger = logging.getLogger(__name__)


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
    # If a raw 0-100 score is supplied, the shared router decides the verdict
    # (pass/needs_review/reject) -- one consistent rule for every stage. The
    # prompt never sees the threshold; routing lives here.
    if score is not None:
        from src.services.evaluation import route_score

        verdict = route_score(score)
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

    # 3. FAIL -> reject (auto lane sends the rejection email via the existing path).
    if verdict_val == StageVerdict.FAIL.value:
        from src.services.auto_progress import auto_reject_if_configured

        rejected = await auto_reject_if_configured(
            application_id=application_id,
            reason=f"stage '{completed_stage_key}' verdict=fail",
        )
        return f"rejected:{completed_stage_key}" if rejected else f"fail_recorded:{completed_stage_key}"

    # 4. PASS -> advance through the role's pipeline (engine decides the next move).
    from src.services.auto_progress import auto_progress

    decision = await auto_progress(application_id=application_id)
    return f"advanced:{decision}"


async def complete_assignment_submission(
    application_id: UUID, *, result_ref: Any | None = None
) -> str:
    """The candidate submitted their take-home. Decide what "done" means for THIS
    role's pipeline -- the one place the "auto-send on entry, review on submit" rule
    lives, so the careers form (pipeline/v1) and the agentic apply path stay in sync:

    - If the role still has a separate ``assessment_review`` gate (a legacy
      pipeline), PASS the assignment so that gate performs the human review.
    - Otherwise (the merged single-stage model) PARK the assignment itself for HR
      review: the candidate stays on ``assignment`` until a recruiter approves or
      rejects via the needs-review resolve endpoint. No second stage required.
    """
    # ASSESSMENT_REVIEW is deprecated — all new pipelines use the merged model
    # where the assignment stage itself parks for HR review on submit.
    verdict = StageVerdict.NEEDS_REVIEW
    return await advance_candidate(
        application_id=application_id,
        completed_stage_key="assignment",
        verdict=verdict,
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
