#!/usr/bin/env bash
set -euo pipefail

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  echo "[entrypoint] running alembic upgrade head"
  alembic upgrade head
fi

# Write Google OAuth token from env var if provided
if [ -n "${GOOGLE_OAUTH_TOKEN_JSON:-}" ]; then
  mkdir -p secrets
  echo "$GOOGLE_OAUTH_TOKEN_JSON" > secrets/google_token.json
  echo "[entrypoint] wrote secrets/google_token.json from env var"
fi

exec "$@"
