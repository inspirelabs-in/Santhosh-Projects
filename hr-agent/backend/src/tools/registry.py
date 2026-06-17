"""Unified tool registry for recruiter agent + supervisor + pipeline activities.

Single source of truth for every action the system can take. Each tool has:
- A handler function (async callable)
- An OpenAI-format schema (for LLM tool calling)
- Access control (which actors can invoke it)

The recruiter agent's existing TOOLS dict and schemas are imported and
registered automatically. Pipeline activities are wrapped as tools that
the supervisor can invoke.

Usage:
    from src.tools.registry import get_registry
    registry = get_registry()
    result = await registry.call("list_candidates", {"stage": "applied"}, actor="recruiter")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


@dataclass
class ToolDefinition:
    name: str
    description: str
    handler: Callable[..., Coroutine[Any, Any, dict[str, Any]]]
    parameters_schema: dict[str, Any] = field(default_factory=dict)
    allowed_actors: frozenset[str] = field(
        default_factory=lambda: frozenset({"recruiter", "supervisor"})
    )
    category: str = "general"
    requires_application_id: bool = False
    is_write: bool = False

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema or {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_for_actor(self, actor: str) -> list[ToolDefinition]:
        return [t for t in self._tools.values() if actor in t.allowed_actors]

    def openai_schemas_for_actor(self, actor: str) -> list[dict]:
        return [t.to_openai_schema() for t in self.list_for_actor(actor)]

    async def call(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        *,
        actor: str = "supervisor",
    ) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            return {"error": f"unknown_tool: {name}"}
        if actor not in tool.allowed_actors:
            return {"error": f"actor '{actor}' not allowed for tool '{name}'"}
        try:
            return await tool.handler(**(args or {}))
        except TypeError as e:
            return {"error": f"bad_args: {e}"}
        except Exception as e:
            logger.exception("tool %s crashed", name)
            return {"error": f"tool_error: {e}"}

    @property
    def tool_count(self) -> int:
        return len(self._tools)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = _build_registry()
    return _registry


def _build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    _register_recruiter_tools(registry)
    _register_supervisor_tools(registry)
    return registry


# ---------------------------------------------------------------------------
# Import recruiter agent tools
# ---------------------------------------------------------------------------

_READ_TOOLS = frozenset({
    "list_candidates", "get_candidate", "list_roles", "pipeline_metrics",
    "stuck_applications", "audit_tail", "search_candidates", "get_journey_report",
    "metrics_period", "list_meetings", "list_voice_calls", "read_audit",
    "recall", "smart_defaults_for_role", "generate_assignment_for_role",
    "draft_linkedin_post",
})


def _register_recruiter_tools(registry: ToolRegistry) -> None:
    from src.recruiter_agent.schemas import RECRUITER_TOOLS
    from src.recruiter_agent.tools import TOOLS

    schema_by_name = {
        t["function"]["name"]: t["function"]
        for t in RECRUITER_TOOLS
    }

    for name, handler in TOOLS.items():
        schema = schema_by_name.get(name, {})
        is_write = name not in _READ_TOOLS

        # Supervisor can read but only invoke a subset of writes
        supervisor_allowed = not is_write or name in {
            "send_custom_email", "trigger_chat_invite", "override_stage",
            "schedule_interview", "propose_slots", "add_candidate_note",
        }

        registry.register(ToolDefinition(
            name=name,
            description=schema.get("description", name),
            handler=handler,
            parameters_schema=schema.get("parameters", {}),
            allowed_actors=frozenset(
                {"recruiter", "supervisor"} if supervisor_allowed
                else {"recruiter"}
            ),
            category="recruiter",
            is_write=is_write,
        ))


# ---------------------------------------------------------------------------
# Supervisor-specific tools (pipeline activities as tools)
# ---------------------------------------------------------------------------


def _register_supervisor_tools(registry: ToolRegistry) -> None:
    """Register pipeline-level actions the supervisor can invoke."""

    async def advance_stage(*, application_id: str, target_stage: str) -> dict[str, Any]:
        from uuid import UUID
        from src.db.connection import session_scope
        from src.db.repositories.v1_application import set_stage
        from src.models.v1 import PipelineStage
        async with session_scope() as session:
            await set_stage(session, UUID(application_id), PipelineStage(target_stage))
        return {"success": True, "new_stage": target_stage}

    async def escalate_to_hr(
        *, application_id: str, reason: str, severity: str = "warning"
    ) -> dict[str, Any]:
        from uuid import UUID
        from src.channels import teams as teams_channel
        from src.db.base import PipelineAlert
        from src.db.connection import session_scope
        async with session_scope() as session:
            alert = PipelineAlert(
                application_id=UUID(application_id),
                alert_type="supervisor_escalation",
                details={"reason": reason, "severity": severity},
            )
            session.add(alert)
        await teams_channel.notify_hr(
            title="Supervisor escalation",
            text=reason,
            fields={"application_id": application_id, "severity": severity},
        )
        return {"success": True, "alert_created": True}

    async def send_candidate_email(
        *, application_id: str, template: str, subject: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from uuid import UUID
        from src.channels.email import send_email
        from src.db.base import Application, Candidate, Role
        from src.db.connection import session_scope
        async with session_scope() as session:
            app = await session.get(Application, UUID(application_id))
            if not app:
                return {"success": False, "error": "application_not_found"}
            candidate = await session.get(Candidate, app.candidate_id)
            if not candidate or not candidate.email:
                return {"success": False, "error": "candidate_email_missing"}
            role = await session.get(Role, app.role_id) if app.role_id else None
            tmpl_vars = {
                "candidate_name": candidate.name or "Candidate",
                "role_title": role.title if role else "the role",
                "company_name": "GrabOn",
                **(variables or {}),
            }
        result = await send_email(
            to=candidate.email,
            template=template,
            variables=tmpl_vars,
            application_id=application_id,
            candidate_id=str(app.candidate_id),
            idempotency_key=f"supervisor:{application_id}:{template}",
        )
        return {"success": result.success, "message_id": result.message_id, "error": result.error}

    async def pause_assignment_timer(*, application_id: str, reason: str) -> dict[str, Any]:
        from uuid import UUID
        from src.db.connection import session_scope
        from src.db.repositories.audit import log_audit
        async with session_scope() as session:
            await log_audit(
                session,
                application_id=UUID(application_id),
                action="assignment_timer_paused",
                actor="supervisor",
                details={"reason": reason},
            )
        return {"success": True, "note": "timer pause recorded"}

    async def get_evidence_chain(*, application_id: str) -> dict[str, Any]:
        from uuid import UUID
        from src.db.connection import session_scope
        from src.db.repositories.evidence import (
            get_decisions_for_application,
            get_evidence_for_application,
        )
        async with session_scope() as session:
            evidence = await get_evidence_for_application(session, UUID(application_id))
            decisions = await get_decisions_for_application(session, UUID(application_id))
        return {
            "evidence_count": len(evidence),
            "decision_count": len(decisions),
            "evidence": [
                {"fact_key": e.fact_key, "fact_value": e.fact_value, "source_stage": e.source_stage}
                for e in evidence[:30]
            ],
            "decisions": [
                {"type": d.decision_type, "outcome": d.outcome}
                for d in decisions[:20]
            ],
        }

    async def send_candidate_whatsapp(
        *, application_id: str, template_name: str,
        body_params: list[str] | None = None,
    ) -> dict[str, Any]:
        from uuid import UUID
        from src.channels.whatsapp import send_template
        from src.db.base import Application, Candidate
        from src.db.connection import session_scope
        async with session_scope() as session:
            app = await session.get(Application, UUID(application_id))
            if not app:
                return {"success": False, "error": "application_not_found"}
            candidate = await session.get(Candidate, app.candidate_id)
            if not candidate or not candidate.phone:
                return {"success": False, "error": "candidate_phone_missing"}
        result = await send_template(
            to_phone=candidate.phone,
            template_name=template_name,
            body_params=body_params,
        )
        return {"success": result.success, "message_id": result.message_id, "error": result.error}

    async def reschedule_meeting(
        *, application_id: str, reason: str,
        preferred_date: str | None = None,
    ) -> dict[str, Any]:
        from uuid import UUID
        from src.db.connection import session_scope
        from src.db.repositories.audit import log_audit
        from src.services.queue import enqueue
        app_id = UUID(application_id)
        queued = await enqueue(
            "schedule_meeting_reattempt",
            application_id,
            reason=reason,
            preferred_date=preferred_date,
        )
        async with session_scope() as session:
            await log_audit(
                session,
                application_id=app_id,
                action="supervisor_reschedule_requested",
                actor="supervisor",
                details={
                    "reason": reason,
                    "preferred_date": preferred_date,
                    "queued": queued,
                },
            )
        return {"success": True, "queued": queued, "reason": reason}

    async def mark_withdrawal(
        *, application_id: str, reason: str,
    ) -> dict[str, Any]:
        from uuid import UUID
        from src.channels import teams as teams_channel
        from src.db.base import Application
        from src.db.connection import session_scope
        from src.db.repositories.audit import log_audit
        from src.db.repositories.v1_application import set_stage
        from src.models.v1 import PipelineStage
        app_id = UUID(application_id)
        async with session_scope() as session:
            app = await session.get(Application, app_id)
            await set_stage(session, app_id, PipelineStage.REJECTED, force=True)
            await log_audit(
                session,
                application_id=app_id,
                candidate_id=app.candidate_id if app else None,
                action="candidate_withdrawal",
                actor="supervisor",
                details={"reason": reason, "source": "auto_detected"},
            )
        await teams_channel.notify_hr(
            title="Candidate withdrawal",
            text=f"Candidate withdrew: {reason}",
            fields={"application_id": application_id},
        )
        return {"success": True, "new_stage": "rejected", "reason": reason}

    async def generate_and_send_offer(
        *, application_id: str, offer_note: str | None = None,
    ) -> dict[str, Any]:
        from uuid import UUID
        from src.activities.offer import generate_offer
        return await generate_offer(
            application_id=UUID(application_id),
            offer_note=offer_note,
            approved_by="supervisor",
        )

    # --- Gap 1: Proactive nudge tool ---
    async def send_nudge(
        *, application_id: str, nudge_type: str = "gentle",
        custom_message: str | None = None,
    ) -> dict[str, Any]:
        """Send escalating nudge: gentle → urgent → final. Picks channel from strategy."""
        from uuid import UUID
        from src.channels.email import send_email
        from src.db.base import Application, Candidate, Role
        from src.db.connection import session_scope
        from src.db.repositories.audit import log_audit

        templates = {
            "gentle": {"template": "screening_reminder", "subject": "Friendly reminder about your application"},
            "urgent": {"template": "nudge", "subject": "Action needed — your application is waiting"},
            "final": {"template": "nudge", "subject": "Last chance — application closing soon"},
        }
        tmpl = templates.get(nudge_type, templates["gentle"])

        async with session_scope() as session:
            app = await session.get(Application, UUID(application_id))
            if not app:
                return {"success": False, "error": "application_not_found"}
            candidate = await session.get(Candidate, app.candidate_id)
            if not candidate or not candidate.email:
                return {"success": False, "error": "no_email"}
            role = await session.get(Role, app.role_id) if app.role_id else None

            variables = {
                "candidate_name": candidate.name or "Candidate",
                "role_title": role.title if role else "the role",
                "urgency": nudge_type,
                "application_id": application_id,
            }
            if custom_message:
                variables["custom_message"] = custom_message

            result = await send_email(
                to=candidate.email,
                template=tmpl["template"],
                subject=tmpl["subject"],
                variables=variables,
                idempotency_key=f"nudge:{application_id}:{nudge_type}",
            )
            await log_audit(
                session,
                application_id=UUID(application_id),
                candidate_id=app.candidate_id,
                action=f"supervisor_nudge_{nudge_type}",
                actor="supervisor",
                details={"nudge_type": nudge_type, "channel": "email"},
            )
        return {"success": True, "nudge_type": nudge_type, "channel": "email"}

    # --- Gap 3: Answer candidate questions ---
    async def answer_candidate_question(
        *, application_id: str, question: str, channel: str = "email",
    ) -> dict[str, Any]:
        """Answer candidate question using role data + evidence + LLM."""
        from uuid import UUID
        from src.channels.email import send_email
        from src.db.base import Application, Candidate, EvidenceRecord, Role
        from src.db.connection import session_scope
        from src.db.repositories.audit import log_audit
        from src.llm.client import get_llm_client
        from sqlalchemy import select

        async with session_scope() as session:
            app = await session.get(Application, UUID(application_id))
            if not app:
                return {"success": False, "error": "application_not_found"}
            candidate = await session.get(Candidate, app.candidate_id)
            role = await session.get(Role, app.role_id) if app.role_id else None

            evidence = (await session.execute(
                select(EvidenceRecord)
                .where(EvidenceRecord.application_id == UUID(application_id))
                .where(EvidenceRecord.superseded_by_id.is_(None))
                .limit(20)
            )).scalars().all()

        role_context = ""
        if role:
            role_context = (
                f"Role: {role.title}\nLocation: {role.location}\n"
                f"Remote: {role.remote_policy}\nCTC: {role.ctc_min_lpa}-{role.ctc_max_lpa} LPA\n"
                f"Notice: max {role.max_notice_days} days\n"
                f"JD: {(role.jd_text or '')[:2000]}\n"
            )
        evidence_context = "\n".join(
            f"- {e.fact_key}: {e.fact_value}" for e in evidence[:15]
        )

        client = get_llm_client()
        prompt = (
            f"A candidate asked: \"{question}\"\n\n"
            f"Role information:\n{role_context}\n"
            f"Known facts about this candidate:\n{evidence_context}\n\n"
            f"Write a helpful, professional, and accurate response. "
            f"Only answer with information available above. If unsure, say you'll check with the hiring team. "
            f"Keep under 150 words. Do NOT make up facts."
        )
        result = await client.complete(
            prompt=prompt,
            model=client.smart,
            trace_name="answer_candidate_question",
            system="You are an HR assistant answering candidate questions accurately and warmly.",
            max_tokens=500,
        )
        answer_text = result.text

        if channel == "email" and candidate and candidate.email:
            await send_email(
                to=candidate.email,
                subject=f"Re: Your question about the {role.title if role else 'position'}",
                body=answer_text,
                idempotency_key=f"faq:{application_id}:{hash(question) % 10000}",
            )

        from src.db.connection import session_scope as _ss
        async with _ss() as session:
            await log_audit(
                session,
                application_id=UUID(application_id),
                candidate_id=app.candidate_id,
                action="candidate_question_answered",
                actor="supervisor",
                details={"question": question[:500], "channel": channel},
            )
        return {"success": True, "answer": answer_text[:500], "channel": channel}

    # --- Gap 4: Initiate interview scheduling ---
    async def schedule_interview(
        *, application_id: str, round: str = "technical",
    ) -> dict[str, Any]:
        """Initiate first-time interview scheduling for a candidate."""
        from uuid import UUID
        from src.activities.v1_schedule_meeting import schedule_meeting
        try:
            result = await schedule_meeting(
                application_id=UUID(application_id),
                round=round,
            )
            return {"success": True, "round": round, "result": str(result)[:200]}
        except Exception as exc:
            return {"success": False, "error": str(exc)[:300]}

    # --- Gap 5: Multi-candidate context ---
    async def get_role_candidates_summary(
        *, role_id: str, limit: int = 10,
    ) -> dict[str, Any]:
        """Get comparative summary of all active candidates for a role."""
        from uuid import UUID
        from sqlalchemy import select
        from src.db.base import Application, Candidate
        from src.db.connection import session_scope
        async with session_scope() as session:
            apps = (await session.execute(
                select(Application)
                .where(Application.role_id == UUID(role_id))
                .where(Application.status == "active")
                .order_by(Application.fit_score.desc().nullslast())
                .limit(limit)
            )).scalars().all()

            summaries = []
            for app in apps:
                cand = await session.get(Candidate, app.candidate_id)
                summaries.append({
                    "application_id": str(app.id),
                    "candidate_name": cand.name if cand else "Unknown",
                    "stage": app.current_stage,
                    "fit_score": app.fit_score,
                    "fit_tier": app.fit_tier,
                    "screening_score": app.screening_score,
                })
        return {
            "success": True,
            "role_id": role_id,
            "candidate_count": len(summaries),
            "candidates": summaries,
        }

    # --- Gap 8: Check calendar availability ---
    async def check_panel_availability(
        *, application_id: str, round: str = "technical",
        preferred_dates: list[str] | None = None,
    ) -> dict[str, Any]:
        """Check interview panel availability via MS Graph before proposing times."""
        from uuid import UUID
        from src.db.base import Application, Role
        from src.db.connection import session_scope
        async with session_scope() as session:
            app = await session.get(Application, UUID(application_id))
            if not app or not app.role_id:
                return {"success": False, "error": "application_or_role_not_found"}
            role = await session.get(Role, app.role_id)
            if not role:
                return {"success": False, "error": "role_not_found"}

        interview_config = (role.scoring_rubric or {}).get("interview_config", {})
        round_cfg = interview_config.get(round, {})
        panel_emails = round_cfg.get("panel_emails", [])

        if not panel_emails:
            from sqlalchemy import select
            from src.db.base import PanelMember
            async with session_scope() as session:
                members = (await session.execute(
                    select(PanelMember)
                    .where(PanelMember.role_type == round)
                    .where(PanelMember.is_active.is_(True))
                )).scalars().all()
                panel_emails = [m.email for m in members]

        if not panel_emails:
            return {"success": False, "error": "no_panel_configured", "round": round}

        try:
            from src.services.ms_graph import get_free_busy
            slots = await get_free_busy(
                attendees=panel_emails,
                preferred_dates=preferred_dates,
            )
            return {
                "success": True,
                "panel_emails": panel_emails,
                "available_slots": slots[:10],
                "round": round,
            }
        except Exception as exc:
            return {
                "success": False,
                "error": f"graph_api_failed: {str(exc)[:200]}",
                "panel_emails": panel_emails,
            }

    # --- Gap 2: Evaluate assignment submission ---
    async def evaluate_assignment(
        *, application_id: str,
    ) -> dict[str, Any]:
        """Smart assignment evaluator — handles Loom, GitHub, video, docs, incomplete."""
        from uuid import UUID
        from src.db.base import Application, Role
        from src.db.connection import session_scope
        from src.db.repositories.audit import log_audit
        from src.db.repositories.evidence import record_evidence_batch_verified
        from src.llm.client import get_llm_client

        async with session_scope() as session:
            app = await session.get(Application, UUID(application_id))
            if not app:
                return {"success": False, "error": "application_not_found"}
            role = await session.get(Role, app.role_id) if app.role_id else None
            submission = app.assignment_submission or {}

        if not submission:
            return {"success": False, "error": "no_submission_found"}

        # Detect submission type from content
        submission_text = str(submission.get("content", "") or submission.get("text", ""))
        submission_url = submission.get("url", "") or ""
        attachments = submission.get("attachments", []) or []

        submission_types: list[str] = []
        extracted_content: list[str] = []

        # URL detection
        import re
        urls_found = re.findall(r'https?://[^\s<>"]+', submission_text + " " + submission_url)
        for url in urls_found:
            if "loom.com" in url:
                submission_types.append("loom_video")
                extracted_content.append(f"[Loom video link]: {url}")
            elif "github.com" in url:
                submission_types.append("github_repo")
                extracted_content.append(f"[GitHub repo]: {url}")
            elif "youtube.com" in url or "youtu.be" in url:
                submission_types.append("youtube_video")
                extracted_content.append(f"[YouTube video]: {url}")
            elif "drive.google.com" in url:
                submission_types.append("google_drive")
                extracted_content.append(f"[Google Drive]: {url}")
            elif "figma.com" in url:
                submission_types.append("figma_design")
                extracted_content.append(f"[Figma design]: {url}")
            else:
                submission_types.append("external_link")
                extracted_content.append(f"[External link]: {url}")

        if attachments:
            for att in attachments:
                fname = att.get("filename", "") or att.get("name", "")
                submission_types.append(f"attachment:{fname.split('.')[-1] if '.' in fname else 'unknown'}")
                extracted_content.append(f"[Attachment]: {fname}")

        if submission_text and len(submission_text.strip()) > 50:
            submission_types.append("text_response")
            extracted_content.append(f"[Text content]: {submission_text[:3000]}")
        elif submission_text and len(submission_text.strip()) < 50 and not urls_found:
            submission_types.append("incomplete")
            extracted_content.append(f"[Incomplete/minimal text]: {submission_text}")

        if not submission_types:
            submission_types.append("unknown")
            extracted_content.append(f"[Raw submission]: {str(submission)[:2000]}")

        # Build evaluation prompt
        rubric = (role.scoring_rubric or {}).get("assignment_rubric", "") if role else ""
        if not rubric:
            rubric = "Evaluate: completeness, code quality (if applicable), problem-solving approach, communication clarity, attention to detail."

        role_title = role.title if role else "the position"
        jd = (role.jd_text or "")[:2000] if role else ""

        eval_prompt = (
            f"# Assignment Evaluation\n\n"
            f"Role: {role_title}\n"
            f"JD excerpt: {jd}\n\n"
            f"## Evaluation rubric:\n{rubric}\n\n"
            f"## Submission types detected: {', '.join(submission_types)}\n\n"
            f"## Submission content:\n" + "\n".join(extracted_content) + "\n\n"
            f"## Instructions:\n"
            f"1. Assess what the candidate submitted and its type\n"
            f"2. For video/Loom links: note that link is present, assess if description accompanies it\n"
            f"3. For GitHub repos: note the repo link, assess any README or description provided\n"
            f"4. For incomplete submissions: flag what's missing, suggest what to request\n"
            f"5. Score 0-100 on: completeness, quality, effort, communication\n"
            f"6. Give overall verdict: strong_pass, pass, borderline, fail, incomplete\n"
            f"7. If submission is just a link with no context, verdict=incomplete, recommend asking for walkthrough\n\n"
            f"Respond in JSON:\n"
            f'{{"submission_types": [...], "completeness_score": N, "quality_score": N, '
            f'"effort_score": N, "communication_score": N, "overall_score": N, '
            f'"verdict": "...", "strengths": [...], "weaknesses": [...], '
            f'"missing_items": [...], "recommendation": "...", "summary": "..."}}'
        )

        client = get_llm_client()
        result = await client.complete(
            prompt=eval_prompt,
            model=client.smart,
            trace_name="evaluate_assignment",
            system="You evaluate candidate assignment submissions objectively. Return valid JSON only.",
            application_id=UUID(application_id),
            max_tokens=1500,
        )

        import json as _json
        try:
            evaluation = _json.loads(result.text)
        except _json.JSONDecodeError:
            evaluation = {
                "summary": result.text[:500],
                "verdict": "needs_review",
                "overall_score": None,
            }

        # Store evaluation + evidence
        async with session_scope() as session:
            app = await session.get(Application, UUID(application_id))
            if app:
                app.assignment_submission = {
                    **(app.assignment_submission or {}),
                    "evaluation": evaluation,
                    "evaluated_at": __import__("datetime").datetime.now(
                        __import__("datetime").UTC
                    ).isoformat(),
                }

            evidence_batch = [
                {
                    "application_id": UUID(application_id),
                    "candidate_id": app.candidate_id if app else None,
                    "source_stage": "assignment_evaluation",
                    "source_type": "assignment",
                    "extraction_method": "llm",
                    "fact_key": "assignment.overall_score",
                    "fact_value": evaluation.get("overall_score"),
                    "langfuse_trace_id": result.trace_id,
                    "model_version": result.model,
                },
                {
                    "application_id": UUID(application_id),
                    "candidate_id": app.candidate_id if app else None,
                    "source_stage": "assignment_evaluation",
                    "source_type": "assignment",
                    "extraction_method": "llm",
                    "fact_key": "assignment.verdict",
                    "fact_value": evaluation.get("verdict"),
                    "langfuse_trace_id": result.trace_id,
                    "model_version": result.model,
                },
                {
                    "application_id": UUID(application_id),
                    "candidate_id": app.candidate_id if app else None,
                    "source_stage": "assignment_evaluation",
                    "source_type": "assignment",
                    "extraction_method": "deterministic",
                    "fact_key": "assignment.submission_types",
                    "fact_value": submission_types,
                },
            ]
            if evaluation.get("strengths"):
                for s in evaluation["strengths"][:5]:
                    evidence_batch.append({
                        "application_id": UUID(application_id),
                        "candidate_id": app.candidate_id if app else None,
                        "source_stage": "assignment_evaluation",
                        "source_type": "assignment",
                        "extraction_method": "llm",
                        "fact_key": "assignment.strength",
                        "fact_value": s,
                    })

            await record_evidence_batch_verified(
                session, records=evidence_batch, application_id=UUID(application_id),
            )

            await log_audit(
                session,
                application_id=UUID(application_id),
                candidate_id=app.candidate_id if app else None,
                action="assignment_evaluated",
                actor="supervisor",
                details={
                    "verdict": evaluation.get("verdict"),
                    "overall_score": evaluation.get("overall_score"),
                    "submission_types": submission_types,
                    "trace_id": result.trace_id,
                },
            )

        return {
            "success": True,
            "verdict": evaluation.get("verdict"),
            "overall_score": evaluation.get("overall_score"),
            "submission_types": submission_types,
            "summary": evaluation.get("summary", "")[:300],
            "recommendation": evaluation.get("recommendation", "")[:200],
        }

    supervisor_tools = [
        ToolDefinition(
            name="advance_stage",
            description="Advance an application to a new pipeline stage",
            handler=advance_stage,
            parameters_schema={
                "type": "object",
                "properties": {
                    "application_id": {"type": "string"},
                    "target_stage": {"type": "string"},
                },
                "required": ["application_id", "target_stage"],
            },
            allowed_actors=frozenset({"supervisor"}),
            category="supervisor",
            requires_application_id=True,
            is_write=True,
        ),
        ToolDefinition(
            name="escalate_to_hr",
            description="Create an alert and notify HR about a situation requiring human judgment",
            handler=escalate_to_hr,
            parameters_schema={
                "type": "object",
                "properties": {
                    "application_id": {"type": "string"},
                    "reason": {"type": "string"},
                    "severity": {"type": "string", "enum": ["info", "warning", "urgent"]},
                },
                "required": ["application_id", "reason"],
            },
            allowed_actors=frozenset({"supervisor"}),
            category="supervisor",
            requires_application_id=True,
            is_write=True,
        ),
        ToolDefinition(
            name="send_candidate_email",
            description="Send a templated email to a candidate (looks up candidate email from application)",
            handler=send_candidate_email,
            parameters_schema={
                "type": "object",
                "properties": {
                    "application_id": {"type": "string"},
                    "template": {"type": "string", "description": "Email template name (e.g. screening_invite, reschedule, status_update)"},
                    "subject": {"type": "string"},
                    "variables": {"type": "object", "description": "Extra template variables"},
                },
                "required": ["application_id", "template"],
            },
            allowed_actors=frozenset({"supervisor"}),
            category="supervisor",
            requires_application_id=True,
            is_write=True,
        ),
        ToolDefinition(
            name="pause_assignment_timer",
            description="Pause the assignment deadline for a candidate (e.g., during an inbound call)",
            handler=pause_assignment_timer,
            parameters_schema={
                "type": "object",
                "properties": {
                    "application_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["application_id", "reason"],
            },
            allowed_actors=frozenset({"supervisor"}),
            category="supervisor",
            requires_application_id=True,
            is_write=True,
        ),
        ToolDefinition(
            name="send_candidate_whatsapp",
            description="Send a WhatsApp template message to a candidate",
            handler=send_candidate_whatsapp,
            parameters_schema={
                "type": "object",
                "properties": {
                    "application_id": {"type": "string"},
                    "template_name": {"type": "string", "description": "Pre-approved WhatsApp template name"},
                    "body_params": {"type": "array", "items": {"type": "string"}, "description": "Template body parameters"},
                },
                "required": ["application_id", "template_name"],
            },
            allowed_actors=frozenset({"supervisor"}),
            category="supervisor",
            requires_application_id=True,
            is_write=True,
        ),
        ToolDefinition(
            name="reschedule_meeting",
            description="Reschedule a candidate's interview meeting by finding new available slots",
            handler=reschedule_meeting,
            parameters_schema={
                "type": "object",
                "properties": {
                    "application_id": {"type": "string"},
                    "reason": {"type": "string"},
                    "preferred_date": {"type": "string", "description": "ISO date candidate prefers (optional)"},
                },
                "required": ["application_id", "reason"],
            },
            allowed_actors=frozenset({"supervisor"}),
            category="supervisor",
            requires_application_id=True,
            is_write=True,
        ),
        ToolDefinition(
            name="mark_withdrawal",
            description="Mark a candidate as withdrawn, transition to rejected, and notify hiring manager",
            handler=mark_withdrawal,
            parameters_schema={
                "type": "object",
                "properties": {
                    "application_id": {"type": "string"},
                    "reason": {"type": "string", "description": "Why the candidate withdrew"},
                },
                "required": ["application_id", "reason"],
            },
            allowed_actors=frozenset({"supervisor"}),
            category="supervisor",
            requires_application_id=True,
            is_write=True,
        ),
        ToolDefinition(
            name="generate_and_send_offer",
            description="Generate a personalized offer note and send the offer email to the candidate",
            handler=generate_and_send_offer,
            parameters_schema={
                "type": "object",
                "properties": {
                    "application_id": {"type": "string"},
                    "offer_note": {"type": "string", "description": "Custom offer note (generated if not provided)"},
                },
                "required": ["application_id"],
            },
            allowed_actors=frozenset({"supervisor"}),
            category="supervisor",
            requires_application_id=True,
            is_write=True,
        ),
        ToolDefinition(
            name="get_evidence_chain",
            description="Retrieve the full evidence and decision trail for an application",
            handler=get_evidence_chain,
            parameters_schema={
                "type": "object",
                "properties": {
                    "application_id": {"type": "string"},
                },
                "required": ["application_id"],
            },
            allowed_actors=frozenset({"supervisor", "recruiter"}),
            category="supervisor",
            requires_application_id=True,
            is_write=False,
        ),
        ToolDefinition(
            name="send_nudge",
            description="Send escalating nudge to stalled candidate (gentle → urgent → final). Auto-picks template based on nudge_type.",
            handler=send_nudge,
            parameters_schema={
                "type": "object",
                "properties": {
                    "application_id": {"type": "string"},
                    "nudge_type": {"type": "string", "enum": ["gentle", "urgent", "final"], "description": "Escalation level"},
                    "custom_message": {"type": "string", "description": "Optional custom message to include"},
                },
                "required": ["application_id", "nudge_type"],
            },
            allowed_actors=frozenset({"supervisor"}),
            category="supervisor",
            requires_application_id=True,
            is_write=True,
        ),
        ToolDefinition(
            name="answer_candidate_question",
            description="Answer a candidate's question using role data, evidence, and LLM. Sends response via email.",
            handler=answer_candidate_question,
            parameters_schema={
                "type": "object",
                "properties": {
                    "application_id": {"type": "string"},
                    "question": {"type": "string", "description": "The candidate's question"},
                    "channel": {"type": "string", "enum": ["email", "chat", "whatsapp"], "default": "email"},
                },
                "required": ["application_id", "question"],
            },
            allowed_actors=frozenset({"supervisor"}),
            category="supervisor",
            requires_application_id=True,
            is_write=True,
        ),
        ToolDefinition(
            name="schedule_interview",
            description="Initiate first-time interview scheduling (technical/ceo/hr round)",
            handler=schedule_interview,
            parameters_schema={
                "type": "object",
                "properties": {
                    "application_id": {"type": "string"},
                    "round": {"type": "string", "enum": ["technical", "ceo", "hr"]},
                },
                "required": ["application_id", "round"],
            },
            allowed_actors=frozenset({"supervisor"}),
            category="supervisor",
            requires_application_id=True,
            is_write=True,
        ),
        ToolDefinition(
            name="get_role_candidates_summary",
            description="Get comparative summary of all active candidates for a role (scores, stages, ranking)",
            handler=get_role_candidates_summary,
            parameters_schema={
                "type": "object",
                "properties": {
                    "role_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["role_id"],
            },
            allowed_actors=frozenset({"supervisor", "recruiter"}),
            category="supervisor",
            is_write=False,
        ),
        ToolDefinition(
            name="check_panel_availability",
            description="Check interview panel calendar availability before proposing meeting times",
            handler=check_panel_availability,
            parameters_schema={
                "type": "object",
                "properties": {
                    "application_id": {"type": "string"},
                    "round": {"type": "string", "enum": ["technical", "ceo", "hr"]},
                    "preferred_dates": {"type": "array", "items": {"type": "string"}, "description": "ISO dates to check"},
                },
                "required": ["application_id", "round"],
            },
            allowed_actors=frozenset({"supervisor"}),
            category="supervisor",
            requires_application_id=True,
            is_write=False,
        ),
        ToolDefinition(
            name="evaluate_assignment",
            description="Smart evaluate candidate assignment — handles Loom videos, GitHub repos, documents, incomplete submissions",
            handler=evaluate_assignment,
            parameters_schema={
                "type": "object",
                "properties": {
                    "application_id": {"type": "string"},
                },
                "required": ["application_id"],
            },
            allowed_actors=frozenset({"supervisor"}),
            category="supervisor",
            requires_application_id=True,
            is_write=True,
        ),
    ]

    for tool in supervisor_tools:
        registry.register(tool)
