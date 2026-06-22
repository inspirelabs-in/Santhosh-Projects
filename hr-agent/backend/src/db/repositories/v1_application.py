"""V1 application repository: state-machine transitions + JSONB setters.

Concentrates all writes to the new V1 columns so activities stay thin.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import Application
from src.models.v1 import PipelineStage, can_transition

logger = logging.getLogger(__name__)


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
    old_stage = app.current_stage
    app.current_stage = new_stage.value

    # Stage-1 cutover dual-write: track the candidate's position in the new
    # role_pipeline_stages model (current_stage_key/stage_status) and emit a
    # durable domain_event (powers the trace + realtime + future inbox). Legacy
    # current_stage stays authoritative; this is best-effort and never blocks.
    try:
        from src.services.state_machine import legacy_stage_to_key

        skey, sstatus = legacy_stage_to_key(new_stage)
        if skey:
            app.current_stage_key = skey
            app.stage_status = sstatus

        from src.db.events import emit_event

        await emit_event(
            session,
            type="stage_changed",
            org_id=getattr(app, "org_id", None),
            application_id=application_id,
            role_id=app.role_id,
            payload={
                "from": old_stage,
                "to": new_stage.value,
                "stage_key": skey,
                "status": sstatus,
                "forced": force,
            },
        )
    except Exception:  # noqa: BLE001
        logger.debug("stage_key dual-write / domain event emit failed (non-fatal)", exc_info=True)

    # Emit supervisor event (no-op if supervisor disabled)
    from src.services.typed_event_bus import EventType, publish_event
    await publish_event(
        session,
        EventType.STAGE_CHANGED,
        application_id=application_id,
        candidate_id=app.candidate_id,
        payload={"old_stage": old_stage, "new_stage": new_stage.value, "forced": force},
        dedup_extra=f"{old_stage}->{new_stage.value}",
    )


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


# ---------------------------------------------------------------------------
# Per-stage outcome overlay (the generic stage-runner).
#
# applications.stage_results is a map keyed by role_pipeline_stages.stage_key:
#   { "<stage_key>": {
#        "processing_status": "unprocessed|processing|processed|failed",
#        "verdict":           "pending|on_going|pass|fail",
#        "result_ref":        <any json>,   # score / audit id / blob pointer
#        "updated_at":        "<iso8601>" } }
#
# "Processed vs not" for stages the runner hasn't touched is DERIVED from the
# cursor + the role template; this overlay stores only the per-stage verdict +
# the processing claim (idempotency). Absent stage == unprocessed/pending.
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _merge_stage_result(app: Application, stage_key: str, patch: dict[str, Any]) -> dict:
    """Merge ``patch`` into stage_results[stage_key]; reassign for JSONB dirty-tracking."""
    results = dict(app.stage_results or {})
    entry = dict(results.get(stage_key) or {})
    entry.update(patch)
    entry["updated_at"] = _now_iso()
    results[stage_key] = entry
    app.stage_results = results
    return entry


async def claim_stage_processing(
    session: AsyncSession, application_id: UUID, stage_key: str
) -> bool:
    """Atomically claim a stage for processing (idempotency guard).

    Returns True if THIS caller won the claim (status moved to 'processing'),
    False if the stage was already 'processing' or 'processed' (skip — someone
    else owns it). A 'failed' stage may be re-claimed (retry).
    """
    app = await session.get(Application, application_id, with_for_update=True)
    if app is None:
        raise ValueError(f"application {application_id} not found")
    entry = (app.stage_results or {}).get(stage_key) or {}
    status = entry.get("processing_status")
    if status in ("processing", "processed"):
        return False
    _merge_stage_result(
        app, stage_key,
        {"processing_status": "processing",
         "verdict": entry.get("verdict") or "on_going"},
    )
    return True


async def record_stage_verdict(
    session: AsyncSession,
    application_id: UUID,
    stage_key: str,
    *,
    verdict: str,
    result_ref: Any | None = None,
    processing_status: str = "processed",
) -> None:
    """Finalize a stage's outcome: set verdict (+ optional result pointer) and
    mark it processed (or failed). The single place evaluators record "what
    happened" before asking the runner what's next."""
    app = await session.get(Application, application_id, with_for_update=True)
    if app is None:
        raise ValueError(f"application {application_id} not found")
    patch: dict[str, Any] = {"verdict": verdict, "processing_status": processing_status}
    if result_ref is not None:
        patch["result_ref"] = result_ref
    _merge_stage_result(app, stage_key, patch)


async def mark_stage_failed(
    session: AsyncSession, application_id: UUID, stage_key: str, *, error: str | None = None
) -> None:
    """Mark a stage's processing as failed (retryable). Distinct from verdict=fail
    (a candidate failing the stage); this is OUR processing erroring out."""
    app = await session.get(Application, application_id, with_for_update=True)
    if app is None:
        raise ValueError(f"application {application_id} not found")
    patch: dict[str, Any] = {"processing_status": "failed"}
    if error:
        patch["error"] = error[:300]
    _merge_stage_result(app, stage_key, patch)


async def candidate_stage_view(
    session: AsyncSession, application_id: UUID
) -> list[dict[str, Any]]:
    """The full per-candidate stage list = role template joined with the overlay.

    Each item: {stage_key, stage_type, label, position, mode, is_enabled,
    is_current, processing_status, verdict}. Stages the runner hasn't touched
    default to processing_status='unprocessed', verdict='pending'. "Processed vs
    not" is derived from the cursor position; the overlay supplies verdict + any
    explicit processing/verdict the runner recorded. This is the read model that
    answers "what stages has this candidate processed / not?".
    """
    from src.db.repositories import role_pipeline_stage as _stage_repo

    app = await session.get(Application, application_id)
    if app is None:
        raise ValueError(f"application {application_id} not found")

    overlay = app.stage_results or {}
    cursor = app.current_stage_key
    rows = (
        await _stage_repo.for_role(session, app.role_id, enabled_only=False)
        if app.role_id is not None
        else []
    )
    # Position of the cursor in the (enabled-or-not) ordered list, for derivation.
    ordered = sorted(rows, key=lambda r: int(getattr(r, "position", 0)))
    cursor_pos = next(
        (i for i, r in enumerate(ordered) if r.stage_key == cursor), None
    )

    view: list[dict[str, Any]] = []
    for i, r in enumerate(ordered):
        entry = overlay.get(r.stage_key) or {}
        # Derive a default processing_status from position when the runner hasn't
        # recorded one (so the read model is complete without materializing rows).
        if entry.get("processing_status"):
            proc = entry["processing_status"]
        elif cursor_pos is None:
            proc = "unprocessed"
        elif i < cursor_pos:
            proc = "processed"
        elif i == cursor_pos:
            proc = "processing"
        else:
            proc = "unprocessed"
        view.append(
            {
                "stage_key": r.stage_key,
                "stage_type": r.stage_type,
                "label": r.label,
                "position": int(getattr(r, "position", i)),
                "mode": getattr(r, "mode", None) or "manual",
                "is_enabled": bool(getattr(r, "is_enabled", True)),
                "is_current": r.stage_key == cursor,
                "processing_status": proc,
                "verdict": entry.get("verdict") or "pending",
                "result_ref": entry.get("result_ref"),
            }
        )
    return view
