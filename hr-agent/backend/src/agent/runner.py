"""Runner: orchestrates one chat turn and produces an SSE-friendly stream.

Public entry points:

    * ``prewarm(application_id)`` -- generate the 2 tailored questions and
      an assignment skeleton at invite-time so the candidate's first turn
      streams almost instantly.

    * ``run_turn(...)`` -- async-iterator yielding event dicts the API layer
      forwards to SSE. Events: ``stage_change``, ``token``, ``tool_call_start``,
      ``tool_call_end``, ``brief``, ``done``, ``error``.

The runner owns:
    * Loading conversation state from Postgres
    * Persisting the inbound user message
    * Invoking the LangGraph
    * Building the system prompt + history
    * Streaming the assistant reply via ``llm_stream.stream_chat``
    * Persisting the assistant message + state updates
    * Publishing each SSE event to the per-application Redis channel so
      the recruiter dashboard can mirror the conversation in real time
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator
from uuid import UUID

from src.agent.generators import gen_assignment, gen_tailored_questions
from src.agent.graph import get_graph
from src.agent.llm_stream import stream_chat
from src.agent.prompts import CHAT_TURN_SYSTEM_V1, CHAT_TURN_VERSION
from src.llm.prompt_manager import compile_prompt
from src.agent.state import AgentState, empty_state
from src.config import get_settings
from src.db.base import Application, Candidate, CandidateProfileRow, Role
from src.db.connection import session_scope
from src.db.repositories import (
    assignment as assignment_repo,
    conversation as conversation_repo,
    screening_answer as screening_repo,
)
from src.llm.client import LLMError
from src.services.events import publish_event
from sqlalchemy import select as sa_select

logger = logging.getLogger(__name__)
_settings = get_settings()


# ---------------------------------------------------------------------------
# Pre-warm
# ---------------------------------------------------------------------------


async def prewarm(*, application_id: UUID) -> None:
    """Run at invite-time. Parallel: tailored questions + assignment skeleton.

    Both artifacts are cached on ``conversations`` so the first candidate
    turn does not pay LLM latency for them. Idempotent: re-running just
    overwrites the cache.
    """
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise ValueError(f"application {application_id} not found")
        role = await session.get(Role, app.role_id) if app.role_id else None
        if role is None:
            raise ValueError(f"role missing for application {application_id}")
        cand_profile = (
            await session.execute(
                sa_select(CandidateProfileRow)
                .where(CandidateProfileRow.candidate_id == app.candidate_id)
                .order_by(CandidateProfileRow.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        conv = await conversation_repo.get_or_create_conversation(
            session,
            application_id=application_id,
            initial_state={"role_id": str(role.id), "candidate_id": str(app.candidate_id)},
        )
        conversation_id = conv.id

    candidate_profile = (cand_profile.parsed_data if cand_profile else {}) or {}
    deadline_days = role.assignment_deadline_days or 7
    time_budget_hours = max(2, min(8, deadline_days * 2))

    async def _gen_qs():
        return await gen_tailored_questions(
            role_title=role.title,
            jd_text=role.jd_text,
            candidate_profile=candidate_profile,
            application_id=application_id,
            candidate_id=app.candidate_id,
        )

    async def _gen_assign():
        return await gen_assignment(
            role_title=role.title,
            jd_text=role.jd_text,
            candidate_profile=candidate_profile,
            screening_answers=None,  # not yet collected at prewarm
            time_budget_hours=time_budget_hours,
            deadline_days=deadline_days,
            application_id=application_id,
            candidate_id=app.candidate_id,
        )

    qs_result, assign_result = await asyncio.gather(
        _gen_qs(), _gen_assign(), return_exceptions=True
    )

    questions: list[dict[str, Any]] | None = None
    if isinstance(qs_result, Exception):
        logger.warning("prewarm tailored_qs failed: %s", qs_result)
    else:
        questions = [q.model_dump() for q in qs_result.questions]

    assignment_payload: dict[str, Any] | None = None
    if isinstance(assign_result, Exception):
        logger.warning("prewarm assignment failed: %s", assign_result)
    else:
        assignment_payload = assign_result.model_dump()

    async with session_scope() as session:
        await conversation_repo.set_prewarmed(
            session,
            conversation_id,
            questions=questions,
            assignment=assignment_payload,
        )
        # Seed state with prewarmed bits so the first turn skips a round trip.
        patch: dict[str, Any] = {}
        if questions:
            patch["tailored_questions"] = questions
        if assignment_payload:
            patch["assignment"] = assignment_payload
        if patch:
            await conversation_repo.update_state(
                session, conversation_id, state_patch=patch
            )


# ---------------------------------------------------------------------------
# Run one turn
# ---------------------------------------------------------------------------


def _system_prompt(state: AgentState, role: Role, *, is_opening: bool = False) -> str:
    pending = [
        q for q in (state.get("tailored_questions") or [])
        if q["id"] not in (state.get("tailored_answers") or {})
    ]
    already = {
        **(state.get("logistics") or {}),
        **{f"tailored_a{i+1}_present": True for i, _ in enumerate((state.get("tailored_answers") or {}).keys())},
    }
    base = compile_prompt(
        "chat_turn_system",
        fallback=CHAT_TURN_SYSTEM_V1,
        company_name="GrabOn",
        role_title=role.title,
        role_location=role.location or "our office",
        stage=state.get("stage") or "intake",
        already_captured_json=json.dumps(already, ensure_ascii=False),
        pending_tailored_json=json.dumps(pending, ensure_ascii=False),
        next_field=state.get("last_asked") or "q1",
        review_sla_days=3,
    )
    if is_opening:
        base += (
            "\n\nFIRST TURN: greet the candidate by first name (if known), say "
            "in one sentence what this conversation is for, and immediately ask "
            "the first pending question. Do not acknowledge anything -- there is "
            "no prior turn to acknowledge."
        )
    return base


def _history_messages(
    db_msgs: list[Any], system_prompt: str, max_turns: int = 12
) -> list[dict[str, Any]]:
    """Build the chat-completion message list for this turn.

    Keep only the last ``max_turns`` user/assistant turns to bound cost.
    Tool messages from earlier turns are dropped -- their effect is already
    baked into the persistent state.

    User messages are wrapped in XML delimiters so the LLM can distinguish
    candidate input from system instructions even if the candidate attempts
    prompt injection.
    """
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    relevant = [m for m in db_msgs if m.role in ("user", "assistant") and m.content]
    relevant = relevant[-max_turns:]
    for m in relevant:
        if m.role == "user":
            messages.append({
                "role": "user",
                "content": f"<candidate_message>{m.content}</candidate_message>",
            })
        else:
            messages.append({"role": m.role, "content": m.content})
    return messages


async def _state_from_db(conv) -> AgentState:
    """Hydrate AgentState from the conversation row."""
    state = AgentState(
        conversation_id=str(conv.id),
        application_id=str(conv.application_id),
        candidate_id=(conv.state or {}).get("candidate_id", ""),
        role_id=(conv.state or {}).get("role_id", ""),
        stage=conv.stage or "intake",
        tailored_questions=(conv.state or {}).get("tailored_questions") or conv.prewarmed_questions or [],
        assignment=(conv.state or {}).get("assignment") or conv.prewarmed_assignment,
        logistics=(conv.state or {}).get("logistics") or {},
        tailored_answers=(conv.state or {}).get("tailored_answers") or {},
        asked_question_ids=(conv.state or {}).get("asked_question_ids") or [],
        last_asked=(conv.state or {}).get("last_asked"),
        messages=[],
        last_node=None,
        error=None,
    )
    return state


async def run_turn(
    *,
    conversation_id: UUID,
    user_message: str | None,
) -> AsyncIterator[dict[str, Any]]:
    """One turn of the agent. Yields SSE-shaped events.

    ``user_message`` is None on the very first turn (when the candidate
    just opened the link and we want to push an opening greeting).
    """
    # Load conversation + role + recent messages.
    async with session_scope() as session:
        conv = await conversation_repo.get_conversation_by_id(session, conversation_id)
        if conv is None:
            yield {"type": "error", "message": "conversation_not_found"}
            return
        application = await session.get(Application, conv.application_id)
        if application is None:
            yield {"type": "error", "message": "application_not_found"}
            return
        role = await session.get(Role, application.role_id) if application.role_id else None
        if role is None:
            yield {"type": "error", "message": "role_not_found"}
            return

        # Persist inbound user message first so a streaming crash still
        # records what the candidate said.
        if user_message is not None and user_message.strip():
            await conversation_repo.append_message(
                session,
                conversation_id=conversation_id,
                role="user",
                content=user_message.strip(),
            )

        db_msgs = await conversation_repo.list_messages(session, conversation_id)

    application_id = application.id

    # Hydrate state and append the latest user message into in-memory
    # AgentState (graph reducer expects messages list).
    state = await _state_from_db(conv)
    state["candidate_id"] = str(application.candidate_id)
    state["role_id"] = str(application.role_id) if application.role_id else ""
    if user_message and user_message.strip():
        state["messages"] = [
            *(state.get("messages") or []),
            {"role": "user", "content": user_message.strip()},
        ]

    # ---- Run the LangGraph: extract -> route -> maybe_gen_assignment ----
    yield {"type": "thinking", "label": "Reading your reply"}
    # Heads-up to the UI when we know the slow-ish assignment generation
    # branch is about to fire (no prewarmed brief + screening complete).
    if (
        not (conv.prewarmed_assignment or (state.get("assignment")))
        and (
            (state.get("logistics") or {}).get("willing_to_relocate") is not None
            and len((state.get("tailored_answers") or {})) >= 2
        )
    ):
        yield {"type": "thinking", "label": "Preparing your assignment"}
    try:
        graph = get_graph()
        new_state: AgentState = await graph.ainvoke(state)
    except LLMError as e:
        logger.warning("agent graph LLMError: %s", e)
        yield {"type": "error", "message": str(e)}
        return
    except Exception as e:  # noqa: BLE001
        logger.exception("agent graph crash")
        yield {"type": "error", "message": f"agent_error: {e}"}
        return

    # Persist any state changes from the graph.
    async with session_scope() as session:
        prev_stage = conv.stage
        await conversation_repo.update_state(
            session,
            conversation_id,
            stage=new_state.get("stage"),
            state_patch={
                "tailored_questions": new_state.get("tailored_questions"),
                "assignment": new_state.get("assignment"),
                "logistics": new_state.get("logistics"),
                "tailored_answers": new_state.get("tailored_answers"),
                "last_asked": new_state.get("last_asked"),
                "asked_question_ids": new_state.get("asked_question_ids"),
            },
        )
    if new_state.get("stage") and new_state.get("stage") != prev_stage:
        yield {
            "type": "stage_change",
            "from": prev_stage,
            "to": new_state.get("stage"),
        }
        await publish_event(
            application_id,
            event="chat_stage_change",
            data={"from": prev_stage, "to": new_state.get("stage")},
        )

    # If we just transitioned into assignment, push the brief card before
    # the assistant's text. The chat reply will be a 2-line orientation.
    if new_state.get("stage") == "assignment" and new_state.get("assignment"):
        yield {
            "type": "brief",
            "assignment": new_state["assignment"],
        }

    # ---- Build messages + stream candidate-facing reply ----
    # ``db_msgs`` was loaded inside the same session that persisted the
    # incoming user message, so it already includes the latest turn.
    is_opening = user_message is None and not db_msgs
    system_prompt = _system_prompt(new_state, role, is_opening=is_opening)
    chat_messages = _history_messages(db_msgs, system_prompt)
    # Opening greeting: the system prompt is augmented with a "this is the
    # first turn" instruction (see ``_system_prompt``). We send a single
    # neutral user-role primer so providers that require at least one
    # non-system message accept the request. The primer is not persisted
    # and never appears in subsequent turns' history.
    if is_opening:
        chat_messages.append(
            {"role": "user", "content": "(candidate just opened the link)"}
        )

    full_text = ""
    usage: dict[str, Any] = {}
    try:
        async for ev in stream_chat(
            model=_settings.llm_model_fast,
            messages=chat_messages,
            temperature=0.4,
            max_tokens=600,
        ):
            if ev["type"] == "token":
                yield {"type": "token", "delta": ev["delta"]}
            elif ev["type"] == "done":
                full_text = ev["text"]
                usage = ev
    except LLMError as e:
        yield {"type": "error", "message": str(e)}
        return

    # Persist assistant message.
    async with session_scope() as session:
        await conversation_repo.append_message(
            session,
            conversation_id=conversation_id,
            role="assistant",
            content=full_text,
            model=usage.get("model"),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            latency_ms=usage.get("latency_ms"),
        )

    yield {
        "type": "done",
        "stage": new_state.get("stage"),
        "prompt_version": CHAT_TURN_VERSION,
    }
    await publish_event(
        application_id,
        event="chat_message",
        data={
            "role": "assistant",
            "preview": full_text[:140],
            "stage": new_state.get("stage"),
        },
    )
