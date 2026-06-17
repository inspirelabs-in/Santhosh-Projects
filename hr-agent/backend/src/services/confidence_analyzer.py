"""Pipeline confidence analyzer.

Aggregates evidence quality signals across the application lifecycle to
produce a single confidence score. Used by progressive gate removal to
decide whether a human gate can be auto-advanced.

Signals:
1. Evidence confidence — average of EvidenceRecord.confidence per stage
2. Fact consistency — ratio of non-contradicted facts
3. Supervisor accuracy — historical approve rate for this stage type
4. Cross-stage agreement — whether different stages agree on key facts
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import (
    DecisionRecord,
    EvidenceRecord,
    PipelineAlert,
    SupervisorAction,
)

logger = logging.getLogger(__name__)


@dataclass
class StageConfidence:
    stage: str
    evidence_count: int = 0
    avg_confidence: float = 0.0
    contradiction_count: int = 0
    consistency_ratio: float = 1.0


@dataclass
class PipelineConfidence:
    overall: float = 0.0
    per_stage: dict[str, StageConfidence] = field(default_factory=dict)
    supervisor_accuracy: float | None = None
    recommendation: str = "human_review"
    details: dict = field(default_factory=dict)


async def analyze_confidence(
    session: AsyncSession,
    application_id: UUID,
    *,
    current_stage: str | None = None,
) -> PipelineConfidence:
    """Compute pipeline confidence for an application."""

    # 1. Evidence confidence per stage
    evidence_rows = await session.execute(
        select(EvidenceRecord)
        .where(
            EvidenceRecord.application_id == application_id,
            EvidenceRecord.superseded_by_id.is_(None),
        )
        .order_by(EvidenceRecord.source_stage)
    )
    evidence_list = list(evidence_rows.scalars().all())

    stage_evidence: dict[str, list[float]] = {}
    for e in evidence_list:
        stage = e.source_stage or "unknown"
        stage_evidence.setdefault(stage, []).append(e.confidence or 0.5)

    per_stage: dict[str, StageConfidence] = {}
    for stage, confidences in stage_evidence.items():
        avg = sum(confidences) / len(confidences) if confidences else 0.0
        per_stage[stage] = StageConfidence(
            stage=stage,
            evidence_count=len(confidences),
            avg_confidence=round(avg, 3),
        )

    # 2. Contradiction rate
    contradiction_alerts = await session.scalar(
        select(func.count())
        .select_from(PipelineAlert)
        .where(
            PipelineAlert.application_id == application_id,
            PipelineAlert.alert_type == "contradiction",
            PipelineAlert.resolved_at.is_(None),
        )
    ) or 0

    total_facts = len(evidence_list)
    consistency_ratio = (
        max(0.0, 1.0 - (contradiction_alerts / total_facts))
        if total_facts > 0
        else 1.0
    )

    for sc in per_stage.values():
        sc.contradiction_count = 0
        sc.consistency_ratio = consistency_ratio

    # 3. Decision coverage — how many stages have decisions
    decision_count = await session.scalar(
        select(func.count())
        .select_from(DecisionRecord)
        .where(DecisionRecord.application_id == application_id)
    ) or 0

    # 4. Supervisor accuracy (global, not per-application)
    total_reviewed = await session.scalar(
        select(func.count()).select_from(
            select(SupervisorAction).where(
                SupervisorAction.approved_by.isnot(None)
                | SupervisorAction.rejected_by.isnot(None)
            ).subquery()
        )
    ) or 0

    approved_count = await session.scalar(
        select(func.count()).select_from(
            select(SupervisorAction).where(
                SupervisorAction.approved_by.isnot(None)
            ).subquery()
        )
    ) or 0

    supervisor_accuracy = (
        round(approved_count / total_reviewed, 3)
        if total_reviewed >= 10
        else None
    )

    # 5. Cross-stage agreement on key facts
    key_facts = ("total_experience_years", "expected_ctc_lpa", "notice_period_days")
    agreement_score = 1.0
    for fact_key in key_facts:
        fact_rows = [e for e in evidence_list if e.fact_key == fact_key]
        if len(fact_rows) >= 2:
            values = [e.fact_value for e in fact_rows if e.fact_value is not None]
            if len(set(str(v) for v in values)) > 1:
                agreement_score -= 0.15

    agreement_score = max(0.0, agreement_score)

    # 6. Composite score
    stage_confidences = [sc.avg_confidence for sc in per_stage.values()] or [0.0]
    avg_evidence_confidence = sum(stage_confidences) / len(stage_confidences)

    evidence_weight = 0.35
    consistency_weight = 0.25
    agreement_weight = 0.20
    coverage_weight = 0.20

    expected_stages = max(1, _expected_stage_count(current_stage))
    coverage_ratio = min(1.0, decision_count / expected_stages)

    overall = (
        avg_evidence_confidence * evidence_weight
        + consistency_ratio * consistency_weight
        + agreement_score * agreement_weight
        + coverage_ratio * coverage_weight
    )

    if supervisor_accuracy is not None:
        overall = overall * 0.85 + supervisor_accuracy * 0.15

    overall = round(min(1.0, max(0.0, overall)), 3)

    if overall >= 0.85:
        recommendation = "auto_advance"
    elif overall >= 0.6:
        recommendation = "human_review"
    else:
        recommendation = "escalate"

    return PipelineConfidence(
        overall=overall,
        per_stage=per_stage,
        supervisor_accuracy=supervisor_accuracy,
        recommendation=recommendation,
        details={
            "avg_evidence_confidence": round(avg_evidence_confidence, 3),
            "consistency_ratio": round(consistency_ratio, 3),
            "agreement_score": round(agreement_score, 3),
            "coverage_ratio": round(coverage_ratio, 3),
            "contradiction_count": contradiction_alerts,
            "total_evidence": total_facts,
            "total_decisions": decision_count,
        },
    )


_STAGE_ORDER = [
    "applied", "screening_sent", "screening_submitted", "screening_evaluated",
    "voice_screen_scheduled", "voice_screen_completed", "voice_screen_evaluated",
    "assessment_invited", "assessment_completed", "assessment_evaluated",
    "technical_meeting_scheduled", "technical_meeting_completed", "technical_evaluated",
    "ceo_meeting_scheduled", "ceo_meeting_completed",
    "hr_meeting_scheduled", "hr_meeting_completed", "hr_evaluated",
]


def _expected_stage_count(current_stage: str | None) -> int:
    """How many decision-producing stages we expect by this point."""
    if not current_stage:
        return 1
    try:
        idx = _STAGE_ORDER.index(current_stage.lower())
        decision_stages = {"screening_evaluated", "voice_screen_evaluated",
                          "assessment_evaluated", "technical_evaluated"}
        return sum(1 for s in _STAGE_ORDER[:idx + 1] if s in decision_stages) or 1
    except ValueError:
        return 1
