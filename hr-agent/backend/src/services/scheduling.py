"""Slot finder + Microsoft Graph free/busy integration.

Two layers:

  1. ``find_candidate_slot()`` -- given a ``RoundScheduling`` + a
     ``meeting_sessions`` history (so we don't double-book the same panel),
     returns the first ``(start, end)`` that fits. Pure-Python; no Graph
     required.

  2. If ``GRAPH_TENANT_ID`` + service-account creds are configured, we
     additionally intersect the panel's *real* free/busy schedule via
     Graph ``getSchedule``. When Graph fails or is not configured the
     finder degrades to local-only.

This module deliberately avoids Cal.com / Google Calendar -- ops standardised
on Microsoft for the panel.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db.base import MeetingSession
from src.models.scheduling import RoundScheduling

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProposedSlot:
    start: datetime  # tz-aware UTC
    end: datetime
    panel_emails: list[str]


# ---------------------------------------------------------------------------
# Local window-based finder
# ---------------------------------------------------------------------------


def _expand_windows(
    *,
    round_cfg: RoundScheduling,
    panel_tz: str,
    horizon_days: int,
    min_lead_hours: int,
) -> list[tuple[datetime, datetime]]:
    """Expand recurring windows into concrete (start, end) tuples in UTC."""
    if not round_cfg.windows:
        return []
    tz = ZoneInfo(panel_tz)
    now_local = datetime.now(tz)
    earliest = now_local + timedelta(hours=min_lead_hours)
    out: list[tuple[datetime, datetime]] = []
    for d in range(horizon_days + 7):
        day_local = (now_local + timedelta(days=d)).date()
        weekday = (day_local.weekday())  # 0=Mon
        for w in round_cfg.windows:
            if weekday not in w.days:
                continue
            try:
                sh, sm = (int(p) for p in w.start_hhmm.split(":"))
                eh, em = (int(p) for p in w.end_hhmm.split(":"))
            except ValueError:
                continue
            start = datetime.combine(day_local, time(sh, sm), tzinfo=tz)
            end = datetime.combine(day_local, time(eh, em), tzinfo=tz)
            if end <= earliest:
                continue
            if start < earliest:
                start = earliest
            if end <= start:
                continue
            out.append((start.astimezone(timezone.utc), end.astimezone(timezone.utc)))
    return out


async def _existing_meetings_for_panel(
    session: AsyncSession,
    panel_emails: list[str],
    horizon_end_utc: datetime,
) -> list[tuple[datetime, datetime]]:
    """Return blocking intervals from already-scheduled meeting_sessions.

    We don't have per-attendee tracking on meeting_sessions yet, so this is
    conservative: every scheduled meeting in the horizon counts as a block
    for any panel containing at least one of the listed emails. For the V2
    rollout this matches the "one panel per round per role" assumption.
    """

    if not panel_emails:
        return []
    stmt = (
        select(MeetingSession.scheduled_at, MeetingSession.duration_sec)
        .where(MeetingSession.scheduled_at.isnot(None))
        .where(MeetingSession.scheduled_at <= horizon_end_utc)
        .where(MeetingSession.bot_status.in_(["pending", "scheduled", "in_call", "done"]))
    )
    rows = (await session.execute(stmt)).all()
    blocks: list[tuple[datetime, datetime]] = []
    for start, dur in rows:
        if start is None:
            continue
        d = float(dur or 0) or 60 * 45  # default 45 min if duration unknown
        blocks.append((start.astimezone(timezone.utc), (start + timedelta(seconds=d)).astimezone(timezone.utc)))
    return blocks


# ---------------------------------------------------------------------------
# Microsoft Graph getSchedule
# ---------------------------------------------------------------------------


async def _graph_token() -> str | None:
    settings = get_settings()
    if not (settings.graph_tenant_id and settings.graph_client_id and settings.graph_client_secret):
        return None
    url = f"https://login.microsoftonline.com/{settings.graph_tenant_id}/oauth2/v2.0/token"
    data = {
        "client_id": settings.graph_client_id,
        "client_secret": settings.graph_client_secret,
        "grant_type": "client_credentials",
        "scope": "https://graph.microsoft.com/.default",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, data=data)
            resp.raise_for_status()
            return resp.json().get("access_token")
    except Exception as exc:  # noqa: BLE001
        logger.warning("graph token failed: %s", exc)
        return None


async def _graph_busy_intervals(
    *,
    panel_emails: list[str],
    start_utc: datetime,
    end_utc: datetime,
    organiser_email: str,
) -> list[tuple[datetime, datetime]] | None:
    """Use Graph getSchedule under the organiser mailbox. Returns None on failure."""

    token = await _graph_token()
    if token is None or not panel_emails:
        return None
    url = f"https://graph.microsoft.com/v1.0/users/{organiser_email}/calendar/getSchedule"
    payload = {
        "schedules": panel_emails,
        "startTime": {"dateTime": start_utc.isoformat(), "timeZone": "UTC"},
        "endTime": {"dateTime": end_utc.isoformat(), "timeZone": "UTC"},
        "availabilityViewInterval": 30,
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"authorization": f"Bearer {token}", "content-type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("graph getSchedule failed: %s", exc)
        return None

    blocks: list[tuple[datetime, datetime]] = []
    for entry in data.get("value", []):
        for item in entry.get("scheduleItems", []):
            try:
                s = datetime.fromisoformat(item["start"]["dateTime"]).replace(tzinfo=timezone.utc)
                e = datetime.fromisoformat(item["end"]["dateTime"]).replace(tzinfo=timezone.utc)
                blocks.append((s, e))
            except Exception:  # noqa: BLE001
                continue
    return blocks


# ---------------------------------------------------------------------------
# Public finder
# ---------------------------------------------------------------------------


def _slot_collides(
    start: datetime, end: datetime, blocks: list[tuple[datetime, datetime]]
) -> bool:
    return any(b_start < end and b_end > start for b_start, b_end in blocks)


async def find_candidate_slot(
    session: AsyncSession,
    *,
    round_cfg: RoundScheduling,
    panel_tz: str,
    horizon_days: int,
    min_lead_hours: int,
    avoid_starts: list[datetime] | None = None,
) -> ProposedSlot | None:
    """Return the first slot inside the panel's windows that nobody is busy in."""

    if not round_cfg.panel_emails:
        return None

    expanded = _expand_windows(
        round_cfg=round_cfg,
        panel_tz=panel_tz,
        horizon_days=horizon_days,
        min_lead_hours=min_lead_hours,
    )
    if not expanded:
        return None
    horizon_end = max(end for _, end in expanded)

    db_blocks = await _existing_meetings_for_panel(
        session, round_cfg.panel_emails, horizon_end
    )
    graph_blocks = await _graph_busy_intervals(
        panel_emails=round_cfg.panel_emails,
        start_utc=expanded[0][0],
        end_utc=horizon_end,
        organiser_email=round_cfg.panel_emails[0],
    )
    blocks = db_blocks + (graph_blocks or [])

    duration = timedelta(minutes=round_cfg.duration_minutes)
    avoid = set((d.astimezone(timezone.utc).replace(microsecond=0) for d in (avoid_starts or [])))

    for window_start, window_end in expanded:
        cursor = window_start
        # Round up to the next half-hour for cleanly-aligned slots.
        if cursor.minute % 30 != 0:
            extra = 30 - (cursor.minute % 30)
            cursor = cursor + timedelta(minutes=extra)
        while cursor + duration <= window_end:
            slot_start = cursor
            slot_end = cursor + duration
            if slot_start.replace(microsecond=0) not in avoid and not _slot_collides(
                slot_start, slot_end, blocks
            ):
                return ProposedSlot(
                    start=slot_start,
                    end=slot_end,
                    panel_emails=round_cfg.panel_emails,
                )
            cursor += timedelta(minutes=30)
    return None
