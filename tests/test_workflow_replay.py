"""End-to-end replay test of DossierWF using Temporal time-skipping env.

The dossier workflow now delegates research to a single
`run_research_graph_activity`. We mock that + the persist activities and
confirm the workflow:
  - calls the graph activity once
  - writes the dossier with the graph's reported cost
  - records an agent trace at the end
"""
from __future__ import annotations

import uuid

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from grabon_intel.worker import build_runner
from grabon_intel.workflows.activities import (
    DossierWrite,
    DossierWriteResult,
    GraphRunRequest,
    GraphRunResult,
    TraceWrite,
)
from grabon_intel.workflows.dossier import DossierWF, DossierWFInput

_CALLS: dict[str, list[object]] = {"graph": [], "dossier": [], "trace": []}


@activity.defn(name="run_research_graph")
async def fake_graph(req: GraphRunRequest) -> GraphRunResult:
    _CALLS["graph"].append(req)
    return GraphRunResult(
        brand_id=req.brand_id,
        research={"company": {"brand_name": "Test"}},
        competitor={"competitors": []},
        opportunity={"diagnosis": "—"},
        score={"total": 72, "tier": "warm", "confidence": 0.8, "breakdown": {}, "why": []},
        outreach={"subjects": ["a", "b", "c", "d", "e"]},
        nodes=[
            {"node": "research", "model": "fake", "in_tokens": 100, "out_tokens": 50, "cost_cents": 1, "tools_used": ["website_fetch"], "preview": "...", "error": None},
            {"node": "competitor", "model": "fake", "in_tokens": 80, "out_tokens": 40, "cost_cents": 1, "tools_used": ["serper"], "preview": "...", "error": None},
            {"node": "opportunity", "model": "fake", "in_tokens": 90, "out_tokens": 60, "cost_cents": 2, "tools_used": ["google_news"], "preview": "...", "error": None},
            {"node": "score", "model": "fake", "in_tokens": 70, "out_tokens": 30, "cost_cents": 1, "tools_used": [], "preview": "...", "error": None},
            {"node": "outreach", "model": "fake", "in_tokens": 120, "out_tokens": 80, "cost_cents": 2, "tools_used": [], "preview": "...", "error": None},
        ],
        total_cost_cents=7,
        tool_data={"website": {"title": "Test"}},
    )


@activity.defn(name="record_dossier")
async def fake_record(w: DossierWrite) -> DossierWriteResult:
    _CALLS["dossier"].append(w)
    return DossierWriteResult(dossier_id=99, version=1)


@activity.defn(name="persist_trace")
async def fake_trace(t: TraceWrite) -> None:
    _CALLS["trace"].append(t)


@pytest.mark.asyncio
async def test_dossier_workflow_runs_graph_and_persists() -> None:
    for k in _CALLS:
        _CALLS[k].clear()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="t",
            workflows=[DossierWF],
            activities=[fake_graph, fake_record, fake_trace],
            workflow_runner=build_runner(),
        ):
            result = await env.client.execute_workflow(
                DossierWF.run,
                DossierWFInput(brand_id=42, reason="unit"),
                id=f"wf-{uuid.uuid4()}",
                task_queue="t",
            )

    assert result.brand_id == 42
    assert result.dossier_id == 99
    assert result.version == 1
    assert result.total_cost_cents == 7
    assert result.score_total == 72
    assert result.tier == "warm"
    assert result.nodes == ["research", "competitor", "opportunity", "score", "outreach"]

    # one graph call
    assert len(_CALLS["graph"]) == 1
    g = _CALLS["graph"][0]
    assert g.brand_id == 42

    # dossier persisted with graph cost
    assert len(_CALLS["dossier"]) == 1
    dw = _CALLS["dossier"][0]
    assert dw.cost_cents == 7
    assert dw.data["score"]["total"] == 72
    assert "research" in dw.data and "outreach" in dw.data

    # trace persisted
    assert len(_CALLS["trace"]) == 1
    tr = _CALLS["trace"][0]
    assert tr.agent == "dossier"
    assert tr.total_cost_cents == 7
    assert len(tr.steps) == 5
