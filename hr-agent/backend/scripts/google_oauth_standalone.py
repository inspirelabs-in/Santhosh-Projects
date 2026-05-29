"""Standalone Google OAuth bootstrap.

Does NOT import src.config -- avoids pulling backend deps. Reads creds
straight from backend/secrets/client_secret_*.json. Run once on host:

    cd backend
    python -m pip install google-auth-oauthlib google-api-python-client
    python -m scripts.google_oauth_standalone
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]

SECRETS_DIR = Path(__file__).resolve().parent.parent / "secrets"
TOKEN_PATH = SECRETS_DIR / "google_token.json"


def main() -> int:
    matches = glob.glob(str(SECRETS_DIR / "client_secret_*.json"))
    if not matches:
        print(f"ERROR: no client_secret_*.json in {SECRETS_DIR}")
        return 1
    client_file = matches[0]
    print(f"Using client config: {client_file}")

    raw = json.loads(Path(client_file).read_text(encoding="utf-8"))
    block = raw.get("installed") or raw.get("web")
    if not block:
        print("ERROR: client config missing 'installed' or 'web' block")
        return 1

    client_config = {
        "installed": {
            "client_id": block["client_id"],
            "client_secret": block["client_secret"],
            "project_id": block.get("project_id", "hr-agent"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    print(f"OK -- token written to {TOKEN_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
