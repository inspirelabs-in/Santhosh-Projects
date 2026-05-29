"""Runtime configuration overlay.

Resolution order: env defaults (`Settings(...)`) -> DB rows in
`config_settings` -> in-process cache. Secrets are decrypted on read.

Updates land via `set_value()` which writes the row, audits the change, and
publishes `config:invalidate` over Redis so every API/worker process drops its
cache. A 30s safety TTL ensures stale values can never linger.

This module is import-safe: it never touches the DB at import time. The
overlay loads lazily on the first `get_settings()` call after initialization.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any

from sqlalchemy import select

from src.config import Settings, _build_settings  # type: ignore[attr-defined]
from src.config_schema import (
    FIELDS,
    get_field,
    get_field_by_attr,
    secret_keys,
)
from src.services.config_crypto import decrypt, encrypt, is_encrypted

logger = logging.getLogger(__name__)

INVALIDATE_CHANNEL = "config:invalidate"
CACHE_TTL_SECONDS = 30.0


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()
_cached: Settings | None = None
_cached_at: float = 0.0
_dirty = False


def _now() -> float:
    return time.monotonic()


def _is_stale() -> bool:
    if _cached is None:
        return True
    if _dirty:
        return True
    return (_now() - _cached_at) > CACHE_TTL_SECONDS


def invalidate() -> None:
    """Mark cache stale. Next get_settings() rebuilds."""
    global _dirty
    _dirty = True


def _coerce_to_attr(field_default: Any, raw: Any) -> Any:
    """Coerce JSONB-shaped value back to the python type the Settings field expects."""
    if raw is None:
        return None
    if isinstance(field_default, bool):
        return bool(raw)
    if isinstance(field_default, int) and not isinstance(field_default, bool):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return field_default
    if isinstance(field_default, float):
        try:
            return float(raw)
        except (TypeError, ValueError):
            return field_default
    return raw


def _apply_overlay_sync(base: Settings) -> Settings:
    """Read DB rows synchronously and overlay onto `base`.

    Uses a short-lived sync engine (psycopg) so config can load even before
    the async engine is initialized (e.g. during import-time init paths).
    """
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        engine = create_engine(base.database_url_sync, pool_pre_ping=True, future=True)
        with Session(engine) as session:
            from src.db.base import ConfigSetting

            rows = session.execute(select(ConfigSetting)).scalars().all()
            for row in rows:
                field = get_field(row.key)
                if field is None:
                    continue
                value = row.value
                if field.is_secret and isinstance(value, str) and is_encrypted(value):
                    try:
                        value = decrypt(value)
                    except Exception:
                        logger.exception("config secret decrypt failed key=%s", row.key)
                        continue
                # JSON wrapper: we store {"v": <value>} so JSONB always parses.
                if isinstance(value, dict) and set(value.keys()) == {"v"}:
                    value = value["v"]
                current_default = getattr(base, field.settings_attr, None)
                coerced = _coerce_to_attr(current_default, value)
                try:
                    object.__setattr__(base, field.settings_attr, coerced)
                except Exception:
                    logger.exception("overlay assign failed attr=%s", field.settings_attr)
        engine.dispose()
    except Exception:
        # First-boot before migration: table doesn't exist. That's fine —
        # env defaults are still in effect.
        logger.warning("config overlay unavailable; using env defaults", exc_info=True)
    return base


def _build() -> Settings:
    base = _build_settings()
    return _apply_overlay_sync(base)


def get_settings() -> Settings:
    global _cached, _cached_at, _dirty
    with _cache_lock:
        if _is_stale():
            _cached = _build()
            _cached_at = _now()
            _dirty = False
        return _cached  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


def _wrap_for_jsonb(value: Any) -> Any:
    """Postgres JSONB requires JSON-typed values; wrap scalars in {"v": ...}."""
    if isinstance(value, (dict, list)):
        return value
    return {"v": value}


def _unwrap(value: Any) -> Any:
    if isinstance(value, dict) and set(value.keys()) == {"v"}:
        return value["v"]
    return value


async def set_values(
    updates: dict[str, Any],
    *,
    actor: str,
    actor_role: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Persist multiple updates in one transaction. Audits each diff. Returns
    the dict of stored (post-coercion, masked-for-secret) values."""

    from src.db.base import ConfigAuditRow, ConfigSetting
    from src.db.connection import session_scope, get_redis

    stored: dict[str, Any] = {}
    async with session_scope() as session:
        for key, raw_value in updates.items():
            field = get_field(key)
            if field is None:
                raise ValueError(f"unknown config key: {key}")

            # validate + coerce
            new_value = _validate_value(field, raw_value)

            existing = await session.get(ConfigSetting, key)
            old_db_value = existing.value if existing else None

            persisted_value: Any
            if field.is_secret and isinstance(new_value, str) and new_value:
                persisted_value = encrypt(new_value)
            else:
                persisted_value = new_value
            wrapped = _wrap_for_jsonb(persisted_value)

            if existing:
                existing.value = wrapped
                existing.is_secret = field.is_secret
                existing.updated_by = actor
                existing.version = (existing.version or 1) + 1
            else:
                session.add(
                    ConfigSetting(
                        key=key,
                        value=wrapped,
                        is_secret=field.is_secret,
                        updated_by=actor,
                        version=1,
                    )
                )

            # audit (mask secrets)
            audit_old = _mask_for_audit(field, _unwrap(old_db_value)) if old_db_value is not None else None
            audit_new = _mask_for_audit(field, new_value)
            session.add(
                ConfigAuditRow(
                    key=key,
                    old_value=_wrap_for_jsonb(audit_old) if audit_old is not None else None,
                    new_value=_wrap_for_jsonb(audit_new),
                    actor=actor,
                    actor_role=actor_role,
                    action="update" if existing else "create",
                    ip=ip,
                    user_agent=user_agent,
                )
            )

            stored[key] = "***set***" if field.is_secret else new_value

    # Publish invalidation. Best-effort: don't fail the write if pub/sub is down.
    try:
        redis = get_redis()
        await redis.publish(INVALIDATE_CHANNEL, "1")
    except Exception:
        logger.exception("redis publish %s failed", INVALIDATE_CHANNEL)
    invalidate()
    return stored


