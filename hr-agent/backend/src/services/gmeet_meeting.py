"""Google Calendar -- create + cancel events with auto-generated Meet links.

Uses OAuth user-flow (refresh token persisted to disk). Run
``python -m scripts.google_oauth_bootstrap`` once to mint the token.

Mirrors the public surface of ``services/teams_meeting.py`` so the
``services/online_meeting.py`` facade can swap providers transparently.

Required scopes: ``calendar.events`` (and ``calendar`` for free/busy).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.config import get_settings
from src.constants.external import GOOGLE_CALENDAR_OAUTH_SCOPES

logger = logging.getLogger(__name__)

SCOPES = GOOGLE_CALENDAR_OAUTH_SCOPES


def _resolve_token_path() -> Path:
    settings = get_settings()
    raw = settings.google_oauth_token_path
    p = Path(raw)
    if not p.is_absolute():
        # Resolve relative to backend/ working dir (where alembic.ini sits).
        p = Path(os.getcwd()) / raw
    return p


def _load_credentials() -> Credentials:
    settings = get_settings()
    if not (settings.google_oauth_client_id and settings.google_oauth_client_secret):
        raise RuntimeError("Google OAuth client credentials not configured")
    token_path = _resolve_token_path()
    if not token_path.exists():
        raise RuntimeError(
            f"Google OAuth token not found at {token_path}. "
            "Run `python -m scripts.google_oauth_bootstrap` once to authorise."
        )
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_event_body(
    *,
    subject: str,
    start_utc: datetime,
    end_utc: datetime,
    attendee_emails: list[str],
) -> dict[str, Any]:
    return {
        "summary": subject,
        "start": {"dateTime": _iso_z(start_utc), "timeZone": "UTC"},
        "end": {"dateTime": _iso_z(end_utc), "timeZone": "UTC"},
        "attendees": [{"email": e} for e in attendee_emails if e],
        "conferenceData": {
            "createRequest": {
                "requestId": str(uuid.uuid4()),
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
        "reminders": {"useDefault": True},
    }


def _create_event_sync(
    *,
    organiser_email: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    creds = _load_credentials()
    svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return (
        svc.events()
        .insert(
            calendarId=organiser_email or "primary",
            body=body,
            conferenceDataVersion=1,
            sendUpdates="all",
        )
        .execute()
    )


def _delete_event_sync(*, organiser_email: str, event_id: str) -> None:
    creds = _load_credentials()
    svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
    try:
        svc.events().delete(
            calendarId=organiser_email or "primary",
            eventId=event_id,
            sendUpdates="all",
        ).execute()
    except HttpError as exc:
        if exc.resp.status in (404, 410):
            return
        raise


def _patch_event_sync(
    *,
    organiser_email: str,
    event_id: str,
    start_utc: datetime,
    end_utc: datetime,
    attendee_emails: list[str] | None = None,
) -> dict[str, Any]:
    creds = _load_credentials()
    svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
    body: dict[str, Any] = {
        "start": {"dateTime": _iso_z(start_utc), "timeZone": "UTC"},
        "end": {"dateTime": _iso_z(end_utc), "timeZone": "UTC"},
    }
    if attendee_emails is not None:
        body["attendees"] = [{"email": e} for e in attendee_emails if e]
    return (
        svc.events()
        .patch(
            calendarId=organiser_email or "primary",
            eventId=event_id,
            body=body,
            sendUpdates="all",
        )
        .execute()
    )


async def create_online_meeting(
    *,
    organiser_email: str,
    subject: str,
    start_utc: datetime,
    end_utc: datetime,
    attendee_emails: list[str],
) -> tuple[str, str]:
    """Create a Google Calendar event with an auto-attached Meet link.

    Returns ``(meet_join_url, calendar_event_id)``.
    Google sends invite emails to attendees automatically (sendUpdates="all").
    """
    body = _build_event_body(
        subject=subject,
        start_utc=start_utc,
        end_utc=end_utc,
        attendee_emails=attendee_emails,
    )
    try:
        event = await asyncio.to_thread(
            _create_event_sync, organiser_email=organiser_email, body=body
        )
    except HttpError as exc:
        raise RuntimeError(
            f"Google Calendar insert failed: {exc.status_code} {exc.reason}"
        ) from exc

    meet_url = event.get("hangoutLink")
    if not meet_url:
        cd = event.get("conferenceData") or {}
        for ep in cd.get("entryPoints") or []:
            if ep.get("entryPointType") == "video" and ep.get("uri"):
                meet_url = ep["uri"]
                break
    event_id = event.get("id")
    if not meet_url or not event_id:
        raise RuntimeError(
            f"Google Calendar response missing fields: {json.dumps(event)[:300]}"
        )
    return str(meet_url), str(event_id)


async def cancel_online_meeting(*, organiser_email: str, meeting_id: str) -> None:
    try:
        await asyncio.to_thread(
            _delete_event_sync,
            organiser_email=organiser_email,
            event_id=meeting_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("google calendar event delete failed: %s", exc)


async def reschedule_online_meeting(
    *,
    organiser_email: str,
    meeting_id: str,
    start_utc: datetime,
    end_utc: datetime,
    attendee_emails: list[str] | None = None,
) -> str:
    """Move an existing calendar event to a new time in place (PATCH).

    Keeps the same event id and Meet link. Google emails all attendees the
    updated invite automatically (sendUpdates="all"). Returns the Meet join
    URL (unchanged from the original event).
    """
    try:
        event = await asyncio.to_thread(
            _patch_event_sync,
            organiser_email=organiser_email,
            event_id=meeting_id,
            start_utc=start_utc,
            end_utc=end_utc,
            attendee_emails=attendee_emails,
        )
    except HttpError as exc:
        raise RuntimeError(
            f"Google Calendar patch failed: {exc.status_code} {exc.reason}"
        ) from exc

    meet_url = event.get("hangoutLink")
    if not meet_url:
        cd = event.get("conferenceData") or {}
        for ep in cd.get("entryPoints") or []:
            if ep.get("entryPointType") == "video" and ep.get("uri"):
                meet_url = ep["uri"]
                break
    return str(meet_url or "")
