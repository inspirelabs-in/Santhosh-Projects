"""Progressive gate removal: auto-advance past non-finals human gates.

When confidence is high enough AND autonomy is full_auto, the pipeline
skips NEEDS_HR_REVIEW and advances to the next stage directly.

PERMANENT_HUMAN_GATES (technical_pending_approval, ceo_pending_approval,
hr_evaluated) are NEVER auto-advanced regardless of confidence.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repositories.audit import log_audit
from src.db.repositories.policy import resolve_policy
from src.services.confidence_analyzer import analyze_confidence
from src.supervisor.autonomy import AutonomyLevel, resolve_autonomy_level
from src.supervisor.guardrails import PERMANENT_HUMAN_GATES

logger = logging.getLogger(__name__)

_NON_REMOVABLE_GATES = PERMANENT_HUMAN_GATES | frozenset({
    "hired", "rejected",
})


async def should_auto_advance_gate(
    session: AsyncSession,
    application_id: UUID,
    current_stage: str,
    *,
    role_id: UUID | None = None,
    role_rubric: dict | None = None,
) -> bool:
    """Check whether a human gate can be auto-advanced.

    Returns True if ALL conditions met:
    1. enable_confidence_gates is on
    2. Stage is NOT in PERMANENT_HUMAN_GATES
    3. Autonomy level for stage is FULL_AUTO
    4. Pipeline confidence >= role's threshold (default 0.85)
    """
    from src.config import get_settings
    settings = get_settings()

    if not settings.enable_confidence_gates:
        return False

    if current_stage.lower() in _NON_REMOVABLE_GATES:
        return False

    autonomy = await resolve_autonomy_level(
        session, current_stage, role_id=role_id, role_rubric=role_rubric,
    )
    if autonomy != AutonomyLevel.FULL_AUTO:
        return False

    confidence = await analyze_confidence(
        session, application_id, current_stage=current_stage,
    )

    threshold, _ = await resolve_policy(
        session, "confidence_gate_threshold", role_id, fallback=0.85,
    )

    can_advance = confidence.overall >= threshold

    await log_audit(
        session,
        application_id=application_id,
        action="confidence_gate_check",
        actor="agent",
        details={
            "stage": current_stage,
            "confidence": confidence.overall,
            "threshold": threshold,
            "recommendation": confidence.recommendation,
            "auto_advance": can_advance,
            "autonomy": autonomy.value,
        },
    )

    if can_advance:
        logger.info(
            "confidence gate auto-advance: app=%s stage=%s confidence=%.3f threshold=%.3f",
            application_id, current_stage, confidence.overall, threshold,
        )
    else:
        logger.info(
            "confidence gate: park for HR: app=%s stage=%s confidence=%.3f < threshold=%.3f",
            application_id, current_stage, confidence.overall, threshold,
        )

    return can_advance
