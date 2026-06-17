"""MS Graph calendar helpers for supervisor tools.

Wraps the existing scheduling._graph_busy_intervals to expose a simpler
interface for the supervisor's check_panel_availability tool.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


async def get_free_busy(
    *,
    attendees: list[str],
    preferred_dates: list[str] | None = None,
    horizon_days: int = 7,
    slot_minutes: int = 60,
) -> list[dict[str, Any]]:
    """Check calendar availability and return available slots.

    Uses MS Graph getSchedule under the organiser mailbox. Falls back to
    returning a "manual_scheduling_needed" result if Graph is not configured.
    """
    from src.config import get_settings
    from src.services.scheduling import _graph_busy_intervals, _graph_token

    settings = get_settings()

    if not attendees:
        return [{"error": "no_attendees"}]

    organiser = settings.graph_organiser_email or (attendees[0] if attendees else None)
    if not organiser:
        return [{"status": "manual_scheduling_needed", "reason": "no_organiser_configured"}]

    # Determine time window
    now = datetime.now(UTC)
    if preferred_dates:
        try:
            start = datetime.fromisoformat(preferred_dates[0]).replace(tzinfo=timezone.utc)
            end = start + timedelta(days=max(len(preferred_dates), 3))
        except (ValueError, IndexError):
            start = now
            end = now + timedelta(days=horizon_days)
    else:
        start = now
        end = now + timedelta(days=horizon_days)

    # Get busy blocks from Graph
    busy_blocks = await _graph_busy_intervals(
        panel_emails=attendees,
        start_utc=start,
        end_utc=end,
        organiser_email=organiser,
    )

    if busy_blocks is None:
        return [{"status": "graph_unavailable", "reason": "MS Graph not configured or token failed"}]

    # Find free slots (business hours: 10am-6pm UTC+5:30 ≈ 4:30am-12:30pm UTC)
    available_slots: list[dict[str, Any]] = []
    current = start.replace(hour=5, minute=0, second=0, microsecond=0)
    if current < now:
        current += timedelta(days=1)

    while current < end and len(available_slots) < 10:
        if current.weekday() < 5:  # weekdays only
            for hour in [5, 6, 7, 8, 9, 10, 11]:  # ~10:30am-5:30pm IST
                slot_start = current.replace(hour=hour, minute=0)
                slot_end = slot_start + timedelta(minutes=slot_minutes)
                if slot_start < now:
                    continue
                if not any(bs < slot_end and be > slot_start for bs, be in busy_blocks):
                    available_slots.append({
                        "start": slot_start.isoformat(),
                        "end": slot_end.isoformat(),
                        "attendees_free": True,
                    })
        current += timedelta(days=1)

    return available_slots
