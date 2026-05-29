"""Funnel + override + channel metrics, computed on demand from Postgres.

We don't need a dedicated metrics store for the pilot -- Retool queries
these endpoints and renders the numbers directly. If volume grows, swap
this module for precomputed rollups without changing the API.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import Application, AuditLog, Candidate


async def funnel_counts(
    session: AsyncSession, *, since_days: int = 30, role_id: UUID | None = None
) -> dict[str, int]:
    """Count applications at each stage.

    Stages are derived from `applications.status` + terminal audit actions,
    so re-categorising (e.g. moving a candidate back from `cold`) is handled
    by the latest status.
    """
    cutoff = datetime.now(tz=UTC) - timedelta(days=since_days)
    base = select(Application.status, func.count()).where(Application.created_at >= cutoff)
    if role_id is not None:
        base = base.where(Application.role_id == role_id)
    rows = (await session.execute(base.group_by(Application.status))).all()
    return {status: count for status, count in rows}


async def override_rate(
    session: AsyncSession, *, since_days: int = 30
) -> dict[str, Any]:
    """Percentage of agent decisions HR overrode.

    Denominator: number of applications reaching `scored` or later.
    Numerator: audit entries with action='hr_override'.
    """
    cutoff = datetime.now(tz=UTC) - timedelta(days=since_days)
    denom = await session.scalar(
        select(func.count()).select_from(Application).where(
            and_(
                Application.created_at >= cutoff,
                Application.fit_score.isnot(None),
            )
        )
    )
    numer = await session.scalar(
        select(func.count()).select_from(AuditLog).where(
            and_(
                AuditLog.created_at >= cutoff,
                AuditLog.action == "hr_override",
            )
        )
    )
    denom = int(denom or 0)
    numer = int(numer or 0)
    rate = (numer / denom) if denom > 0 else 0.0
    return {"overrides": numer, "decisions": denom, "rate": round(rate, 3), "since_days": since_days}


async def channel_delivery_stats(
    session: AsyncSession, *, since_days: int = 30
) -> dict[str, Any]:
    """Count of outbound sends by channel + success rate, from audit log."""
    cutoff = datetime.now(tz=UTC) - timedelta(days=since_days)
    rows = (
        await session.execute(
            select(AuditLog.action, AuditLog.details).where(
                and_(
                    AuditLog.created_at >= cutoff,
                    AuditLog.action.in_(
                        [
                            "acknowledgement_sent",
                            "acknowledgement_failed",
                            "screening_invite_sent",
                            "screening_reminder_sent",
                            "screening_final_reminder_sent",
                            "rejection_sent",
                            "rejection_send_failed",
                        ]
                    ),
                )
            )
        )
    ).all()

    per_channel = {"email": {"ok": 0, "fail": 0}, "whatsapp": {"ok": 0, "fail": 0}, "sms": {"ok": 0, "fail": 0}}
    for action, details in rows:
        details = details or {}
        failed = action.endswith("_failed") or any(
            f.startswith(ch + ":") for ch in per_channel for f in details.get("failures", [])
        )
        if "email" in details:
            per_channel["email"]["ok" if details["email"] else "fail"] += 1
        if "whatsapp" in details:
            per_channel["whatsapp"]["ok" if details["whatsapp"] else "fail"] += 1
        if "sms" in details:
            per_channel["sms"]["ok" if details["sms"] else "fail"] += 1
        if action == "acknowledgement_sent":
            per_channel["email"]["ok"] += 1
        if action == "acknowledgement_failed":
            per_channel["email"]["fail"] += 1
        if action == "rejection_sent":
            per_channel["email"]["ok"] += 1
        if action == "rejection_send_failed":
            per_channel["email"]["fail"] += 1

    # Compute rates.
    summary = {}
    for ch, counts in per_channel.items():
        total = counts["ok"] + counts["fail"]
        summary[ch] = {
            **counts,
            "total": total,
            "success_rate": round(counts["ok"] / total, 3) if total else 0.0,
        }
    return summary


async def pipeline_snapshot(session: AsyncSession) -> dict[str, Any]:
    """Lightweight current-state snapshot for the dashboard home."""
    total_candidates = int(await session.scalar(select(func.count()).select_from(Candidate)) or 0)
    by_status = dict(
        (
            (r[0], int(r[1]))
            for r in (
                await session.execute(
                    select(Candidate.status, func.count()).group_by(Candidate.status)
                )
            ).all()
        )
    )
    apps_by_tier = dict(
        (
            (r[0], int(r[1]))
            for r in (
                await session.execute(
                    select(Application.fit_tier, func.count())
                    .where(Application.fit_tier.isnot(None))
                    .group_by(Application.fit_tier)
                )
            ).all()
        )
    )
    return {
        "total_candidates": total_candidates,
        "candidates_by_status": by_status,
        "applications_by_tier": apps_by_tier,
    }
