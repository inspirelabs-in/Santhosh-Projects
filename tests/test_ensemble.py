"""Ensemble vote aggregation."""
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

import grabon_intel.llm.ensemble as ensemble_mod
from grabon_intel.llm import Tier


@dataclass
class _FakeLLMResult:
    content: str
    model_id: str = "fake/m"
    input_tokens: int = 10
    output_tokens: int = 10
    cache_read_tokens: int = 0
    cost_cents: int = 1
    raw: dict = None  # type: ignore[assignment]


def _make_fake(responses: dict[Tier, str]):
    async def fake_complete(*, tier: Tier, **_):
        if tier not in responses:
            raise RuntimeError(f"no fixture for tier {tier}")
        return _FakeLLMResult(content=responses[tier])

    return fake_complete


@pytest.mark.asyncio
async def test_consensus_low_disagreement(monkeypatch) -> None:
    monkeypatch.setattr(ensemble_mod, "complete", _make_fake({
        Tier.CHEAP: json.dumps({"total": 70, "tier": "warm", "confidence": 0.7}),
        Tier.FAST: json.dumps({"total": 72, "tier": "warm", "confidence": 0.8}),
        Tier.SMART: json.dumps({"total": 75, "tier": "warm", "confidence": 0.85}),
    }))
    out = await ensemble_mod.score_ensemble("prompt")
    assert out.total == 72
    assert out.tier == "warm"
    assert out.disagreement == 5
    assert out.needs_review is False
    assert out.confidence > 0.65


@pytest.mark.asyncio
async def test_high_disagreement_flags_review(monkeypatch) -> None:
    monkeypatch.setattr(ensemble_mod, "complete", _make_fake({
        Tier.CHEAP: json.dumps({"total": 30, "tier": "watchlist", "confidence": 0.6}),
        Tier.FAST: json.dumps({"total": 75, "tier": "warm", "confidence": 0.8}),
        Tier.SMART: json.dumps({"total": 85, "tier": "hot", "confidence": 0.85}),
    }))
    out = await ensemble_mod.score_ensemble("prompt")
    assert out.disagreement >= 50
    assert out.needs_review is True


@pytest.mark.asyncio
async def test_all_fail_returns_review(monkeypatch) -> None:
    async def boom(**_):
        raise RuntimeError("provider down")

    monkeypatch.setattr(ensemble_mod, "complete", boom)
    out = await ensemble_mod.score_ensemble("prompt")
    assert out.total is None
    assert out.needs_review is True
    assert out.confidence == 0.0
