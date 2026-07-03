"""Hardcoded third-party API base URLs / OAuth endpoints shared across the
channels, services, api, and workers packages.

Extracted as part of a behavior-preserving refactor: every value below is
IDENTICAL to the literal(s) it replaced. Call sites still assemble their own
full URL by appending path suffixes to these bases -- only the shared,
repeated prefix is centralised here.
"""

from __future__ import annotations

ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"
"""ElevenLabs ConvAI REST API base (voice provider, webhooks, watchdog, jobs,
integration test probe)."""

MS_GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
"""Microsoft Graph REST API base (mail send/fetch, Teams meetings,
scheduling free/busy)."""

MS_GRAPH_DEFAULT_SCOPE = "https://graph.microsoft.com/.default"
"""OAuth2 client-credentials scope requested for all Microsoft Graph app-only
tokens (mail, Teams meetings, scheduling, integration test probe)."""

MS_GRAPH_TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
"""Azure AD v2.0 OAuth2 token endpoint template; ``{tenant}`` is the Graph
tenant id (or ``"common"``)."""

GOOGLE_CALENDAR_OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]
"""Google OAuth2 scopes requested for Calendar/Google Meet integration
(calendar_sync, gmeet_meeting)."""
