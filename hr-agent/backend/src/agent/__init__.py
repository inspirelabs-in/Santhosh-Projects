"""Chat-first agentic V2 hiring agent.

Replaces V1 form-driven screening + assignment with a conversational
LangGraph agent. One conversation per application; agent owns the flow:

    intake -> screening (2 tailored + 4 logistics) -> assignment_brief
    -> submission -> finalize

Public surface (import directly from the submodule to avoid pulling the
LLM client + LangGraph at module-load time when callers only need pure
helpers like the Pydantic schemas):

    from src.agent.runner import prewarm, run_turn
    from src.agent.state import AgentState
    from src.agent.schemas import ExtractedTurn, AssignmentBriefOut
"""
