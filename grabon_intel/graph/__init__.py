"""LangGraph supervisor — orchestrates research → competitor → opportunity
→ score → outreach nodes with real tool calls. Built as a `StateGraph`
over a typed `ResearchState` so each node sees only inputs it needs and
nothing leaks across.
"""
from .state import NodeRecord, ResearchInput, ResearchOutput, ResearchState
from .supervisor import ToolRegistry, build_supervisor, run_research

__all__ = [
    "ResearchState",
    "ResearchInput",
    "ResearchOutput",
    "NodeRecord",
    "ToolRegistry",
    "build_supervisor",
    "run_research",
]
