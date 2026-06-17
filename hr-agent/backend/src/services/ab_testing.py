"""A/B testing framework for supervisor decisions.

Allows running controlled experiments on supervisor behavior:
- Prompt variants (different system prompts or reasoning instructions)
- Confidence threshold variants
- Action preference variants (e.g., email-first vs WhatsApp-first)

Experiments are deterministic: same application always gets same variant
(hash-based assignment). Outcomes tracked via supervisor_actions table.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import SupervisorAction
from src.db.connection import session_scope

logger = logging.getLogger(__name__)

# In-memory experiment registry (loaded from DB on startup, refreshed periodically)
_active_experiments: dict[str, "Experiment"] = {}


@dataclass
class Variant:
    name: str
    weight: float  # 0-1, all weights in experiment should sum to 1
    config: dict[str, Any]  # overrides applied to supervisor behavior


@dataclass
class Experiment:
    id: UUID
    name: str
    description: str
    variants: list[Variant]
    target_stage: str | None  # only apply to specific stages, None = all
    target_event_types: list[str]  # only apply to specific events, empty = all
    sample_rate: float  # 0-1, fraction of eligible events to include
    status: str  # active | paused | concluded
    created_at: datetime
    concluded_at: datetime | None = None
    conclusion_notes: str | None = None


def _hash_assignment(experiment_id: UUID, application_id: UUID) -> float:
    """Deterministic 0-1 hash for consistent variant assignment."""
    h = hashlib.sha256(f"{experiment_id}:{application_id}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def assign_variant(
    experiment: Experiment,
    application_id: UUID,
) -> Variant | None:
    """Deterministically assign an application to a variant.

    Returns None if application falls outside sample_rate.
    """
    if experiment.status != "active":
        return None

    hash_val = _hash_assignment(experiment.id, application_id)

    if hash_val > experiment.sample_rate:
        return None

    # Normalize within sample: map [0, sample_rate) → [0, 1)
    normalized = hash_val / experiment.sample_rate
    cumulative = 0.0
    for variant in experiment.variants:
        cumulative += variant.weight
        if normalized < cumulative:
            return variant

    return experiment.variants[-1] if experiment.variants else None


def get_active_experiments(
    stage: str | None = None,
    event_type: str | None = None,
) -> list[Experiment]:
    """Get experiments applicable to this stage/event combo."""
    results = []
    for exp in _active_experiments.values():
        if exp.status != "active":
            continue
        if exp.target_stage and stage and exp.target_stage != stage:
            continue
        if exp.target_event_types and event_type and event_type not in exp.target_event_types:
            continue
        results.append(exp)
    return results


def resolve_experiment_overrides(
    application_id: UUID,
    stage: str | None = None,
    event_type: str | None = None,
) -> dict[str, Any]:
    """Get merged config overrides from all active experiments for this application.

    Returns dict of overrides to apply to supervisor behavior.
    Keys: system_prompt_suffix, confidence_overrides, preferred_channel, etc.
    """
    overrides: dict[str, Any] = {}
    experiments = get_active_experiments(stage, event_type)

    assigned_variants: list[dict[str, str]] = []

    for exp in experiments:
        variant = assign_variant(exp, application_id)
        if variant:
            overrides.update(variant.config)
            assigned_variants.append({
                "experiment": exp.name,
                "variant": variant.name,
            })

    if assigned_variants:
        overrides["_experiments"] = assigned_variants

    return overrides


# ---------------------------------------------------------------------------
# DB-backed experiment CRUD
# ---------------------------------------------------------------------------

try:
    from src.db.base import Base, func
    from sqlalchemy import Boolean, DateTime, Float, String, Text
    from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
    from sqlalchemy.orm import Mapped, mapped_column

    class ExperimentRow(Base):
        __tablename__ = "supervisor_experiments"

        id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=None)
        name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
        description: Mapped[str | None] = mapped_column(Text)
        variants: Mapped[list] = mapped_column(JSONB, default=list)
        target_stage: Mapped[str | None] = mapped_column(String(64))
        target_event_types: Mapped[list] = mapped_column(JSONB, default=list)
        sample_rate: Mapped[float] = mapped_column(Float, default=1.0)
        status: Mapped[str] = mapped_column(String(16), default="active")
        created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
        concluded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
        conclusion_notes: Mapped[str | None] = mapped_column(Text)

except Exception:
    ExperimentRow = None  # type: ignore[assignment,misc]


def _row_to_experiment(row: Any) -> Experiment:
    return Experiment(
        id=row.id,
        name=row.name,
        description=row.description or "",
        variants=[
            Variant(name=v["name"], weight=v.get("weight", 0.5), config=v.get("config", {}))
            for v in (row.variants or [])
        ],
        target_stage=row.target_stage,
        target_event_types=row.target_event_types or [],
        sample_rate=row.sample_rate or 1.0,
        status=row.status,
        created_at=row.created_at,
        concluded_at=row.concluded_at,
        conclusion_notes=row.conclusion_notes,
    )


async def load_experiments() -> int:
    """Load active experiments from DB into memory. Returns count loaded."""
    if ExperimentRow is None:
        return 0
    try:
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(ExperimentRow).where(ExperimentRow.status == "active")
                )
            ).scalars().all()
            _active_experiments.clear()
            for row in rows:
                exp = _row_to_experiment(row)
                _active_experiments[exp.name] = exp
            return len(rows)
    except Exception:
        logger.warning("failed to load experiments", exc_info=True)
        return 0


async def create_experiment(
    session: AsyncSession,
    *,
    name: str,
    description: str,
    variants: list[dict[str, Any]],
    target_stage: str | None = None,
    target_event_types: list[str] | None = None,
    sample_rate: float = 1.0,
) -> Experiment:
    """Create a new experiment."""
    import uuid
    row = ExperimentRow(
        id=uuid.uuid4(),
        name=name,
        description=description,
        variants=variants,
        target_stage=target_stage,
        target_event_types=target_event_types or [],
        sample_rate=sample_rate,
        status="active",
    )
    session.add(row)
    await session.flush()
    exp = _row_to_experiment(row)
    _active_experiments[exp.name] = exp
    return exp


async def conclude_experiment(
    session: AsyncSession,
    experiment_name: str,
    *,
    notes: str = "",
) -> Experiment | None:
    """Conclude an experiment and compute results."""
    if ExperimentRow is None:
        return None
    row = (
        await session.execute(
            select(ExperimentRow).where(ExperimentRow.name == experiment_name)
        )
    ).scalar_one_or_none()
    if not row:
        return None

    row.status = "concluded"
    row.concluded_at = datetime.now(UTC)
    row.conclusion_notes = notes
    await session.flush()

    exp = _row_to_experiment(row)
    _active_experiments.pop(experiment_name, None)
    return exp


async def get_experiment_results(
    session: AsyncSession,
    experiment_name: str,
) -> dict[str, Any]:
    """Compute outcome metrics per variant for an experiment."""
    if experiment_name not in _active_experiments:
        # Try loading from DB
        if ExperimentRow is not None:
            row = (
                await session.execute(
                    select(ExperimentRow).where(ExperimentRow.name == experiment_name)
                )
            ).scalar_one_or_none()
            if row:
                exp = _row_to_experiment(row)
            else:
                return {"error": "experiment not found"}
        else:
            return {"error": "experiment not found"}
    else:
        exp = _active_experiments[experiment_name]

    # Query all supervisor actions that have experiment metadata
    actions = (
        await session.execute(
            select(SupervisorAction)
            .where(SupervisorAction.action_params.op("->")("_experiments").isnot(None))
            .order_by(SupervisorAction.created_at.desc())
            .limit(5000)
        )
    ).scalars().all()

    variant_metrics: dict[str, dict[str, Any]] = {
        v.name: {
            "total": 0,
            "executed": 0,
            "approved": 0,
            "rejected": 0,
            "avg_confidence": 0.0,
            "confidence_sum": 0.0,
        }
        for v in exp.variants
    }

    for action in actions:
        params = action.action_params or {}
        experiments = params.get("_experiments", [])
        for e in experiments:
            if e.get("experiment") == experiment_name:
                vname = e.get("variant", "")
                if vname in variant_metrics:
                    m = variant_metrics[vname]
                    m["total"] += 1
                    if action.executed:
                        m["executed"] += 1
                    if action.approved_by:
                        m["approved"] += 1
                    if action.rejected_by:
                        m["rejected"] += 1
                    if action.confidence:
                        m["confidence_sum"] += action.confidence

    for vname, m in variant_metrics.items():
        if m["total"] > 0:
            m["avg_confidence"] = round(m["confidence_sum"] / m["total"], 3)
            m["approval_rate"] = round(m["approved"] / max(m["total"], 1), 3)
            m["rejection_rate"] = round(m["rejected"] / max(m["total"], 1), 3)
            m["execution_rate"] = round(m["executed"] / max(m["total"], 1), 3)
        del m["confidence_sum"]

    return {
        "experiment": experiment_name,
        "status": exp.status,
        "variants": variant_metrics,
    }
