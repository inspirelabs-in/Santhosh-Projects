"""Agent state shared across LangGraph nodes.

Persisted to ``conversations.state`` JSONB at the end of every turn so the
agent can resume on reconnect / cold start. Keep this dict-shaped (not
Pydantic) -- LangGraph merges it with reducer rules.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict
from uuid import UUID

from langgraph.graph import add_messages


class AgentState(TypedDict, total=False):
    # Identifiers (carried through every node).
    conversation_id: str
    application_id: str
    candidate_id: str
    role_id: str

    # Stage in the V2 flow: intake | screening | assignment | submitted | completed
    stage: str

    # Pre-warmed artifacts (set at invite-time prewarm; agent reads these
    # instead of regenerating on the candidate-facing turn).
    tailored_questions: list[dict[str, Any]]   # [{id, question, expected_signal}, ...]
    assignment: dict[str, Any] | None           # {brief_md, problems, submission_format, ...}

    # Logistics fields the agent has already captured. Mirrors columns in
    # screening_answers; the extractor patches this dict each turn.
    logistics: dict[str, Any]
    # ^^ keys: current_ctc_lpa, expected_ctc_lpa, notice_period_days,
    #         willing_to_relocate

    # Tailored Q answers indexed by question id (q1, q2).
    tailored_answers: dict[str, str]

    # Tracking which question the agent is currently waiting on. Lets the
    # candidate volunteer info out of order without confusing the agent.
    asked_question_ids: list[str]
    last_asked: str | None  # one of: q1 | q2 | ctc_current | ctc_expected | notice | relocate | None

    # Conversation messages -- LangGraph add_messages reducer concats new
    # turns onto the list. Stored *only* in-memory during a single graph
    # run; persistence to the messages table is handled by the runner.
    messages: Annotated[list[dict[str, Any]], add_messages]

    # Debug breadcrumbs.
    last_node: str | None
    error: str | None


def empty_state(
    *,
    conversation_id: UUID,
    application_id: UUID,
    candidate_id: UUID,
    role_id: UUID,
) -> AgentState:
    return AgentState(
        conversation_id=str(conversation_id),
        application_id=str(application_id),
        candidate_id=str(candidate_id),
        role_id=str(role_id),
        stage="intake",
        tailored_questions=[],
        assignment=None,
        logistics={},
        tailored_answers={},
        asked_question_ids=[],
        last_asked=None,
        messages=[],
        last_node=None,
        error=None,
    )
