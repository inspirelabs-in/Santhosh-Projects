"""Perception layer: assemble full application context for supervisor reasoning.

Given a SupervisorEvent, builds a SupervisorContext that contains everything
the LLM needs to reason about what action to take — without re-querying mid-turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import (
    Application,
    AuditLog,
    Candidate,
    EvidenceRecord,
    PipelineAlert,
    Role,
    SupervisorAction,
    SupervisorEvent,
)
from src.db.repositories.evidence import (
    get_decisions_for_application,
    get_evidence_for_application,
)


@dataclass
class SupervisorContext:
    """Everything the supervisor needs to reason about one event."""

    event: dict[str, Any]
    application: dict[str, Any] | None = None
    candidate: dict[str, Any] | None = None
    role: dict[str, Any] | None = None
    current_stage: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    recent_audit: list[dict[str, Any]] = field(default_factory=list)
    active_alerts: list[dict[str, Any]] = field(default_factory=list)
    contradictions: list[dict[str, Any]] = field(default_factory=list)
    prior_supervisor_actions: list[dict[str, Any]] = field(default_factory=list)
    engagement: dict[str, Any] | None = None
    channel_strategy: dict[str, Any] | None = None
    sibling_candidates: list[dict[str, Any]] = field(default_factory=list)

    def to_prompt_context(self) -> str:
        """Render as structured text for LLM consumption."""
        lines = [
            f"## Event: {self.event.get('event_type', 'unknown')}",
            f"Payload: {self.event.get('payload', {})}",
            "",
        ]
        if self.application:
            lines.append(f"## Application")
            lines.append(f"- ID: {self.application.get('id')}")
            lines.append(f"- Stage: {self.current_stage}")
            lines.append(f"- Status: {self.application.get('status')}")
            lines.append(f"- Fit score: {self.application.get('fit_score')}")
            lines.append(f"- Screening score: {self.application.get('screening_score')}")
            lines.append("")

        if self.candidate:
            lines.append(f"## Candidate")
            lines.append(f"- Name: {self.candidate.get('name')}")
            lines.append(f"- Email: {self.candidate.get('email')}")
            lines.append("")

        if self.role:
            lines.append(f"## Role: {self.role.get('title')}")
            lines.append(f"- CTC band: {self.role.get('ctc_min_lpa')}–{self.role.get('ctc_max_lpa')} LPA")
            lines.append(f"- Max notice: {self.role.get('max_notice_days')}d")
            lines.append("")

        if self.evidence:
            lines.append(f"## Evidence ({len(self.evidence)} records)")
            for e in self.evidence[-15:]:
                conf = f" (conf={e['confidence']:.2f})" if e.get("confidence") else ""
                lines.append(
                    f"- [{e['source_stage']}] {e['fact_key']} = {e['fact_value']}{conf}"
                )
            lines.append("")

        if self.contradictions:
            lines.append(f"## Contradictions ({len(self.contradictions)})")
            for c in self.contradictions:
                details = c.get("details", {})
                lines.append(
                    f"- {details.get('fact_key')}: "
                    f"{details.get('old_source_stage')}={details.get('old_value')} "
                    f"vs {details.get('new_source_stage')}={details.get('new_value')}"
                )
            lines.append("")

        if self.active_alerts:
            lines.append(f"## Active alerts ({len(self.active_alerts)})")
            for a in self.active_alerts[-10:]:
                lines.append(
                    f"- [{a.get('alert_type')}] {(a.get('details') or {}).get('message', '')}"
                )
            lines.append("")

        if self.channel_strategy:
            cs = self.channel_strategy
            lines.append(f"## Channel strategy")
            lines.append(f"- Primary: {cs.get('primary_channel')} ({cs.get('primary_reason')})")
            if cs.get("fallbacks"):
                lines.append(f"- Fallbacks: {', '.join(cs['fallbacks'])}")
            lines.append(f"- Available: {', '.join(cs.get('available', []))}")
            lines.append("")

        if self.engagement:
            eng = self.engagement
            lines.append(f"## Candidate engagement")
            lines.append(f"- Overall: {eng.get('overall', 'N/A')} (risk: {eng.get('risk', 'N/A')})")
            lines.append(f"- Response speed: {eng.get('response_speed', 'N/A')}")
            lines.append(f"- Completion rate: {eng.get('completion_rate', 'N/A')}")
            lines.append(f"- Scheduling flexibility: {eng.get('scheduling_flexibility', 'N/A')}")
            if eng.get("signals"):
                lines.append(f"- Signals: {', '.join(eng['signals'])}")
            lines.append("")

        if self.prior_supervisor_actions:
            lines.append(f"## Prior supervisor actions ({len(self.prior_supervisor_actions)})")
            for a in self.prior_supervisor_actions:
                status = "executed" if a.get("executed") else "proposed"
                if a.get("approved_by"):
                    status = "approved"
                elif a.get("rejected_by"):
                    status = "rejected"
                lines.append(
                    f"- [{status}] {a.get('action_type')}: {(a.get('reasoning') or '')[:100]} "
                    f"(conf={a.get('confidence', 0):.2f}, {a.get('created_at')})"
                )
            lines.append("")

        if self.sibling_candidates:
            lines.append(f"## Other candidates for this role ({len(self.sibling_candidates)})")
            for s in self.sibling_candidates[:8]:
                lines.append(
                    f"- {s.get('name', '?')} | stage={s.get('stage')} | "
                    f"fit={s.get('fit_score', 'N/A')} | screening={s.get('screening_score', 'N/A')}"
                )
            lines.append("")

        if self.recent_audit:
            lines.append(f"## Recent audit ({len(self.recent_audit)} entries)")
            for a in self.recent_audit[-10:]:
                lines.append(f"- {a.get('action')} by {a.get('actor')} at {a.get('created_at')}")
            lines.append("")

        return "\n".join(lines)


async def build_context(
    session: AsyncSession,
    event: SupervisorEvent,
) -> SupervisorContext:
    """Assemble full context for a supervisor event."""
    ctx = SupervisorContext(
        event={
            "id": str(event.id),
            "event_type": event.event_type,
            "payload": event.payload or {},
            "application_id": str(event.application_id) if event.application_id else None,
            "candidate_id": str(event.candidate_id) if event.candidate_id else None,
            "created_at": str(event.created_at),
        }
    )

    if not event.application_id:
        return ctx

    app = await session.get(Application, event.application_id)
    if app is None:
        return ctx

    ctx.application = {
        "id": str(app.id),
        "status": app.status,
        "fit_score": app.fit_score,
        "fit_tier": app.fit_tier,
        "screening_score": app.screening_score,
        "current_stage": app.current_stage,
    }
    ctx.current_stage = app.current_stage

    candidate = await session.get(Candidate, app.candidate_id)
    if candidate:
        ctx.candidate = {
            "id": str(candidate.id),
            "name": candidate.name,
            "email": candidate.email,
            "phone": candidate.phone,
        }

    if app.role_id:
        role = await session.get(Role, app.role_id)
        if role:
            ctx.role = {
                "id": str(role.id),
                "title": role.title,
                "ctc_min_lpa": role.ctc_min_lpa,
                "ctc_max_lpa": role.ctc_max_lpa,
                "max_notice_days": role.max_notice_days,
                "location": role.location,
                "remote_policy": role.remote_policy,
            }

    # Evidence + decisions
    evidence_rows = await get_evidence_for_application(session, event.application_id)
    ctx.evidence = [
        {
            "id": str(e.id),
            "fact_key": e.fact_key,
            "fact_value": e.fact_value,
            "source_stage": e.source_stage,
            "confidence": e.confidence,
            "created_at": str(e.created_at),
        }
        for e in evidence_rows
    ]

    decision_rows = await get_decisions_for_application(session, event.application_id)
    ctx.decisions = [
        {
            "id": str(d.id),
            "decision_type": d.decision_type,
            "outcome": d.outcome,
            "outcome_value": d.outcome_value,
            "created_at": str(d.created_at),
        }
        for d in decision_rows
    ]

    # Active alerts
    alert_rows = (
        await session.execute(
            select(PipelineAlert)
            .where(PipelineAlert.application_id == event.application_id)
            .where(PipelineAlert.resolved_at.is_(None))
            .order_by(PipelineAlert.created_at.desc())
            .limit(20)
        )
    ).scalars().all()
    ctx.active_alerts = [
        {
            "id": str(a.id),
            "alert_type": a.alert_type,
            "stage": a.stage,
            "details": a.details,
            "created_at": str(a.created_at),
        }
        for a in alert_rows
    ]
    ctx.contradictions = [
        a for a in ctx.active_alerts if a["alert_type"] == "fact_contradiction"
    ]

    # Channel selection strategy
    try:
        from src.services.channel_selector import select_channel
        urgency = (event.payload or {}).get("urgency", "normal")
        ch = await select_channel(session, app.candidate_id, urgency=urgency)
        ctx.channel_strategy = {
            "primary_channel": ch.primary.channel,
            "primary_reason": ch.primary.reason,
            "fallbacks": [f.channel for f in ch.fallbacks],
            "available": ch.available_channels,
        }
    except Exception:
        logger.warning("channel selection failed", exc_info=True)

    # Engagement scoring
    try:
        from src.services.engagement_scorer import compute_engagement
        eng = await compute_engagement(session, event.application_id)
        ctx.engagement = {
            "overall": eng.overall,
            "response_speed": eng.response_speed,
            "completion_rate": eng.completion_rate,
            "scheduling_flexibility": eng.scheduling_flexibility,
            "signals": eng.signals,
            "risk": eng.risk,
        }
    except Exception:
        logger.warning("engagement scoring failed", exc_info=True)

    # Prior supervisor actions for this application (conversation memory)
    prior_action_rows = (
        await session.execute(
            select(SupervisorAction)
            .where(SupervisorAction.application_id == event.application_id)
            .order_by(SupervisorAction.created_at.desc())
            .limit(10)
        )
    ).scalars().all()
    ctx.prior_supervisor_actions = [
        {
            "action_type": a.action_type,
            "reasoning": a.reasoning,
            "confidence": a.confidence,
            "executed": a.executed,
            "approved_by": a.approved_by,
            "rejected_by": a.rejected_by,
            "execution_result": a.execution_result,
            "created_at": str(a.created_at),
        }
        for a in prior_action_rows
    ]

    # Sibling candidates (multi-candidate awareness)
    if app.role_id:
        try:
            sibling_rows = (
                await session.execute(
                    select(Application, Candidate)
                    .join(Candidate, Application.candidate_id == Candidate.id)
                    .where(Application.role_id == app.role_id)
                    .where(Application.id != app.id)
                    .where(Application.status == "active")
                    .order_by(Application.fit_score.desc().nullslast())
                    .limit(8)
                )
            ).all()
            ctx.sibling_candidates = [
                {
                    "name": c.name,
                    "stage": a.current_stage,
                    "fit_score": a.fit_score,
                    "fit_tier": a.fit_tier,
                    "screening_score": a.screening_score,
                }
                for a, c in sibling_rows
            ]
        except Exception:
            pass

    # Recent audit
    audit_rows = (
        await session.execute(
            select(AuditLog)
            .where(AuditLog.application_id == event.application_id)
            .order_by(AuditLog.created_at.desc())
            .limit(15)
        )
    ).scalars().all()
    ctx.recent_audit = [
        {
            "action": a.action,
            "actor": a.actor,
            "details": a.details,
            "created_at": str(a.created_at),
        }
        for a in audit_rows
    ]

    return ctx
