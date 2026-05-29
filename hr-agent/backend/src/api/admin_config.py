"""Admin config API — runtime-editable settings.

All routes require the `admin` role. Reads return secrets masked. Writes are
validated against the schema registry, encrypted on the way to disk for any
field flagged `is_secret`, and audited in `config_audit`.

Cross-process invalidation: every successful PATCH publishes on Redis
`config:invalidate` so other API/worker processes drop their in-memory
cache and re-read the DB on next access.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.api.auth import require_admin, require_viewer
from src.config_schema import (
    FIELDS,
    GROUPS,
    get_field,
    schema_payload,
)
from src.services import config_store
from src.services.integration_tests import run_probe

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/config", tags=["admin-config"])


def _actor_id(request: Request) -> str:
    raw = request.headers.get("x-dashboard-key", "")
    return raw[:8] + "…" if len(raw) > 8 else (raw or "unknown")


@router.get("/schema")
async def get_schema(_: Annotated[str, Depends(require_viewer)]) -> dict[str, Any]:
    """Returns groups + field metadata for the form generator. No values."""
    return schema_payload()


@router.get("/values")
async def get_all_values(
    _: Annotated[str, Depends(require_admin)],
) -> dict[str, dict[str, Any]]:
    """Current effective values (env defaults + DB overlay), secrets masked."""
    out: dict[str, dict[str, Any]] = {g: {} for g in GROUPS}
    from src.config import get_settings

    s = get_settings()
    for f in FIELDS:
        cur = getattr(s, f.settings_attr, None)
        out.setdefault(f.group, {})[f.key] = {
            "value": "***set***" if (f.is_secret and cur) else cur,
            "configured": cur not in (None, ""),
            "is_secret": f.is_secret,
        }
    return out


RESTART_CHANNEL = "config:restart-workers"


@router.get("/setup-status")
async def setup_status(_: Annotated[str, Depends(require_viewer)]) -> dict[str, Any]:
    from src.config import get_settings

    s = get_settings()
    has_llm_key = bool(s.openai_api_key or s.anthropic_api_key or s.groq_api_key)
    has_email = bool(s.resend_api_key or s.smtp_host)
    missing: list[str] = []
    if not has_llm_key:
        missing.append("LLM Engine")
    if not has_email:
        missing.append("Email")
    return {
        "completed": not missing,
        "missing_groups": missing,
        "has_llm_key": has_llm_key,
        "has_email": has_email,
    }


@router.post("/restart-workers")
async def restart_workers(
    request: Request,
    role: Annotated[str, Depends(require_admin)],
) -> dict[str, Any]:
    from src.db.base import ConfigAuditRow
    from src.db.connection import get_redis, session_scope

    try:
        await get_redis().publish(RESTART_CHANNEL, "1")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {exc}") from exc

    async with session_scope() as session:
        session.add(
            ConfigAuditRow(
                key="_restart-workers",
                old_value=None,
                new_value={"v": "signaled"},
                actor=_actor_id(request),
                actor_role=role,
                action="update",
                ip=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
            )
        )
    return {"ok": True}


@router.get("/{group}")
async def get_group(
    group: str,
    _: Annotated[str, Depends(require_admin)],
) -> dict[str, Any]:
    if group not in GROUPS:
        raise HTTPException(status_code=404, detail=f"Unknown group: {group}")
    fields = await config_store.list_for_group(group)
    return {"group": group, "fields": fields}


@router.delete("/{key}")
async def reset_to_default(
    key: str,
    request: Request,
    role: Annotated[str, Depends(require_admin)],
) -> dict[str, Any]:
    if get_field(key) is None:
        raise HTTPException(status_code=404, detail=f"Unknown key: {key}")

    from src.db.base import ConfigAuditRow, ConfigSetting
    from src.db.connection import get_redis, session_scope

    async with session_scope() as session:
        existing = await session.get(ConfigSetting, key)
        if not existing:
            return {"deleted": False, "key": key}
        old_value = existing.value
        await session.delete(existing)
        session.add(
            ConfigAuditRow(
                key=key,
                old_value=old_value,
                new_value=None,
                actor=_actor_id(request),
                actor_role=role,
                action="delete",
                ip=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
            )
        )

    try:
        await get_redis().publish("config:invalidate", "1")
    except Exception:
        logger.exception("invalidate publish failed")
    config_store.invalidate()
    return {"deleted": True, "key": key}


@router.patch("")
async def update_values(
    request: Request,
    payload: dict[str, Any],
    role: Annotated[str, Depends(require_admin)],
) -> dict[str, Any]:
    """Body: {"updates": {KEY: value, ...}}. Validates each key against schema."""
    updates = payload.get("updates") or {}
    if not isinstance(updates, dict) or not updates:
        raise HTTPException(status_code=400, detail="Body must include non-empty 'updates' dict")

    # Reject unknown keys early so we don't half-apply.
    unknown = [k for k in updates if get_field(k) is None]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown keys: {unknown}")

    # Don't accept the masked sentinel as a write — that means the admin
    # didn't change the secret.
    cleaned: dict[str, Any] = {}
    for k, v in updates.items():
        if v == "***set***":
            continue
        cleaned[k] = v
    if not cleaned:
        return {"updated": {}, "skipped": list(updates.keys())}

    try:
        stored = await config_store.set_values(
            cleaned,
            actor=_actor_id(request),
            actor_role=role,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"updated": stored}


@router.post("/test/{integration}")
async def test_integration(
    integration: str,
    request: Request,
    payload: dict[str, Any],
    _: Annotated[str, Depends(require_admin)],
) -> dict[str, Any]:
    """Body: {"config": {KEY: value, ...}} — values about to be saved.

    The probe runs against the CANDIDATE values (not persisted) so HR can
    verify a key works before clicking Save.
    """
    candidate = payload.get("config") or {}
    if not isinstance(candidate, dict):
        raise HTTPException(status_code=400, detail="config must be a dict")
    # Inherit current effective config for missing keys so partial probes
    # (e.g. Resend test that only changes the from-name) still work.
    from src.config import get_settings

    s = get_settings()
    merged: dict[str, Any] = {}
    for f in FIELDS:
        cur = getattr(s, f.settings_attr, None)
        if cur is not None:
            merged[f.key] = cur
    merged.update({k: v for k, v in candidate.items() if v != "***set***"})

    result = await run_probe(integration, merged)

    # Audit the test attempt (never the candidate values).
    try:
        from src.db.base import ConfigAuditRow
        from src.db.connection import session_scope

        async with session_scope() as session:
            session.add(
                ConfigAuditRow(
                    key=f"_test:{integration}",
                    old_value=None,
                    new_value={"v": "ok" if result.ok else "fail"},
                    actor=_actor_id(request),
                    actor_role="admin",
                    action="test",
                    ip=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                )
            )
    except Exception:
        logger.exception("audit write failed for test/%s", integration)

    return result.to_dict()


@router.get("/audit/log")
async def get_audit_log(
    _: Annotated[str, Depends(require_admin)],
    key: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be 1..500")
    rows = await config_store.get_audit(key, limit=limit)
    return {"rows": rows}
