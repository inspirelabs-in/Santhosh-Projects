"""Outbound event bus + CRM webhook dispatcher.

Pattern: producers insert into `events` table; a background activity
drains undelivered rows and posts to every active webhook matching
the event's topic. Payloads are HMAC-signed so the receiver can verify
authenticity without TLS-only trust.

Topics (string-typed for forward compat):
  brand.created       — new brand discovered/resolved
  dossier.completed   — new dossier version persisted
  score.tier_changed  — brand crossed score-tier boundary
  approval.created    — new HITL item
  approval.decided    — rep approved/rejected
  outreach.sent       — message went out
  reply.received      — prospect responded
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .db import session as session_ctx
from .logging import get_logger

log = get_logger(__name__)


async def emit(session: AsyncSession, *, topic: str, brand_id: int | None, payload: dict[str, Any]) -> int:
    row = (
        await session.execute(
            text(
                "INSERT INTO events (topic, brand_id, payload) "
                "VALUES (:t, :b, CAST(:p AS JSONB)) RETURNING id"
            ),
            {"t": topic, "b": brand_id, "p": json.dumps(payload, default=str)},
        )
    ).first()
    return int(row[0]) if row else 0


def _sign(secret: str, body: str) -> str:
    return hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()


async def drain(batch_size: int = 50, max_attempts: int = 5) -> dict[str, int]:
    """Dispatch undelivered events. Idempotent — receivers must dedupe on event_id."""
    delivered = 0
    failed = 0

    async with session_ctx() as s:
        ev_rows = (
            await s.execute(
                text(
                    "SELECT id, topic, brand_id, payload, delivery_attempts "
                    "FROM events WHERE delivered_at IS NULL "
                    "AND delivery_attempts < :max "
                    "ORDER BY created_at ASC LIMIT :n FOR UPDATE SKIP LOCKED"
                ),
                {"max": max_attempts, "n": batch_size},
            )
        ).all()
        if not ev_rows:
            return {"delivered": 0, "failed": 0}

        hooks = (
            await s.execute(
                text("SELECT id, name, url, topics, secret FROM crm_webhooks WHERE active")
            )
        ).all()

    if not hooks:
        # Mark events as delivered (no subscribers).
        async with session_ctx() as s:
            await s.execute(
                text("UPDATE events SET delivered_at = NOW() WHERE id = ANY(:ids)"),
                {"ids": [int(r[0]) for r in ev_rows]},
            )
        return {"delivered": len(ev_rows), "failed": 0}

    async with httpx.AsyncClient(timeout=20) as client:
        for ev_id, topic, brand_id, payload, attempts in ev_rows:
            event_body = {
                "event_id": int(ev_id),
                "topic": topic,
                "brand_id": brand_id,
                "payload": json.loads(payload) if isinstance(payload, str) else payload,
                "occurred_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            }
            raw = json.dumps(event_body, default=str)
            any_failed = False
            for _hid, _name, url, topics, secret in hooks:
                subs = topics if isinstance(topics, list) else (json.loads(topics) if topics else [])
                if topic not in subs and "*" not in subs:
                    continue
                sig = _sign(secret, raw)
                try:
                    r = await client.post(
                        url,
                        content=raw,
                        headers={
                            "Content-Type": "application/json",
                            "X-Grabon-Signature": f"sha256={sig}",
                            "X-Grabon-Event": topic,
                            "X-Grabon-Event-Id": str(ev_id),
                        },
                    )
                    if r.status_code >= 400:
                        any_failed = True
                        log.warning("event.delivery_4xx", status=r.status_code, url=url, event_id=int(ev_id))
                except Exception as exc:  # noqa: BLE001
                    any_failed = True
                    log.warning("event.delivery_error", url=url, exc=str(exc))

            async with session_ctx() as s:
                if any_failed:
                    await s.execute(
                        text(
                            "UPDATE events SET delivery_attempts = delivery_attempts + 1, "
                            "last_error = :err WHERE id = :id"
                        ),
                        {"err": "one or more webhooks failed", "id": int(ev_id)},
                    )
                    failed += 1
                else:
                    await s.execute(
                        text("UPDATE events SET delivered_at = NOW() WHERE id = :id"),
                        {"id": int(ev_id)},
                    )
                    delivered += 1

    return {"delivered": delivered, "failed": failed}


def verify_signature(secret: str, body: str, signature_header: str | None) -> bool:
    if not signature_header:
        return False
    sig = signature_header.removeprefix("sha256=")
    expected = _sign(secret, body)
    return hmac.compare_digest(expected, sig)
