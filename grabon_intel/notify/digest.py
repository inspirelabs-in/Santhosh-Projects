"""Daily / weekly digest. Pulls top brands + recent signals + pending
approvals, packs into one Notification, fans out via every available
notifier.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import text

from ..db import session as session_ctx
from ..logging import get_logger
from .base import Notification, get_notifiers

log = get_logger(__name__)


async def build_digest(period_hours: int = 24) -> Notification:
    since = dt.datetime.utcnow() - dt.timedelta(hours=period_hours)
    async with session_ctx() as s:
        signals = (
            await s.execute(
                text(
                    "SELECT type, COUNT(*) c FROM signals "
                    "WHERE ingested_at > :since GROUP BY type ORDER BY c DESC"
                ),
                {"since": since},
            )
        ).all()
        # New dossiers with their score.
        top = (
            await s.execute(
                text(
                    "SELECT d.brand_id, b.name, b.domain, "
                    "(d.data->'score'->>'total')::int AS total, "
                    "(d.data->'score'->>'tier') AS tier "
                    "FROM dossiers d JOIN brands b ON b.id = d.brand_id "
                    "WHERE d.generated_at > :since "
                    "ORDER BY total DESC NULLS LAST LIMIT 10"
                ),
                {"since": since},
            )
        ).all()
        pend_approvals = (
            (
                await s.execute(text("SELECT COUNT(*) FROM approvals WHERE status='pending'"))
            ).scalar()
            or 0
        )
        total_cost = (
            (
                await s.execute(
                    text(
                        "SELECT COALESCE(SUM(total_cost_cents),0) FROM agent_traces "
                        "WHERE created_at > :since"
                    ),
                    {"since": since},
                )
            ).scalar()
            or 0
        )

    summary_lines = [
        f"{len(top)} new dossiers, {pend_approvals} approvals pending, "
        f"${(int(total_cost))/100:.2f} spent in last {period_hours}h."
    ]
    if top:
        summary_lines.append("Top:")
        for r in top[:5]:
            summary_lines.append(f"  • {r[1]} ({r[2]}) — {r[3]} [{r[4] or '—'}]")

    facts: list[tuple[str, str]] = [
        (f"signals.{t}", str(c)) for t, c in signals[:8]
    ]

    actions: list[tuple[str, str]] = [("Open Workspace", "http://localhost:3000")]
    return Notification(
        title=f"Grabon Intel — {period_hours}h digest",
        summary="\n".join(summary_lines),
        facts=facts,
        actions=actions,
        severity="info",
        extra={"top": [list(r) for r in top]},
    )


async def send_digest(period_hours: int = 24) -> dict[str, Any]:
    note = await build_digest(period_hours)
    results: dict[str, bool] = {}
    for n in get_notifiers():
        results[n.name] = await n.send(note)
    return {"sent": results, "title": note.title, "summary_lines": note.summary.count("\n") + 1}
