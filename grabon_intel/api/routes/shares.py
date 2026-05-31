"""Reasoning-trace share links.

Issue a signed, opaque token bound to a single agent_trace row. Public
read endpoint validates the token, returns sanitised trace JSON (LLM
preview text + node names + cost; no raw prompts or API responses).

Signing: HMAC-SHA256 over `trace_id|expires_at` using a long-lived
server secret. Token is URL-safe base64 of `payload:signature`.

Revoke by flipping `revoked=true`.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from ...config import get_settings
from ...db import session as session_ctx

router = APIRouter(prefix="/shares", tags=["shares"])


def _server_secret() -> bytes:
    s = get_settings()
    # Derive a stable secret from API keys env. NOT for real prod auth — for
    # this MVP it's good enough; rotate by changing GRABON_API_KEYS.
    seed = "|".join(sorted(s.parsed_api_keys())) or "dev-secret"
    return hashlib.sha256(seed.encode("utf-8")).digest()


def _sign(trace_id: str, expires_at: str) -> str:
    return hmac.new(_server_secret(), f"{trace_id}|{expires_at}".encode("utf-8"), hashlib.sha256).hexdigest()


def make_token(trace_id: str, ttl_hours: int) -> tuple[str, dt.datetime]:
    expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=ttl_hours)
    iso = expires.isoformat()
    sig = _sign(trace_id, iso)
    raw = f"{trace_id}|{iso}|{sig}"
    token = base64.urlsafe_b64encode(raw.encode("utf-8")).rstrip(b"=").decode("ascii")
    return token, expires


def parse_token(token: str) -> tuple[str, dt.datetime] | None:
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        trace_id, iso, sig = raw.split("|", 2)
    except Exception:
        return None
    expected = _sign(trace_id, iso)
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        exp = dt.datetime.fromisoformat(iso)
    except ValueError:
        return None
    if exp < dt.datetime.now(dt.timezone.utc):
        return None
    return trace_id, exp


class ShareCreate(BaseModel):
    trace_id: str
    ttl_hours: int = 168  # 1 week default
    created_by: str | None = None


@router.post("")
async def create_share(body: ShareCreate) -> dict:
    token, expires = make_token(body.trace_id, body.ttl_hours)
    async with session_ctx() as s:
        # Sanity check: trace must exist.
        row = (
            await s.execute(
                text("SELECT brand_id FROM agent_traces WHERE id = :id"),
                {"id": body.trace_id},
            )
        ).first()
        if not row:
            raise HTTPException(404, "trace not found")
        brand_id = row[0]
        await s.execute(
            text(
                "INSERT INTO reasoning_shares (token, trace_id, brand_id, expires_at, created_by) "
                "VALUES (:tok, :tr, :b, :ex, :cb)"
            ),
            {"tok": token[:64], "tr": body.trace_id, "b": brand_id, "ex": expires, "cb": body.created_by},
        )
    return {"token": token, "expires_at": expires.isoformat()}


# Note: this endpoint is mounted WITHOUT the api-key dependency in main.py
# so it works as a public share URL. We rely on the signed token instead.
public_router = APIRouter(prefix="/public/shares", tags=["public-shares"])


@public_router.get("/{token}")
async def read_share(token: str) -> dict[str, Any]:
    parsed = parse_token(token)
    if not parsed:
        raise HTTPException(404, "invalid or expired share")
    trace_id, _ = parsed
    async with session_ctx() as s:
        row = (
            await s.execute(
                text(
                    "SELECT t.workflow_id, t.agent, t.steps, t.total_cost_cents, "
                    "t.duration_ms, t.status, t.brand_id, b.name, b.domain, "
                    "r.revoked "
                    "FROM agent_traces t "
                    "LEFT JOIN brands b ON b.id = t.brand_id "
                    "LEFT JOIN reasoning_shares r ON r.trace_id = t.id "
                    "WHERE t.id = :id"
                ),
                {"id": trace_id},
            )
        ).mappings().first()
    if not row or row["revoked"]:
        raise HTTPException(404, "trace not found or revoked")
    # Sanitise: drop preview text contents to avoid leaking PII; keep counts.
    steps = []
    for step in (row["steps"] or []):
        if not isinstance(step, dict):
            continue
        steps.append(
            {
                "node": step.get("node"),
                "model": step.get("model"),
                "in_tokens": step.get("in_tokens"),
                "out_tokens": step.get("out_tokens"),
                "cost_cents": step.get("cost_cents"),
                "tools_used": step.get("tools_used", []),
            }
        )
    return {
        "workflow_id": row["workflow_id"],
        "agent": row["agent"],
        "brand": {"id": row["brand_id"], "name": row["name"], "domain": row["domain"]},
        "steps": steps,
        "total_cost_cents": row["total_cost_cents"],
        "duration_ms": row["duration_ms"],
        "status": row["status"],
        "shared_via": "grabon-intel",
    }
