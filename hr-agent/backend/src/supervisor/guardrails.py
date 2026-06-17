"""Six-layer guardrail enforcement for the supervisor engine.

Layer 1: HARD_DENY — actions that are never allowed regardless of context
Layer 2: ALLOWED_TRANSITIONS — stage transitions must follow the DAG
Layer 3: PERMANENT_HUMAN_GATES — finals stages always require human approval
Layer 4: Autonomy dial — per-role/per-stage permission level
Layer 5: Confidence threshold — action confidence must exceed stage threshold
Layer 6: Rate limiting — max N actions per event (prevents infinite loops)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from src.models.v1 import ALLOWED_TRANSITIONS, PipelineStage

logger = logging.getLogger(__name__)

# Per-candidate rate limiting: max actions per candidate per hour
MAX_ACTIONS_PER_CANDIDATE_PER_HOUR = 10

# Auto-demotion: consecutive wrong calls before demoting to shadow
AUTO_DEMOTION_THRESHOLD = 3

# Layer 1: Actions that are NEVER allowed for the supervisor
HARD_DENY: frozenset[str] = frozenset({
    "override_stage",
    "auto_hire",
    "auto_reject_finals",
    "delete_candidate",
    "delete_application",
    "modify_role_permissions",
})

# Layer 3: Stage transitions that ALWAYS require human confirmation.
# Not behind a feature flag. Cannot be accidentally disabled.
PERMANENT_HUMAN_GATES: frozenset[str] = frozenset({
    "technical_pending_approval",
    "ceo_pending_approval",
    "hr_evaluated",
})

# Default confidence thresholds per action category
DEFAULT_CONFIDENCE_THRESHOLDS: dict[str, float] = {
    "advance_stage": 0.8,
    "send_email": 0.7,
    "send_whatsapp": 0.7,
    "send_nudge": 0.6,
    "send_notification": 0.5,
    "schedule_interview": 0.7,
    "schedule_meeting": 0.7,
    "reschedule_meeting": 0.6,
    "check_availability": 0.3,
    "evaluate_assignment": 0.6,
    "answer_question": 0.7,
    "get_candidates": 0.3,
    "pause_timer": 0.5,
    "escalate_to_hr": 0.3,
    "request_information": 0.3,
    "withdrawal": 0.7,
    "generate_offer": 0.8,
}

DEFAULT_MIN_CONFIDENCE = 0.5


@dataclass
class GuardrailResult:
    allowed: bool
    layer: str | None = None
    reason: str | None = None


def check_hard_deny(action_type: str) -> GuardrailResult:
    """Layer 1: absolute deny list."""
    if action_type in HARD_DENY:
        return GuardrailResult(
            allowed=False,
            layer="hard_deny",
            reason=f"action '{action_type}' is permanently blocked",
        )
    return GuardrailResult(allowed=True)


def check_stage_transition(
    action_type: str,
    current_stage: str | None,
    target_stage: str | None,
) -> GuardrailResult:
    """Layer 2: transitions must follow ALLOWED_TRANSITIONS DAG."""
    if action_type != "advance_stage" or target_stage is None:
        return GuardrailResult(allowed=True)

    if current_stage is None:
        return GuardrailResult(
            allowed=False,
            layer="allowed_transitions",
            reason="no current stage to transition from",
        )

    try:
        src = PipelineStage(current_stage)
        dst = PipelineStage(target_stage)
    except ValueError:
        return GuardrailResult(
            allowed=False,
            layer="allowed_transitions",
            reason=f"invalid stage: {current_stage} → {target_stage}",
        )

    allowed_set = ALLOWED_TRANSITIONS.get(src, set())
    if dst not in allowed_set:
        return GuardrailResult(
            allowed=False,
            layer="allowed_transitions",
            reason=f"transition {current_stage} → {target_stage} not in DAG",
        )
    return GuardrailResult(allowed=True)


def check_human_gate(
    action_type: str,
    target_stage: str | None,
) -> GuardrailResult:
    """Layer 3: permanent human gates on finals decisions."""
    if action_type == "advance_stage" and target_stage in PERMANENT_HUMAN_GATES:
        return GuardrailResult(allowed=True)

    if action_type in ("approve_hire", "approve_reject"):
        return GuardrailResult(
            allowed=False,
            layer="permanent_human_gate",
            reason=f"'{action_type}' requires human decision (legal requirement)",
        )
    return GuardrailResult(allowed=True)


def check_autonomy_level(
    action_type: str,
    supervisor_mode: str,
    stage_autonomy: str | None = None,
) -> GuardrailResult:
    """Layer 4: autonomy dial — does current mode allow execution?"""
    if supervisor_mode == "shadow":
        return GuardrailResult(
            allowed=False,
            layer="autonomy_dial",
            reason="supervisor is in shadow mode (propose only)",
        )

    if stage_autonomy == "human_required":
        return GuardrailResult(
            allowed=False,
            layer="autonomy_dial",
            reason="stage requires human action",
        )

    return GuardrailResult(allowed=True)


def check_confidence(
    action_type: str,
    confidence: float | None,
) -> GuardrailResult:
    """Layer 5: action confidence must exceed threshold."""
    if confidence is None:
        return GuardrailResult(allowed=True)

    threshold = DEFAULT_CONFIDENCE_THRESHOLDS.get(
        action_type, DEFAULT_MIN_CONFIDENCE
    )
    if confidence < threshold:
        return GuardrailResult(
            allowed=False,
            layer="confidence",
            reason=f"confidence {confidence:.2f} < threshold {threshold:.2f} for '{action_type}'",
        )
    return GuardrailResult(allowed=True)


def check_rate_limit(
    actions_taken: int,
    max_actions: int,
) -> GuardrailResult:
    """Layer 6: cap actions per event to prevent loops."""
    if actions_taken >= max_actions:
        return GuardrailResult(
            allowed=False,
            layer="rate_limit",
            reason=f"max actions per event reached ({max_actions})",
        )
    return GuardrailResult(allowed=True)


async def check_candidate_rate_limit(
    application_id: UUID | None,
) -> GuardrailResult:
    """Layer 7: per-candidate hourly rate limit."""
    if not application_id:
        return GuardrailResult(allowed=True)
    try:
        from datetime import UTC, datetime, timedelta
        from sqlalchemy import func, select
        from src.db.base import SupervisorAction
        from src.db.connection import session_scope
        one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
        async with session_scope() as session:
            count = await session.scalar(
                select(func.count()).select_from(
                    select(SupervisorAction)
                    .where(SupervisorAction.application_id == application_id)
                    .where(SupervisorAction.created_at >= one_hour_ago)
                    .subquery()
                )
            ) or 0
        if count >= MAX_ACTIONS_PER_CANDIDATE_PER_HOUR:
            return GuardrailResult(
                allowed=False,
                layer="candidate_rate_limit",
                reason=f"exceeded {MAX_ACTIONS_PER_CANDIDATE_PER_HOUR} actions/hour for this candidate",
            )
    except Exception:
        logger.warning("candidate rate limit check failed", exc_info=True)
    return GuardrailResult(allowed=True)


async def check_auto_demotion(
    application_id: UUID | None,
) -> GuardrailResult:
    """Layer 8: auto-demote to shadow after consecutive rejected actions."""
    if not application_id:
        return GuardrailResult(allowed=True)
    try:
        from sqlalchemy import select
        from src.db.base import SupervisorAction
        from src.db.connection import session_scope
        async with session_scope() as session:
            recent = (
                await session.execute(
                    select(SupervisorAction)
                    .where(SupervisorAction.application_id == application_id)
                    .order_by(SupervisorAction.created_at.desc())
                    .limit(AUTO_DEMOTION_THRESHOLD)
                )
            ).scalars().all()
        if len(recent) >= AUTO_DEMOTION_THRESHOLD:
            all_rejected = all(a.rejected_by is not None for a in recent)
            if all_rejected:
                logger.warning(
                    "auto-demotion triggered for app=%s: %d consecutive rejected actions",
                    application_id, AUTO_DEMOTION_THRESHOLD,
                )
                return GuardrailResult(
                    allowed=False,
                    layer="auto_demotion",
                    reason=f"{AUTO_DEMOTION_THRESHOLD} consecutive actions rejected — demoted to shadow",
                )
    except Exception:
        logger.warning("auto-demotion check failed", exc_info=True)
    return GuardrailResult(allowed=True)


def validate_action(
    *,
    action_type: str,
    confidence: float | None = None,
    current_stage: str | None = None,
    target_stage: str | None = None,
    supervisor_mode: str = "shadow",
    stage_autonomy: str | None = None,
    actions_taken: int = 0,
    max_actions: int = 5,
) -> GuardrailResult:
    """Run all 6 guardrail layers. Returns first failure or allowed."""
    checks = [
        lambda: check_hard_deny(action_type),
        lambda: check_stage_transition(action_type, current_stage, target_stage),
        lambda: check_human_gate(action_type, target_stage),
        lambda: check_autonomy_level(action_type, supervisor_mode, stage_autonomy),
        lambda: check_confidence(action_type, confidence),
        lambda: check_rate_limit(actions_taken, max_actions),
    ]

    for check in checks:
        result = check()
        if not result.allowed:
            logger.info(
                "guardrail blocked: layer=%s action=%s reason=%s",
                result.layer, action_type, result.reason,
            )
            return result

    return GuardrailResult(allowed=True)
