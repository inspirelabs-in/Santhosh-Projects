"""Golden replay test for LangGraph supervisor.

Mocks every external dependency:
  - tools (return fixed payloads)
  - LLM (`grabon_intel.llm.complete` patched to return canned JSON)

Verifies node ordering, state propagation, cost accumulation, and that
the outreach node is skipped when score < 40.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

import grabon_intel.graph.nodes as nodes_mod
import grabon_intel.graph.supervisor as supervisor_mod
from grabon_intel.graph.supervisor import ToolRegistry, run_research
from grabon_intel.tools.base import ToolResult


@dataclass(slots=True)
class _FakeTool:
    name: str
    payload: dict
    degraded: bool = False
    available: bool = True

    async def safe(self, **_: Any) -> ToolResult:
        return ToolResult(name=self.name, data=self.payload, degraded=self.degraded)


def _registry() -> ToolRegistry:
    return ToolRegistry(
        website=_FakeTool(name="website_fetch", payload={"title": "Test brand"}),  # type: ignore[arg-type]
        search=_FakeTool(name="searxng", payload={"results": [{"title": "x", "link": "https://x"}]}),  # type: ignore[arg-type]
        wappalyzer=_FakeTool(name="wappalyzer_local", payload={}, degraded=True, available=False),  # type: ignore[arg-type]
        news=_FakeTool(name="google_news", payload={"items": []}),  # type: ignore[arg-type]
    )


class _FakeLLMResult:
    def __init__(self, content: str) -> None:
        self.content = content
        self.model_id = "fake/model"
        self.input_tokens = 100
        self.output_tokens = 50
        self.cache_read_tokens = 0
        self.cost_cents = 1
        self.raw = {}


_LLM_RESPONSES_HOT = [
    json.dumps({"company": {"brand_name": "Test", "hq": "Mumbai", "employees_est": "50-100", "revenue_band": "1-5M", "founded_year": 2020}, "positioning": {"category": "beauty", "audience": "women 18-35", "USP_summary": "natural skincare"}, "digital_footprint": {"seo_signal": "basic", "social_signal": "moderate", "paid_signal": "none", "email_signal": "basic", "content_maturity": "basic", "programmatic_signal": "none"}, "funding_history": []}),
    json.dumps({"competitors": [{"name": "Comp", "domain": "comp.com", "matrix": {}}], "gap_map": {"seo": "weak"}}),
    json.dumps({"diagnosis": "weak SEO", "top_3_weaknesses": ["a", "b", "c"], "services_recommended": [{"service": "seo", "rationale": "—", "estimated_impact": "—", "confidence": 0.7}]}),
    json.dumps({"total": 78, "tier": "warm", "breakdown": {"seo": 0.6}, "why": ["a", "b", "c"], "red_flags": [], "confidence": 0.8}),
    json.dumps({"subjects": ["a", "b", "c", "d", "e"], "bodies": ["x"] * 5, "linkedin_inmail": "...", "voicemail_script": "..."}),
]

_LLM_RESPONSES_PARKED = [
    json.dumps({"company": {"brand_name": "Test"}}),
    # retry response — fills missing company/positioning fields
    json.dumps({"company": {"brand_name": "Test", "hq": "Unknown (inferred)", "category": "general"}, "positioning": {"category": "general (inferred)", "audience": "consumers (inferred)"}, "digital_footprint": {"seo_signal": "none"}, "funding_history": []}),
    json.dumps({"competitors": [], "gap_map": {}}),
    json.dumps({"diagnosis": "—", "top_3_weaknesses": [], "services_recommended": []}),
    json.dumps({"total": 22, "tier": "park", "breakdown": {}, "why": [], "red_flags": [], "confidence": 0.2}),
    # outreach skipped for score < 40.
]


@pytest.fixture()
def patch_llm(monkeypatch):
    """Patch `complete` in both modules that import it."""
    queue: list[str] = []

    async def fake_complete(**kw):
        if not queue:
            raise AssertionError("LLM called more times than queued responses")
        content = queue.pop(0)
        return _FakeLLMResult(content)

    monkeypatch.setattr(nodes_mod, "complete", fake_complete)
    # supervisor doesn't call complete directly, but make sure.
    if hasattr(supervisor_mod, "complete"):
        monkeypatch.setattr(supervisor_mod, "complete", fake_complete)
    return queue


@pytest.mark.asyncio
async def test_supervisor_runs_all_nodes_when_score_high(patch_llm) -> None:
    patch_llm.extend(_LLM_RESPONSES_HOT)
    out = await run_research(
        brand_id=1, brand_name="Test", brand_domain="test.com", tools=_registry()
    )
    assert [n["node"] for n in out.nodes] == [
        "research",
        "competitor",
        "opportunity",
        "score",
        "outreach",
    ]
    # 5 nodes × 1c each.
    assert out.total_cost_cents == 5
    assert out.score and out.score["total"] == 78
    assert out.outreach and out.outreach["subjects"] == ["a", "b", "c", "d", "e"]
    # All LLM responses consumed.
    assert patch_llm == []


@pytest.mark.asyncio
async def test_supervisor_skips_outreach_when_score_low(patch_llm) -> None:
    patch_llm.extend(_LLM_RESPONSES_PARKED)
    out = await run_research(
        brand_id=2, brand_name="Test2", brand_domain="test2.com", tools=_registry()
    )
    # Outreach still appears as a node entry but flagged skipped.
    assert [n["node"] for n in out.nodes] == [
        "research",
        "competitor",
        "opportunity",
        "score",
        "outreach",
    ]
    assert out.outreach == {"skipped": True, "reason": "score<40"}
    # research retry replaces original cost; 4 nodes with cost (retry + competitor + opportunity + score), outreach skipped.
    assert out.total_cost_cents == 4
    assert patch_llm == []
