"""Manage outbound CRM webhooks."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from ...db import session as session_ctx

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookCreate(BaseModel):
    name: str
    url: str
    topics: list[str]


@router.get("")
async def list_webhooks() -> dict:
    async with session_ctx() as s:
        rows = (
            await s.execute(
                text("SELECT id, name, url, topics, active, created_at FROM crm_webhooks ORDER BY id DESC")
            )
        ).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.post("")
async def create_webhook(body: WebhookCreate) -> dict:
    if not body.topics:
        raise HTTPException(422, "topics required")
    secret = secrets.token_urlsafe(32)
    async with session_ctx() as s:
        row = (
            await s.execute(
                text(
                    "INSERT INTO crm_webhooks (name, url, topics, secret) "
                    "VALUES (:n, :u, CAST(:t AS JSONB), :s) RETURNING id"
                ),
                {"n": body.name, "u": body.url, "t": _json(body.topics), "s": secret},
            )
        ).first()
    return {"id": int(row[0]), "secret": secret, "note": "store secret now; not retrievable later"}


@router.delete("/{wid}")
async def delete_webhook(wid: int) -> dict:
    async with session_ctx() as s:
        row = (await s.execute(text("DELETE FROM crm_webhooks WHERE id = :id RETURNING id"), {"id": wid})).first()
    if not row:
        raise HTTPException(404, "not found")
    return {"deleted": int(row[0])}


def _json(obj) -> str:
    import orjson

    return orjson.dumps(obj).decode("utf-8")
