"""Evidence and decision record repositories.

Write helpers for the provenance layer. Every pipeline fact gets an
EvidenceRecord; every agent decision gets a DecisionRecord citing which
evidence and policy rules it used.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import DecisionRecord, EvidenceRecord


async def record_evidence(
    session: AsyncSession,
    *,
    application_id: UUID,
    candidate_id: UUID,
    fact_key: str,
    fact_value: Any,
    source_stage: str,
    source_type: str,
    extraction_method: str,
    evidence_text: str | None = None,
    source_ref: str | None = None,
    char_offset_start: int | None = None,
    char_offset_end: int | None = None,
    confidence: float | None = None,
    langfuse_trace_id: str | None = None,
    model_version: str | None = None,
) -> EvidenceRecord:
    row = EvidenceRecord(
        application_id=application_id,
        candidate_id=candidate_id,
        fact_key=fact_key,
        fact_value=fact_value,
        source_stage=source_stage,
        source_type=source_type,
        extraction_method=extraction_method,
        evidence_text=evidence_text,
        source_ref=source_ref,
        char_offset_start=char_offset_start,
        char_offset_end=char_offset_end,
        confidence=confidence,
        langfuse_trace_id=langfuse_trace_id,
        model_version=model_version,
    )
    session.add(row)
    await session.flush()
    return row


async def record_evidence_batch(
    session: AsyncSession,
    *,
    records: list[dict[str, Any]],
) -> list[EvidenceRecord]:
    rows = [EvidenceRecord(**r) for r in records]
    session.add_all(rows)
    await session.flush()
    return rows


async def record_decision(
    session: AsyncSession,
    *,
    application_id: UUID,
    candidate_id: UUID,
    decision_type: str,
    outcome: str,
    outcome_value: dict | None = None,
    evidence_ids: list[UUID] | None = None,
    policy_rule_ids: list[UUID] | None = None,
    audit_log_id: int | None = None,
    langfuse_trace_id: str | None = None,
    model_version: str | None = None,
    prompt_version: str | None = None,
) -> DecisionRecord:
    row = DecisionRecord(
        application_id=application_id,
        candidate_id=candidate_id,
        decision_type=decision_type,
        outcome=outcome,
        outcome_value=outcome_value or {},
        evidence_ids=[str(eid) for eid in (evidence_ids or [])],
        policy_rule_ids=[str(pid) for pid in (policy_rule_ids or [])],
        audit_log_id=audit_log_id,
        langfuse_trace_id=langfuse_trace_id,
        model_version=model_version,
        prompt_version=prompt_version,
    )
    session.add(row)
    await session.flush()
    return row


async def record_evidence_batch_verified(
    session: AsyncSession,
    *,
    records: list[dict[str, Any]],
    application_id: UUID,
    candidate_name: str | None = None,
) -> tuple[list[EvidenceRecord], list[dict]]:
    """Write evidence batch and run contradiction checks.

    Returns (saved_evidence, contradictions). Contradictions list is empty
    when fact verification is disabled or no conflicts found.
    """
    from src.config import get_settings

    rows = await record_evidence_batch(session, records=records)

    settings = get_settings()
    contradictions: list[dict] = []
    if settings.enable_fact_verification and rows:
        from src.services.contradiction_detector import (
            check_contradictions,
            create_contradiction_alerts,
        )
        contradictions = await check_contradictions(
            session, application_id=application_id, new_evidence=rows
        )
        if contradictions:
            await create_contradiction_alerts(
                session,
                application_id=application_id,
                contradictions=contradictions,
                candidate_name=candidate_name,
            )

    return rows, contradictions


async def get_evidence_for_application(
    session: AsyncSession,
    application_id: UUID,
    fact_key: str | None = None,
) -> list[EvidenceRecord]:
    stmt = (
        select(EvidenceRecord)
        .where(EvidenceRecord.application_id == application_id)
        .where(EvidenceRecord.superseded_by_id.is_(None))
    )
    if fact_key:
        stmt = stmt.where(EvidenceRecord.fact_key == fact_key)
    stmt = stmt.order_by(EvidenceRecord.created_at)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_decisions_for_application(
    session: AsyncSession,
    application_id: UUID,
    decision_type: str | None = None,
) -> list[DecisionRecord]:
    stmt = (
        select(DecisionRecord)
        .where(DecisionRecord.application_id == application_id)
    )
    if decision_type:
        stmt = stmt.where(DecisionRecord.decision_type == decision_type)
    stmt = stmt.order_by(DecisionRecord.created_at)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def supersede_evidence(
    session: AsyncSession,
    old_id: UUID,
    new_id: UUID,
) -> None:
    old_row = await session.get(EvidenceRecord, old_id)
    if old_row:
        old_row.superseded_by_id = new_id
        await session.flush()
