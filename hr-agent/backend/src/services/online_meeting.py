"""Provider-agnostic online meeting facade.

Dispatches to Microsoft Graph (Teams) or Google Calendar (Meet) based on
``settings.online_meeting_provider``. Call sites import from here so we
can swap providers without touching scheduling logic.
"""

from __future__ import annotations

from datetime import datetime

from src.config import get_settings


async def create_online_meeting(
    *,
    organiser_email: str,
    subject: str,
    start_utc: datetime,
    end_utc: datetime,
    attendee_emails: list[str],
) -> tuple[str, str]:
    """Returns ``(join_url, provider_meeting_id)``."""
    settings = get_settings()
    if settings.online_meeting_provider == "gmeet":
        from src.services.gmeet_meeting import create_online_meeting as _create
    else:
        from src.services.teams_meeting import create_online_meeting as _create
    return await _create(
        organiser_email=organiser_email,
        subject=subject,
        start_utc=start_utc,
        end_utc=end_utc,
        attendee_emails=attendee_emails,
    )


async def cancel_online_meeting(*, organiser_email: str, meeting_id: str) -> None:
    settings = get_settings()
    if settings.online_meeting_provider == "gmeet":
        from src.services.gmeet_meeting import cancel_online_meeting as _cancel
    else:
        from src.services.teams_meeting import cancel_online_meeting as _cancel
    await _cancel(organiser_email=organiser_email, meeting_id=meeting_id)


async def reschedule_online_meeting(
    *,
    organiser_email: str,
    meeting_id: str,
    subject: str,
    start_utc: datetime,
    end_utc: datetime,
    attendee_emails: list[str],
) -> tuple[str, str]:
    """Move an existing meeting to a new time.

    Returns ``(join_url, provider_meeting_id)``.

    * gmeet: PATCH the event in place — same event id and same Meet link;
      Google emails all attendees the updated invite automatically.
    * teams (graph): no clean in-place update of time/attendees, so we
      cancel the old meeting and create a fresh one (new id + new link).
    """
    settings = get_settings()
    if settings.online_meeting_provider == "gmeet":
        from src.services.gmeet_meeting import (
            reschedule_online_meeting as _reschedule,
        )

        join_url = await _reschedule(
            organiser_email=organiser_email,
            meeting_id=meeting_id,
            start_utc=start_utc,
            end_utc=end_utc,
            attendee_emails=attendee_emails,
        )
        return join_url, meeting_id

    # Teams / Graph: delete + recreate.
    await cancel_online_meeting(organiser_email=organiser_email, meeting_id=meeting_id)
    return await create_online_meeting(
        organiser_email=organiser_email,
        subject=subject,
        start_utc=start_utc,
        end_utc=end_utc,
        attendee_emails=attendee_emails,
    )


def get_organiser_email() -> str | None:
    """Return the organiser email for the active provider."""
    settings = get_settings()
    if settings.online_meeting_provider == "gmeet":
        return settings.google_oauth_organiser_email or settings.read_ai_organiser_email
    return settings.graph_organiser_email
