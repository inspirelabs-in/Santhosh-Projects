"""Google Calendar availability + event management.

Uses a service account with domain-wide delegation that impersonates a
scheduling bot user (`GOOGLE_CALENDAR_IMPERSONATE_USER`). Service account
JSON is loaded from `GOOGLE_CALENDAR_SERVICE_ACCOUNT_JSON` which may be
either a path or an inline JSON string.

All times are timezone-aware and returned in IST (Asia/Kolkata). Business
hours are 10:00–18:00 IST; slots are always `slot_duration_minutes` long
and aligned to the top of the hour by default.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from src.config import get_settings
from src.constants.external import GOOGLE_CALENDAR_OAUTH_SCOPES
from src.models.scheduling import TimeSlot

logger = logging.getLogger(__name__)
_settings = get_settings()

_IST = timezone(timedelta(hours=5, minutes=30))
_BUSINESS_START = time(10, 0)
_BUSINESS_END = time(18, 0)
_SCOPES = GOOGLE_CALENDAR_OAUTH_SCOPES


@dataclass
class CalendarEvent:
    event_id: str
    meeting_link: str | None
    html_link: str | None
    start: datetime
    end: datetime


def _load_service_account_info() -> dict[str, Any] | None:
    raw = _settings.google_calendar_service_account_json
    if not raw:
        return None
    # Inline JSON?
    raw = raw.strip()
    if raw.startswith("{"):
        return json.loads(raw)
    # Otherwise treat as file path.
    p = Path(raw)
    if not p.is_file():
        logger.warning("GOOGLE_CALENDAR_SERVICE_ACCOUNT_JSON path not found: %s", raw)
        return None
    return json.loads(p.read_text())


def _build_service():
    """Build an authorised Google Calendar v3 service client, or return None."""
    info = _load_service_account_info()
    if info is None:
        return None
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        logger.warning("google-api-python-client not installed; calendar disabled")
        return None

    creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    if _settings.google_calendar_impersonate_user:
        creds = creds.with_subject(_settings.google_calendar_impersonate_user)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _in_business_hours(start: datetime, end: datetime) -> bool:
    local_start = start.astimezone(_IST)
    local_end = end.astimezone(_IST)
    if local_start.weekday() >= 5:  # Saturday(5), Sunday(6)
        return False
    return (
        local_start.time() >= _BUSINESS_START
        and local_end.time() <= _BUSINESS_END
        and local_start.date() == local_end.date()
    )


def _generate_candidate_slots(
    *,
    date_range_days: int,
    slot_duration_minutes: int,
) -> list[tuple[datetime, datetime]]:
    """Every top-of-the-hour slot inside business hours for the next N days."""
    now_ist = datetime.now(tz=_IST)
    # Never propose < 4h from now (candidate reaction time).
    horizon_start = now_ist + timedelta(hours=4)
    horizon_start = horizon_start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    horizon_end = now_ist + timedelta(days=date_range_days)
    step = timedelta(minutes=slot_duration_minutes)
    slots: list[tuple[datetime, datetime]] = []
    cursor = horizon_start
    while cursor + step <= horizon_end:
        if _in_business_hours(cursor, cursor + step):
            slots.append((cursor, cursor + step))
        cursor += step
    return slots


def _overlaps_any(slot: tuple[datetime, datetime], busy: list[dict[str, str]]) -> bool:
    s_start, s_end = slot
    for period in busy:
        b_start = datetime.fromisoformat(period["start"].replace("Z", "+00:00"))
        b_end = datetime.fromisoformat(period["end"].replace("Z", "+00:00"))
        if s_start < b_end and b_start < s_end:
            return True
    return False


async def find_free_slots(
    *,
    interviewer_emails: list[str],
    date_range_days: int = 7,
    slot_duration_minutes: int = 60,
    max_slots: int = 6,
) -> list[TimeSlot]:
    """Intersect freeBusy across all interviewers and return top-N free slots.

    Fallback (v1): when Google Calendar is not configured OR the role has no
    interviewer panel, we still return `max_slots` static business-hours slots
    so the candidate receives a proposal. The booking flow records the
    interview row without creating a real calendar event. HR can invite panel
    members manually until Calendar is wired up.
    """
    service = _build_service()
    if service is None or not interviewer_emails:
        logger.info(
            "Calendar unavailable or no interviewer panel; falling back to static slot proposal."
        )
        candidate_slots = _generate_candidate_slots(
            date_range_days=date_range_days,
            slot_duration_minutes=slot_duration_minutes,
        )
        fallback: list[TimeSlot] = []
        for start, end in candidate_slots:
            fallback.append(
                TimeSlot(
                    start=start,
                    end=end,
                    interviewer_emails=list(interviewer_emails),
                    slot_token=secrets.token_urlsafe(12),
                )
            )
            if len(fallback) >= max_slots:
                break
        return fallback

    time_min = datetime.now(tz=timezone.utc)
    time_max = time_min + timedelta(days=date_range_days)

    def _freebusy() -> dict[str, Any]:
        body = {
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "items": [{"id": email} for email in interviewer_emails],
        }
        return service.freebusy().query(body=body).execute()

    result = await asyncio.to_thread(_freebusy)
    calendars = result.get("calendars", {})

    busy_union: list[dict[str, str]] = []
    for email in interviewer_emails:
        busy_union.extend(calendars.get(email, {}).get("busy", []))

    candidate_slots = _generate_candidate_slots(
        date_range_days=date_range_days,
        slot_duration_minutes=slot_duration_minutes,
    )

    free: list[TimeSlot] = []
    for start, end in candidate_slots:
        if _overlaps_any((start, end), busy_union):
            continue
        free.append(
            TimeSlot(
                start=start,
                end=end,
                interviewer_emails=interviewer_emails,
                slot_token=secrets.token_urlsafe(12),
            )
        )
        if len(free) >= max_slots:
            break
    return free
