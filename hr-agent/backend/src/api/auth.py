"""Dashboard auth: shared API key in the ``X-Dashboard-Key`` header.

The key map lives in the ``DASHBOARD_KEYS`` env var. Two formats accepted:

  1. JSON (preferred):
       DASHBOARD_KEYS={"admin_xxx":"admin","rec_yyy":"recruiter"}
  2. Comma list (env-file friendly fallback):
       DASHBOARD_KEYS=admin_xxx:admin,rec_yyy:recruiter

We deliberately read ``os.environ`` directly here -- never via pydantic
Settings -- so this module can never crash the app on import even if other
config validators fail. ``.env`` is loaded explicitly via ``python-dotenv``
on first call so local ``uvicorn`` runs without docker-compose's
``env_file`` directive still pick up the values.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Header, HTTPException, status

logger = logging.getLogger(__name__)

Role = Literal["admin", "recruiter", "viewer"]


_DOTENV_LOADED = False


def _ensure_dotenv() -> None:
    """Load backend/.env into os.environ once per process."""

    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    try:
        from dotenv import load_dotenv  # transitive dep of pydantic-settings
    except ImportError:
        return
    # Walk up looking for backend/.env. Works whether the process runs from
    # /app/backend (docker), backend/ (uvicorn dev), or repo root.
    here = Path(__file__).resolve()
    for parent in [here.parents[2], here.parents[3], Path.cwd()]:
        env_path = parent / ".env"
        if env_path.is_file():
            load_dotenv(env_path, override=False)
            return


def _raw_keys_string() -> str:
    _ensure_dotenv()
    return os.environ.get("DASHBOARD_KEYS", "") or ""


_cached_raw: str | None = None
_cached_keys: dict[str, Role] = {}


def _parse(raw: str) -> dict[str, Role]:
    raw = (raw or "").strip()
    if raw and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
        raw = raw[1:-1]  # strip wrapping quotes some shells add
    if not raw or raw == "{}":
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return {
                str(k): v
                for k, v in data.items()
                if v in ("admin", "recruiter", "viewer")
            }
    except json.JSONDecodeError:
        pass
    out: dict[str, Role] = {}
    for chunk in raw.split(","):
        if ":" not in chunk:
            continue
        key, _, role = chunk.partition(":")
        key = key.strip()
        role = role.strip()
        if key and role in ("admin", "recruiter", "viewer"):
            out[key] = role  # type: ignore[assignment]
    return out


def _load_keys() -> dict[str, Role]:
    global _cached_raw, _cached_keys
    raw = _raw_keys_string()
    if raw != _cached_raw:
        _cached_raw = raw
        _cached_keys = _parse(raw)
        logger.info("dashboard auth: %d key(s) loaded", len(_cached_keys))
    return _cached_keys


def _resolve(key: str | None) -> Role:
    if not key:
        logger.warning("auth: missing X-Dashboard-Key header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Dashboard-Key header",
        )
    keys = _load_keys()
    role = keys.get(key.strip())
    if role is None:
        logger.warning("auth: invalid dashboard key (prefix=%s)", key[:8] if key else "")
        if not keys:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No dashboard keys configured (DASHBOARD_KEYS is empty in the backend env).",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid dashboard key",
        )
    return role


def require_role(*allowed: Role):
    def _dep(
        x_dashboard_key: Annotated[str | None, Header(alias="X-Dashboard-Key")] = None,
    ) -> Role:
        role = _resolve(x_dashboard_key)
        if role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of: {', '.join(allowed)}",
            )
        return role

    return _dep


require_viewer = require_role("admin", "recruiter", "viewer")
require_recruiter = require_role("admin", "recruiter")
require_admin = require_role("admin")
require_ceo = require_admin


# ---------------------------------------------------------------------------
# Diagnostic helper -- mounted from main.py at /diagnostics/auth
# ---------------------------------------------------------------------------


def auth_diagnostic() -> dict[str, object]:
    """Return non-sensitive info to debug auth misconfiguration."""

    raw = _raw_keys_string()
    keys = _load_keys()
    return {
        "loaded_keys": len(keys),
        "raw_present": bool(raw),
        "raw_length": len(raw),
        "first_chars": (raw[:6] + "...") if raw else "",
        "roles_loaded": sorted({r for r in keys.values()}),
    }
