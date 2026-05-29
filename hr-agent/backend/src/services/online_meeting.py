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


def get_organiser_email() -> str | None:
    """Return the organiser email for the active provider."""
    settings = get_settings()
    if settings.online_meeting_provider == "gmeet":
        return settings.google_oauth_organiser_email or settings.read_ai_organiser_email
    return settings.graph_organiser_email
