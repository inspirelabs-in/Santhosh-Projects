"""Semantic talent pool search powered by pgvector embeddings.

Leverages the existing candidate_profiles.embedding column (1536-dim,
IVFFlat-indexed) to find similar candidates by natural language query
or by reference to an existing candidate profile.

Endpoints:
  GET /talent-search/query       -- free-text semantic search
  GET /talent-search/similar/{candidate_id} -- find candidates similar to a known one
  GET /talent-search/match-role/{role_id}   -- find talent pool matches for a role
"""

from __future__ import annotations

import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select, text

from src.api.auth import require_viewer
from src.config import get_settings
from src.db.base import Application, Candidate, CandidateProfileRow, Role
from src.db.connection import session_scope
from src.llm.client import get_llm_client

logger = logging.getLogger(__name__)
_settings = get_settings()

router = APIRouter(prefix="/talent-search", tags=["talent-search"])


class TalentMatch(BaseModel):
    candidate_id: UUID
    candidate_name: str | None
    candidate_email: str | None
    similarity: float
    top_skills: list[str] = Field(default_factory=list)
    experience_years: float | None = None
    current_title: str | None = None
    current_company: str | None = None
    location: str | None = None
    last_application_status: str | None = None
    last_role_applied: str | None = None


class TalentSearchResponse(BaseModel):
    query: str | None = None
    matches: list[TalentMatch]
    total: int


def _extract_profile_fields(parsed_data: dict) -> dict[str, Any]:
    """Pull display fields from parsed_data JSONB."""
    skills = parsed_data.get("skills") or parsed_data.get("top_skills") or []
    if isinstance(skills, list):
        skills = skills[:8]
    elif isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",")][:8]
    else:
        skills = []

    experience = parsed_data.get("total_experience_years") or parsed_data.get("experience_years")
    work = parsed_data.get("work_history") or parsed_data.get("experience") or []
    current_title = None
    current_company = None
    if isinstance(work, list) and work:
        latest = work[0] if isinstance(work[0], dict) else {}
        current_title = latest.get("title") or latest.get("designation")
        current_company = latest.get("company") or latest.get("organization")

    return {
        "top_skills": skills,
        "experience_years": float(experience) if experience else None,
        "current_title": current_title,
        "current_company": current_company,
        "location": parsed_data.get("location") or parsed_data.get("city"),
    }


async def _embed_text(text_input: str) -> list[float]:
    """Generate embedding for search query using the same model as resume parsing."""
    import litellm
    response = await litellm.aembedding(
        model=_settings.embedding_model or "text-embedding-3-large",
        input=[text_input],
    )
    return response.data[0]["embedding"]


async def _search_by_embedding(
    embedding: list[float],
    *,
    limit: int = 20,
    exclude_candidate_id: UUID | None = None,
    exclude_active_for_role: UUID | None = None,
    min_similarity: float = 0.3,
) -> list[TalentMatch]:
    """Core vector search against candidate_profiles."""
    async with session_scope() as session:
        distance_expr = CandidateProfileRow.embedding.cosine_distance(embedding)
        similarity_expr = (1 - distance_expr).label("similarity")

        q = (
            select(
                CandidateProfileRow,
                Candidate,
                similarity_expr,
            )
            .join(Candidate, Candidate.id == CandidateProfileRow.candidate_id)
            .where(CandidateProfileRow.embedding.isnot(None))
        )

        if exclude_candidate_id:
            q = q.where(CandidateProfileRow.candidate_id != exclude_candidate_id)

        q = (
            q.order_by(distance_expr.asc())
            .limit(limit * 2)
        )

        rows = (await session.execute(q)).all()

        # Enrich with latest application info
        candidate_ids = [r[1].id for r in rows]
        app_info: dict[UUID, tuple[str, str | None]] = {}
        if candidate_ids:
            app_rows = (
                await session.execute(
                    select(Application, Role)
                    .join(Role, Role.id == Application.role_id, isouter=True)
                    .where(Application.candidate_id.in_(candidate_ids))
                    .order_by(Application.created_at.desc())
                )
            ).all()
            for app, role in app_rows:
                if app.candidate_id not in app_info:
                    app_info[app.candidate_id] = (
                        app.status,
                        role.title if role else None,
                    )

        matches: list[TalentMatch] = []
        seen: set[UUID] = set()
        for profile_row, cand, sim in rows:
            if cand.id in seen:
                continue
            if sim < min_similarity:
                continue
            if exclude_active_for_role and cand.id in app_info:
                continue
            seen.add(cand.id)

            pf = _extract_profile_fields(profile_row.parsed_data or {})
            app_status, app_role = app_info.get(cand.id, (None, None))

            matches.append(
                TalentMatch(
                    candidate_id=cand.id,
                    candidate_name=cand.name,
                    candidate_email=cand.email,
                    similarity=round(float(sim), 4),
                    last_application_status=app_status,
                    last_role_applied=app_role,
                    **pf,
                )
            )
            if len(matches) >= limit:
                break

        return matches


