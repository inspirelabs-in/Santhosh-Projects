"""Embedding fallback + projection shape."""
from __future__ import annotations

import math

import pytest

from grabon_intel.embeddings import DIM, _fallback_embedding, embed, project_for_embedding


def test_projection_extracts_signal_fields() -> None:
    dossier = {
        "research": {
            "company": {"brand_name": "Mamaearth"},
            "positioning": {"category": "beauty", "audience": "millennials", "USP_summary": "natural"},
        },
        "opportunity": {
            "diagnosis": "weak SEO",
            "services_recommended": [{"service": "seo"}, {"service": "social_media"}],
        },
        "score": {"tier": "warm"},
    }
    s = project_for_embedding(dossier)
    assert "Mamaearth" in s
    assert "beauty" in s
    assert "seo,social_media" in s
    assert "tier=warm" in s


def test_fallback_embedding_unit_norm() -> None:
    v = _fallback_embedding("hello world")
    assert len(v) == DIM
    norm = math.sqrt(sum(x * x for x in v))
    assert abs(norm - 1.0) < 1e-4


def test_fallback_embedding_deterministic() -> None:
    a = _fallback_embedding("hello")
    b = _fallback_embedding("hello")
    assert a == b


@pytest.mark.asyncio
async def test_embed_uses_fallback_when_no_keys(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    from grabon_intel.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    v = await embed("test")
    assert len(v) == DIM
