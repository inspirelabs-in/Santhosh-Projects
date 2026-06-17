"""LangGraph state machine for the V2 chat agent.

Per turn, the graph runs:

    extract -> persist_screening -> route -> [maybe_gen_assignment] -> reply

``reply`` is the only node that streams to the client. All others run
synchronously at sub-second latency on gpt-4o-mini.

The graph is invoked once per inbound user message. The runner orchestrates
streaming; the graph returns AgentState updates + the system+context block
the runner uses to produce the streamed reply.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from langgraph.graph import END, START, StateGraph

from src.agent.state import AgentState
from src.config import get_settings
from src.db.base import Application, Role
from src.db.connection import session_scope
from src.db.repositories import (
    assignment as assignment_repo,
    conversation as conversation_repo,
    screening_answer as screening_repo,
)
from src.db.repositories.audit import log_audit
from src.db.repositories.policy import resolve_policy
from src.db.repositories.v1_application import set_stage
from src.models.v1 import PipelineStage

logger = logging.getLogger(__name__)
_settings = get_settings()


def _msg_to_dict(m) -> dict[str, str]:
    """Normalise a LangChain message object or plain dict to {"role", "content"}."""
    if isinstance(m, dict):
        return m
    role_map = {"human": "user", "ai": "assistant", "system": "system"}
    return {"role": role_map.get(getattr(m, "type", ""), "user"), "content": getattr(m, "content", "")}


# ---------------------------------------------------------------------------
# Node: extract
# ---------------------------------------------------------------------------


async def node_extract(state: AgentState) -> dict[str, Any]:
    """Pull structured fields from the latest user message.

    No-ops on the first turn (no user message yet).
    """
    from src.agent.generators import extract_turn  # lazy: avoids LLM stack at import

    raw_msgs = state.get("messages") or []
    msgs = [_msg_to_dict(m) for m in raw_msgs]
    last_user = next(
        (m for m in reversed(msgs) if m.get("role") == "user" and m.get("content")),
        None,
    )
    if not last_user:
        return {"last_node": "extract", "last_asked": _pick_first_pending(state)}

    pending_qs = [
        q
        for q in (state.get("tailored_questions") or [])
        if q["id"] not in (state.get("tailored_answers") or {})
    ]
    already = {
        **(state.get("logistics") or {}),
        **{f"tailored_a{i+1}": v for i, v in enumerate(_ordered_tailored_answers(state))},
    }
    extracted = await extract_turn(
        candidate_message=last_user["content"],
        already_captured=already,
        pending_questions=pending_qs,
        recent_history=msgs,
        application_id=UUID(state["application_id"]),
        candidate_id=UUID(state["candidate_id"]),
    )

    # Patch state with anything new.
    logistics = dict(state.get("logistics") or {})
    for key in ("current_ctc_lpa", "expected_ctc_lpa", "notice_period_days", "willing_to_relocate"):
        v = getattr(extracted, key)
        if v is not None and logistics.get(key) is None:
            logistics[key] = v

    tailored_answers = dict(state.get("tailored_answers") or {})
    if extracted.tailored_a1 and "q1" not in tailored_answers:
        tailored_answers["q1"] = extracted.tailored_a1
    if extracted.tailored_a2 and "q2" not in tailored_answers:
        tailored_answers["q2"] = extracted.tailored_a2

    # Persist incrementally so a crash mid-conversation doesn't lose data.
    async with session_scope() as session:
        patch: dict[str, Any] = {}
        if extracted.tailored_a1:
            patch["tailored_a1"] = extracted.tailored_a1
            tq1 = next((q for q in state.get("tailored_questions") or [] if q["id"] == "q1"), None)
            if tq1:
                patch["tailored_q1"] = tq1["question"]
        if extracted.tailored_a2:
            patch["tailored_a2"] = extracted.tailored_a2
            tq2 = next((q for q in state.get("tailored_questions") or [] if q["id"] == "q2"), None)
            if tq2:
                patch["tailored_q2"] = tq2["question"]
        for k in ("current_ctc_lpa", "expected_ctc_lpa", "notice_period_days", "willing_to_relocate"):
            v = logistics.get(k)
            if v is not None:
                patch[k] = v
        if patch:
            await screening_repo.upsert(
                session, UUID(state["application_id"]), **patch
            )

    return {
        "logistics": logistics,
        "tailored_answers": tailored_answers,
        "last_asked": extracted.next_question_to_ask,
        "last_node": "extract",
    }


# ---------------------------------------------------------------------------
# Node: route -- decide stage transitions
# ---------------------------------------------------------------------------


def _ordered_tailored_answers(state: AgentState) -> list[str]:
    answers = state.get("tailored_answers") or {}
    return [answers[k] for k in ("q1", "q2") if k in answers]


def _pick_first_pending(state: AgentState) -> str:
    """Order: tailored q1, q2, then logistics. Returns 'none' when done."""
    answers = state.get("tailored_answers") or {}
    questions = state.get("tailored_questions") or []
    question_ids = {q["id"] for q in questions}
    if "q1" in question_ids and "q1" not in answers:
        return "q1"
    if "q2" in question_ids and "q2" not in answers:
        return "q2"
    log = state.get("logistics") or {}
    if log.get("current_ctc_lpa") is None:
        return "ctc_current"
    if log.get("expected_ctc_lpa") is None:
        return "ctc_expected"
    if log.get("notice_period_days") is None:
        return "notice"
    if log.get("willing_to_relocate") is None:
        return "relocate"
    return "none"


def _screening_complete(state: AgentState) -> bool:
    return _pick_first_pending(state) == "none"


async def _evaluate_screening(
    *,
    logistics: dict[str, Any],
    role: Role,
) -> dict[str, Any]:
    """Heuristic knock-out evaluation with policy-driven thresholds."""
    async with session_scope() as session:
        ctc_multiplier, _ = await resolve_policy(
            session, "ctc_overshoot_multiplier", role.id, fallback=1.15
        )
        knock_score, _ = await resolve_policy(
            session, "screening_knock_score", role.id, fallback=50
        )
        pass_score, _ = await resolve_policy(
            session, "screening_pass_score", role.id, fallback=75
        )

    from src.services.knockout import check_hard_knockouts

    expected = logistics.get("expected_ctc_lpa")
    notice = logistics.get("notice_period_days")
    relocate = logistics.get("willing_to_relocate")

    ko_result = check_hard_knockouts(
        expected_ctc=float(expected) if expected is not None else None,
        notice_days=int(notice) if notice is not None else None,
        willing_to_relocate=relocate,
        role_ctc_max=float(role.ctc_max_lpa) if role.ctc_max_lpa is not None else None,
        role_max_notice_days=int(role.max_notice_days) if role.max_notice_days is not None else None,
        role_remote_policy=role.remote_policy,
        ctc_multiplier=ctc_multiplier,
    )
    reasons = ko_result.reasons
    knock = ko_result.triggered
    score = knock_score if knock else pass_score
    return {
        "evaluation": {
            "method": "heuristic_v1",
            "reasons": reasons,
            "logistics_snapshot": logistics,
        },
        "composite_score": score,
        "knock_out_triggered": knock,
        "knock_out_reason": "; ".join(reasons)[:255] or None,
    }


async def _advance_pipeline_to_assignment(
    application_id: UUID,
    *,
    logistics: dict[str, Any],
) -> dict[str, Any] | None:
    """Run on the chat-side transition into assignment.

    Walks the V1 pipeline in one transaction:
      SCREENING_SENT -> SCREENING_SUBMITTED -> SCREENING_EVALUATED ->
      (ASSIGNMENT_SENT | REJECTED)

    Returns the evaluation dict so callers can persist + display it.
    """
    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            return None
        role = await session.get(Role, app.role_id) if app.role_id else None
        if role is None:
            return None

        eval_payload = await _evaluate_screening(logistics=logistics, role=role)
        stages_to_walk = [
            PipelineStage.SCREENING_SENT,
            PipelineStage.SCREENING_SUBMITTED,
            PipelineStage.SCREENING_EVALUATED,
        ]
        for s in stages_to_walk:
            try:
                await set_stage(session, application_id, s)
            except Exception:  # noqa: BLE001
                pass

        await screening_repo.upsert(
            session,
            application_id,
            evaluation=eval_payload["evaluation"],
            composite_score=eval_payload["composite_score"],
            knock_out_triggered=eval_payload["knock_out_triggered"],
            knock_out_reason=eval_payload["knock_out_reason"],
        )
        await screening_repo.mark_submitted(session, application_id)

        if eval_payload["knock_out_triggered"]:
            await set_stage(
                session, application_id, PipelineStage.REJECTED, force=True
            )
        else:
            await set_stage(session, application_id, PipelineStage.ASSIGNMENT_SENT)

        await log_audit(
            session,
            application_id=application_id,
            candidate_id=app.candidate_id,
            action="chat_screening_completed",
            actor="agent",
            details=eval_payload["evaluation"]
            | {"score": eval_payload["composite_score"], "knock_out": eval_payload["knock_out_triggered"]},
        )
        # Update applications.screening_score for dashboard display.
        app.screening_score = eval_payload["composite_score"]
        return eval_payload


async def node_route(state: AgentState) -> dict[str, Any]:
    stage = state.get("stage") or "intake"
    next_stage = stage
    transitioned_to_assignment = False
    if stage in ("intake", "screening"):
        if _screening_complete(state):
            next_stage = "assignment"
            transitioned_to_assignment = True
        else:
            next_stage = "screening"
    elif stage == "assignment":
        # Stays in assignment until candidate signals submission. The
        # submission upload endpoint flips stage to 'submitted' directly.
        next_stage = "assignment"

    # Fire V1 PipelineStage transitions ONCE on the screening->assignment
    # crossover so the recruiter dashboard advances and screening_score
    # lands. Knock-outs flip stage to REJECTED inside the helper.
    eval_result: dict[str, Any] | None = None
    if transitioned_to_assignment:
        eval_result = await _advance_pipeline_to_assignment(
            UUID(state["application_id"]),
            logistics=state.get("logistics") or {},
        )
        if eval_result and eval_result["knock_out_triggered"]:
            # Don't proceed to assignment generation when knocked out.
            next_stage = "rejected"

    last_asked = _pick_first_pending(state) if next_stage in ("intake", "screening") else state.get("last_asked")

    return {
        "stage": next_stage,
        "last_asked": last_asked,
        "last_node": "route",
    }


# ---------------------------------------------------------------------------
# Node: maybe_gen_assignment -- generate brief on first transition only
# ---------------------------------------------------------------------------


async def node_maybe_gen_assignment(state: AgentState) -> dict[str, Any]:
    from src.agent.generators import gen_assignment  # lazy

    if state.get("stage") != "assignment":
        return {"last_node": "maybe_gen_assignment"}
    if state.get("assignment"):
        return {"last_node": "maybe_gen_assignment"}

    application_id = UUID(state["application_id"])
    candidate_id = UUID(state["candidate_id"])

    # Pull JD + resume + screening answers.
    from src.db.base import CandidateProfileRow
    from sqlalchemy import select as sa_select

    async with session_scope() as session:
        app = await session.get(Application, application_id)
        if app is None:
            return {"error": "application_not_found", "last_node": "maybe_gen_assignment"}
        role = await session.get(Role, app.role_id) if app.role_id else None
        cand_profile_row = (
            await session.execute(
                sa_select(CandidateProfileRow)
                .where(CandidateProfileRow.candidate_id == app.candidate_id)
                .order_by(CandidateProfileRow.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        screening_row = await screening_repo.get(session, application_id)

    candidate_profile = (cand_profile_row.parsed_data if cand_profile_row else {}) or {}
    screening_payload = (
        {
            "tailored_q1": screening_row.tailored_q1 if screening_row else None,
            "tailored_a1": screening_row.tailored_a1 if screening_row else None,
            "tailored_q2": screening_row.tailored_q2 if screening_row else None,
            "tailored_a2": screening_row.tailored_a2 if screening_row else None,
            "current_ctc_lpa": float(screening_row.current_ctc_lpa) if screening_row and screening_row.current_ctc_lpa is not None else None,
            "expected_ctc_lpa": float(screening_row.expected_ctc_lpa) if screening_row and screening_row.expected_ctc_lpa is not None else None,
            "notice_period_days": screening_row.notice_period_days if screening_row else None,
            "willing_to_relocate": screening_row.willing_to_relocate if screening_row else None,
        }
        if screening_row
        else None
    )

    deadline_days = (role.assignment_deadline_days if role else 7) or 7
    time_budget_hours = max(2, min(8, deadline_days * 2))

    # Reuse pre-warmed assignment if present (set by prewarm()).
    if not state.get("assignment"):
        async with session_scope() as session:
            conv = await conversation_repo.get_conversation_by_id(
                session, UUID(state["conversation_id"])
            )
            if conv and conv.prewarmed_assignment:
                brief = conv.prewarmed_assignment
            else:
                brief_obj = await gen_assignment(
                    role_title=role.title if role else "the role",
                    jd_text=role.jd_text if role else "",
                    candidate_profile=candidate_profile,
                    screening_answers=screening_payload,
                    time_budget_hours=time_budget_hours,
                    deadline_days=deadline_days,
                    application_id=application_id,
                    candidate_id=candidate_id,
                )
                brief = brief_obj.model_dump()

        async with session_scope() as session:
            await assignment_repo.save_generated(
                session,
                application_id,
                brief_md=brief["brief_md"],
                problems=brief["problems"],
                submission_format=brief["submission_format"],
                evaluation_rubric=brief.get("evaluation_rubric"),
            )

        return {"assignment": brief, "last_node": "maybe_gen_assignment"}

    return {"last_node": "maybe_gen_assignment"}


# ---------------------------------------------------------------------------
# Graph wiring
# ---------------------------------------------------------------------------


def build_graph():
    """Compile the LangGraph state machine. Reused per turn (cheap)."""
    g = StateGraph(AgentState)
    g.add_node("extract", node_extract)
    g.add_node("route", node_route)
    g.add_node("maybe_gen_assignment", node_maybe_gen_assignment)

    g.add_edge(START, "extract")
    g.add_edge("extract", "route")
    g.add_edge("route", "maybe_gen_assignment")
    g.add_edge("maybe_gen_assignment", END)
    return g.compile()


_compiled = None


def get_graph():
    global _compiled
    if _compiled is None:
        _compiled = build_graph()
    return _compiled
