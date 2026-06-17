"""Cross-stage fact verification (Phase 2).

Compares newly written evidence against prior records for the same
(application_id, fact_key). When a meaningful discrepancy is found,
creates a PipelineAlert and sends a Teams notification so HR can
investigate before the pipeline continues with stale or conflicting data.

Tolerance thresholds live in PolicyRule:
    contradiction_tolerance.<fact_key>   (float, default varies by field)
    voice_backfill_on_contradiction      (bool, default True)

Called from evidence-writing activities when ENABLE_FACT_VERIFICATION is on.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.channels import teams as teams_channel
from src.db.base import EvidenceRecord, PipelineAlert
from src.db.repositories.policy import resolve_policy

logger = logging.getLogger(__name__)

_DEFAULT_NUMERIC_TOLERANCE: dict[str, float] = {
    "total_experience_years": 1.0,
    "relevant_experience_years": 1.0,
    "current_ctc_lpa": 2.0,
    "expected_ctc_lpa": 2.0,
    "notice_period_days": 15,
}

_DEFAULT_TOLERANCE = 0.0


async def check_contradictions(
    session: AsyncSession,
    *,
    application_id: UUID,
    new_evidence: list[EvidenceRecord],
) -> list[dict]:
    """Compare new evidence against existing records. Returns list of contradictions found."""
    if not new_evidence:
        return []

    contradictions: list[dict] = []

    for record in new_evidence:
        if record.fact_value is None:
            continue

        prior = (
            await session.execute(
                select(EvidenceRecord)
                .where(
                    and_(
                        EvidenceRecord.application_id == application_id,
                        EvidenceRecord.fact_key == record.fact_key,
                        EvidenceRecord.id != record.id,
                        EvidenceRecord.superseded_by_id.is_(None),
                    )
                )
                .order_by(EvidenceRecord.created_at.desc())
            )
        ).scalars().all()

        if not prior:
            continue

        latest_prior = prior[0]
        contradiction = _detect_contradiction(
            record.fact_key, latest_prior.fact_value, record.fact_value
        )
        if contradiction is None:
            continue

        tolerance_key = f"contradiction_tolerance.{record.fact_key}"
        default_tol = _DEFAULT_NUMERIC_TOLERANCE.get(
            record.fact_key, _DEFAULT_TOLERANCE
        )
        tolerance, _ = await resolve_policy(
            session, tolerance_key, fallback=default_tol
        )

        if not _exceeds_tolerance(
            record.fact_key, latest_prior.fact_value, record.fact_value, tolerance
        ):
            continue

        contradictions.append({
            "fact_key": record.fact_key,
            "old_value": latest_prior.fact_value,
            "old_source_stage": latest_prior.source_stage,
            "new_value": record.fact_value,
            "new_source_stage": record.source_stage,
            "old_evidence_id": str(latest_prior.id),
            "new_evidence_id": str(record.id),
        })

    return contradictions


def _detect_contradiction(
    fact_key: str, old_value: Any, new_value: Any
) -> str | None:
    """Return a description string if values differ, None if they agree."""
    if old_value is None or new_value is None:
        return None

    if isinstance(old_value, (int, float)) and isinstance(new_value, (int, float)):
        if old_value != new_value:
            return f"{fact_key}: {old_value} vs {new_value}"
        return None

    if isinstance(old_value, bool) and isinstance(new_value, bool):
        if old_value != new_value:
            return f"{fact_key}: {old_value} vs {new_value}"
        return None

    if isinstance(old_value, str) and isinstance(new_value, str):
        if old_value.strip().lower() != new_value.strip().lower():
            return f"{fact_key}: '{old_value}' vs '{new_value}'"
        return None

    if isinstance(old_value, list) and isinstance(new_value, list):
        old_set = {str(v).lower() for v in old_value}
        new_set = {str(v).lower() for v in new_value}
        if old_set != new_set:
            return f"{fact_key}: lists differ"
        return None

    if str(old_value) != str(new_value):
        return f"{fact_key}: {old_value} vs {new_value}"
    return None


def _exceeds_tolerance(
    fact_key: str, old_value: Any, new_value: Any, tolerance: float
) -> bool:
    """Check if the difference exceeds the configured tolerance."""
    if isinstance(old_value, (int, float)) and isinstance(new_value, (int, float)):
        return abs(float(old_value) - float(new_value)) > tolerance

    # Non-numeric: any difference is a contradiction (tolerance ignored)
    return True


async def create_contradiction_alerts(
    session: AsyncSession,
    *,
    application_id: UUID,
    contradictions: list[dict],
    candidate_name: str | None = None,
) -> list[PipelineAlert]:
    """Write PipelineAlert rows and send Teams notifications for each contradiction."""
    alerts = []

    for c in contradictions:
        existing = await session.scalar(
            select(PipelineAlert.id).where(
                and_(
                    PipelineAlert.application_id == application_id,
                    PipelineAlert.alert_type == "fact_contradiction",
                    PipelineAlert.resolved_at.is_(None),
                    PipelineAlert.details["fact_key"].astext == c["fact_key"],
                )
            )
        )
        if existing:
            continue

        alert = PipelineAlert(
            application_id=application_id,
            alert_type="fact_contradiction",
            stage=c["new_source_stage"],
            details={
                "fact_key": c["fact_key"],
                "old_value": c["old_value"],
                "old_source_stage": c["old_source_stage"],
                "new_value": c["new_value"],
                "new_source_stage": c["new_source_stage"],
                "old_evidence_id": c["old_evidence_id"],
                "new_evidence_id": c["new_evidence_id"],
                "severity": "warning",
                "message": (
                    f"{c['fact_key']}: {c['old_source_stage']} says "
                    f"{c['old_value']}, {c['new_source_stage']} says {c['new_value']}"
                ),
            },
        )
        session.add(alert)
        alerts.append(alert)

        try:
            await teams_channel.notify_hr(
                title=f"Fact contradiction: {c['fact_key']}",
                text=(
                    f"**{c['old_source_stage']}** reported `{c['old_value']}` "
                    f"but **{c['new_source_stage']}** reports `{c['new_value']}`."
                ),
                fields={
                    "application_id": str(application_id),
                    "candidate": candidate_name or "unknown",
                    "fact_key": c["fact_key"],
                },
            )
        except Exception:
            logger.exception("Failed to send contradiction alert to Teams")

    if alerts:
        await session.flush()
        # Emit supervisor events for each contradiction
        from src.services.typed_event_bus import EventType, publish_event
        for c in contradictions:
            await publish_event(
                session,
                EventType.CONTRADICTION_DETECTED,
                application_id=application_id,
                payload=c,
                dedup_extra=f"contradiction:{c['fact_key']}:{c['new_evidence_id']}",
            )

    return alerts
