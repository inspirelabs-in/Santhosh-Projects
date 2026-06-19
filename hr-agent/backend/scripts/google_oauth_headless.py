"""Headless Google OAuth bootstrap — prints URL for manual visit.

Usage:
    docker exec -it hiring-agent-backend python -m scripts.google_oauth_headless        # prints URL, prompts for code
    docker exec hiring-agent-backend python -m scripts.google_oauth_headless --code CODE  # non-interactive
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
    flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

    print("=" * 60)
    print("VISIT THIS URL IN YOUR BROWSER (sign in with the organiser Gmail):")
    print("=" * 60)
    print(auth_url)
    print()

    code = None
    for i, arg in enumerate(sys.argv):
        if arg == "--code" and i + 1 < len(sys.argv):
            code = sys.argv[i + 1]

    if code:
        flow.fetch_token(code=code)
    else:
        code = input("Paste the authorization code here: ").strip()
        flow.fetch_token(code=code)

    creds = flow.credentials

    token_path = Path(settings.google_oauth_token_path)
    if not token_path.is_absolute():
        token_path = Path.cwd() / token_path
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")

    print(f"OK — token written to {token_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
