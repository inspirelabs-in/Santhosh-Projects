"""Microsoft Graph -- create + cancel Teams online meetings.

Uses application-permission flow with delegated mailbox ID
(``GRAPH_ORGANISER_USER_ID`` / falls back to first panel email's mailbox).
Required Graph permission: ``OnlineMeetings.ReadWrite.All``.

Usage::

    link, event_id = await create_online_meeting(
        organiser_email="careers@grabon.in",
        subject="GrabOn -- Technical interview · Aisha",
        start_utc=datetime(...),
        end_utc=datetime(...),
        attendee_emails=["panel@grabon.in", "aisha@example.com"],
    )
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from src.config import get_settings
from src.constants.external import MS_GRAPH_API_BASE, MS_GRAPH_DEFAULT_SCOPE, MS_GRAPH_TOKEN_URL_TEMPLATE

logger = logging.getLogger(__name__)


async def _token() -> str:
    settings = get_settings()
    if not (
        settings.graph_tenant_id
        and settings.graph_client_id
        and settings.graph_client_secret
    ):
        raise RuntimeError("Microsoft Graph credentials not configured")
    url = MS_GRAPH_TOKEN_URL_TEMPLATE.format(tenant=settings.graph_tenant_id)
    data = {
        "client_id": settings.graph_client_id,
        "client_secret": settings.graph_client_secret,
        "grant_type": "client_credentials",
        "scope": MS_GRAPH_DEFAULT_SCOPE,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, data=data)
        resp.raise_for_status()
        return resp.json()["access_token"]


def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


async def create_online_meeting(
    *,
    organiser_email: str,
    subject: str,
    start_utc: datetime,
    end_utc: datetime,
    attendee_emails: list[str],
) -> tuple[str, str]:
    """Create the Teams meeting.

    Returns ``(joinWebUrl, onlineMeeting_id)``. We deliberately use the
    ``onlineMeetings`` endpoint (not ``calendar/events``) because it gives
    us the join link without requiring an event invite. Calendar invites
    go out via plain email through the existing outbound channel so we
    don't depend on Graph mail-send permissions.
    """

    token = await _token()
    body = {
        "subject": subject,
        "startDateTime": _iso_z(start_utc) + "Z",
        "endDateTime": _iso_z(end_utc) + "Z",
        "participants": {
            "attendees": [
                {
                    "upn": email,
                    "role": "attendee",
                    "identity": {"@odata.type": "#microsoft.graph.identitySet"},
                }
                for email in attendee_emails
            ]
        },
        "lobbyBypassSettings": {
            "scope": "everyone",
            "isDialInBypassEnabled": True,
        },
        "allowMeetingChat": "enabled",
    }
    url = f"{MS_GRAPH_API_BASE}/users/{organiser_email}/onlineMeetings"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            json=body,
            headers={
                "authorization": f"Bearer {token}",
                "content-type": "application/json",
            },
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
    join_url = data.get("joinWebUrl") or data.get("joinUrl")
    meeting_id = data.get("id")
    if not join_url or not meeting_id:
        raise RuntimeError(f"Graph onlineMeeting response missing fields: {list(data)}")
    return str(join_url), str(meeting_id)


async def cancel_online_meeting(*, organiser_email: str, meeting_id: str) -> None:
    token = await _token()
    url = f"{MS_GRAPH_API_BASE}/users/{organiser_email}/onlineMeetings/{meeting_id}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.delete(
                url, headers={"authorization": f"Bearer {token}"}
            )
            if resp.status_code not in (200, 202, 204, 404):
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("graph onlineMeeting delete failed: %s", exc)
