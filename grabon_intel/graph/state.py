"""Typed state for the LangGraph supervisor.

LangGraph requires a dict-like state. We use a `TypedDict` so type-checkers
catch shape drift, and document node-by-node contracts in this file.
"""
from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict


class ResearchInput(TypedDict, total=False):
    brand_id: int
    brand_name: str | None
    brand_domain: str | None
    reason: str


class NodeRecord(TypedDict, total=False):
    node: str
    model: str | None
    in_tokens: int
    out_tokens: int
    cost_cents: int
    tools_used: list[str]
    preview: str
    error: str | None


class ResearchState(TypedDict, total=False):
    """Mutable state passed between nodes.

    Each node may add keys, but should not delete or rewrite keys owned by
    earlier nodes. The supervisor enforces ordering via the graph edges.
    """

    # input
    brand_id: int
    brand_name: str | None
    brand_domain: str | None
    reason: str

    # tool outputs
    serper: dict | None
    website: dict | None
    similarweb: dict | None
    builtwith: dict | None
    wappalyzer: dict | None
    search: dict | None
    news: dict | None

    # collector signal data (injected from DB before graph runs)
    signal_data: dict | None

    # node outputs
    research: dict | None
    competitor: dict | None
    opportunity: dict | None
    score: dict | None
    outreach: dict | None

    # cross-cutting — reducers append/sum across nodes.
    nodes: Annotated[list[NodeRecord], operator.add]
    total_cost_cents: Annotated[int, operator.add]


@dataclass(slots=True)
class ResearchOutput:
    brand_id: int
    research: dict | None = None
    competitor: dict | None = None
    opportunity: dict | None = None
    score: dict | None = None
    outreach: dict | None = None
    nodes: list[NodeRecord] = field(default_factory=list)
    total_cost_cents: int = 0
    tool_data: dict[str, dict] = field(default_factory=dict)
    signal_data: dict | None = None
