"""Contact/person endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from ...db import session as session_ctx

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.get("")
async def list_contacts(
    brand_id: int | None = Query(default=None),
    verified: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    where: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if brand_id is not None:
        where.append("p.brand_id = :brand_id")
        params["brand_id"] = brand_id
    if verified is not None:
        where.append("p.verified = :verified")
        params["verified"] = verified
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = text(
        "SELECT p.*, b.name as brand_name, b.domain as brand_domain "
        "FROM persons p "
        "LEFT JOIN brands b ON b.id = p.brand_id "
        f"{where_sql} "
        "ORDER BY p.confidence DESC, p.created_at DESC "
        "LIMIT :limit OFFSET :offset"
    )
    async with session_ctx() as s:
        rows = (await s.execute(sql, params)).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.get("/{person_id}")
async def get_contact(person_id: int) -> dict:
    async with session_ctx() as s:
        row = (
            await s.execute(
                text(
                    "SELECT p.*, b.name as brand_name, b.domain as brand_domain "
                    "FROM persons p "
                    "LEFT JOIN brands b ON b.id = p.brand_id "
                    "WHERE p.id = :id"
                ),
                {"id": person_id},
            )
        ).mappings().first()
    if not row:
        raise HTTPException(404, "contact not found")
    return dict(row)


class PersonCreate(BaseModel):
    brand_id: int
    name: str
    title: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    source: str = "manual"
    confidence: float = 0.5


@router.post("")
async def create_contact(body: PersonCreate) -> dict:
    async with session_ctx() as s:
        row = (
            await s.execute(
                text(
                    "INSERT INTO persons (brand_id, name, title, email, phone, linkedin_url, source, confidence) "
                    "VALUES (:brand_id, :name, :title, :email, :phone, :linkedin_url, :source, :confidence) "
                    "ON CONFLICT (brand_id, email) WHERE email IS NOT NULL DO UPDATE SET "
                    "  name = EXCLUDED.name, title = COALESCE(EXCLUDED.title, persons.title), "
                    "  phone = COALESCE(EXCLUDED.phone, persons.phone), "
                    "  linkedin_url = COALESCE(EXCLUDED.linkedin_url, persons.linkedin_url), "
                    "  confidence = GREATEST(persons.confidence, EXCLUDED.confidence), "
                    "  updated_at = now() "
                    "RETURNING id, brand_id, name, email"
                ),
                body.model_dump(),
            )
        ).mappings().first()
    return dict(row)


class PersonVerify(BaseModel):
    verified: bool
    email: str | None = None


@router.patch("/{person_id}/verify")
async def verify_contact(person_id: int, body: PersonVerify) -> dict:
    updates = ["verified = :verified"]
    params: dict[str, Any] = {"id": person_id, "verified": body.verified}
    if body.email is not None:
        updates.append("email = :email")
        params["email"] = body.email
    async with session_ctx() as s:
        row = (
            await s.execute(
                text(f"UPDATE persons SET {', '.join(updates)}, updated_at = now() WHERE id = :id RETURNING *"),
                params,
            )
        ).mappings().first()
    if not row:
        raise HTTPException(404, "contact not found")
    return dict(row)


@router.delete("/{person_id}")
async def delete_contact(person_id: int) -> dict:
    async with session_ctx() as s:
        result = await s.execute(
            text("DELETE FROM persons WHERE id = :id RETURNING id"),
            {"id": person_id},
        )
        if not result.first():
            raise HTTPException(404, "contact not found")
    return {"deleted": person_id}
