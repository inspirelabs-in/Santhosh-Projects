"""Dossier embeddings + similarity search.

Embedder: **fastembed** (BAAI/bge-small-en-v1.5, 384-dim, MIT-licensed).
Runs locally on CPU, no API key, no per-call cost. First call downloads
the model (~33 MB) into the local cache.

Why not OpenAI embeddings:
  - Per-call cost on a hot path (every dossier write).
  - Embedding quality at 384 dims is excellent for clustering /
    similarity within a domain; we don't need 3072-dim retrieval.

To swap providers later, replace `_encode()` only. Public API stable.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from typing import Any

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import get_logger

log = get_logger(__name__)

DIM = 384
_MODEL_NAME = "BAAI/bge-small-en-v1.5"


# fastembed is cheap to construct but model load takes ~1s — cache singleton.
_model_lock = threading.Lock()
_model: Any = None


def _get_model() -> Any:
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            try:
                from fastembed import TextEmbedding  # type: ignore[import-not-found]

                _model = TextEmbedding(model_name=_MODEL_NAME)
                log.info("embeddings.model_ready", model=_MODEL_NAME, dim=DIM)
            except Exception as exc:  # noqa: BLE001
                log.warning("embeddings.fastembed_unavailable", exc=str(exc))
                _model = False  # sentinel for "tried and failed"
    return _model


def project_for_embedding(data: dict[str, Any]) -> str:
    """Distill a dossier into compact text for embedding.

    Long boilerplate dilutes the vector. Keep ~20 short factual fields.
    """
    chunks: list[str] = []
    research = (data or {}).get("research") or {}
    positioning = research.get("positioning") or {}
    company = research.get("company") or {}
    opp = (data or {}).get("opportunity") or {}
    score = (data or {}).get("score") or {}

    chunks.append(f"brand={company.get('brand_name') or ''}")
    chunks.append(f"category={positioning.get('category') or ''}")
    chunks.append(f"sub_category={positioning.get('sub_category') or ''}")
    chunks.append(f"audience={positioning.get('audience') or ''}")
    chunks.append(f"price_band={positioning.get('price_band') or ''}")
    if usp := positioning.get("USP_summary"):
        chunks.append(f"usp={usp}")
    if diag := opp.get("diagnosis"):
        chunks.append(f"diagnosis={diag}")
    services = [
        s.get("service") for s in (opp.get("services_recommended") or []) if isinstance(s, dict)
    ]
    if services:
        chunks.append(f"services={','.join(services)}")
    if tier := score.get("tier"):
        chunks.append(f"tier={tier}")
    return " | ".join(c for c in chunks if c)


def _encode_sync(text_payload: str) -> list[float]:
    model = _get_model()
    if model is False or model is None:
        return _fallback(text_payload)
    vecs = list(model.embed([text_payload]))
    arr = np.asarray(vecs[0], dtype=np.float32)
    n = float(np.linalg.norm(arr))
    if n > 1e-8:
        arr = arr / n
    return arr.tolist()


async def embed(text_payload: str) -> list[float]:
    """Async wrapper. fastembed inference is CPU-bound, so offload to a thread."""
    return await asyncio.to_thread(_encode_sync, text_payload)


def _fallback(text_payload: str) -> list[float]:
    """Deterministic seed-based fallback for tests / when fastembed unavailable.

    NOT representative of real embedding quality — never deploy with this path.
    """
    rng = np.random.default_rng(int.from_bytes(hashlib.sha256(text_payload.encode("utf-8")).digest()[:8], "big"))
    v = rng.normal(0.0, 1.0, size=DIM).astype(np.float32)
    v /= max(float(np.linalg.norm(v)), 1e-8)
    return v.tolist()


# --- DB helpers ---------------------------------------------------------------


async def update_dossier_embedding(session: AsyncSession, dossier_id: int) -> bool:
    row = (
        await session.execute(
            text("SELECT brand_id, data FROM dossiers WHERE id = :id"),
            {"id": dossier_id},
        )
    ).first()
    if not row:
        return False
    data = json.loads(row[1]) if isinstance(row[1], str) else row[1]
    payload = project_for_embedding(data)
    vec = await embed(payload)
    await session.execute(
        text("UPDATE dossiers SET embedding = :v WHERE id = :id"),
        {"v": _pg_literal(vec), "id": dossier_id},
    )
    return True


def _pg_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


async def similar_to_brand(
    session: AsyncSession, brand_id: int, *, limit: int = 10, exclude_self: bool = True
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                """
                WITH src AS (
                  SELECT embedding FROM dossiers
                  WHERE brand_id = :b AND embedding IS NOT NULL
                  ORDER BY version DESC LIMIT 1
                )
                SELECT d.brand_id, b.name, b.domain,
                       (d.data->'score'->>'total')::int AS score_total,
                       (1 - (d.embedding <=> src.embedding)) AS similarity
                FROM dossiers d
                JOIN src ON true
                JOIN brands b ON b.id = d.brand_id
                WHERE d.embedding IS NOT NULL
                  AND (:excl IS FALSE OR d.brand_id <> :b)
                ORDER BY d.embedding <=> src.embedding
                LIMIT :l
                """
            ),
            {"b": brand_id, "l": limit, "excl": exclude_self},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def reverse_icp(session: AsyncSession, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                """
                WITH wins AS (
                  SELECT d.embedding
                  FROM grabon_feedback f
                  JOIN LATERAL (
                    SELECT embedding FROM dossiers
                    WHERE brand_id = f.brand_id AND embedding IS NOT NULL
                    ORDER BY version DESC LIMIT 1
                  ) d ON true
                  WHERE f.label = 'closed_won'
                ), centroid AS (
                  SELECT AVG(embedding)::vector(384) AS v FROM wins
                )
                SELECT d.brand_id, b.name, b.domain,
                       (1 - (d.embedding <=> centroid.v)) AS similarity
                FROM dossiers d
                JOIN brands b ON b.id = d.brand_id
                JOIN centroid ON true
                WHERE d.embedding IS NOT NULL
                ORDER BY d.embedding <=> centroid.v
                LIMIT :l
                """
            ),
            {"l": limit},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


# expose fallback for tests
_fallback_embedding = _fallback
