"""Intent classifier behavior — uses a mocked LLM."""
from __future__ import annotations

import json

import pytest

import grabon_intel.chat.intents as intents_mod
from grabon_intel.chat import classify
from grabon_intel.chat.intents import Intent


class _Result:
    def __init__(self, content: str) -> None:
        self.content = content
        self.model_id = "fake"
        self.input_tokens = 10
        self.output_tokens = 10
        self.cost_cents = 0
        self.cache_read_tokens = 0
        self.raw = {}


@pytest.mark.asyncio
async def test_classify_discover(monkeypatch) -> None:
    async def fake(**_):
        return _Result(json.dumps({
            "intent": "discover",
            "confidence": 0.92,
            "args": {"collector": "meta_ad_library", "params": {"search_terms": "skincare", "country": "IN"}},
            "explanation": "plural ask",
        }))

    monkeypatch.setattr(intents_mod, "complete", fake)
    r = await classify("find 20 D2C skincare brands in India")
    assert r.intent is Intent.DISCOVER
    assert r.args["collector"] == "meta_ad_library"
    assert r.confidence > 0.9


@pytest.mark.asyncio
async def test_classify_dossier(monkeypatch) -> None:
    async def fake(**_):
        return _Result(json.dumps({
            "intent": "dossier",
            "confidence": 0.88,
            "args": {"brand_name": "Mamaearth", "brand_domain": "mamaearth.in"},
            "explanation": "single brand",
        }))

    monkeypatch.setattr(intents_mod, "complete", fake)
    r = await classify("Build a dossier for Mamaearth")
    assert r.intent is Intent.DOSSIER
    assert r.args["brand_name"] == "Mamaearth"


@pytest.mark.asyncio
async def test_low_confidence_collapses_to_query(monkeypatch) -> None:
    async def fake(**_):
        return _Result(json.dumps({
            "intent": "discover",
            "confidence": 0.3,
            "args": {"collector": "meta_ad_library"},
            "explanation": "unsure",
        }))

    monkeypatch.setattr(intents_mod, "complete", fake)
    r = await classify("vague thing")
    assert r.intent is Intent.QUERY
    assert "low_confidence" in r.explanation


@pytest.mark.asyncio
async def test_malformed_llm_output_falls_back(monkeypatch) -> None:
    async def fake(**_):
        return _Result("not json at all")

    monkeypatch.setattr(intents_mod, "complete", fake)
    r = await classify("anything")
    assert r.intent is Intent.QUERY
    assert r.explanation == "parse_fallback"
