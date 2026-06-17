"""Supervisor engine: perceive → reason → act.

Processes SupervisorEvents by assembling context, asking the LLM to reason
about what action to take, then validating through guardrails and recording
the action. In shadow mode, actions are proposed but not executed.

Runs as a background loop (Arq job or standalone asyncio task).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from src.config import get_settings
from src.db.base import SupervisorAction, SupervisorEvent
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.evidence import record_decision
from src.llm.client import get_llm_client
from src.services.typed_event_bus import claim_pending_events, mark_processed
from src.supervisor.autonomy import AutonomyLevel, can_execute, resolve_autonomy_level
from src.supervisor.guardrails import (
    check_auto_demotion,
    check_candidate_rate_limit,
    validate_action,
)
from src.supervisor.perception import SupervisorContext, build_context
from src.tools.registry import get_registry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM output schema
# ---------------------------------------------------------------------------


class ProposedAction(BaseModel):
    """Single action the supervisor wants to take."""
    action_type: str = Field(
        description="One of: advance_stage, send_email, send_whatsapp, send_nudge, "
        "send_notification, schedule_interview, reschedule_meeting, check_availability, "
        "evaluate_assignment, answer_question, get_candidates, pause_timer, "
        "escalate_to_hr, request_information, withdrawal, generate_offer, no_action"
    )
    target_stage: str | None = Field(
        default=None,
        description="Target pipeline stage (only for advance_stage)",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Action-specific parameters",
    )
    reasoning: str = Field(
        description="Why this action is appropriate given the evidence",
    )
    confidence: float = Field(
        ge=0, le=1,
        description="How confident you are this is the right action (0-1)",
    )
    evidence_citations: list[str] = Field(
        default_factory=list,
        description="IDs of evidence records that support this action",
    )


class SupervisorDecision(BaseModel):
    """Structured output from the supervisor LLM reasoning step."""
    situation_summary: str = Field(
        description="One-paragraph summary of the current situation",
    )
    actions: list[ProposedAction] = Field(
        default_factory=list,
        max_length=5,
        description="Ordered list of actions to take (max 5)",
    )
    requires_human: bool = Field(
        default=False,
        description="True if this situation needs human judgment",
    )
    human_reason: str | None = Field(
        default=None,
        description="Why human review is needed (if requires_human=True)",
    )


SUPERVISOR_SYSTEM_PROMPT = """You are the HR pipeline supervisor — an autonomous agent that manages the
full recruitment lifecycle. You receive events about candidates and take action.

## Core Rules
1. Every action must cite specific evidence records by ID
2. Never assume facts — only use data present in the context
3. Stage transitions must follow the pipeline DAG
4. Finals decisions (hire/reject after tech/CEO/HR interview) ALWAYS require human approval
5. When uncertain, escalate to HR rather than acting
6. Prefer the least disruptive action that resolves the situation
7. If no action is needed, return actions=[] with clear reasoning

## Available Actions
- advance_stage: move to next pipeline stage (requires target_stage)
- send_email: templated email (params: template, subject, variables)
- send_whatsapp: WhatsApp template (params: template_name, body_params)
- send_nudge: escalating reminder (params: nudge_type=gentle|urgent|final, custom_message)
- send_notification: notify HR/recruiter via Teams
- schedule_interview: initiate first-time interview scheduling (params: round=technical|ceo|hr)
- reschedule_meeting: reschedule existing meeting (params: reason, preferred_date)
- check_availability: check panel calendar before scheduling (params: round, preferred_dates)
- evaluate_assignment: smart-evaluate submitted assignment (handles Loom/GitHub/video/docs)
- answer_question: answer candidate question from role data (params: question, channel)
- get_candidates: get comparative summary of all candidates for same role
- pause_timer: pause assignment/screening deadline
- escalate_to_hr: flag for human review with evidence bundle
- withdrawal: mark candidate as withdrawn (params: reason)
- generate_offer: generate and send offer letter
- request_information: ask candidate for additional info (params: question, channel)
- no_action: explicitly do nothing (still requires reasoning)

## Domain Playbooks