@router.get("/query", response_model=TalentSearchResponse)
async def search_talent_pool(
    _: Annotated[str, Depends(require_viewer)],
    q: str = Query(..., min_length=3, max_length=500, description="Natural language search"),
    limit: int = Query(20, ge=1, le=100),
    min_similarity: float = Query(0.3, ge=0.0, le=1.0),
) -> TalentSearchResponse:
    """Semantic search across all candidate profiles.

    Examples:
      - "React developer with 5 years experience in fintech"
      - "Senior Python backend engineer who knows AWS"
      - "UI/UX designer with Figma and design systems experience"
    """
    embedding = await _embed_text(q)
    matches = await _search_by_embedding(
        embedding, limit=limit, min_similarity=min_similarity
    )
    return TalentSearchResponse(query=q, matches=matches, total=len(matches))


@router.get("/similar/{candidate_id}", response_model=TalentSearchResponse)
async def find_similar_candidates(
    candidate_id: UUID,
    _: Annotated[str, Depends(require_viewer)],
    limit: int = Query(15, ge=1, le=50),
) -> TalentSearchResponse:
    """Find candidates with similar profiles to a given candidate."""
    async with session_scope() as session:
        profile = await session.scalar(
            select(CandidateProfileRow)
            .where(CandidateProfileRow.candidate_id == candidate_id)
            .where(CandidateProfileRow.embedding.isnot(None))
            .order_by(CandidateProfileRow.created_at.desc())
            .limit(1)
        )
        if profile is None:
            raise HTTPException(404, "No embedded profile for this candidate")

    matches = await _search_by_embedding(
        profile.embedding,
        limit=limit,
        exclude_candidate_id=candidate_id,
    )
    return TalentSearchResponse(
        query=f"similar to candidate {candidate_id}",
        matches=matches,
        total=len(matches),
    )


@router.get("/match-role/{role_id}", response_model=TalentSearchResponse)
async def match_role_to_talent_pool(
    role_id: UUID,
    _: Annotated[str, Depends(require_viewer)],
    limit: int = Query(20, ge=1, le=100),
    exclude_current_applicants: bool = Query(True),
) -> TalentSearchResponse:
    """Find past candidates in the talent pool who match a role's JD.

    Use this to re-engage candidates from the talent pool for new openings
    without waiting for them to apply.
    """
    async with session_scope() as session:
        role = await session.get(Role, role_id)
        if role is None:
            raise HTTPException(404, "Role not found")
        search_text = f"{role.title}. {role.jd_text[:2000]}"

    embedding = await _embed_text(search_text)
    matches = await _search_by_embedding(
        embedding,
        limit=limit,
        exclude_active_for_role=role_id if exclude_current_applicants else None,
    )
    return TalentSearchResponse(
        query=f"role: {role.title}",
        matches=matches,
        total=len(matches),
    )
