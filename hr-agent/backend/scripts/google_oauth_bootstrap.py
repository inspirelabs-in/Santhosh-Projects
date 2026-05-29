"""One-time OAuth consent for Google Calendar / Meet integration.

Run once on a machine with a browser:

    cd backend
    python -m scripts.google_oauth_bootstrap

Opens browser, asks consent for the configured Gmail account, persists
``google_token.json`` (path = settings.google_oauth_token_path).

The refresh token inside that file is what services/gmeet_meeting.py uses
at runtime. While the OAuth consent screen is in "Testing" mode the
refresh token expires every 7 days -- re-run this script to renew, OR
submit the consent screen for verification (production mode).
"""

from __future__ import annotations

import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from src.config import get_settings

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]


def main() -> int:
    settings = get_settings()
    if not (settings.google_oauth_client_id and settings.google_oauth_client_secret):
        print("ERROR: GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET not set in .env")
        return 1

    client_config = {
        "installed": {
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "project_id": settings.google_oauth_project_id or "hr-agent",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    token_path = Path(settings.google_oauth_token_path)
    if not token_path.is_absolute():
        token_path = Path.cwd() / token_path
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")

    print(f"OK -- token written to {token_path}")
    print(f"Authorised account: {creds.id_token or '(check token file)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