### Stall Events — BE PROACTIVE
When you see a stall_detected event:
- First stall (hours_stuck < 96): send_nudge with nudge_type="gentle"
- Second stall or hours_stuck > 96: send_nudge with nudge_type="urgent"
- Third stall or hours_stuck > 168: send_nudge with nudge_type="final", then if no response → escalate_to_hr
- NEVER let a stall go unaddressed. Every stall event needs at least one action.
- Check prior_supervisor_actions: if you already nudged for this stall, escalate instead of re-nudging.

### Candidate Questions
When intent=question in a candidate message:
- Use answer_question to respond from role data. Don't escalate questions you can answer.
- Only escalate if: question is about salary negotiation, special accommodations, or legal matters.

### Assignment Submissions
When event=assignment_submitted:
- ALWAYS call evaluate_assignment immediately. Do NOT wait for human review.
- If verdict=strong_pass or pass: advance_stage to assessment_evaluated
- If verdict=incomplete: answer_question asking candidate to provide missing items
- If verdict=fail: advance_stage to rejected (only if confidence > 0.85)
- If verdict=borderline: escalate_to_hr with evaluation summary

### Interview Scheduling
When a candidate reaches a stage that needs scheduling (assessment_evaluated, technical_evaluated):
- First check_availability for the round
- Then schedule_interview if slots exist
- If no slots: escalate_to_hr with "no available slots" reason

### CTC/Compensation Analysis
- If expected_ctc > role.ctc_max_lpa * 1.2: flag early, escalate_to_hr with "CTC gap >20%"
- If expected_ctc > role.ctc_max_lpa * 1.0 but < 1.2: note in reasoning but don't block
- If candidate didn't discuss CTC in screening but expects high: flag for negotiation risk

### Notice Period Risk
- If notice_period_days > role.max_notice_days: escalate with "notice period exceeds max"
- If notice_period_days > role.max_notice_days * 0.8: note as risk factor

### Experience Gaps
- If total_experience differs by >2 years between resume and voice screen: flag contradiction
- If experience < role minimum but skills are strong: note but don't auto-reject

### Engagement Monitoring
- If engagement.risk = "high": send_nudge before candidate ghosts
- If engagement.risk = "high" AND multiple_no_answers signal: escalate_to_hr as potential withdrawal
- If engagement.overall < 0.3 AND stage > screening: consider proactive withdrawal check

### Re-engagement Sequence
For disengaging candidates (low engagement score):
1. Day 0: send_nudge gentle — "We're still interested, here's what's next"
2. Day 3: send_nudge urgent — "Your application needs attention"
3. Day 7: send_nudge final — "We'll close your application in 48 hours"
4. Day 9: withdrawal if still no response

### Multi-Candidate Awareness
When making stage advancement decisions, consider:
- Use get_candidates to see pipeline fullness
- If role has many qualified candidates, be stricter on borderline cases
- If role has few candidates, be more lenient and invest in re-engagement

### Meeting No-Shows
When event=meeting_no_show:
- Check if this is first no-show: send_nudge gentle asking to reschedule
- If second no-show (check prior_supervisor_actions): send_nudge urgent with 48h deadline
- If third no-show: escalate_to_hr recommending withdrawal
- Always check engagement score — low engagement + no-show = likely ghosting

### Meeting Completed
When event=meeting_completed:
- Wait for interview_feedback_received to evaluate
- If transcript_turns < 5: flag as potential no-show or technical failure
- Cross-reference with evidence: did candidate demonstrate skills claimed in resume?

### Voicemail Detected
When event=voicemail_detected:
- Check how many call attempts (from prior_supervisor_actions)
- First voicemail: schedule retry call in 24h, send WhatsApp "we tried to reach you"
- Second voicemail: send_nudge urgent with link to self-schedule
- Third voicemail: escalate_to_hr — candidate unreachable

### Panel Unavailable
When event=panel_unavailable:
- check_availability with expanded date range (horizon +7 days)
- If still no slots: escalate_to_hr with panel emails for manual coordination
- send_email to candidate: "We're working on finding the best time"

### Role Frozen
When event=role_frozen:
- Do NOT advance any candidates in this role
- send_email to active candidates: "Brief pause in our process, we'll be in touch soon"
- Set no_action for any pending actions on this role
- Wait for role to reopen before resuming pipeline actions

