"""Blocklist / allowlist management."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from ...db import session as session_ctx

router = APIRouter(prefix="/blocklist", tags=["blocklist"])


class BlocklistEntry(BaseModel):
    kind: str  # block | allow | existing_client
    pattern: str
    scope: str = "domain"  # domain | name
    reason: str | None = None
    created_by: str | None = None


@router.get("")
async def list_entries(kind: str | None = Query(default=None)) -> dict:
    async with session_ctx() as s:
        if kind:
            rows = (
                await s.execute(
                    text(
                        "SELECT id, kind, pattern, scope, reason, created_by, created_at "
                        "FROM brand_blocklist WHERE kind = :k ORDER BY id DESC"
                    ),
                    {"k": kind},
                )
            ).mappings().all()
        else:
            rows = (
                await s.execute(
                    text(
                        "SELECT id, kind, pattern, scope, reason, created_by, created_at "
                        "FROM brand_blocklist ORDER BY id DESC"
                    )
                )
            ).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.post("")
async def add_entry(body: BlocklistEntry) -> dict:
    if body.kind not in {"block", "allow", "existing_client"}:
        raise HTTPException(422, "invalid kind")
    if body.scope not in {"domain", "name"}:
        raise HTTPException(422, "invalid scope")
    async with session_ctx() as s:
        row = (
            await s.execute(
                text(
                    "INSERT INTO brand_blocklist (kind, pattern, scope, reason, created_by) "
                    "VALUES (:k, :p, :sc, :r, :cb) RETURNING id"
                ),
                {"k": body.kind, "p": body.pattern.lower().strip(), "sc": body.scope, "r": body.reason, "cb": body.created_by},
            )
        ).first()
    return {"id": int(row[0])}


@router.delete("/{entry_id}")
async def remove_entry(entry_id: int) -> dict:
    async with session_ctx() as s:
        row = (
            await s.execute(
                text("DELETE FROM brand_blocklist WHERE id = :id RETURNING id"),
                {"id": entry_id},
            )
        ).first()
    if not row:
        raise HTTPException(404, "not found")
    return {"id": int(row[0]), "deleted": True}
