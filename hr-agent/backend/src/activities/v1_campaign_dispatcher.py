"""Tick-based campaign dispatcher for bulk voice calls.

Runs as a cron job every 15 seconds. Each tick scans ALL dispatching
campaigns, dispatching up to (max_concurrent - in_flight) calls each.
Auto-pauses on high failure rates; auto-completes when all calls terminal.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from src.activities.v1_dispatch_voice_call import dispatch_voice_call
from src.db.connection import session_scope
from src.db.repositories.voice_campaign import (
    complete_campaign,
    count_in_flight,
    get_campaign,
    get_campaign_progress,
    get_dispatching_campaigns,
    get_undispatched_targets,
    pause_campaign,
    update_campaign_counters,
)
from src.models.v1 import CallKind

logger = logging.getLogger(__name__)

_FAILURE_RATE_THRESHOLD = 0.2


async def campaign_dispatch_tick_all() -> dict:
    """Scan all dispatching campaigns and dispatch one tick for each."""
    async with session_scope() as session:
        campaign_ids = await get_dispatching_campaigns(session)

    if not campaign_ids:
        return {"status": "idle", "campaigns": 0}

    results = {}
    for cid in campaign_ids:
        results[str(cid)] = await _tick_one(cid)
    return {"status": "processed", "campaigns": len(campaign_ids), "results": results}


async def _tick_one(campaign_id: UUID) -> dict:
    """Process one dispatch tick for a single campaign."""

    async with session_scope() as session:
        campaign = await get_campaign(session, campaign_id)
        if campaign is None:
            return {"status": "not_found"}

        if campaign.status != "dispatching":
            return {"status": campaign.status}

        max_concurrent = campaign.max_concurrent
        call_kind_str = campaign.call_kind
        target_app_ids = campaign.target_application_ids or []

        progress = await get_campaign_progress(session, campaign_id)
        total_terminal = sum(
            progress.get(s, 0)
            for s in ("completed", "failed", "no_answer", "declined")
        )
        total_failed = sum(
            progress.get(s, 0) for s in ("failed", "no_answer")
        )
        if total_terminal >= 5 and total_failed / total_terminal > _FAILURE_RATE_THRESHOLD:
            logger.warning(
                "campaign %s auto-paused: %.0f%% failure rate (%d/%d)",
                campaign_id,
                total_failed / total_terminal * 100,
                total_failed,
                total_terminal,
            )
            await pause_campaign(session, campaign_id)
            return {"status": "auto_paused", "failed": total_failed, "total": total_terminal}

        in_flight = await count_in_flight(session, campaign_id)
        slots = max_concurrent - in_flight
        if slots <= 0:
            return {"status": "at_capacity", "in_flight": in_flight}

        undispatched = await get_undispatched_targets(
            session, campaign_id, target_app_ids, limit=slots
        )
        if not undispatched:
            pending_count = progress.get("pending", 0)
            dialing_count = progress.get("dialing", 0)
            in_progress_count = progress.get("in_progress", 0)
            callback_count = progress.get("callback_requested", 0)

            if pending_count == 0 and dialing_count == 0 and in_progress_count == 0 and callback_count == 0:
                await update_campaign_counters(session, campaign_id)
                await complete_campaign(session, campaign_id)
                return {"status": "completed"}

            return {"status": "waiting", "in_flight": in_flight}

    call_kind = CallKind(call_kind_str)
    dispatched = 0
    failed = 0

    for app_id in undispatched:
        try:
            await dispatch_voice_call(
                application_id=app_id,
                call_kind=call_kind,
                attempt_no=1,
                scheduled_at=datetime.now(UTC),
                campaign_id=campaign_id,
            )
            dispatched += 1
        except Exception as exc:
            logger.warning(
                "campaign %s: dispatch failed for app %s: %s",
                campaign_id, app_id, exc,
            )
            failed += 1

    async with session_scope() as session:
        await update_campaign_counters(session, campaign_id)

    return {
        "status": "dispatching",
        "dispatched": dispatched,
        "failed": failed,
        "in_flight": in_flight + dispatched,
    }