def _validate_value(field: Any, raw: Any) -> Any:
    """Type/range-check raw incoming value. Raises ValueError on bad input."""
    t = field.type
    if raw is None or raw == "":
        if t == "bool":
            return False
        return None
    if t == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.lower() in ("1", "true", "yes", "on")
        return bool(raw)
    if t == "int":
        v = int(raw)
        if field.min is not None and v < field.min:
            raise ValueError(f"{field.key} below min {field.min}")
        if field.max is not None and v > field.max:
            raise ValueError(f"{field.key} above max {field.max}")
        return v
    if t == "float":
        v = float(raw)
        if field.min is not None and v < field.min:
            raise ValueError(f"{field.key} below min {field.min}")
        if field.max is not None and v > field.max:
            raise ValueError(f"{field.key} above max {field.max}")
        return v
    if t == "select":
        if field.options and raw not in field.options:
            raise ValueError(f"{field.key} must be one of {field.options}")
        return raw
    if t == "json":
        if isinstance(raw, str):
            try:
                json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{field.key} invalid JSON: {exc}") from exc
            return raw
        return json.dumps(raw)
    # string / secret / url / email / textarea
    return str(raw)


def _mask_for_audit(field: Any, value: Any) -> Any:
    if field.is_secret and value:
        # Audit a fingerprint, not the value.
        import hashlib

        if isinstance(value, str):
            return f"sha256:{hashlib.sha256(value.encode()).hexdigest()[:16]}"
        return "***set***"
    return value


# ---------------------------------------------------------------------------
# Read helpers for the API layer
# ---------------------------------------------------------------------------


async def list_for_group(group: str) -> list[dict[str, Any]]:
    """Return current values for a group, masking secrets."""
    s = get_settings()
    out: list[dict[str, Any]] = []
    for f in FIELDS:
        if f.group != group:
            continue
        cur = getattr(s, f.settings_attr, None)
        out.append(
            {
                "key": f.key,
                "value": "***set***" if (f.is_secret and cur) else cur,
                "configured": cur not in (None, ""),
            }
        )
    return out


async def get_audit(key: str | None, limit: int = 50) -> list[dict[str, Any]]:
    from src.db.base import ConfigAuditRow
    from src.db.connection import session_scope

    async with session_scope() as session:
        stmt = select(ConfigAuditRow).order_by(ConfigAuditRow.created_at.desc()).limit(limit)
        if key:
            stmt = stmt.where(ConfigAuditRow.key == key)
        rows = (await session.execute(stmt)).scalars().all()
        return [
            {
                "id": r.id,
                "key": r.key,
                "old_value": _unwrap(r.old_value),
                "new_value": _unwrap(r.new_value),
                "actor": r.actor,
                "actor_role": r.actor_role,
                "action": r.action,
                "ip": r.ip,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


# ---------------------------------------------------------------------------
# Pub/sub listener (started from FastAPI lifespan)
# ---------------------------------------------------------------------------


async def run_invalidation_listener() -> None:
    """Long-running task that drops local cache when any node publishes."""
    from src.db.connection import get_redis

    while True:
        try:
            redis = get_redis()
            pubsub = redis.pubsub()
            await pubsub.subscribe(INVALIDATE_CHANNEL)
            logger.info("config invalidation listener subscribed")
            async for message in pubsub.listen():
                if message.get("type") == "message":
                    invalidate()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("config invalidation listener crashed; restarting in 5s")
            await asyncio.sleep(5)
