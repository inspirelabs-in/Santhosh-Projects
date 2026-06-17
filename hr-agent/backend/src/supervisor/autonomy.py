"""Autonomy dial: per-role, per-stage permission levels.

Determines whether the supervisor can act autonomously or must defer to
a human at each pipeline stage. Levels (ascending autonomy):

    human_required      — always needs human action (finals gates)
    supervisor_shadow   — supervisor proposes, human approves
    supervisor_execute  — supervisor acts, human can override
    full_auto           — no human in the loop

Resolution order:
    1. role.scoring_rubric["autonomy"][stage]  (per-role override)
    2. PolicyRule "autonomy.<stage>"           (global default)
    3. Hardcoded defaults                     (conservative)
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repositories.policy import resolve_policy
from src.supervisor.guardrails import PERMANENT_HUMAN_GATES


class AutonomyLevel(StrEnum):
    HUMAN_REQUIRED = "human_required"
    SUPERVISOR_SHADOW = "supervisor_shadow"
    SUPERVISOR_EXECUTE = "supervisor_execute"
    FULL_AUTO = "full_auto"


# Conservative defaults: most stages shadow, finals always human
_STAGE_DEFAULTS: dict[str, AutonomyLevel] = {
    "applied": AutonomyLevel.FULL_AUTO,
    "screening_sent": AutonomyLevel.FULL_AUTO,
    "screening_submitted": AutonomyLevel.SUPERVISOR_EXECUTE,
    "screening_evaluated": AutonomyLevel.SUPERVISOR_EXECUTE,
    "needs_hr_review": AutonomyLevel.SUPERVISOR_SHADOW,
    "assignment_sent": AutonomyLevel.SUPERVISOR_EXECUTE,
    "assignment_submitted": AutonomyLevel.SUPERVISOR_EXECUTE,
    "report_ready": AutonomyLevel.SUPERVISOR_SHADOW,
    "voice_screen_scheduled": AutonomyLevel.SUPERVISOR_EXECUTE,
    "voice_screen_in_progress": AutonomyLevel.SUPERVISOR_EXECUTE,
    "voice_screen_completed": AutonomyLevel.SUPERVISOR_EXECUTE,
    "voice_screen_evaluated": AutonomyLevel.SUPERVISOR_EXECUTE,
    "technical_meeting_scheduled": AutonomyLevel.SUPERVISOR_SHADOW,
    "technical_meeting_completed": AutonomyLevel.SUPERVISOR_SHADOW,
    "technical_evaluated": AutonomyLevel.SUPERVISOR_SHADOW,
    "technical_pending_approval": AutonomyLevel.HUMAN_REQUIRED,
    "ceo_meeting_scheduled": AutonomyLevel.SUPERVISOR_SHADOW,
    "ceo_meeting_completed": AutonomyLevel.SUPERVISOR_SHADOW,
    "ceo_pending_approval": AutonomyLevel.HUMAN_REQUIRED,
    "hr_meeting_scheduled": AutonomyLevel.SUPERVISOR_SHADOW,
    "hr_meeting_completed": AutonomyLevel.SUPERVISOR_SHADOW,
    "hr_evaluated": AutonomyLevel.HUMAN_REQUIRED,
    "rejected": AutonomyLevel.HUMAN_REQUIRED,
    "hired": AutonomyLevel.HUMAN_REQUIRED,
}


async def resolve_autonomy_level(
    session: AsyncSession,
    stage: str,
    role_id: UUID | None = None,
    role_rubric: dict | None = None,
) -> AutonomyLevel:
    """Resolve autonomy level for a stage. Permanent gates override everything."""
    # Layer 0: permanent human gates cannot be overridden
    if stage in PERMANENT_HUMAN_GATES:
        return AutonomyLevel.HUMAN_REQUIRED

    # Layer 1: per-role override from scoring_rubric
    if role_rubric:
        autonomy_config = role_rubric.get("autonomy", {})
        if stage in autonomy_config:
            level_str = autonomy_config[stage].get("level")
            if level_str:
                try:
                    return AutonomyLevel(level_str)
                except ValueError:
                    pass

    # Layer 2: PolicyRule global default
    policy_val, _ = await resolve_policy(
        session, f"autonomy.{stage}", role_id, fallback=None
    )
    if policy_val:
        try:
            return AutonomyLevel(policy_val)
        except ValueError:
            pass

    # Layer 3: hardcoded conservative default
    return _STAGE_DEFAULTS.get(stage, AutonomyLevel.SUPERVISOR_SHADOW)


def can_execute(level: AutonomyLevel, supervisor_mode: str) -> bool:
    """Whether the supervisor can execute (not just propose) at this autonomy level."""
    if level == AutonomyLevel.HUMAN_REQUIRED:
        return False
    if level == AutonomyLevel.SUPERVISOR_SHADOW:
        return False
    if level == AutonomyLevel.SUPERVISOR_EXECUTE:
        return supervisor_mode == "execute"
    if level == AutonomyLevel.FULL_AUTO:
        return supervisor_mode in ("execute", "shadow")
    return False
