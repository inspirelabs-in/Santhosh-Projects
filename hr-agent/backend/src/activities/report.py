"""Stage 8b: Post-interview report synthesis.

Input: application_id + optional transcript text (otherwise fetched from R2
via `transcript_r2_key`). Output: a structured `InterviewReport` persisted
to `interviews.report` JSONB and pushed to the HR dashboard + Slack.
"""

# [SCRAPE] dead: run_interview_report/interview_report_activity superseded by
# v1_meeting_analysis + v1_journey_report. No live importer.
from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from temporalio import activity

from src.channels import teams as teams_channel
from src.config import get_settings
from src.db.connection import session_scope
from src.db.repositories.audit import log_audit
from src.db.repositories.candidate import get_application, get_candidate
from src.db.repositories.interview import get_latest_for_application
from src.db.repositories.role import get_role
from src.llm.client import get_llm_client
from src.llm.model_registry import Stage, model_for
from src.llm.prompt_manager import compile_prompt
from src.llm.prompts import INTERVIEW_REPORT_V1, INTERVIEW_REPORT_VERSION
from src.models.llm_outputs import InterviewReport
from src.models.scheduling import InterviewStatus
from src.services.file_storage import download

logger = logging.getLogger(__name__)
_settings = get_settings()

_MAX_TRANSCRIPT_CHARS = 40_000


@dataclass
class InterviewReportInput:
    candidate_id: UUID
    application_id: UUID
    transcript_text: str | None = None  # pass inline for tests; otherwise fetched from R2


@dataclass
class InterviewReportResult:
    interview_id: UUID
    recommendation: str
    trace_id: str | None


async def run_interview_report(payload: InterviewReportInput) -> InterviewReportResult:
    async with session_scope() as session:
        app = await get_application(session, payload.application_id)
        candidate = await get_candidate(session, payload.candidate_id)
        if app is None or candidate is None:
            raise ValueError("application or candidate not found")
        role = await get_role(session, app.role_id) if app.role_id else None
        interview = await get_latest_for_application(session, payload.application_id)
        if interview is None:
            raise ValueError("no interview found for this application")
        snapshot = {
            "role_title": role.title if role else "(unknown role)",
            "candidate_name": candidate.name or "(unknown)",
            "panel": [m.get("calendar_email") for m in (role.interviewer_panel or [])] if role else [],
            "competencies": list((role.scoring_rubric or {}).get("interview_competencies", []))
            if role
            else [],
            "transcript_r2_key": interview.transcript_r2_key,
            "interview_id": interview.id,
        }

    transcript = payload.transcript_text
    if not transcript and snapshot["transcript_r2_key"]:
        raw = await download(_settings.r2_bucket_resumes, snapshot["transcript_r2_key"])
        transcript = raw.decode("utf-8", errors="replace")

    if not transcript:
        raise ValueError("no transcript text available to synthesise report")

    transcript = transcript[:_MAX_TRANSCRIPT_CHARS]
    competencies_list = (
        ", ".join(snapshot["competencies"])
        if snapshot["competencies"]
        else "role-appropriate technical and behavioural competencies"
    )

    import json as _json
    _cc_json = _json.dumps(snapshot.get("company_context") or {}, ensure_ascii=False, default=str)[:2000]
    _es_json = _json.dumps(snapshot.get("evaluation_spec") or {}, ensure_ascii=False, default=str)[:2000]

    prompt = compile_prompt(
        "interview_report",
        fallback=INTERVIEW_REPORT_V1,
        company_context_json=_cc_json,
        evaluation_spec_json=_es_json,
        role_title=snapshot["role_title"],
        candidate_name=snapshot["candidate_name"],
        interviewer_names=", ".join(snapshot["panel"]) or "(unspecified)",
        competencies_list=competencies_list,
        transcript_text=transcript,
    )

    client = get_llm_client()
    result = await client.complete(
        prompt=prompt,
        response_model=InterviewReport,
        model=model_for(Stage.INTERVIEW_REPORT),
        trace_name="interview_report",
        prompt_version=INTERVIEW_REPORT_VERSION,
        candidate_id=payload.candidate_id,
        application_id=payload.application_id,
        temperature=0.0,
        max_tokens=3000,
    )
    report = result.parsed

    async with session_scope() as session:
        fresh_interview = await get_latest_for_application(session, payload.application_id)
        if fresh_interview is not None:
            fresh_interview.report = report.model_dump(mode="json")
            fresh_interview.status = InterviewStatus.COMPLETED.value
        await log_audit(
            session,
            action="interview_report_generated",
            actor="agent",
            candidate_id=payload.candidate_id,
            application_id=payload.application_id,
            details={
                "recommendation": report.recommendation,
                "competency_ratings": [c.model_dump() for c in report.competencies],
                "concerns": report.concerns,
                "strengths": report.strengths,
            },
            model_version=result.model,
            prompt_version=result.prompt_version,
            langfuse_trace_id=result.trace_id,
        )

    # Notify HR (non-blocking -- failures only logged).
    await teams_channel.notify_hr(
        title=f"Interview report ready: {snapshot['candidate_name']} ({snapshot['role_title']})",
        text=f"*Recommendation:* `{report.recommendation}`\n\n{report.summary}",
        fields={
            "application_id": str(payload.application_id),
            "concerns": ", ".join(report.concerns) or "—",
            "strengths": ", ".join(report.strengths) or "—",
        },
    )

    return InterviewReportResult(
        interview_id=snapshot["interview_id"],
        recommendation=report.recommendation,
        trace_id=result.trace_id,
    )


@activity.defn(name="interview_report")
async def interview_report_activity(payload: InterviewReportInput) -> InterviewReportResult:
    return await run_interview_report(payload)
