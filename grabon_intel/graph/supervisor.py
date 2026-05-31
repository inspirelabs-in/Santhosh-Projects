"""Supervisor — LangGraph StateGraph wiring nodes in order."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, START, StateGraph

from ..tools import (
    GoogleNewsTool,
    SearXNGTool,
    WappalyzerLocalTool,
    WebsiteFetchTool,
)
from .nodes import (
    make_competitor_node,
    make_opportunity_node,
    make_outreach_node,
    make_research_node,
    make_score_node,
)
from .state import ResearchOutput, ResearchState


@dataclass(slots=True)
class ToolRegistry:
    website: WebsiteFetchTool
    search: SearXNGTool
    wappalyzer: WappalyzerLocalTool
    news: GoogleNewsTool

    @classmethod
    def default(cls) -> "ToolRegistry":
        return cls(
            website=WebsiteFetchTool(),
            search=SearXNGTool(),
            wappalyzer=WappalyzerLocalTool(),
            news=GoogleNewsTool(),
        )


def build_supervisor(tools: ToolRegistry | None = None):
    tools = tools or ToolRegistry.default()
    g: StateGraph = StateGraph(ResearchState)
    g.add_node("research", make_research_node(tools))
    g.add_node("competitor", make_competitor_node(tools))
    g.add_node("opportunity", make_opportunity_node(tools))
    g.add_node("score", make_score_node(tools))
    g.add_node("outreach", make_outreach_node(tools))
    g.add_edge(START, "research")
    g.add_edge("research", "competitor")
    g.add_edge("competitor", "opportunity")
    g.add_edge("opportunity", "score")
    g.add_edge("score", "outreach")
    g.add_edge("outreach", END)
    return g.compile()


async def run_research(
    *,
    brand_id: int,
    brand_name: str | None = None,
    brand_domain: str | None = None,
    reason: str = "manual",
    tools: ToolRegistry | None = None,
    signal_data: dict | None = None,
) -> ResearchOutput:
    graph = build_supervisor(tools)
    init: ResearchState = {
        "brand_id": brand_id,
        "brand_name": brand_name,
        "brand_domain": brand_domain,
        "reason": reason,
        "signal_data": signal_data,
        "nodes": [],
        "total_cost_cents": 0,
    }
    final: dict[str, Any] = await graph.ainvoke(init)
    return ResearchOutput(
        brand_id=brand_id,
        research=final.get("research"),
        competitor=final.get("competitor"),
        opportunity=final.get("opportunity"),
        score=final.get("score"),
        outreach=final.get("outreach"),
        nodes=final.get("nodes", []),
        total_cost_cents=int(final.get("total_cost_cents", 0)),
        tool_data={
            k: final.get(k)
            for k in ("website", "wappalyzer", "search", "news")
            if final.get(k) is not None
        },
        signal_data=final.get("signal_data"),
    )


from collections.abc import AsyncIterator as _AsyncIterator


async def run_research_streaming(
    *,
    brand_id: int,
    brand_name: str | None = None,
    brand_domain: str | None = None,
    reason: str = "manual",
    tools: ToolRegistry | None = None,
    on_node: Any = None,
    signal_data: dict | None = None,
) -> _AsyncIterator[tuple[str, dict[str, Any]]]:
    """Yield (node_name, state_update) per completed node, then final ResearchOutput."""
    graph = build_supervisor(tools)
    init: ResearchState = {
        "brand_id": brand_id,
        "brand_name": brand_name,
        "brand_domain": brand_domain,
        "reason": reason,
        "signal_data": signal_data,
        "nodes": [],
        "total_cost_cents": 0,
    }
    final: dict[str, Any] = {}
    async for update in graph.astream(init, stream_mode="updates"):
        for node_name, node_data in update.items():
            final.update(node_data)
            yield node_name, node_data

    yield "__done__", {
        "result": ResearchOutput(
            brand_id=brand_id,
            research=final.get("research"),
            competitor=final.get("competitor"),
            opportunity=final.get("opportunity"),
            score=final.get("score"),
            outreach=final.get("outreach"),
            nodes=final.get("nodes", []),
            total_cost_cents=int(final.get("total_cost_cents", 0)),
            tool_data={
                k: final.get(k)
                for k in ("website", "wappalyzer", "search", "news")
                if final.get(k) is not None
            },
            signal_data=signal_data,
        )
    }