### Assignment Overdue
When event=assignment_overdue:
- If first alert: send_nudge gentle with deadline reminder
- Check engagement score: if high engagement but overdue, extend deadline via pause_timer
- If low engagement + overdue: begin re-engagement sequence
- If >168 hours overdue with no response: escalate_to_hr for potential withdrawal

### Reschedule Requests
When intent=reschedule_request in a candidate message (from any channel):
- Acknowledge receipt immediately via the same channel the candidate used
- Use reschedule_meeting with the candidate's preferred date if provided
- If no preferred date: check_availability then propose 2-3 options via answer_question
- If rescheduling fails (no slots): escalate_to_hr with "reschedule requested, no slots available"
- Update audit trail — reschedule is NOT a negative signal, do not penalize engagement score

### Inbound Call (Candidate Calls Back)
When channel=phone_inbound in a candidate message event:
- Candidate called the number that previously called them
- System auto-queues outbound callback — acknowledge this in reasoning
- If application is in voice_screen stage: the callback IS the screening, let voice pipeline handle
- If in other stage: treat as engagement signal, note in reasoning, no extra action needed unless intent is withdrawal/reschedule

### Channel Selection
Use channel_strategy from context:
- Follow primary_channel recommendation for routine messages
- Override with WhatsApp for time-sensitive nudges
- Use email for formal communications (offers, rejections, scheduling)

### Unknown / Unhandled Scenarios
If an event arrives that does not match any playbook above:
- NEVER ignore silently. NEVER return no_action without reasoning.
- Classify intent from available context (payload, stage, candidate history)
- If intent is clear and low-risk: take the appropriate action (answer_question, send_nudge, etc.)
- If intent is ambiguous or high-risk: escalate_to_hr with full context payload
- Log the unmatched event type in reasoning so patterns can be identified for new playbooks

