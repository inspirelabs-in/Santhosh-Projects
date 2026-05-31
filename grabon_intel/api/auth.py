"""API key auth.

Simple header check. Acceptable for an internal tool guarded by a
network perimeter. JWT/OIDC slots in later via `verify_token` — same
dependency signature, drop-in replacement.

Header: `X-API-Key: <key>`. Multiple keys allowed via comma-separated env.
Constant-time compare prevents timing side channels.
"""
from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from ..config import get_settings


async def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> str:
    allowed = get_settings().parsed_api_keys()
    if not allowed:
        # Failing closed in prod, but allow dev mode if explicitly empty.
        # Set GRABON_API_KEYS="" to disable; otherwise we require a key.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="server has no API keys configured",
        )
    if x_api_key is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing X-API-Key")
    for k in allowed:
        if hmac.compare_digest(k, x_api_key):
            return x_api_key
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid X-API-Key")
