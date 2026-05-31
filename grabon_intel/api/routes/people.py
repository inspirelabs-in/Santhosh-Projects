"""Decision-maker hop graph — warm-intro candidates.

Populator stub: takes a person + employment history payload and persists.
Reader: for a target brand, returns people who previously worked at
brands tagged `closed_won` or `meeting_booked` — natural warm intros.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import text

from ...db import session as session_ctx

router = APIRouter(prefix="/people", tags=["people"])


class EmploymentEntry(BaseModel):
    person_name: str
    linkedin_url: str | None = None
    brand_id: int | None = None
    brand_name: str | None = None
    title: str | None = None
    started_at: dt.date | None = None
    ended_at: dt.date | None = None
    current: bool = False
    source: str = "manual"


@router.post("/employment")
async def add_employment(entries: list[EmploymentEntry]) -> dict:
    async with session_ctx() as s:
        ids: list[int] = []
        for e in entries:
            row = (
                await s.execute(
                    text(
                        "INSERT INTO person_employment "
                        "(person_name, linkedin_url, brand_id, brand_name, title, "
                        " started_at, ended_at, current, source) "
                        "VALUES (:n, :li, :bid, :bn, :t, :st, :en, :cur, :src) "
                        "RETURNING id"
                    ),
                    {
                        "n": e.person_name,
                        "li": e.linkedin_url,
                        "bid": e.brand_id,
                        "bn": e.brand_name,
                        "t": e.title,
                        "st": e.started_at,
                        "en": e.ended_at,
                        "cur": e.current,
                        "src": e.source,
                    },
                )
            ).first()
            if row:
                ids.append(int(row[0]))
    return {"inserted": ids}


@router.get("/warm-intros/{target_brand_id}")
async def warm_intros(
    target_brand_id: int,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    """People currently at the target brand who previously worked at a
    closed_won / meeting_booked brand."""
    async with session_ctx() as s:
        rows = (
            await s.execute(
                text(
                    """
                    WITH friendly AS (
                      SELECT DISTINCT brand_id FROM grabon_feedback
                      WHERE label IN ('closed_won', 'meeting_booked')
                    )
                    SELECT p_now.person_name, p_now.title AS current_title,
                           p_now.linkedin_url,
                           p_past.brand_id AS friendly_brand_id,
                           p_past.brand_name AS friendly_brand_name,
                           p_past.title AS friendly_title,
                           p_past.ended_at
                    FROM person_employment p_now
                    JOIN person_employment p_past ON p_past.person_name = p_now.person_name
                      AND p_past.brand_id <> p_now.brand_id
                    JOIN friendly f ON f.brand_id = p_past.brand_id
                    WHERE p_now.brand_id = :b
                      AND p_now.current = TRUE
                      AND p_past.current = FALSE
                    ORDER BY p_past.ended_at DESC NULLS LAST
                    LIMIT :l
                    """
                ),
                {"b": target_brand_id, "l": limit},
            )
        ).mappings().all()
    return {"target_brand_id": target_brand_id, "candidates": [dict(r) for r in rows]}