## Context You Receive
- Event type + payload (intent, confidence, urgency)
- Application state (stage, scores, fit tier)
- Candidate info + role requirements
- Evidence records with provenance
- Active alerts + contradictions
- Channel strategy + engagement scoring
- Prior supervisor actions (conversation memory)
- Prior audit trail
"""


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------


async def _reason(
    context: SupervisorContext,
    application_id: UUID | None,
    candidate_id: UUID | None,
    experiment_overrides: dict | None = None,
) -> SupervisorDecision:
    """Ask the LLM to reason about what to do."""
    settings = get_settings()
    client = get_llm_client()

    prompt = (
        f"{context.to_prompt_context()}\n\n"
        "Based on this context, what actions should be taken? "
        "Cite evidence by ID. If uncertain, set requires_human=true."
    )

    system = SUPERVISOR_SYSTEM_PROMPT
    if experiment_overrides and experiment_overrides.get("system_prompt_suffix"):
        system = system + "\n\n" + experiment_overrides["system_prompt_suffix"]

    for attempt in range(3):
        try:
            result = await client.complete(
                prompt=prompt,
                response_model=SupervisorDecision,
                model=settings.llm_model_smart,
                trace_name="supervisor_reason",
                system=system,
                candidate_id=candidate_id,
                application_id=application_id,
                temperature=0.0,
                max_tokens=2000,
            )
            return result.parsed
        except Exception:
            if attempt == 2:
                raise
            logger.warning("supervisor LLM call failed (attempt %d/3), retrying", attempt + 1, exc_info=True)
            await asyncio.sleep(2 ** attempt)


async def process_event(event: SupervisorEvent) -> list[SupervisorAction]:
    """Process one event: perceive → reason → act."""
    settings = get_settings()
    actions_created: list[SupervisorAction] = []

    async with session_scope() as session:
        try:
            # 1. Perceive
            context = await build_context(session, event)

            # 1b. Resolve A/B experiment overrides
            experiment_overrides: dict = {}
            if event.application_id:
                try:
                    from src.services.ab_testing import resolve_experiment_overrides
                    experiment_overrides = resolve_experiment_overrides(
                        event.application_id,
                        stage=context.current_stage,
                        event_type=event.event_type,
                    )
                except Exception:
                    logger.debug("ab testing resolution failed", exc_info=True)

            # 2. Reason
            decision = await _reason(
                context,
                event.application_id,
                event.candidate_id,
                experiment_overrides=experiment_overrides or None,
            )

            # 2b. Resolve autonomy level for current stage
            role_rubric = (context.role or {}).get("scoring_rubric")
            role_id_str = (context.role or {}).get("id")
            from uuid import UUID as _UUID
            role_id = _UUID(role_id_str) if role_id_str else None
            autonomy = await resolve_autonomy_level(
                session,
                context.current_stage or "applied",
                role_id=role_id,
                role_rubric=role_rubric,
            )
            stage_autonomy = autonomy.value

            # 2c. Async guardrails: per-candidate rate limit + auto-demotion
            rate_limit_result = await check_candidate_rate_limit(event.application_id)
            demotion_result = await check_auto_demotion(event.application_id)

            if demotion_result and not demotion_result.allowed:
                stage_autonomy = "human_required"
                logger.warning(
                    "auto-demotion active for app=%s, forcing shadow",
                    event.application_id,
                )
                await log_audit(
                    session,
                    application_id=event.application_id,
                    candidate_id=event.candidate_id,
                    action="supervisor_auto_demotion",
                    actor="supervisor",
                    details={
                        "reason": demotion_result.reason,
                        "event_id": str(event.id),
                    },
                )

            # 3. Act (validate + record each proposed action)
            for i, proposed in enumerate(decision.actions):
                if proposed.action_type == "no_action":
                    continue

                # Check per-candidate rate limit first
                if not rate_limit_result.allowed:
                    action = SupervisorAction(
                        event_id=event.id,
                        application_id=event.application_id,
                        candidate_id=event.candidate_id,
                        action_type=proposed.action_type,
                        action_params={"target_stage": proposed.target_stage, **proposed.params},
                        reasoning=proposed.reasoning,
                        confidence=proposed.confidence,
                        mode=settings.supervisor_mode,
                        executed=False,
                        evidence_ids=[str(eid) for eid in proposed.evidence_citations],
                        execution_result={
                            "blocked": True,
                            "layer": rate_limit_result.layer,
                            "reason": rate_limit_result.reason,
                        },
                    )
                    session.add(action)
                    actions_created.append(action)
                    continue

                guardrail = validate_action(
                    action_type=proposed.action_type,
                    confidence=proposed.confidence,
                    current_stage=context.current_stage,
                    target_stage=proposed.target_stage,
                    supervisor_mode=settings.supervisor_mode,
                    stage_autonomy=stage_autonomy,
                    actions_taken=i,
                    max_actions=settings.supervisor_max_actions_per_event,
                )

                should_execute = (
                    guardrail.allowed
                    and can_execute(autonomy, settings.supervisor_mode)
                )

                action_params_merged = {
                    "target_stage": proposed.target_stage,
                    **proposed.params,
                }
                if experiment_overrides.get("_experiments"):
                    action_params_merged["_experiments"] = experiment_overrides["_experiments"]

                # Apply experiment confidence overrides
                effective_confidence = proposed.confidence
                if experiment_overrides.get("confidence_overrides"):
                    co = experiment_overrides["confidence_overrides"]
                    if proposed.action_type in co:
                        effective_confidence = max(proposed.confidence, co[proposed.action_type])

                action = SupervisorAction(
                    event_id=event.id,
                    application_id=event.application_id,
                    candidate_id=event.candidate_id,
                    action_type=proposed.action_type,
                    action_params=action_params_merged,
                    reasoning=proposed.reasoning,
                    confidence=effective_confidence,
                    mode=settings.supervisor_mode,
                    executed=False,
                    evidence_ids=[str(eid) for eid in proposed.evidence_citations],
                )

                if not guardrail.allowed:
                    action.execution_result = {
                        "blocked": True,
                        "layer": guardrail.layer,
                        "reason": guardrail.reason,
                    }

                if should_execute:
                    exec_result = await _execute_via_registry(proposed)
                    action.executed = exec_result.get("success", False)
                    action.execution_result = exec_result

                session.add(action)
                actions_created.append(action)

            # Record decision
            if event.application_id and event.candidate_id:
                await record_decision(
                    session,
                    application_id=event.application_id,
                    candidate_id=event.candidate_id,
                    decision_type=f"supervisor_{event.event_type}",
                    outcome=(
                        "executed" if any(a.executed for a in actions_created)
                        else "proposed" if actions_created
                        else "no_action"
                    ),
                    outcome_value={
                        "situation": decision.situation_summary[:500],
                        "action_count": len(actions_created),
                        "requires_human": decision.requires_human,
                        "human_reason": decision.human_reason,
                    },
                )

            await log_audit(
                session,
                application_id=event.application_id,
                candidate_id=event.candidate_id,
                action=f"supervisor_processed_{event.event_type}",
                actor="supervisor",
                details={
                    "event_id": str(event.id),
                    "mode": settings.supervisor_mode,
                    "actions_proposed": len(actions_created),
                    "actions_executed": sum(1 for a in actions_created if a.executed),
                    "requires_human": decision.requires_human,
                },
            )

            await mark_processed(session, event.id)

        except Exception as exc:
            logger.exception("supervisor failed on event %s", event.id)
            await mark_processed(session, event.id, error=str(exc)[:1000])
            raise

    return actions_created


async def _execute_via_registry(proposed: ProposedAction) -> dict[str, Any]:
    """Execute a proposed action through the unified tool registry."""
    registry = get_registry()

    # Map supervisor action types to registry tool names
    tool_name = proposed.action_type
    args = dict(proposed.params)

    if proposed.action_type == "advance_stage":
        args["target_stage"] = proposed.target_stage
    elif proposed.action_type == "send_email":
        tool_name = "send_candidate_email"
    elif proposed.action_type == "send_whatsapp":
        tool_name = "send_candidate_whatsapp"
    elif proposed.action_type == "send_nudge":
        tool_name = "send_nudge"
    elif proposed.action_type == "send_notification":
        tool_name = "escalate_to_hr"
        args.setdefault("reason", proposed.reasoning or "")
        args.setdefault("severity", "info")
    elif proposed.action_type == "schedule_interview":
        tool_name = "schedule_interview"
    elif proposed.action_type == "reschedule_meeting":
        args.setdefault("reason", proposed.reasoning or "")
    elif proposed.action_type == "check_availability":
        tool_name = "check_panel_availability"
    elif proposed.action_type == "evaluate_assignment":
        tool_name = "evaluate_assignment"
    elif proposed.action_type == "answer_question":
        tool_name = "answer_candidate_question"
    elif proposed.action_type == "pause_timer":
        tool_name = "pause_assignment_timer"
    elif proposed.action_type == "request_information":
        tool_name = "answer_candidate_question"
        args.setdefault("question", proposed.reasoning or "requesting additional information")
    elif proposed.action_type == "get_candidates":
        tool_name = "get_role_candidates_summary"
        if not args.get("role_id") and proposed.params.get("role_id"):
            args["role_id"] = proposed.params["role_id"]
    elif proposed.action_type == "withdrawal":
        tool_name = "mark_withdrawal"
        args.setdefault("reason", proposed.reasoning or "")
    elif proposed.action_type == "generate_offer":
        tool_name = "generate_and_send_offer"

    try:
        result = await registry.call(tool_name, args, actor="supervisor")
        return result
    except Exception as exc:
        logger.exception("tool execution failed: %s", tool_name)
        return {"success": False, "error": str(exc)[:500]}


# ---------------------------------------------------------------------------
# Background loop
# ---------------------------------------------------------------------------


async def run_supervisor_loop(worker_id: str = "supervisor-main") -> None:
    """Background loop that claims and processes supervisor events.

    Intended to run as an asyncio task alongside the Arq worker or
    as a standalone process. Polls every 5 seconds when idle.
    """
    settings = get_settings()
    if not settings.enable_supervisor:
        logger.info("supervisor disabled, loop exiting")
        return

    logger.info("supervisor loop starting (mode=%s)", settings.supervisor_mode)

    while True:
        try:
            async with session_scope() as session:
                events = await claim_pending_events(session, worker_id, batch_size=5)

            for event in events:
                try:
                    actions = await process_event(event)
                    logger.info(
                        "processed event %s: %d actions",
                        event.id, len(actions),
                    )
                except Exception:
                    logger.exception("failed to process event %s", event.id)

        except asyncio.CancelledError:
            logger.info("supervisor loop cancelled")
            return
        except Exception:
            logger.exception("supervisor loop error")

        await asyncio.sleep(5)
